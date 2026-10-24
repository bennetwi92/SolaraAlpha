"""The portfolio manager's pack: the whole book and the week's ideas on one page (ADR 0027).

Built from the DB only, after the research stage: every research-ACT candidate (the research
verdict, its trade levels, the financing card, the results-day gap risk, the plan at the rules'
risk per trade and what each risk in the PM's band would buy after the caps), the research
WATCH names (context: they cannot be funded), the open positions (P&L, R, stop, weeks held,
next event, the latest monitor and review view) and the pending entries, the money (equity,
cash, heat, sector exposure, drawdown and circuit breaker), the market regime, the run's macro
notes, weekly-return correlations across candidates and holdings, the limits, and the week's
agent failures. ADR 0032's "Must" cards: per candidate the validator's view, the research note
and the last close against the entry; per holding its kill criteria as the latest daily run found
them; per pending entry its validity, whether it lapses before the next weekly run (not kept unless
the PM renews it, ADR 0034), whether it was placed, what it waits for and the last close;
for the desk the run's failures by item, the scan's coverage, the book against Trading212, the
results calendar and what changed in the code since the previous scan. The PM decides the allocation, the rotations and the renewals from it and records them
through ``record.py verdict`` (:func:`agents.record_book`).
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.agents import BOOK, SECTOR_PREFIX, lapsing, next_run, trade_levels
from solaraalpha.config import settings
from solaraalpha.portfolio import book, risk
from solaraalpha.portfolio.fit import RANK_NOTE
from solaraalpha.portfolio.planner import _weekly_returns, pending_positions, plan_run
from solaraalpha.portfolio.rules import Rules, load_rules
from solaraalpha.portfolio.weekly_pack import (
    agent_week,
    drawdown_from_store,
    latest_regime,
    latest_review_cards,
)
from solaraalpha.store import Conn

CORRELATION_WEEKS = 26
CORRELATION_SHOW = 0.5  # pairs at or above this go in the pack
MIN_EVENT_MOVES = 4  # fewer past results days than this: the gap estimate is a small sample
STALE_DAYS = 5  # a saved bar older than this before the run: the stock was not seen
CALENDAR_WEEKS = 4  # the results calendar looks this far ahead
LIMIT_KEYS = (
    "risk_per_trade",
    "risk_per_trade_range",
    "max_positions",
    "max_position_pct",
    "max_sector_pct",
    "max_heat",
    "max_adv_pct",
    "cash_buffer",
    "circuit_breaker",
    "max_drawdown_new_entries",
    "horizon_weeks",
    "target_moves",
    "event_flag_days",
)


def _clean(v: Any) -> Any:
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v


def latest_card(conn: Conn, ticker: str, model: str) -> dict[str, Any] | None:
    """The latest ``model`` card for ``ticker`` across runs (with its ``run_id``), or None."""
    row = conn.execute(
        "SELECT MAX(run_id) FROM results WHERE ticker = ? AND model = ?", (ticker, model)
    ).fetchone()
    if not row or row[0] is None:
        return None
    card = store.load_cards(int(row[0]), ticker, conn=conn).get(model)
    return {"run_id": int(row[0]), **card} if card else None


def financing(card: dict[str, Any] | None) -> dict[str, Any] | None:
    """The financing card (runway, dilution, raises; #218) as JSON-able context, or None."""
    if not card:
        return None
    return {k: _clean(v) for k, v in card.items() if k != "date"}


def _naive(ts: Any) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_convert(None) if t.tzinfo is not None else t


def event_gap(
    events: dict[str, Any] | None,
    sized: dict[str, Any] | None,
    started: str,
    equity: float,
    rules: Rules,
) -> dict[str, Any] | None:
    """What a results-day gap like the stock's past ones would cost (#219, #214 F5): the next
    scheduled event, whether it falls in the first half of the horizon, the past moves
    (``n``, ``worst``, ``p90_abs``; ``small_sample`` under :data:`MIN_EVENT_MOVES`) and, at each
    risk of the band (``sizing``), the loss in GBP and % of equity at the worst and p90 moves.
    Information only: sizing is unchanged. None without an events card."""
    if not events:
        return None
    n = int(events.get("event_moves") or 0)
    worst, p90 = _clean(events.get("event_worst_move")), _clean(events.get("event_p90_move"))
    nxt = _clean(events.get("next_event"))
    weeks = (_naive(nxt) - _naive(started)).days / 7 if nxt else None
    cost: dict[str, Any] = {}
    for r, at in ((sized or {}).get("at") or {}).items():
        value = float(at["value_gbp"])
        loss = {"worst": -min(worst, 0.0) if worst is not None else None, "p90": p90}
        cost[r] = {
            f"{k}_gbp": round(value * v, 2) if v is not None else None for k, v in loss.items()
        } | {
            f"{k}_pct_equity": round(value * v / equity, 4) if v is not None and equity else None
            for k, v in loss.items()
        }
    return {
        "next_event": nxt,
        "weeks_to_event": round(weeks, 1) if weeks is not None else None,
        "in_first_half": weeks is not None and weeks <= rules.horizon_weeks[1] / 2,
        "n": n,
        "worst": worst,
        "p90_abs": p90,
        "small_sample": n < MIN_EVENT_MOVES,
        "cost": cost,
    }


def sizing(
    entry: float,
    stop: float,
    currency: str,
    equity: float,
    cash: float,
    adv_gbp: float | None,
    rules: Rules,
) -> dict[str, Any]:
    """What each risk in ``rules.risk_per_trade_range`` (low, mid, high) buys: position value
    and the risk actually taken after the caps (max position, liquidity, cash), GBP."""
    stop_pct = (entry - stop) / entry
    caps = {"max_position_pct": rules.max_position_pct * equity}
    if adv_gbp is not None and not math.isnan(adv_gbp):
        caps["liquidity"] = rules.max_adv_pct * adv_gbp
    caps["cash"] = max(0.0, cash - rules.cash_buffer * equity)
    lo, hi = rules.risk_per_trade_range
    out: dict[str, Any] = {"stop_pct": stop_pct, "caps_gbp": caps, "at": {}}
    for r in sorted({lo, (lo + hi) / 2, hi}):
        want = equity * r / stop_pct
        cap = min(caps, key=lambda k: caps[k])
        value = min(want, caps[cap])
        out["at"][f"{r:.2%}"] = {
            "value_gbp": round(value, 2),
            "risk_gbp": round(value * stop_pct, 2),
            "risk_pct_taken": round(value * stop_pct / equity, 4) if equity else None,
            "capped_by": cap if caps[cap] < want else None,
        }
    out["currency"] = currency
    return out


def last_close(conn: Conn, ticker: str, entry: float | None = None) -> dict[str, Any] | None:
    """The latest saved daily close (``date``, ``close``) and, given an ``entry``, how far the
    close is from it (``vs_entry``, a fraction: + = above the entry). None without prices."""
    px = store.load_prices(ticker, conn=conn)
    if px.empty:
        return None
    close = float(px["close"].iloc[-1])
    out: dict[str, Any] = {"date": px.index[-1].strftime("%Y-%m-%d"), "close": close}
    if entry:
        out["vs_entry"] = round(close / float(entry) - 1, 4)
    return out


def validator(cards: dict[str, Any], label: dict[str, Any] | None) -> dict[str, Any] | None:
    """The validator's view of a candidate (its label row, the card's reasons and checks)."""
    card = cards.get("validate")
    if not card and not label:
        return None
    card = card or {}
    return {
        "label": (label or {}).get("label") or card.get("label"),
        "confidence": _clean(card.get("confidence")),
        "reasons": card.get("reasons") or (label or {}).get("reason"),
        "checks": json.loads(str(card.get("checks") or "{}")),
    }


def coverage(conn: Conn, run_id: int, started: str) -> dict[str, Any]:
    """What the scan saw: tickers scanned, each toolkit ERROR by item, and tickers whose latest
    saved bar is more than :data:`STALE_DAYS` older than the run (an idea nobody saw)."""
    steps = store.query(
        "SELECT ticker, toolkit, status, reason FROM steps WHERE run_id = ?", conn, [run_id]
    )
    errs = steps[steps["status"] == "error"]
    last = store.query(
        "SELECT ticker, MAX(date) AS last FROM prices WHERE run_id = ? GROUP BY ticker",
        conn,
        [run_id],
    )
    cut = (_naive(started) - pd.Timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
    return {
        "scanned": int(steps["ticker"].nunique()) if len(steps) else 0,
        "errors": [
            {"ticker": str(r["ticker"]), "toolkit": str(r["toolkit"]), "reason": str(r["reason"])}
            for r in errs.to_dict("records")
        ],
        "stale_prices": {
            str(r["ticker"]): str(r["last"])[:10]
            for r in last.to_dict("records")
            if str(r["last"])[:10] < cut
        },
    }


def failures(conn: Conn, run_id: int) -> list[dict[str, Any]]:
    """Every ERROR of the run by item (stage, ticker, reason; ADR 0032), with the agent's turns
    and minutes when its usage was recorded."""
    from solaraalpha.notify import error_rows

    runs = store.query(
        "SELECT ticker, stage, turns, seconds FROM agent_runs WHERE run_id = ?", conn, [run_id]
    )
    use = {(str(r["ticker"]), str(r["stage"])): r for r in runs.to_dict("records")}
    out = []
    for e in error_rows(conn, run_id, "all"):
        u = use.get((e["ticker"], e["stage"])) or {}
        out.append(
            {
                **e,
                "turns": _clean(u.get("turns")),
                "minutes": round(float(u["seconds"]) / 60, 1) if _clean(u.get("seconds")) else None,
            }
        )
    return out


def latest_daily(conn: Conn) -> int | None:
    row = conn.execute("SELECT MAX(run_id) FROM runs WHERE script = 'daily'").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def broker_check(conn: Conn) -> dict[str, Any]:
    """The book against Trading212 as of the latest daily run (ADR 0030): the differences it
    could not book itself, with the command to fix each. ``daily_run`` None: no daily run yet."""
    run = latest_daily(conn)
    if run is None:
        return {"daily_run": None, "differences": []}
    trig = store.load_triggers(run, conn=conn)
    rows = trig[trig["kind"] == "broker"].to_dict("records") if len(trig) else []
    return {
        "daily_run": run,
        "at": str(store.run_info(run, conn=conn)["started_at"]),
        "differences": [
            {"ticker": str(r["ticker"]), **{k: r["detail"].get(k) for k in ("text", "command")}}
            for r in rows
        ],
    }


def kill_criteria_now(conn: Conn, position_id: int, daily_run: int | None) -> list[dict[str, Any]]:
    """The position's thesis conditions and whether the latest daily run found them hit
    (``machine`` ones only: the daily job checks them on fresh cards; ``agent`` ones are the
    monitor's call, ``hit`` None)."""
    hit: set[int] = set()
    if daily_run is not None:
        trig = store.load_triggers(daily_run, kind="thesis_hit", conn=conn)
        hit = {
            int(r["detail"]["condition_id"])
            for r in (trig.to_dict("records") if len(trig) else [])
            if r["position_id"] == position_id
        }
    return [
        {
            "condition": str(r["condition"]),
            "kind": str(r["kind"]),
            "hit": int(r["condition_id"]) in hit if r["kind"] == "machine" else None,
        }
        for r in book.thesis_conditions(conn, position_id).to_dict("records")
    ]


def results_calendar(
    events: list[tuple[str, str, Any]], started: str, weeks: int = CALENDAR_WEEKS
) -> list[dict[str, Any]]:
    """Scheduled events in the next ``weeks`` across the book and the candidates: ``events`` is
    ``(ticker, role, next_event)`` (role candidate / open / pending), soonest first."""
    start = _naive(started)
    end = start + pd.Timedelta(weeks=weeks)
    out = [
        {"date": str(_clean(nxt))[:10], "ticker": t, "role": role}
        for t, role, nxt in events
        if nxt and start <= _naive(nxt) <= end
    ]
    return sorted(out, key=lambda r: (r["date"], r["ticker"]))


WATCHED_PATHS = {  # a change under one of these is flagged: the rules or an agent changed
    "config/portfolio.toml": "rules",
    "docs/decisions/": "ADR",
    ".claude/skills/": "skill",
    "src/solaraalpha/toolkits.py": "bands",
    "src/solaraalpha/portfolio/fit.py": "rank",
}


def parse_log(text: str) -> list[str]:
    """``git log --format=@%h %s --name-only`` output as one line per commit, with what it
    touched among :data:`WATCHED_PATHS` in brackets."""
    out: list[str] = []
    for block in text.split("\n@"):
        lines = [x for x in block.removeprefix("@").splitlines() if x.strip()]
        if not lines:
            continue
        tags = sorted(
            {tag for f in lines[1:] for p, tag in WATCHED_PATHS.items() if f.startswith(p)}
        )
        out.append(lines[0] + (f" [{', '.join(tags)}]" if tags else ""))
    return out


def changes_since(since: str | None) -> list[str] | None:
    """What changed in the code since ``since`` (one line per commit on the checkout), None
    when git or the history isn't available."""
    if since is None:
        return []
    try:
        res = subprocess.run(
            ["git", "log", f"--since={since}", "--format=@%h %s", "--name-only"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[2],
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return parse_log(res.stdout)


def previous_scan(conn: Conn, run_id: int) -> str | None:
    """When the full weekly scan before ``run_id`` started (None: there was none)."""
    before = [r for r in store.weekly_scans(conn) if r < run_id]
    return str(store.run_info(before[-1], conn=conn)["started_at"]) if before else None


def build_pack(conn: Conn, run_id: int, rules: Rules | None = None) -> dict[str, Any]:
    """The PM's pack for scan ``run_id`` (DB only; nothing is written)."""
    rules = rules or load_rules()
    started = str(store.run_info(run_id, conn=conn)["started_at"])
    base = plan_run(conn, run_id, rules, dry_run=True, use_pm=False)
    assert base.equity is not None
    eq = base.equity
    held = base.held if base.held is not None else pd.DataFrame(columns=risk.HOLDING_COLS)
    plans = {p.ticker: p for p in base.plans}
    no_plan = dict(base.plan_errors)
    fit_rejected = {p.ticker: why for p, why in base.report.rejected}
    positions = book.positions(conn)
    drafted = {  # this run's drafts: funding one approves it as drafted (ADR 0027)
        str(r["ticker"]): {"position_id": int(r["position_id"]), "status": str(r["status"])}
        for r in positions[positions["opened_from_run_id"] == run_id].to_dict("records")
        if r["status"] not in ("rejected", "cancelled")
    }

    vlab = store.query(
        "SELECT ticker, label, reason FROM labels WHERE run_id = ? AND stage = 'validate'",
        conn,
        [run_id],
    )
    validated = {
        str(r["ticker"]): {str(k): v for k, v in r.items()} for r in vlab.to_dict("records")
    }
    candidates: list[dict[str, Any]] = []
    watch: list[dict[str, Any]] = []
    for c in base.candidates:
        if c.stage != "research":
            continue
        cards = store.load_cards(run_id, c.ticker, conn=conn)
        res = cards.get("research") or {}
        if c.label != "ACT":
            if c.label == "WATCH":
                watch.append({"ticker": c.ticker, "reasons": res.get("reasons")})
            continue
        lv = trade_levels(res)
        plan = plans.get(c.ticker)
        adv = (cards.get("liquidity") or {}).get("adv_gbp_20")
        currency = book.quote_currency(c.ticker)
        sized = (
            sizing(
                lv.entry,
                lv.stop,
                currency,
                eq.equity,
                eq.cash,
                None if adv is None else float(adv),
                rules,
            )
            if lv
            else None
        )
        scores = {
            k: _clean(res.get(k))
            for k in ("catalyst_score", "event_risk", "thesis", "value", "recent_catalysts")
            if res.get(k) is not None
        }
        candidates.append(
            {
                "ticker": c.ticker,
                "sector": risk.sector_of(c.ticker),
                "screen_rank": c.rank,
                "shortlist": c.shortlist,
                "research": {
                    "confidence": _clean(res.get("confidence")),
                    "scores": scores,
                    "reasons": res.get("reasons"),
                    "kill_criteria": json.loads(str(res.get("kill_criteria") or "[]")),
                    "trade": lv.model_dump() if lv else None,
                    "note_md": res.get("note_md"),
                },
                "validator": validator(cards, validated.get(c.ticker)),
                "last_close": last_close(conn, c.ticker, lv.entry if lv else None),
                "next_event": _clean((cards.get("events") or {}).get("next_event")),
                "financing": financing(cards.get("financing")),
                "adv_gbp_20": _clean(adv),
                "plan_at_default_risk": {
                    "shares": plan.shares,
                    "entry_type": plan.entry_type,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "position_value": plan.position_value,
                    "risk_amount": plan.risk_amount,
                    "capped_by": plan.capped_by,
                    "flags": [*plan.flags, *base.report.flags.get(c.ticker, [])],
                    "fit": fit_rejected.get(c.ticker, "accepted"),
                }
                if plan
                else None,
                "no_plan": no_plan.get(c.ticker),
                "already_drafted": drafted.get(c.ticker),
                "sizing": sized,
                "event_gap": event_gap(cards.get("events"), sized, started, eq.equity, rules),
            }
        )

    reviews = latest_review_cards(conn)
    daily_run = latest_daily(conn)
    open_rows = []
    for row in held.to_dict("records"):
        pid = int(row["position_id"])
        mon = latest_card(conn, str(row["ticker"]), "monitor")
        rev = reviews.get(pid)
        thesis = positions.loc[positions["position_id"] == pid, "thesis"]
        open_rows.append(
            {
                **{str(k): _clean(v) for k, v in row.items()},
                "thesis": str(thesis.iloc[0]) if len(thesis) else "",
                "monitor": {
                    "run_id": mon["run_id"],
                    "label": mon.get("label"),
                    "reasons": mon.get("reasons"),
                    "checks": json.loads(str(mon.get("checks") or "{}")),
                }
                if mon
                else None,
                "review": {"action": rev.get("action"), "reason": rev.get("reason")}
                if rev
                else None,
                "kill_criteria_now": kill_criteria_now(conn, pid, daily_run),
            }
        )
    pend = pending_positions(conn)
    due = lapsing(conn, run_id)  # not kept unless the PM renews them (ADR 0034)
    pending_rows = []
    for row in pend.to_dict("records"):
        pid = int(row["position_id"])
        pe = book.pending_entry(conn, pid)
        od = book.orders(conn, pid).set_index("order_id")
        valid = od["valid_until"].get(pe["order_id"]) if pe else None
        pending_rows.append(
            {
                **{str(k): _clean(v) for k, v in row.items()},
                "status": book.position_status(conn, pid),
                "entry_order": pe,
                "valid_until": _clean(valid),
                "days_left": (_naive(valid) - _naive(started)).days if _clean(valid) else None,
                "lapses": pid in due,
                "placed": book.entry_ever_placed(conn, pid),
                "waits_for": book.waiting_on(conn, pid),
                "last_close": last_close(conn, str(row["ticker"]), (pe or {}).get("price")),
                "opened_from_run_id": _clean(
                    positions.loc[positions["position_id"] == pid, "opened_from_run_id"].iloc[0]
                ),
            }
        )

    tickers = sorted(
        {c["ticker"] for c in candidates}
        | {str(t) for t in held["ticker"]}
        | {str(t) for t in pend["ticker"]}
    )
    rets = {t: r for t in tickers if (r := _weekly_returns(t, started, conn)) is not None}
    pairs = []
    names = sorted(rets)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            both = pd.concat(
                [
                    rets[a].dropna().iloc[-CORRELATION_WEEKS:],
                    rets[b].dropna().iloc[-CORRELATION_WEEKS:],
                ],
                axis=1,
                join="inner",
            )
            if len(both) < 8:
                continue
            corr = float(both.iloc[:, 0].corr(both.iloc[:, 1]))
            if not math.isnan(corr) and abs(corr) >= CORRELATION_SHOW:
                pairs.append({"a": a, "b": b, "corr": round(corr, 2)})

    macro = []
    df = store.query(
        "SELECT ticker, field, value, text FROM results WHERE run_id = ? AND model = 'macro'"
        " AND field IN ('label', 'confidence', 'reasons')",
        conn,
        [run_id],
    )
    for t, g in df.groupby("ticker"):
        note = {
            str(r["field"]): r["text"] if pd.notna(r["text"]) else r["value"]
            for r in g.to_dict("records")
        }
        macro.append({"group": str(t).removeprefix(SECTOR_PREFIX), **note})

    drawdown = drawdown_from_store(conn, started, rules)
    return {
        "run_id": run_id,
        "ticker": BOOK,
        "started_at": started,
        "rank_note": RANK_NOTE,
        "limits": {k: getattr(rules, k) for k in LIMIT_KEYS},
        "money": {
            "equity": eq.equity,
            "cash": eq.cash,
            "deployable_cash": max(0.0, eq.cash - rules.cash_buffer * eq.equity),
            "heat": risk.heat(held, eq),
            "pending_risk": float(pend["risk"].sum()) if len(pend) else 0.0,
            "exposure": {str(k): float(v) for k, v in risk.exposure(held, eq).items()},
            "breaches": risk.breaches(held, eq, rules),
            "drawdown": _clean(drawdown),
            "breaker_tripped": bool(base.report.breaker),
            "slots_free": rules.max_positions - len(held) - len(pend),
            "next_run": next_run(conn, run_id),
            "lapsing": {
                "position_ids": sorted(due),
                "risk": float(pend.loc[pend["position_id"].isin(due), "risk"].sum()),
                "value": float(pend.loc[pend["position_id"].isin(due), "value"].sum()),
                "slots_free_if_none_renewed": rules.max_positions
                - len(held)
                - len(pend)
                + len(due),
            },
        },
        "regime": latest_regime(conn),
        "macro": macro,
        "candidates": candidates,
        "research_watch": watch,
        "open_positions": open_rows,
        "pending_entries": pending_rows,
        "correlations": pairs,
        "rules_only_fit": {
            "accepted": [p.ticker for p in base.report.accepted],
            "rejected": {p.ticker: why for p, why in base.report.rejected},
        },
        "agents_this_week": agent_week(conn, started),
        "failures": failures(conn, run_id),
        "coverage": coverage(conn, run_id, started),
        "broker": broker_check(conn),
        "results_calendar": results_calendar(
            [(c["ticker"], "candidate", c["next_event"]) for c in candidates]
            + [(str(r["ticker"]), "open", r.get("next_event")) for r in open_rows]
            + [
                (str(t), "pending", (latest_card(conn, str(t), "events") or {}).get("next_event"))
                for t in pend["ticker"]
            ],
            started,
        ),
        "changes": changes_since(previous_scan(conn, run_id)),
    }


def write_pack(pack: dict[str, Any], out: Path | None = None) -> Path:
    out = out or settings.processed_dir / "pm" / str(pack["run_id"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "pack.json").write_text(json.dumps(pack, indent=2, default=str))
    return out
