"""Daily triggers: cheap programmatic checks on open positions, watch stocks and pending entries.

Pure functions over daily bars (checked, cleaned, trimmed by the caller), the book's stops and
targets, and the day's filings and headlines. Each returns :class:`Trigger` rows whose
``detail`` carries the numbers that fired. Owner triggers need the owner; agent triggers wake
the monitor (:mod:`.claude/skills/monitor`); every open position and pending entry gets a
``daily_review`` on the weekly review day (Friday, ADR 0039) whether or not anything fired,
carrying what fired as context. Thresholds are placeholders (docs/decisions/0017).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from solaraalpha.analysis.events import material
from solaraalpha.analysis.technical import atr
from solaraalpha.pipeline import Condition
from solaraalpha.portfolio.book import to_account

GAP_ATR = 2.0  # |open - previous close| beyond this many daily ATRs is a gap (placeholder)
ATR_SESSIONS = 14
OWNER_KINDS = (
    "stop_close",
    "stop_touched",
    "target_hit",
    "entry_break",
    "dividend",
    "split",
    "event_due",
    "order_stale",
    "broker",  # a difference between the book and Trading212 (ADR 0030)
)
EVENT_DUE_SESSIONS = 2  # warn when a scheduled event is this many sessions away (#129)
AGENT_KINDS = ("gap", "filing", "thesis_hit", "daily_review")
REVIEW_WEEKDAY = 4  # Friday: the weekly review of every position, after the week's last close
REPORT_KINDS = ("watch_break", "watch_pullback", "news", "market")
PULLBACK_ATR = (
    1.0  # a WATCH stock is "back at the 10w MA" within this many daily ATRs (placeholder)
)


@dataclass(frozen=True)
class Trigger:
    ticker: str
    kind: str
    position_id: int | None
    order_id: int | None
    detail: dict[str, Any] = field(default_factory=dict)
    at: str = ""  # ISO date of the session (or publication time) that fired it


def _last(daily: pd.DataFrame) -> dict[str, float]:
    row = daily.iloc[-1]
    prev = float(daily["close"].iloc[-2]) if len(daily) > 1 else math.nan
    # the ATR known before the session, so the session's own range doesn't dilute its gap
    a = atr(daily, ATR_SESSIONS).iloc[-2] if len(daily) > ATR_SESSIONS + 1 else math.nan
    return {
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "prev_close": prev,
        "atr": float(a),
    }


def _session(daily: pd.DataFrame) -> str:
    return pd.Timestamp(daily.index[-1]).strftime("%Y-%m-%d")


def price_triggers(
    ticker: str,
    daily: pd.DataFrame,
    position_id: int,
    stop: float | None,
    target: float | None = None,
    side: int = 1,
) -> list[Trigger]:
    """Triggers on an open position's latest session.

    ``stop_close``: close through the stop (owner: exit). ``stop_touched``: the session traded
    through the stop but closed on the right side (owner: check whether the broker stop
    filled). ``target_hit``: the session reached the target (owner / weekly review: take profit
    or trail). ``gap``: |open - previous close| > :data:`GAP_ATR` x ATR(14) (agent).
    """
    if daily.empty:
        return []
    bar, at = _last(daily), _session(daily)
    out: list[Trigger] = []
    if stop is not None and not math.isnan(stop):
        through = side * (bar["close"] - stop) < 0
        touched = (bar["low"] <= stop) if side == 1 else (bar["high"] >= stop)
        if through:
            out.append(Trigger(ticker, "stop_close", position_id, None, {**bar, "stop": stop}, at))
        elif touched:
            out.append(
                Trigger(ticker, "stop_touched", position_id, None, {**bar, "stop": stop}, at)
            )
    if target is not None and not math.isnan(target):
        hit = (bar["high"] >= target) if side == 1 else (bar["low"] <= target)
        if hit:
            out.append(
                Trigger(ticker, "target_hit", position_id, None, {**bar, "target": target}, at)
            )
    if not math.isnan(bar["atr"]) and not math.isnan(bar["prev_close"]):
        gap = bar["open"] - bar["prev_close"]
        if abs(gap) > GAP_ATR * bar["atr"]:
            detail = {**bar, "gap": gap, "gap_atr": gap / bar["atr"], "threshold_atr": GAP_ATR}
            out.append(Trigger(ticker, "gap", position_id, None, detail, at))
    return out


def watch_break(ticker: str, daily: pd.DataFrame, trigger: float | None) -> Trigger | None:
    """A WATCH stock with a pending coil whose latest close is above its trigger (report:
    promote candidate; re-run the screen for it)."""
    if daily.empty or trigger is None or math.isnan(trigger):
        return None
    bar = _last(daily)
    if bar["close"] > trigger:
        return Trigger(
            ticker, "watch_break", None, None, {**bar, "trigger": trigger}, _session(daily)
        )
    return None


def watch_pullback(
    ticker: str, daily: pd.DataFrame, level: float | None, ma40: float | None
) -> Trigger | None:
    """A WATCH stock that was extended whose latest close is back within :data:`PULLBACK_ATR`
    daily ATRs of its 10-week MA ``level`` while the trend holds (close above ``ma40``):
    report, re-screen it."""
    if daily.empty or level is None or math.isnan(level):
        return None
    bar = _last(daily)
    if math.isnan(bar["atr"]):
        return None
    if ma40 is not None and not math.isnan(ma40) and bar["close"] <= ma40:
        return None
    if abs(bar["close"] - level) <= PULLBACK_ATR * bar["atr"]:
        return Trigger(
            ticker,
            "watch_pullback",
            None,
            None,
            {
                **bar,
                "level": level,
                "ma40": ma40,
                "distance_atr": (bar["close"] - level) / bar["atr"],
            },
            _session(daily),
        )
    return None


def entry_break(
    ticker: str, daily: pd.DataFrame, position_id: int, order_id: int, trigger: float | None
) -> Trigger | None:
    """A draft / approved entry order waiting for the break whose ticker closed above the
    trigger (owner: place the limit now)."""
    if daily.empty or trigger is None or math.isnan(trigger):
        return None
    bar = _last(daily)
    if bar["close"] > trigger:
        return Trigger(
            ticker,
            "entry_break",
            position_id,
            order_id,
            {**bar, "trigger": trigger},
            _session(daily),
        )
    return None


def review_day(at: Any) -> bool:
    """Whether the daily run at ``at`` (a timestamp) is the weekly review (ADR 0039): Friday in
    London, or a weekend re-run of it."""
    t = pd.Timestamp(at)
    t = t.tz_localize("UTC") if t.tz is None else t
    return t.tz_convert("Europe/London").weekday() >= REVIEW_WEEKDAY


def daily_review(
    ticker: str,
    position_id: int,
    status: str,
    fired: list[Trigger],
    at: str,
    order_id: int | None = None,
) -> Trigger:
    """The weekly agent review of one open position (``status`` ``open``) or pending entry
    (``pending``: a draft / approved position with an unfilled entry order ``order_id``), ADR
    0024 / 0039. ``detail.fired`` lists the day's other triggers on it (kind and date) as
    context."""
    return Trigger(
        ticker,
        "daily_review",
        position_id,
        order_id,
        {"status": status, "fired": [f"{t.kind} {t.at[:10]}".strip() for t in fired]},  # noqa: PD008
        at,
    )


def filing_triggers(
    ticker: str, position_id: int, announcements: pd.DataFrame, since: str | pd.Timestamp
) -> list[Trigger]:
    """One ``filing`` trigger (agent) per material NSM filing published after ``since``."""
    if announcements.empty:
        return []
    cut = pd.Timestamp(since)
    cut = cut.tz_localize("UTC") if cut.tz is None else cut
    new = material(announcements[announcements["published"] > cut])
    return [
        Trigger(
            ticker,
            "filing",
            position_id,
            None,
            {
                "id": str(r["id"]),
                "headline": str(r["headline"]),
                "type_code": str(r["type_code"]),
                "type": str(r["type"]),
                "category": str(r["category"]),
                "url": str(r["url"]),
            },
            pd.Timestamp(r["published"]).isoformat(),
        )
        for r in new.to_dict("records")
    ]


def news_triggers(ticker: str, news: pd.DataFrame, since: str | pd.Timestamp) -> list[Trigger]:
    """``news`` rows (informational, listed in the report) for headlines after ``since``."""
    if news.empty:
        return []
    cut = pd.Timestamp(since)
    cut = cut.tz_localize("UTC") if cut.tz is None else cut
    new = news[news["published"] > cut]
    return [
        Trigger(
            ticker,
            "news",
            None,
            None,
            {"headline": str(r["headline"]), "source": str(r["source"]), "url": str(r["url"])},
            pd.Timestamp(r["published"]).isoformat(),
        )
        for r in new.to_dict("records")
    ]


def action_triggers(
    ticker: str,
    actions: pd.DataFrame,
    position_id: int,
    shares: int,
    since: str,
    currency: str = "GBp",
    recorded_dividends: frozenset[str] = frozenset(),
    recorded_splits: frozenset[str] = frozenset(),
) -> list[Trigger]:
    """Corporate actions on a held stock since ``since`` that the book has not recorded yet.

    ``actions`` is :func:`solaraalpha.data.prices.fetch_corporate_actions`'s frame. ``dividend``
    (owner: confirm the cash against the broker and record it; ``detail`` has the per-share
    amount, the shares held and the GBP amount) and ``split`` (owner: record the ratio).
    ``recorded_*`` are the ex-dates / split dates (``YYYY-MM-DD``) already in the book.
    """
    out: list[Trigger] = []
    if actions is None or actions.empty or shares <= 0:
        return out
    for r in actions.to_dict("records"):
        day = pd.Timestamp(r["date"]).strftime("%Y-%m-%d")
        if day <= since[:10]:
            continue
        if r["kind"] == "dividend" and day not in recorded_dividends:
            per_share = float(r["value"])
            out.append(
                Trigger(
                    ticker,
                    "dividend",
                    position_id,
                    None,
                    {
                        "per_share": per_share,
                        "currency": currency,
                        "shares": shares,
                        "amount_gbp": round(to_account(per_share * shares, currency), 2),
                    },
                    day,
                )
            )
        elif r["kind"] == "split" and day not in recorded_splits:
            out.append(
                Trigger(
                    ticker,
                    "split",
                    position_id,
                    None,
                    {"ratio": str(r["ratio"]), "factor": float(r["value"])},
                    day,
                )
            )
    return out


def event_due(
    ticker: str,
    position_id: int,
    next_event: str | pd.Timestamp | None,
    today: pd.Timestamp,
    policy: str,
) -> Trigger | None:
    """An open position's next scheduled event is :data:`EVENT_DUE_SESSIONS` or fewer trading
    sessions away (and not past), and the policy is not ``hold``: the owner acts on the
    decision made in advance (ADR 0015)."""
    if next_event is None or policy == "hold" or str(next_event) in ("", "None", "nan"):
        return None
    ev = pd.Timestamp(next_event)
    ev = ev.tz_localize("UTC") if ev.tz is None else ev.tz_convert("UTC")
    t = today.tz_localize("UTC") if today.tz is None else today.tz_convert("UTC")
    if ev.normalize() < t.normalize():
        return None
    sessions = len(pd.bdate_range(t.normalize(), ev.normalize())) - 1
    if sessions > EVENT_DUE_SESSIONS:
        return None
    return Trigger(
        ticker,
        "event_due",
        position_id,
        None,
        {"event": ev.strftime("%Y-%m-%d"), "sessions": int(sessions), "policy": policy},
        t.strftime("%Y-%m-%d"),
    )


def stale_order_triggers(stale: pd.DataFrame, today: pd.Timestamp) -> list[Trigger]:
    """One ``order_stale`` trigger per row of :func:`solaraalpha.portfolio.book.stale_orders`
    (owner: enter the fill, or mark it unfilled / expire it)."""
    out = []
    for o in stale.to_dict("records"):
        out.append(
            Trigger(
                str(o["ticker"]),
                "order_stale",
                int(o["position_id"]),
                int(o["order_id"]),
                {
                    "status": str(o["status"]),
                    "purpose": str(o["purpose"]),
                    "valid_until": str(o["valid_until"]),
                    "qty": int(o["qty"]),
                    "price": None
                    if o.get("price") is None or pd.isna(o["price"])
                    else float(o["price"]),
                },
                today.strftime("%Y-%m-%d"),
            )
        )
    return out


def thesis_hits(
    ticker: str,
    position_id: int,
    conditions: pd.DataFrame,
    cards: Mapping[str, Any],
    close: float | None,
    suppressed: frozenset[int] = frozenset(),
) -> tuple[list[Trigger], list[str]]:
    """Evaluate a position's ``machine`` thesis conditions (``field op value``) against fresh
    ``cards`` (``{toolkit: card}``) and the latest daily ``close`` (#131). A condition that
    holds is a ``thesis_hit`` agent trigger (detail: ``condition_id``, ``condition``, ``field``,
    ``value``); one in ``suppressed`` (already acted on) is skipped. Unparseable conditions and
    fields no card carries come back as issue strings, never exceptions."""
    merged: dict[str, Any] = {}
    for card in cards.values():
        if card is None:
            continue
        merged.update(card if isinstance(card, dict) else {k: getattr(card, k) for k in vars(card)})
    if close is not None:
        merged["close"] = close
    out: list[Trigger] = []
    issues: list[str] = []
    for r in conditions.to_dict("records"):
        if str(r["kind"]) != "machine":
            continue
        cid = int(r["condition_id"])
        text = str(r["condition"])
        try:
            cond = Condition.parse(text)
        except ValueError as e:
            issues.append(f"thesis condition {cid} {text!r}: not evaluable ({e})")
            continue
        if cond.field not in merged:
            issues.append(
                f"thesis condition {cid} {text!r}: no toolkit card carries {cond.field!r}"
            )
            continue
        if cid in suppressed:
            continue
        try:
            hit = bool(cond.check(merged))
        except (ValueError, KeyError, TypeError) as e:
            issues.append(f"thesis condition {cid} {text!r}: not evaluable ({e})")
            continue
        if hit:
            value = merged.get(cond.field)
            out.append(
                Trigger(
                    ticker,
                    "thesis_hit",
                    position_id,
                    None,
                    {
                        "condition_id": cid,
                        "condition": text,
                        "field": cond.field,
                        "value": float(value) if isinstance(value, int | float) else value,
                    },
                    "",
                )
            )
    return out, issues


def market_trigger(card: Any) -> Trigger | None:
    """A ``market`` trigger when the index regime is not calm (advisory: consider tightening
    stops and pausing new entries); ``card`` is a :class:`solaraalpha.analysis.regime.RegimeCard`."""
    if card is None or getattr(card, "regime", "calm") == "calm":
        return None
    return Trigger(
        str(card.index),
        "market",
        None,
        None,
        {
            "regime": card.regime,
            "reason": card.reason,
            "drawdown_20d": float(card.drawdown_20d),
            "ret_1d_z": float(card.ret_1d_z),
            "vol_percentile_1y": float(card.vol_percentile_1y),
        },
        pd.Timestamp(card.date).strftime("%Y-%m-%d"),
    )
