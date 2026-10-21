"""Draft the week's positions from a saved scan run: final labels -> plans -> fit -> book.

Reads one run from the results DB (its labels, cards and price snapshots), plans and sizes
every ticker whose final label is ACT at the research agent's entry and stop (its card's
``trade_*`` fields, ADR 0023; no agent levels, no plan) (:mod:`solaraalpha.portfolio.plan`), checks the plans
against the book (:mod:`solaraalpha.portfolio.fit`) and writes the accepted ones as approved
positions (no owner approval, ADR 0026) with their entry order, the full plan in the ``drafted`` payload, every justifying
card frozen as entry conditions and the research's kill criteria as thesis conditions. Re-running
for the same run is a no-op for tickers that already have a position from it. The weekly pack
reuses :func:`plan_run` in dry-run mode. See docs/decisions/0015.

When the portfolio manager has recorded its verdict for the run (ADR 0027) the planner follows
it: only the allocated tickers are planned, each sized at the PM's ``risk_pct``, fit-checked in
the PM's order, with the positions it rotated out no longer taking a slot, heat or sector room
(their sale proceeds count as cash), and the drafts are **approved by the PM**. Without a PM
verdict (no agents, or the PM errored) the drafts wait for the owner as before.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.agents import pm_decision, trade_levels
from solaraalpha.data.clean import to_weekly
from solaraalpha.log import get_logger
from solaraalpha.pipeline import Condition
from solaraalpha.portfolio import book, risk
from solaraalpha.portfolio.fit import FitReport, fit, rank_score
from solaraalpha.portfolio.plan import TradePlan, adv_gbp, make_plan
from solaraalpha.portfolio.rules import Rules, load_rules
from solaraalpha.store import Conn

log = get_logger(__name__)

PM_ACTOR = "agent:pm"
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
    rank: int | None = None  # screen rank (1 = best), #125; every ticker past the floor, #192
    shortlist: str | None = None  # why it was shortlisted ("rank N" / "catalyst: ..."), #192


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
    pm: dict[str, Any] | None = None  # the PM's verdict card the plans followed (ADR 0027)
    rotating: list[int] = field(default_factory=list)  # open positions the PM is exiting
    approved: list[int] = field(default_factory=list)  # earlier drafts the PM approved as drafted


def final_labels(conn: Conn, run_id: int) -> list[Candidate]:
    """Every ticker's final label in ``run_id``: the latest stage present, research > validate
    > screen. ACT tickers first in screen-rank order (unranked last), then by ticker. ``shortlist``
    is the screen row's why (the screen ACT tickers are the week's shortlist, #192)."""
    df = store.query(
        "SELECT ticker, stage, label, reason, rank, shortlist FROM labels WHERE run_id = ?",
        conn,
        [run_id],
    )
    out = []
    for ticker, g in df.groupby("ticker"):
        by = {str(r["stage"]): (str(r["label"]), str(r["reason"])) for r in g.to_dict("records")}
        stage = next(s for s in STAGE_ORDER if s in by)
        scr = g.loc[g["stage"] == "screen"]
        rank = int(scr["rank"].iloc[0]) if len(scr) and pd.notna(scr["rank"].iloc[0]) else None
        why = (
            str(scr["shortlist"].iloc[0])
            if len(scr) and pd.notna(scr["shortlist"].iloc[0])
            else None
        )
        out.append(
            Candidate(
                str(ticker),
                by[stage][0],
                stage,
                {s: by[s][1] for s in STAGE_ORDER if s in by},
                rank,
                why,
            )
        )
    return sorted(out, key=lambda c: (c.label != "ACT", c.rank is None, c.rank or 0, c.ticker))


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


def _exiting(conn: Conn, position_id: int) -> bool:
    """An exit order for the position is still working (drafted, approved or placed)."""
    od = book.orders(conn, position_id)
    return bool(((od["purpose"] == "exit") & od["status"].isin(list(book.OPEN_ORDER))).any())


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


def plan_run(
    conn: Conn,
    run_id: int,
    rules: Rules | None = None,
    dry_run: bool = False,
    replan: bool = False,
    use_pm: bool = True,
) -> PlanRun:
    """Plan, size and fit-check the run's final-ACT tickers; write the accepted plans to the
    book unless ``dry_run``. Once any agent label exists in the run, a ticker whose final
    label is still the screen's (the agents never reached it) is reported, not planned.
    ``replan``: drafts from this run that were rejected or cancelled no longer count as done,
    so those tickers are planned again (after a rules or planner change). ``use_pm``: follow
    the PM's verdict for the run when there is one (ADR 0027); False gives the plans at the
    rules' ``risk_per_trade`` in screen-rank order (the PM's own pack)."""
    rules = rules or load_rules()
    pm = pm_decision(conn, run_id) if use_pm else None
    alloc = {str(a["ticker"]): a for a in pm["allocation"]} if pm else {}
    started = str(store.run_info(run_id, conn=conn)["started_at"])
    candidates = final_labels(conn, run_id)
    existing = book.positions(conn)
    from solaraalpha.portfolio.weekly_pack import latest_regime

    regime = latest_regime(conn)
    risk_off = bool(regime and regime.get("regime") == "risk_off")
    from_run = existing[existing["opened_from_run_id"] == run_id]
    if replan:
        from_run = from_run[~from_run["status"].isin(["rejected", "cancelled"])]
    done = set(from_run["ticker"])

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
    # open positions the PM rotated out (an exit order working): their room is the PM's to
    # reuse, and the sale proceeds count as cash for sizing
    rotating = [
        int(r["position_id"])
        for r in (pm["rotations"] if pm else [])
        if book.position_status(conn, int(r["position_id"])) == "open"
        and _exiting(conn, int(r["position_id"]))
    ]
    # a buy paid for by a rotation waits for that sale (the one it names, else every one)
    waits: dict[str | None, list[int]] = {}
    for r in pm["rotations"] if pm else []:
        if int(r["position_id"]) in rotating:
            waits.setdefault(r.get("funds"), []).append(int(r["position_id"]))
    held_fit = held[~held["position_id"].isin(rotating)]
    cash = equity.cash + float(held.loc[held["position_id"].isin(rotating), "value"].sum())

    plans: list[TradePlan] = []
    errors: list[tuple[str, str]] = []
    scores: dict[str, float] = {}
    returns: dict[str, pd.Series] = {}
    cards_by: dict[str, dict[str, dict[str, Any]]] = {}
    skipped: list[str] = []
    # Once the agents have worked the run, a shortlisted ticker they never reached (past
    # max_agent_tickers) is not planned on the screen's ACT alone (#192).
    reviewed = any(c.stage != "screen" for c in candidates)
    for c in candidates:
        if c.label != "ACT":
            continue
        if c.ticker in done:
            skipped.append(c.ticker)
            continue
        if reviewed and c.stage == "screen":
            errors.append((c.ticker, "not reviewed by the agents (beyond max_agent_tickers)"))
            continue
        if pm and c.ticker not in alloc:
            errors.append((c.ticker, "not funded by the PM"))
            continue
        cards = store.load_cards(run_id, c.ticker, conn=conn)
        cards_by[c.ticker] = cards
        if "levels" not in cards or "technical" not in cards:
            errors.append((c.ticker, "no levels / technical card in the run"))
            continue
        # ADR 0023: the research agent sets the entry and the stop; no rule-based plan.
        agent = trade_levels(cards.get("research"))
        if agent is None:
            errors.append((c.ticker, "no agent levels (no research verdict with a trade)"))
            continue
        daily = _prices(c.ticker, started, conn)
        liq = (cards.get("liquidity") or {}).get("adv_gbp_20")
        if liq is not None and not (isinstance(liq, float) and math.isnan(liq)):
            adv: float | None = float(liq)
        else:
            adv = adv_gbp(daily) if daily is not None and not daily.empty else None
        currency = str(daily.attrs.get("currency") or "GBp") if daily is not None else "GBp"
        try:
            plan = make_plan(
                c.ticker,
                cards["levels"],
                cards["technical"],
                cards.get("events"),
                replace(rules, risk_per_trade=float(alloc[c.ticker]["risk_pct"]))
                if c.ticker in alloc
                else rules,
                equity.equity,
                cash,
                None if adv is None or math.isnan(adv) else adv,
                currency,
                agent=agent,
            )
            if risk_off:  # the market regime (#142): every plan carries the flag while it lasts
                plan = replace(plan, flags=(*plan.flags, "market risk_off"))
            plans.append(plan)
        except ValueError as e:
            errors.append((c.ticker, str(e)))
            continue
        # the PM's order is the priority when it decided, else the screen's rank score
        scores[c.ticker] = len(alloc) - list(alloc).index(c.ticker) if pm else rank_score(cards)
        r = _weekly_returns(c.ticker, started, conn)
        if r is not None:
            returns[c.ticker] = r
    for ticker in open_tickers:
        r = _weekly_returns(ticker, started, conn)
        if r is not None:
            returns[ticker] = r

    from solaraalpha.portfolio.weekly_pack import drawdown_from_store

    report = fit(
        plans,
        held_fit,
        pending_positions(conn),
        equity,
        rules,
        scores,
        returns,
        drawdown=drawdown_from_store(conn, started, rules),
    )
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
        pm=pm,
        rotating=rotating,
    )
    if dry_run:
        return out
    # a draft from this run the PM funds but the owner never approved (drafted before the PM,
    # or a re-run) is approved as drafted: the PM owns it now
    for pid, ticker in zip(from_run["position_id"], from_run["ticker"], strict=True):
        if ticker in alloc and book.position_status(conn, int(pid)) == "draft":
            book.approve_position(
                conn, int(pid), f"funded by the PM as drafted: {alloc[ticker]['reason']}", PM_ACTOR
            )
            out.approved.append(int(pid))
    for plan in report.accepted:
        by = (  # the PM owns the allocation when it decided (ADR 0027)
            {
                "approver": PM_ACTOR,
                "approval": f"allocated by the PM at {float(alloc[plan.ticker]['risk_pct']):.1%}"
                f" risk: {alloc[plan.ticker]['reason']}",
            }
            if pm
            else {}
        )
        out.written[plan.ticker] = write_plan(
            conn,
            plan,
            report.flags.get(plan.ticker, []),
            cards_by[plan.ticker],
            run_id,
            rules,
            started,
            waits_for=waits.get(plan.ticker, []) + waits.get(None, []),
            **by,
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
    approver: str = book.PIPELINE,
    approval: str = "approved as planned (ADR 0026)",
    waits_for: list[int] | None = None,
) -> int:
    """Position + entry order + entry conditions + thesis conditions for one plan, approved as
    it is drafted by ``approver`` (the pipeline, or the PM when it allocated it, ADR 0027): the
    owner is told what to place, never asked to approve (ADR 0026)."""
    research = cards.get("research") or {}
    thesis = str(research.get("reasons") or "")
    payload = {**asdict(plan), "fit_flags": list(flags)}
    if waits_for:  # the positions whose sale pays for it (a PM rotation)
        payload["waits_for"] = sorted(set(waits_for))
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
    book.approve_position(conn, pid, approval, approver, at)
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
