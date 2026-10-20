"""Market regime from the index: is this a "tighten everything" day? (issue #142, S7.9)

Pure function over the market index's daily bars (the daily job passes the rules' ``benchmark``,
``^FTAI`` since #210 phase 0; ``^FTAS`` is the function's default): drawdown from the 20-day high, the day's return in units of 60-day volatility, the
20-day realised volatility and its percentile over the last year, and a three-way ``regime``
with **placeholder** rules: ``risk_off`` when the drawdown is below -8% or the day is a
3-sigma fall; ``stressed`` when realised vol sits above its 80th percentile; else ``calm``.
Realised volatility only: Yahoo serves no free UK implied-volatility series (VFTSE is not
there; ``^VIX`` is US), see docs/data-sources.md. Advisory: the daily report puts it at the
top, the weekly pack shows it, and plans carry a flag while risk_off; nothing is traded on it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

DRAWDOWN_WINDOW = 20
VOL_WINDOW = 20
Z_WINDOW = 60
PERCENTILE_WINDOW = 252
RISK_OFF_DRAWDOWN = -0.08  # placeholder
RISK_OFF_Z = -3.0  # placeholder
STRESSED_PERCENTILE = 0.8  # placeholder
REGIMES = ("calm", "stressed", "risk_off")


@dataclass(frozen=True)
class RegimeCard:
    date: pd.Timestamp
    index: str
    close: float
    drawdown_20d: float  # close / 20-day high - 1 (<= 0)
    ret_1d: float
    ret_1d_z: float  # 1-day return / 60-day daily vol (NaN without history)
    realised_vol_20d: float  # annualised
    vol_percentile_1y: float  # of the 20-day vol over the last year, [0, 1]
    regime: str  # calm / stressed / risk_off
    reason: str


def market_regime(index_daily: pd.DataFrame, index: str = "^FTAS") -> RegimeCard:
    """The regime on the last session of ``index_daily`` (daily bars with ``close``)."""
    close = index_daily["close"].dropna()
    if len(close) < 2:
        raise ValueError("market_regime: need at least two sessions")
    last = float(close.iloc[-1])
    high = float(close.iloc[-DRAWDOWN_WINDOW:].max())
    dd = last / high - 1 if high > 0 else math.nan
    rets = np.log(close).diff().dropna()
    r1 = float(rets.iloc[-1])
    sigma = float(rets.iloc[-Z_WINDOW - 1 : -1].std()) if len(rets) > 5 else math.nan
    z = r1 / sigma if sigma and not math.isnan(sigma) and sigma > 0 else math.nan
    vol = rets.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std() * math.sqrt(252)
    v = float(vol.iloc[-1]) if len(vol) and not math.isnan(vol.iloc[-1]) else math.nan
    hist = vol.dropna().iloc[-PERCENTILE_WINDOW:]
    pct = float((hist <= v).mean()) if len(hist) > 1 and not math.isnan(v) else math.nan

    reasons = []
    if not math.isnan(dd) and dd < RISK_OFF_DRAWDOWN:
        reasons.append(f"index {dd:.1%} below its 20-day high")
    if not math.isnan(z) and z < RISK_OFF_Z:
        reasons.append(f"a {z:.1f}-sigma day")
    if reasons:
        regime = "risk_off"
    elif not math.isnan(pct) and pct > STRESSED_PERCENTILE:
        regime = "stressed"
        reasons.append(f"20-day vol {v:.0%} at its {pct:.0%} percentile over a year")
    else:
        regime = "calm"
        reasons.append("no drawdown, shock or vol spike")
    return RegimeCard(
        date=pd.Timestamp(close.index[-1]),
        index=index,
        close=last,
        drawdown_20d=float(dd),
        ret_1d=float(math.exp(r1) - 1),
        ret_1d_z=float(z),
        realised_vol_20d=v,
        vol_percentile_1y=pct,
        regime=regime,
        reason="; ".join(reasons),
    )
