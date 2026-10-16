"""Weekly review of open positions: every toolkit re-run, compared with the entry conditions.

Each Sunday the scan runs technical (with the position's entry date and average cost, giving
the ``position`` card), levels, relative strength and fundamentals for every open position,
whether or not it is in the watchlist, and :func:`review` turns that into a ``ReviewCard``:
the ratcheted stop, what has deteriorated since entry, the horizon rule and an action. The
pipeline **proposes**: a moved stop is drafted as an approved order (ratchet only, never
lower); an ``exit`` action is reported for the owner. See docs/decisions/0015 "Weekly review".
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
from solaraalpha.portfolio.book import PositionState
from solaraalpha.portfolio.rules import Rules
from solaraalpha.store import Conn
from solaraalpha.toolkits import Inputs

log = get_logger(__name__)

REVIEW_TOOLKITS = ("technical", "levels", "relative_strength", "fundamentals")
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
    new_stop: float  # max(old stop, chandelier stop): ratchet only
    stop_moved: bool
    deteriorated: str  # comma-separated fields that fell by >= DETERIORATION since entry
    past_horizon: bool  # weeks held > the horizon's upper bound
    action: str  # hold / review / exit (exit is reported, never drafted)
    reason: str


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

    * ``new_stop`` = max(old live stop, the ``position`` card's chandelier stop); never lower.
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
            f"stop raised {old:,.1f} → {new:,.1f}"
            if not math.isnan(old)
            else f"stop set {new:,.1f}"
        )
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
    )


@dataclass
class Reviewed:
    state: PositionState
    card: ReviewCard
    steps: list[Step]
    cards: dict[str, Any]
    stop_order_id: int | None = None


def review_positions(
    conn: Conn,
    rules: Rules,
    inputs: dict[str, Inputs] | None = None,
    run_id: int | None = None,
) -> list[Reviewed]:
    """Run every review toolkit on each open position (sharing ``inputs`` by ticker when
    given), review it and, with ``run_id``, save the cards and steps under stage ``review``,
    draft the raised stop (approved, actor pipeline, plus ``stop_raised``) and save the
    ``review`` card."""
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
                if card.stop_moved:
                    item.stop_order_id = raise_stop(conn, state, card.new_stop, run_id)
                rows = [(s.stage, s.toolkit, s.status, s.reason) for s in steps]
                store.save_ticker(
                    run_id,
                    state.ticker,
                    {**cards, "review": card},
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
    ``stop_raised``. Ratchet only: raises if ``new_stop`` is not above the current stop."""
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
        if c.action == "exit":
            line += f"; `book.py exit {c.position_id} --reason time`"
        lines.append(line)
    return "\n".join(lines) + "\n"


def review_dict(card: ReviewCard) -> dict[str, Any]:
    return asdict(card)
