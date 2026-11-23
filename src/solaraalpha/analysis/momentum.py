"""Crowd momentum on daily bars (any stock): is the crowd buying, and does the chart confirm it?

ADR 0043: we buy what the crowd is already buying, confirmed by strong technicals; ADR 0044: the
technicals lead. One card per stock as of a session, from that session's bars and earlier only
(no look-ahead), so a backtest can replay it day by day. Generic across stocks: no per-stock
tuning.

The four components, each in [-1, +1] (+ = the crowd is in):

* ``trend`` — mean of four ±1 votes: close above the 20-day average, the 20 above the 50, the 50
  rising over the last 20 sessions, close above the 200-day average (that vote only once there
  are 200 sessions; a younger listing has three).
* ``high`` — how near the close is to its 52-week high: +1 at the high, 0 at 10% below, -1 at
  20% or more below.
* ``volume`` — participation: log2 of the last 5 sessions' mean volume over the prior 50
  sessions' median (twice the usual volume = +1, half = -1).
* ``strength`` — excess return over the market (the stock's ``market`` in
  ``config/benchmarks.toml``, ``^NDX`` for the Nasdaq-100), mean of 20 and 60 sessions, 10% =
  +1.

``crowd`` is their mean, the screen's rank (``portfolio.fit.rank_score``). It is a placeholder
until the pre-registered test (ADR 0029 protocol) says whether higher scores make better trades.
Context beside it for the agents, not in the score: a 20-day breakout, a new 52-week high, the
excess over the sector ETF, ATR and how far the close is stretched above its 20-day average in
ATRs (``extended`` past :data:`EXTENDED_ATR`: the "careful what we buy" check).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

YEAR = 252  # sessions in a year (the 52-week high)
MIN_SESSIONS = 70  # the 50-day average and its 20-session slope; the 200-day vote needs 200
ATR_N = 14
HIGH_SCALE = 0.10  # 10% below the 52-week high = 0
RS_SCALE = 0.10  # 10% excess return over the market = +1
BREAKOUT_N = 20
VOL_RECENT, VOL_BASE = 5, 50
EXTENDED_ATR = 3.0  # close more than this many ATRs above its 20-day average = extended


@dataclass(frozen=True)
class MomentumCard:
    date: pd.Timestamp  # the session the card is as of
    crowd: float  # mean of the four components, [-1, +1]; the screen rank
    trend: float
    high: float
    volume: float
    strength: float
    close: float
    pct_from_high: float  # close / 52-week high - 1 (<= 0)
    new_high: bool  # close above every close of the prior 52 weeks
    breakout: bool  # close above the prior 20 sessions' highs
    rvol: float  # last 5 sessions' mean volume / prior 50 sessions' median
    rs_market_20: float  # excess return over the market, 20 sessions (NaN: no market)
    rs_market_60: float
    rs_sector_20: float  # excess return over the sector ETF, 20 sessions (NaN: none)
    atr: float  # 14-session average true range, price units
    extension_atr: float  # (close - 20-day average) / ATR
    extended: bool  # extension_atr > EXTENDED_ATR
    sessions: int  # sessions used


def _clip(x: float) -> float:
    return float(np.clip(x, -1.0, 1.0)) if not math.isnan(x) else math.nan


def _excess(close: pd.Series, other: pd.Series | None, n: int) -> float:
    """The stock's return over ``n`` sessions minus ``other``'s over the same dates."""
    if other is None or len(close) <= n:
        return math.nan
    o = other.reindex(close.index, method="ffill")
    if pd.isna(o.iloc[-1]) or pd.isna(o.iloc[-1 - n]):
        return math.nan
    mine = close.iloc[-1] / close.iloc[-1 - n] - 1
    theirs = o.iloc[-1] / o.iloc[-1 - n] - 1
    return float(mine - theirs)


def atr(daily: pd.DataFrame, n: int = ATR_N) -> pd.Series:
    """Average true range (simple mean of the last ``n`` true ranges)."""
    prev = daily["close"].shift()
    tr = pd.concat(
        [daily["high"] - daily["low"], (daily["high"] - prev).abs(), (daily["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n).mean()


def momentum_card(
    daily: pd.DataFrame,
    market: pd.Series | None = None,
    sector: pd.Series | None = None,
    as_of: pd.Timestamp | None = None,
) -> MomentumCard:
    """The card as of ``as_of`` (default: the last session) from clean daily bars (``open``,
    ``high``, ``low``, ``close``, ``volume``) and the market's and sector's daily closes, using
    only sessions up to ``as_of``."""
    d = daily if as_of is None else daily[daily.index <= as_of]
    d = d.dropna(subset=["close"])
    if len(d) < MIN_SESSIONS:
        raise ValueError(f"{len(d)} sessions: crowd momentum needs {MIN_SESSIONS}")
    if as_of is not None:
        market = market[market.index <= as_of] if market is not None else None
        sector = sector[sector.index <= as_of] if sector is not None else None
    close = d["close"]
    last = float(close.iloc[-1])
    sma20, sma50 = close.rolling(20).mean(), close.rolling(50).mean()
    votes = [
        last > sma20.iloc[-1],
        sma20.iloc[-1] > sma50.iloc[-1],
        sma50.iloc[-1] > sma50.iloc[-21],
    ]
    if len(close) >= 200:
        votes.append(last > close.iloc[-200:].mean())
    trend = float(np.mean([1.0 if v else -1.0 for v in votes]))

    year = close.iloc[-YEAR:]  # a younger listing: its whole history
    pct_from_high = last / float(year.max()) - 1
    high = _clip(1 + pct_from_high / HIGH_SCALE)
    prior_year = close.iloc[-YEAR - 1 : -1]
    new_high = bool(last > prior_year.max())
    breakout = bool(last > d["high"].iloc[-BREAKOUT_N - 1 : -1].max())

    vol = d["volume"].astype(float)
    base = vol.iloc[-VOL_RECENT - VOL_BASE : -VOL_RECENT].median()
    rvol = float(vol.iloc[-VOL_RECENT:].mean() / base) if base and base > 0 else math.nan
    volume = _clip(math.log2(rvol)) if rvol and rvol > 0 else math.nan

    rs20, rs60 = _excess(close, market, 20), _excess(close, market, 60)
    rs = [x for x in (rs20, rs60) if not math.isnan(x)]
    strength = _clip(float(np.mean(rs)) / RS_SCALE) if rs else math.nan

    a = float(atr(d).iloc[-1])
    ext = (last - float(sma20.iloc[-1])) / a if a > 0 else math.nan
    parts = [x for x in (trend, high, volume, strength) if not math.isnan(x)]
    return MomentumCard(
        date=pd.Timestamp(d.index[-1]),
        crowd=float(np.mean(parts)) if parts else math.nan,
        trend=trend,
        high=high,
        volume=volume,
        strength=strength,
        close=last,
        pct_from_high=float(pct_from_high),
        new_high=new_high,
        breakout=breakout,
        rvol=rvol,
        rs_market_20=rs20,
        rs_market_60=rs60,
        rs_sector_20=_excess(close, sector, 20),
        atr=a,
        extension_atr=float(ext),
        extended=bool(ext > EXTENDED_ATR) if not math.isnan(ext) else False,
        sessions=len(d),
    )


BETA_N = 120  # sessions of daily returns behind the beta in the net-of-beta variant (research 0017)


def _clip_s(s: pd.Series) -> pd.Series:
    return s.clip(-1.0, 1.0)


def crowd_panel(daily: pd.DataFrame, market: pd.Series | None = None) -> pd.DataFrame:
    """:func:`momentum_card`'s four parts and ``crowd`` for **every** session of one stock at
    once (rows with fewer than :data:`MIN_SESSIONS` sessions behind them are NaN), each row
    from that session and earlier only: the backtest's fast path, tested equal to the card.

    Also ``strength_beta`` / ``crowd_beta``: strength net of beta (return minus beta x market
    return over 20 and 60 sessions, beta from the prior :data:`BETA_N` sessions' daily returns),
    research 0017's variant C."""
    d = daily.dropna(subset=["close"])
    close = d["close"]
    n = pd.Series(np.arange(1, len(d) + 1), index=d.index)
    sma20, sma50 = close.rolling(20).mean(), close.rolling(50).mean()
    votes = [
        np.where(close > sma20, 1.0, -1.0),
        np.where(sma20 > sma50, 1.0, -1.0),
        np.where(sma50 > sma50.shift(20), 1.0, -1.0),
    ]
    v200 = pd.Series(np.where(close > close.rolling(200).mean(), 1.0, -1.0), index=d.index)
    trend = pd.Series(np.mean(votes, axis=0), index=d.index)
    trend = trend.where(n < 200, (trend * 3 + v200) / 4)

    pct = close / close.rolling(YEAR, min_periods=1).max() - 1
    high = _clip_s(1 + pct / HIGH_SCALE)

    vol = d["volume"].astype(float)
    base = vol.shift(VOL_RECENT).rolling(VOL_BASE, min_periods=1).median()
    rvol = vol.rolling(VOL_RECENT).mean() / base.where(base > 0)
    volume = _clip_s(pd.Series(np.log2(rvol.where(rvol > 0)), index=d.index))

    out = pd.DataFrame({"trend": trend, "high": high, "volume": volume}, index=d.index)
    if market is not None:
        m = market.reindex(d.index, method="ffill")
        ex = {k: close / close.shift(k) - 1 - (m / m.shift(k) - 1) for k in (20, 60)}
        out["strength"] = _clip_s(pd.concat(ex.values(), axis=1).mean(axis=1) / RS_SCALE)
        r, rm = close.pct_change(), m.pct_change()
        beta = r.rolling(BETA_N).cov(rm) / rm.rolling(BETA_N).var()
        net = {k: close / close.shift(k) - 1 - beta * (m / m.shift(k) - 1) for k in (20, 60)}
        out["strength_beta"] = _clip_s(pd.concat(net.values(), axis=1).mean(axis=1) / RS_SCALE)
    else:
        out["strength"] = np.nan
        out["strength_beta"] = np.nan
    out["crowd"] = out[["trend", "high", "volume", "strength"]].mean(axis=1)
    out["crowd_beta"] = out[["trend", "high", "volume", "strength_beta"]].mean(axis=1)
    return out.where(n >= MIN_SESSIONS)
