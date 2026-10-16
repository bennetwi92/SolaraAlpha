"""Draft the week's positions from a saved scan run: final labels -> plans -> fit -> book.

Reads one run from the results DB (its labels, cards and price snapshots), plans and sizes
every ticker whose final label is ACT (:mod:`solaraalpha.portfolio.plan`), checks the plans
against the book (:mod:`solaraalpha.portfolio.fit`) and writes the accepted ones as draft
positions with their entry order, the full plan in the ``drafted`` payload, every justifying
card frozen as entry conditions and the research's kill criteria as thesis conditions. Re-running
for the same run is a no-op for tickers that already have a position from it. The weekly pack
reuses :func:`plan_run` in dry-run mode. See docs/decisions/0015.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.data.clean import to_weekly
from solaraalpha.log import get_logger
from solaraalpha.pipeline import Condition
from solaraalpha.portfolio import book, risk
from solaraalpha.portfolio.fit import FitReport, fit, rank_score
from solaraalpha.portfolio.plan import TradePlan, adv_gbp, make_plan
from solaraalpha.portfolio.rules import Rules, load_rules
from solaraalpha.store import Conn

log = get_logger(__name__)

STAGE_ORDER = ("research", "validate", "screen")  # latest stage present decides
ENTRY_CONDITION_MODELS = (
    "technical",
    "levels",
    "relative_strength",
    "fundamentals",
    "validate",
    "research",
)


@dataclass(frozen=True)
class Candidate:
    ticker: str
    label: str
    stage: str  # the stage that decided the final label
    reasons: dict[str, str]  # stage -> reason, every stage present


@dataclass
class PlanRun:
    run_id: int
    candidates: list[Candidate]  # every ticker with a label in the run, ACT first
    plans: list[TradePlan]
    plan_errors: list[tuple[str, str]]  # ticker, why no plan could be made
    report: FitReport
    written: dict[str, int] = field(default_factory=dict)  # ticker -> position_id
    skipped: list[str] = field(default_factory=list)  # already drafted from this run
    equity: risk.Equity | None = None
    held: pd.DataFrame | None = None  # risk.holdings at the run's closes
    prices: dict[str, float] = field(default_factory=dict)  # the closes used


def final_labels(conn: Conn, run_id: int) -> list[Candidate]:
    """Every ticker's final label in ``run_id``: the latest stage present, research > validate
    > screen. ACT tickers first, then by ticker."""
    df = store.query(
        "SELECT ticker, stage, label, reason FROM labels WHERE run_id = ?", conn, [run_id]
    )
    out = []
    for ticker, g in df.groupby("ticker"):
        by = {str(r["stage"]): (str(r["label"]), str(r["reason"])) for r in g.to_dict("records")}
        stage = next(s for s in STAGE_ORDER if s in by)
        out.append(
            Candidate(
                str(ticker), by[stage][0], stage, {s: by[s][1] for s in STAGE_ORDER if s in by}
            )
        )
    return sorted(out, key=lambda c: (c.label != "ACT", c.ticker))


def _weekly_returns(ticker: str, as_seen: str, conn: Conn) -> pd.Series | None:
    daily = _prices(ticker, as_seen, conn)
    if daily is None or daily.empty:
        return None
    weekly = to_weekly(daily, as_of=pd.Timestamp(as_seen))
    return weekly["adj_close"].pct_change(fill_method=None).rename(ticker)


def _prices(ticker: str, as_seen: str, conn: Conn) -> pd.DataFrame | None:
    try:
        df = store._latest(store._read(conn, "prices", ticker, as_seen), ["date"])
    except Exception:
        return None
    if df.empty:
        return None
    currency = df["currency"].iloc[-1]
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("date")), name="date").tz_localize("UTC")
    df = df.sort_index()[store._PRICE_COLS[:-1]].astype("float64")
    df.attrs = {"ticker": ticker, "currency": currency}
    return df


def pending_positions(conn: Conn) -> pd.DataFrame:
    """Draft / approved positions not yet open, with their planned value and risk (GBP) and
    sector, shaped like :func:`risk.holdings`."""
    pos = book.positions(conn)
    pos = pos[pos["status"].isin(["draft", "approved"])]
    rows = []
    for p in pos.to_dict("records"):
        ev = book.position_events(conn, int(p["position_id"]))
        payloads = [x for x in ev.loc[ev["kind"].isin(["drafted", "amended"]), "payload"]]
        plan = payloads[-1] if payloads else {}
        qty = int(plan.get("qty") or plan.get("shares") or 0)
        entry, stop = plan.get("entry"), plan.get("stop")
        value = plan.get("position_value")
        if value is None and entry is not None:
            value = book.to_account(float(entry) * qty, str(p["currency"]))
        risk_amt = plan.get("risk_amount")
        if risk_amt is None and entry is not None and stop is not None:
            risk_amt = book.to_account((float(entry) - float(stop)) * qty, str(p["currency"]))
        rows.append(
            {
                "position_id": p["position_id"],
                "ticker": p["ticker"],
                "value": float(value or 0.0),
                "risk": float(risk_amt or 0.0),
                "sector": risk.sector_of(str(p["ticker"])),
            }
        )
    return pd.DataFrame(rows, columns=["position_id", "ticker", "value", "risk", "sector"])


def plan_run(conn: Conn, run_id: int, rules: Rules | None = None, dry_run: bool = False) -> PlanRun:
    """Plan, size and fit-check the run's ACT tickers; write the accepted plans to the book
    unless ``dry_run``."""
    rules = rules or load_rules()
    started = str(store.run_info(run_id, conn=conn)["started_at"])
    candidates = final_labels(conn, run_id)
    existing = book.positions(conn)
    done = set(existing.loc[existing["opened_from_run_id"] == run_id, "ticker"])

    # Equity and cash at the run's closes (held tickers not in the run: last stored close).
    prices: dict[str, float] = {}
    open_tickers = set(existing.loc[existing["status"] == "open", "ticker"])
    for ticker in open_tickers | {c.ticker for c in candidates if c.label == "ACT"}:
        cards = store.load_cards(run_id, ticker, conn=conn)
        close = (cards.get("levels") or cards.get("technical") or {}).get("close")
        if close is None:
            daily = _prices(ticker, started, conn)
            close = (
                float(daily["close"].iloc[-1]) if daily is not None and not daily.empty else None
            )
        if close is not None:
            prices[ticker] = float(close)
    held, equity, _ = risk.snapshot(conn, prices, as_of=started)

    plans: list[TradePlan] = []
    errors: list[tuple[str, str]] = []
    scores: dict[str, float] = {}
    returns: dict[str, pd.Series] = {}
    cards_by: dict[str, dict[str, dict[str, Any]]] = {}
    skipped: list[str] = []
    for c in candidates:
        if c.label != "ACT":
            continue
        if c.ticker in done:
            skipped.append(c.ticker)
            continue
        cards = store.load_cards(run_id, c.ticker, conn=conn)
        cards_by[c.ticker] = cards
        if "levels" not in cards or "technical" not in cards:
            errors.append((c.ticker, "no levels / technical card in the run"))
            continue
        daily = _prices(c.ticker, started, conn)
        adv = adv_gbp(daily) if daily is not None and not daily.empty else None
        currency = str(daily.attrs.get("currency") or "GBp") if daily is not None else "GBp"
        try:
            plans.append(
                make_plan(
                    c.ticker,
                    cards["levels"],
                    cards["technical"],
                    cards.get("events"),
                    rules,
                    equity.equity,
                    equity.cash,
                    None if adv is None or math.isnan(adv) else adv,
                    currency,
                )
            )
        except ValueError as e:
            errors.append((c.ticker, str(e)))
            continue
        scores[c.ticker] = rank_score(cards)
        r = _weekly_returns(c.ticker, started, conn)
        if r is not None:
            returns[c.ticker] = r
    for ticker in open_tickers:
        r = _weekly_returns(ticker, started, conn)
        if r is not None:
            returns[ticker] = r

    report = fit(plans, held, pending_positions(conn), equity, rules, scores, returns)
    out = PlanRun(
        run_id,
        candidates,
        plans,
        errors,
        report,
        skipped=skipped,
        equity=equity,
        held=held,
        prices=prices,
    )
    if dry_run:
        return out
    for plan in report.accepted:
        out.written[plan.ticker] = write_plan(
            conn,
            plan,
            report.flags.get(plan.ticker, []),
            cards_by[plan.ticker],
            run_id,
            rules,
            started,
        )
    return out


def write_plan(
    conn: Conn,
    plan: TradePlan,
    flags: list[str],
    cards: dict[str, dict[str, Any]],
    run_id: int,
    rules: Rules,
    at: str,
) -> int:
    """Draft position + entry order + entry conditions + thesis conditions for one plan."""
    research = cards.get("research") or {}
    thesis = str(research.get("reasons") or "")
    payload = {**asdict(plan), "fit_flags": list(flags)}
    pid, _ = book.draft_position(
        conn,
        plan.ticker,
        plan.entry,
        plan.stop,
        plan.shares,
        plan.entry_type,
        None if math.isnan(plan.target) else plan.target,
        horizon_weeks=plan.horizon_weeks[1],
        thesis=thesis,
        opened_from_run_id=run_id,
        payload=json.loads(json.dumps(payload, default=str)),
        actor=book.PIPELINE,
        at=at,
        rules=rules,
        valid_until=plan.valid_until.strftime("%Y-%m-%d"),
    )
    frozen = {m: cards[m] for m in ENTRY_CONDITION_MODELS if m in cards}
    book.add_entry_conditions(conn, pid, run_id, frozen)
    for crit in kill_criteria(research):
        book.add_thesis_condition(
            conn, pid, crit["condition"], crit["kind"], crit.get("evidence_id"), at=at
        )
    log.info("%s: drafted position %d from run %d", plan.ticker, pid, run_id)
    return pid


def kill_criteria(research: dict[str, Any]) -> list[dict[str, Any]]:
    """The research card's ``kill_criteria`` (JSON list of condition / kind / evidence_id);
    ``machine`` ones must parse with :class:`Condition`, else they are stored as ``agent``."""
    raw = research.get("kill_criteria")
    if not raw:
        return []
    try:
        items = json.loads(raw) if isinstance(raw, str) else list(raw)
    except (TypeError, ValueError):
        return [{"condition": str(raw), "kind": "agent", "evidence_id": None}]
    out = []
    for item in items:
        if isinstance(item, str):
            item = {"condition": item, "kind": "machine"}
        cond, kind = str(item.get("condition", "")).strip(), str(item.get("kind", "agent"))
        if not cond:
            continue
        if kind == "machine":
            try:
                Condition.parse(cond)
            except ValueError:
                kind = "agent"
        out.append({"condition": cond, "kind": kind, "evidence_id": item.get("evidence_id")})
    return out
