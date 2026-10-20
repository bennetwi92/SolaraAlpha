"""Trade plans: turn an ACT stock's cards into entry, stop, targets, size and review dates.

Pure functions, no I/O. The levels card gives the entry (a breakout trigger when coiled, else
the close), stop and the next level overhead (context only); the targets are the move the
strategy seeks (``rules.target_moves``, ADR 0022); the technical card the fallback stop; the events card the next
scheduled event; the rules the risk budget and caps. Money is in the account currency (GBP) via
:func:`solaraalpha.portfolio.book.to_account`; prices stay in the quote currency (pence for LSE).
See docs/decisions/0015.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from solaraalpha.portfolio.book import to_account
from solaraalpha.portfolio.rules import Rules

EVENT_REVIEW_DAYS = 2  # review a position this many days before a scheduled event
ADV_SESSIONS = 20  # median daily traded value over this many sessions


@dataclass(frozen=True)
class TradePlan:
    date: pd.Timestamp  # the cards' week (Friday)
    ticker: str
    side: int  # 1 (long only)
    entry_type: str  # stop (buy-stop at the trigger) / limit
    entry: float  # quote currency
    wait_for_break: bool  # limit at the trigger, to be placed once a daily close is above it
    valid_until: pd.Timestamp  # unfilled entry orders expire after this
    stop: float
    stop_source: str  # levels / technical
    target: float  # entry x (1 + rules.target_moves[0]): a review when hit, not a sell order
    target_2: float  # entry x (1 + rules.target_moves[1])
    reward_risk: float  # (target - entry) / (entry - stop)
    risk_per_share: float  # entry - stop, quote currency
    shares: int
    position_value: float  # GBP at the entry
    risk_amount: float  # GBP lost if the stop is hit
    capped_by: str  # risk / max_position_pct / liquidity / cash / zero
    horizon_weeks: tuple[int, int]
    trail_atr: float
    review_dates: tuple[pd.Timestamp, ...]
    flags: tuple[str, ...]
    next_level: float = math.nan  # the levels map's next target above the entry; NaN: blue sky


def _get(card: Any, field: str) -> Any:
    if card is None:
        return None
    return card.get(field) if isinstance(card, dict) else getattr(card, field, None)


def _finite(v: Any) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def make_plan(
    ticker: str,
    levels: Any,
    technical: Any,
    events: Any | None,
    rules: Rules,
    equity_gbp: float,
    cash_gbp: float,
    adv_gbp: float | None = None,
    currency: str = "GBp",
) -> TradePlan:
    """Plan a long entry in ``ticker`` from its cards (dataclasses or dicts of their fields).

    * **Entry.** If ``levels.coil_pending`` the entry is a breakout at ``levels.trigger``: a
      ``stop`` order when ``rules.stop_entry_orders``, else a ``limit`` at the trigger with
      ``wait_for_break`` (the daily job alerts when a close is above it). Otherwise a ``limit`` at
      ``close x (1 + rules.entry_limit_buffer)``.
    * **Valid until** the cards' ``date`` + ``rules.entry_validity_days``.
    * **Stop.** ``levels.stop`` if finite and below the entry, else ``technical.stop``;
      ``ValueError`` if neither is below the entry.
    * **Size.** ``floor(equity x risk_per_trade / risk_per_share)`` in GBP terms, then capped
      by ``max_position_pct x equity / entry``, by ``max_adv_pct x adv_gbp / entry`` when
      ``adv_gbp`` is given, and by ``(cash - cash_buffer x equity) / entry``. ``capped_by`` names
      the binding cap (``risk`` when none binds); zero shares flags ``"size zero"``.
    * **Review dates.** ``events.next_event`` - :data:`EVENT_REVIEW_DAYS` when it falls inside
      the horizon; flag ``"event within N days"`` when the event is within
      ``rules.event_flag_days`` of the plan date.
    * **Targets** ``entry x (1 + rules.target_moves)``: the +15-30% move sought, not the next
      level (ADR 0022: the level map is context). The next level above the entry is kept as
      ``next_level`` (NaN: blue sky). A wide stop is not flagged: volatility is wanted.
    """
    date = pd.Timestamp(_get(levels, "date"))
    close = float(_get(levels, "close"))
    trigger = _get(levels, "trigger")
    flags: list[str] = []

    if bool(_get(levels, "coil_pending")) and _finite(trigger):
        entry = float(trigger)
        entry_type = "stop" if rules.stop_entry_orders else "limit"
        wait = not rules.stop_entry_orders
    else:
        entry = close * (1 + rules.entry_limit_buffer)
        entry_type, wait = "limit", False

    stop, source = _get(levels, "stop"), "levels"
    if not (_finite(stop) and float(stop) < entry):
        stop, source = _get(technical, "stop"), "technical"
    if not (_finite(stop) and float(stop) < entry):
        raise ValueError(f"{ticker}: no stop below the entry {entry:.2f} (levels/technical)")
    stop = float(stop)
    risk_ps = entry - stop

    overhead = [float(v) for v in (_get(levels, "target"), _get(levels, "target_2")) if _finite(v)]
    next_level = min((v for v in overhead if v > entry), default=math.nan)
    target, target_2 = (round(entry * (1 + m), 2) for m in rules.target_moves)
    reward_risk = (target - entry) / risk_ps

    entry_gbp, risk_gbp = to_account(entry, currency), to_account(risk_ps, currency)
    caps = {"risk": equity_gbp * rules.risk_per_trade / risk_gbp}
    caps["max_position_pct"] = rules.max_position_pct * equity_gbp / entry_gbp
    if adv_gbp is not None:
        caps["liquidity"] = rules.max_adv_pct * adv_gbp / entry_gbp
    caps["cash"] = (cash_gbp - rules.cash_buffer * equity_gbp) / entry_gbp
    capped_by = min(caps, key=lambda k: caps[k])
    shares = max(0, math.floor(caps[capped_by]))
    if shares == 0:
        flags.append("size zero")
        capped_by = "zero"

    reviews: list[pd.Timestamp] = []
    nxt = _get(events, "next_event") if events is not None else None
    if _finite(nxt) and nxt is not None:
        nxt_ts = pd.Timestamp(nxt)
        if nxt_ts.tz is None:
            nxt_ts = nxt_ts.tz_localize("UTC")
        if date < nxt_ts <= date + pd.Timedelta(weeks=rules.horizon_weeks[1]):
            reviews.append(nxt_ts - pd.Timedelta(days=EVENT_REVIEW_DAYS))
        days = (nxt_ts - date).days
        if 0 <= days <= rules.event_flag_days:
            flags.append(f"event within {days} days")

    return TradePlan(
        date=date,
        ticker=ticker,
        side=1,
        entry_type=entry_type,
        entry=entry,
        wait_for_break=wait,
        valid_until=date + pd.Timedelta(days=rules.entry_validity_days),
        stop=stop,
        stop_source=source,
        target=target,
        target_2=target_2,
        reward_risk=float(reward_risk),
        risk_per_share=risk_ps,
        shares=shares,
        position_value=shares * entry_gbp,
        risk_amount=shares * risk_gbp,
        capped_by=capped_by,
        horizon_weeks=rules.horizon_weeks,
        trail_atr=rules.trail_atr,
        review_dates=tuple(reviews),
        flags=tuple(flags),
        next_level=next_level,
    )


ADD_PULLBACK_ATR = 1.0  # a pullback add: close within this many ATRs above the new support
ADD_MIN_TREND = 0.25  # ... with the trend still in the ACT band


def add_plan(
    state: Any,
    review_card: Any,
    levels: Any,
    technical: Any,
    rules: Rules,
    adds: int = 0,
    equity_gbp: float | None = None,
) -> TradePlan | None:
    """Propose an **add** to a working position, or None (never add to a loser).

    All of these must hold: the position is at least ``rules.add_min_r`` R in profit
    (``review_card.r_multiple``); ``adds`` so far < ``rules.max_adds``; and either a **confirmed
    breakout** (``levels.breakout == 1``: closed above last week's nearest resistance) or a
    **pullback to new support** (close within :data:`ADD_PULLBACK_ATR` ATRs above
    ``levels.support`` with ``technical.trend`` >= :data:`ADD_MIN_TREND`).

    The add is a limit at ``close x (1 + entry_limit_buffer)``. Its stop is the position's
    current stop raised to at least **breakeven on the original entry** (``state.entry_avg``);
    the whole position moves to that stop. Size = ``add_fraction x entry_shares``, capped so
    the open risk of the whole position at the new stop stays <= the original risk
    (``state.r_unit``) and, when ``equity_gbp`` is given, the position's value stays within
    ``max_position_pct``. ``flags`` names the trigger (``add: breakout`` / ``add: pullback``)
    and the cap; zero shares returns None.
    """
    r_mult = _num(_get(review_card, "r_multiple"))
    if math.isnan(r_mult) or r_mult < rules.add_min_r:
        return None
    if adds >= rules.max_adds:
        return None
    close = _num(_get(levels, "close"))
    atr = _num(_get(levels, "atr"))
    support = _num(_get(levels, "support"))
    trend = _num(_get(technical, "trend"))
    if int(_num(_get(levels, "breakout")) or 0) == 1:
        why = "breakout"
    elif (
        not math.isnan(support)
        and not math.isnan(atr)
        and 0 <= close - support <= ADD_PULLBACK_ATR * atr
        and trend >= ADD_MIN_TREND
    ):
        why = "pullback"
    else:
        return None

    currency = str(getattr(state, "currency", "GBp"))
    entry = close * (1 + rules.entry_limit_buffer)
    current_stop = float(state.stop) if state.stop is not None else float("nan")
    stop = (
        max(float(state.entry_avg), current_stop)
        if not math.isnan(current_stop)
        else float(state.entry_avg)
    )
    if stop >= entry:
        return None
    risk_ps = entry - stop
    risk_gbp = to_account(risk_ps, currency)
    held_risk = max(0.0, to_account((float(state.avg_cost) - stop) * int(state.shares), currency))
    budget = max(0.0, float(state.r_unit) - held_risk)
    caps = {
        "add_fraction": rules.add_fraction * int(state.entry_shares),
        "risk": budget / risk_gbp if risk_gbp > 0 else 0.0,
    }
    if equity_gbp is not None:
        held_value = to_account(float(state.avg_cost) * int(state.shares), currency)
        caps["max_position_pct"] = (rules.max_position_pct * equity_gbp - held_value) / to_account(
            entry, currency
        )
    capped_by = min(caps, key=lambda k: caps[k])
    shares = max(0, math.floor(caps[capped_by]))
    if shares == 0:
        return None
    target = _num(_get(levels, "target"))
    target_2 = _num(_get(levels, "target_2"))
    reward_risk = (
        (target - entry) / risk_ps if not math.isnan(target) and target > entry else math.inf
    )
    date = pd.Timestamp(_get(levels, "date"))
    return TradePlan(
        date=date,
        ticker=str(state.ticker),
        side=int(state.side),
        entry_type="limit",
        entry=entry,
        wait_for_break=False,
        valid_until=date + pd.Timedelta(days=rules.entry_validity_days),
        stop=stop,
        stop_source="breakeven" if stop == float(state.entry_avg) else "position",
        target=target,
        target_2=target_2,
        reward_risk=float(reward_risk),
        risk_per_share=risk_ps,
        shares=shares,
        position_value=shares * to_account(entry, currency),
        risk_amount=shares * risk_gbp,
        capped_by=capped_by,
        horizon_weeks=rules.horizon_weeks,
        trail_atr=rules.trail_atr,
        review_dates=(),
        flags=(f"add: {why}", f"add {adds + 1} of {rules.max_adds}"),
    )


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return math.nan
    return f


def adv_gbp(daily: pd.DataFrame, sessions: int = ADV_SESSIONS) -> float:
    """Median daily traded value (close x volume) over the last ``sessions``, in GBP."""
    tail = daily.dropna(subset=["close", "volume"]).iloc[-sessions:]
    if tail.empty:
        return float("nan")
    value = (tail["close"] * tail["volume"]).median()
    return to_account(float(value), daily.attrs.get("currency", "GBp"))
