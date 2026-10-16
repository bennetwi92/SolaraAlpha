"""Daily triggers: cheap programmatic checks on open positions, watch stocks and pending entries.

Pure functions over daily bars (checked, cleaned, trimmed by the caller), the book's stops and
targets, and the day's filings and headlines. Each returns :class:`Trigger` rows whose
``detail`` carries the numbers that fired. Owner triggers need the owner; agent triggers wake
the monitor (:mod:`.claude/skills/monitor`). Thresholds are placeholders (docs/decisions/0017).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from solaraalpha.analysis.events import material
from solaraalpha.analysis.technical import atr
from solaraalpha.portfolio.book import to_account

GAP_ATR = 2.0  # |open - previous close| beyond this many daily ATRs is a gap (placeholder)
ATR_SESSIONS = 14
OWNER_KINDS = ("stop_close", "stop_touched", "target_hit", "entry_break", "dividend", "split")
AGENT_KINDS = ("gap", "filing")
REPORT_KINDS = ("watch_break", "news")


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
