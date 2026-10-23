"""Weekly review of open positions: every toolkit re-run, compared with the entry conditions.

Each Sunday the scan runs technical (with the position's entry date and average cost, giving
the ``position`` card), levels, relative strength and fundamentals for every open position,
whether or not it is in the watchlist, and :func:`review` turns that into a ``ReviewCard``:
the trailing (chandelier) stop, what has deteriorated since entry, the horizon rule and an
action. Since ADR 0024 the stop is the agents' call: the trailing stop is **context** on the card
(``new_stop``, ``stop_moved`` = it is above the live stop) for the daily monitor, never drafted
here; an ``exit`` action is reported for the owner. See docs/decisions/0015 "Weekly review".
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.log import get_logger
from solaraalpha.pipeline import Step, run_toolkit
from solaraalpha.portfolio import book
from solaraalpha.portfolio.book import PIPELINE, PositionState
from solaraalpha.portfolio.rules import Rules
from solaraalpha.store import Conn
from solaraalpha.toolkits import Inputs

log = get_logger(__name__)

REVIEW_TOOLKITS = ("technical", "levels", "relative_strength", "fundamentals", "events")
DETERIORATION = 0.25  # a score falling by at least this much since entry counts (placeholder)
WATCHED = (
    ("technical", "trend"),
    ("technical", "momentum"),
    ("levels", "structure"),
    ("relative_strength", "rs"),
    ("fundamentals", "growth"),
    ("fundamentals", "quality"),
)


@dataclass(frozen=True)
class ReviewCard:
    date: pd.Timestamp  # the cards' week
    position_id: int
    weeks_held: int
    r_multiple: float
    old_stop: float  # the live stop before the review (NaN if none)
    new_stop: float  # max(old stop, chandelier stop): the trail suggests (ADR 0024: context)
    stop_moved: bool  # the trail is above the live stop: the monitor may raise it
    deteriorated: str  # comma-separated fields that fell by >= DETERIORATION since entry
    past_horizon: bool  # weeks held > the horizon's upper bound
    action: str  # hold / review / exit (exit is reported, never drafted)
    reason: str
    event_due: str = ""  # next scheduled event within rules.event_flag_days (YYYY-MM-DD), else ""
    event_status: str = ""  # confirmed / estimated (from the events card)
    event_action: str = ""  # the policy applied: flag / hold / trim / exit, else ""


def _get(card: Any, field: str) -> Any:
    if card is None:
        return None
    return card.get(field) if isinstance(card, dict) else getattr(card, field, None)


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return math.nan
    return f


def review(
    state: PositionState,
    entry_conditions: dict[str, dict[str, Any]],
    cards: dict[str, Any],
    rules: Rules,
) -> ReviewCard:
    """Judge an open position from the fresh ``cards`` (incl. ``position``) against the frozen
    ``entry_conditions`` (``{model: {field: value}}``).

    * ``new_stop`` = max(old live stop, the ``position`` card's chandelier stop): what the
      trail suggests, context for the monitor agent who sets the stop (ADR 0024).
    * ``deteriorated``: among trend, momentum, structure, rs, growth, quality, the fields whose
      score fell by at least :data:`DETERIORATION` since entry.
    * ``action``: ``exit`` if the ``position`` card says exit, or past the horizon
      (weeks held > ``horizon_weeks[1]``) with trend < 0 or momentum <= 0 ("beyond 16 weeks, hold
      only while trend >= 0 and momentum > 0 and the trailing stop holds"); ``review`` if anything
      deteriorated or the ``position`` card says review; else ``hold``.
    """
    pos = cards.get("position")
    date = pd.Timestamp(_get(pos, "date") or _get(cards.get("technical"), "date"))
    weeks = int(_num(_get(pos, "weeks_held")) or 0) if pos is not None else 0
    r_mult = _num(_get(pos, "r_multiple"))
    chandelier = _num(_get(pos, "stop"))
    old = float(state.stop) if state.stop is not None else math.nan
    candidates = [v for v in (old, chandelier) if not math.isnan(v)]
    new = max(candidates) if candidates else math.nan
    moved = not math.isnan(new) and (math.isnan(old) or new > old + 1e-9)

    fell = []
    for model, field in WATCHED:
        then = _num((entry_conditions.get(model) or {}).get(field))
        now = _num(_get(cards.get(model), field))
        if not math.isnan(then) and not math.isnan(now) and now - then <= -DETERIORATION:
            fell.append(f"{field} {then:+.2f}→{now:+.2f}")
    deteriorated = ", ".join(fell)

    past = weeks > rules.horizon_weeks[1]
    trend, momentum = _num(_get(pos, "trend")), _num(_get(pos, "momentum"))
    pos_action = str(_get(pos, "action") or "hold")
    reasons = []
    if pos_action == "exit":
        action = "exit"
        reasons.append("weekly close breached the trailing stop")
    elif past and (trend < 0 or momentum <= 0):
        action = "exit"
        reasons.append(
            f"past the {rules.horizon_weeks[1]}-week horizon with trend {trend:+.2f}, momentum {momentum:+.2f}"
        )
    elif deteriorated or pos_action == "review":
        action = "review"
        if deteriorated:
            reasons.append(f"deteriorated since entry: {deteriorated}")
        if pos_action == "review":
            reasons.append("trend or momentum turned against the position")
    else:
        action = "hold"
        reasons.append("past the horizon, still climbing" if past else "set-up intact")
    if moved:
        reasons.append(
            f"trailing stop {new:,.1f} is above the stop {old:,.1f} (the monitor decides)"
            if not math.isnan(old)
            else f"trailing stop {new:,.1f}, no stop set (the monitor decides)"
        )
    event_due, event_status, event_action = upcoming_event(cards.get("events"), date, rules)
    if event_due:
        reasons.append(f"event due {event_due} ({event_status}): policy {event_action}")
    return ReviewCard(
        date=date,
        position_id=state.position_id,
        weeks_held=weeks,
        r_multiple=r_mult,
        old_stop=old,
        new_stop=new,
        stop_moved=moved,
        deteriorated=deteriorated,
        past_horizon=past,
        action=action,
        reason="; ".join(reasons),
        event_due=event_due,
        event_status=event_status,
        event_action=event_action,
    )


def upcoming_event(events: Any, date: pd.Timestamp, rules: Rules) -> tuple[str, str, str]:
    """``(event_due, event_status, event_action)`` when the events card's ``next_event`` falls
    within ``rules.event_flag_days`` of ``date`` (decided in advance, #129): the date, the
    card's ``next_event_status`` (``estimated`` when absent) and ``rules.event_policy``; three
    empty strings otherwise."""
    nxt = _get(events, "next_event") if events is not None else None
    if nxt is None or (isinstance(nxt, float) and math.isnan(nxt)) or str(nxt) in ("", "None"):
        return "", "", ""
    ts = pd.Timestamp(nxt)
    ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
    d = date.tz_localize("UTC") if date.tz is None else date
    days = (ts.normalize() - d.normalize()).days
    if not 0 <= days <= rules.event_flag_days:
        return "", "", ""
    status = str(_get(events, "next_event_status") or "estimated")
    return ts.strftime("%Y-%m-%d"), status, rules.event_policy


def propose_event_action(
    conn: Conn, state: PositionState, card: ReviewCard, rules: Rules, run_id: int | None
) -> int | None:
    """Record the pre-results decision a ``trim`` or ``exit`` policy implies as a
    revision (``take_partial`` for ``event_trim_fraction`` of the shares, or ``exit``), applied
    as it is recorded (ADR 0026); ``flag`` and ``hold`` propose nothing. None when nothing was
    proposed or this event already has one."""
    if card.event_action not in ("trim", "exit") or state.shares <= 0:
        return None
    rv = book.revisions(conn, state.position_id)
    rv = rv[rv["status"].isin(["proposed", "accepted"])] if not rv.empty else rv
    if (
        not rv.empty
        and rv["trigger"].astype(str).str.startswith(f"event_due {card.event_due}").any()
    ):
        return None  # already decided for this event
    reason = f"event due {card.event_due} ({card.event_status}): policy {card.event_action}"
    trigger = f"event_due {card.event_due}" + (
        f" (review run {run_id})" if run_id is not None else ""
    )
    if card.event_action == "trim":
        qty = max(1, math.ceil(state.shares * rules.event_trim_fraction))
        return book.propose_revision(
            conn,
            state.position_id,
            "take_partial",
            {"qty": qty, "type": "market"},
            reason,
            PIPELINE,
            trigger=trigger,
        )
    return book.propose_revision(
        conn, state.position_id, "exit", {"type": "market"}, reason, PIPELINE, trigger=trigger
    )


@dataclass
class Reviewed:
    state: PositionState
    card: ReviewCard
    steps: list[Step]
    cards: dict[str, Any]
    stop_order_id: int | None = None
    event_revision_id: int | None = None  # the pre-results trim / exit proposed (#129)


def review_positions(
    conn: Conn,
    rules: Rules,
    inputs: dict[str, Inputs] | None = None,
    run_id: int | None = None,
) -> list[Reviewed]:
    """Run every review toolkit on each open position (sharing ``inputs`` by ticker when
    given), review it and, with ``run_id``, save the cards and steps under stage ``review`` and
    the ``review`` card. The trailing stop is not drafted (ADR 0024: the monitor sets stops)."""
    inputs = inputs if inputs is not None else {}
    out: list[Reviewed] = []
    for pid in [int(p) for p in book.positions(conn, status="open")["position_id"]]:
        state = book.position_state(conn, pid)
        x = inputs.setdefault(state.ticker, Inputs(state.ticker))
        entry = pd.Timestamp(state.opened_at).strftime("%Y-%m-%d") if state.opened_at else None
        steps: list[Step] = []
        for tk in REVIEW_TOOLKITS:
            params = (
                {"entry_date": entry, "entry_price": state.entry_avg}
                if tk == "technical" and entry
                else {}
            )
            steps.append(run_toolkit(tk, x, params, stage="review"))
        cards = {m: c for s in steps for m, c in s.cards.items()}
        then_df = book.entry_conditions(conn, pid)
        then: dict[str, dict[str, Any]] = {}
        for r in then_df.to_dict("records"):
            then.setdefault(str(r["model"]), {})[str(r["field"])] = (
                r["text"] if pd.notna(r["text"]) else r["value"]
            )
        if "position" not in cards:
            log.warning("position %d (%s): no position card; review limited", pid, state.ticker)
        card = review(state, then, cards, rules)
        item = Reviewed(state, card, steps, cards)
        if run_id is not None:
            with conn:
                item.event_revision_id = propose_event_action(conn, state, card, rules, run_id)
                # a held stock the scan also screened has its cards and steps in this run
                # already (same date, same keys): save only what the review adds
                saved = {
                    str(r[0])
                    for r in conn.execute(
                        "SELECT DISTINCT model FROM results WHERE run_id = ? AND ticker = ?",
                        (run_id, state.ticker),
                    ).fetchall()
                }
                done = {
                    str(r[0])
                    for r in conn.execute(
                        "SELECT toolkit FROM steps WHERE run_id = ? AND ticker = ?",
                        (run_id, state.ticker),
                    ).fetchall()
                }
                rows = [
                    (s.stage, s.toolkit, s.status, s.reason) for s in steps if s.toolkit not in done
                ]
                new = {m: c for m, c in {**cards, "review": card}.items() if m not in saved}
                store.save_ticker(
                    run_id,
                    state.ticker,
                    new,
                    x.fetched("daily"),
                    x.fetched("fund"),
                    steps=rows,
                    others=x.others,
                    conn=conn,
                )
        out.append(item)
        log.info("%s: review %s (%s)", state.ticker, card.action, card.reason)
    return out


def raise_stop(conn: Conn, state: PositionState, new_stop: float, run_id: int) -> int:
    """Replace the live stop order at ``new_stop`` (approved, actor pipeline) and record
    ``stop_raised``. Ratchet only: raises if ``new_stop`` is not above the current stop. No
    longer called by the weekly review (ADR 0024); kept for a stop the owner sets by rule."""
    if state.stop is not None and new_stop <= state.stop:
        raise ValueError(f"never lower a stop: {new_stop} <= {state.stop}")
    od = book.orders(conn, state.position_id)
    live = od[(od["purpose"] == "stop") & od["status"].isin(book.LIVE_ORDER)]
    reason = f"weekly review, run {run_id}"
    if live.empty:
        oid = book.add_order(
            conn,
            state.position_id,
            -state.side,
            "stop",
            state.shares,
            new_stop,
            "stop",
            None,
            "approved",
            book.PIPELINE,
            reason,
        )
    else:
        oid = book.replace_order(
            conn, int(live["order_id"].iloc[-1]), state.shares, new_stop, reason, book.PIPELINE
        )
    book.position_event(
        conn,
        state.position_id,
        "stop_raised",
        {"old": state.stop, "new": new_stop, "run_id": run_id},
        book.PIPELINE,
        reason,
    )
    return oid


def review_report(items: list[Reviewed]) -> str:
    if not items:
        return "**Open positions:** none\n"
    lines = ["**Open positions (weekly review):**", ""]
    for it in items:
        c = it.card
        line = f"- {it.state.ticker} (position {c.position_id}): **{c.action}** — {c.reason}; {c.weeks_held}w, {c.r_multiple:+.2f}R"
        if it.stop_order_id:
            line += f"; new stop order {it.stop_order_id} approved (see `book.py tickets`)"
        elif c.stop_moved:
            line += "; trailing stop is context for the daily monitor (ADR 0024)"
        if c.action == "exit":
            line += "; the daily monitor decides the exit (ADR 0024)"
        if c.event_due:
            line += f"; **event due {c.event_due}** ({c.event_status}): policy {c.event_action}"
            if it.event_revision_id:
                line += f" — applied (revision {it.event_revision_id}; `book.py undo` to reverse)"
        lines.append(line)
    return "\n".join(lines) + "\n"


def review_dict(card: ReviewCard) -> dict[str, Any]:
    return asdict(card)
