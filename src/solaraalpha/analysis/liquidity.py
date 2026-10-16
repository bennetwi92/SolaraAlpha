"""Liquidity of one stock from its daily bars: how much it trades, and how wide the spread is.

AIM names are thin, so a position must be capped by what the stock trades
(``Rules.max_adv_pct`` of the median daily traded value, docs/decisions/0015). Information only:
the ``liquidity`` toolkit has no bands and is always ACT once run. Money in GBP (pence / 100).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from solaraalpha.portfolio.book import to_account

SHORT, LONG = 20, 60  # sessions for the two traded-value medians; LONG also for the spread


@dataclass(frozen=True)
class LiquidityCard:
    date: pd.Timestamp  # the week (Friday) the card is as of; sessions after it are not used
    adv_gbp_20: float  # median daily traded value (close x volume) over the last 20 sessions, GBP
    adv_gbp_60: float  # the same over 60 sessions
    zero_volume_share: float  # share of the last 60 sessions with no volume
    spread_proxy: float  # Corwin-Schultz high-low spread estimate, 60-session median (fraction)
    max_position_gbp: float  # max_adv_pct x adv_gbp_20: the most a position should be worth


def traded_value(daily: pd.DataFrame, sessions: int) -> float:
    tail = daily.dropna(subset=["close", "volume"]).iloc[-sessions:]
    if tail.empty:
        return math.nan
    return to_account(
        float((tail["close"] * tail["volume"]).median()), daily.attrs.get("currency", "GBp")
    )


def corwin_schultz(daily: pd.DataFrame) -> pd.Series:
    """Per-session Corwin-Schultz (2012) spread estimate from two-day high / low ranges, floored
    at zero (negative estimates mean "no detectable spread")."""
    high, low = daily["high"], daily["low"]
    one_day = pd.Series(np.log(high / low) ** 2, index=daily.index)
    beta = one_day + one_day.shift(1)
    h2, l2 = high.rolling(2).max(), low.rolling(2).min()
    gamma = pd.Series(np.log(h2 / l2) ** 2, index=daily.index)
    k = 3 - 2 * math.sqrt(2)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    spread = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    return spread.clip(lower=0).rename("spread")


def liquidity_card(
    daily: pd.DataFrame, max_adv_pct: float, as_of: pd.Timestamp | None = None
) -> LiquidityCard:
    """Liquidity as of ``as_of`` (default: the last session), using only sessions up to it."""
    d = daily if as_of is None else daily[daily.index <= as_of]
    if d.empty:
        raise ValueError("no sessions to measure liquidity on")
    adv20, adv60 = traded_value(d, SHORT), traded_value(d, LONG)
    last = d.iloc[-LONG:]
    zero = float((last["volume"].fillna(0) <= 0).mean()) if len(last) else math.nan
    spread = corwin_schultz(d).iloc[-LONG:].dropna()
    return LiquidityCard(
        date=as_of if as_of is not None else pd.Timestamp(d.index[-1]),
        adv_gbp_20=adv20,
        adv_gbp_60=adv60,
        zero_volume_share=zero,
        spread_proxy=float(spread.median()) if len(spread) else math.nan,
        max_position_gbp=max_adv_pct * adv20 if not math.isnan(adv20) else math.nan,
    )
