"""Technical-analysis model for any single stock on weekly bars.

Input is a weekly OHLCV frame from :func:`solaraalpha.data.clean.to_weekly` (complete weeks
only). Everything is computed from trailing windows, so the row for week ``t`` uses only data
known at that Friday's close and can be acted on the following week at the earliest.

Three pillars, each scored in ``[-1, +1]`` so they can sit in a wider scorecard:

* **trend** — direction of the market: price vs 40-week MA, 10w vs 40w MA, 40w MA slope,
  damped when ADX says there is no trend. ``+1`` strong uptrend, ``-1`` strong downtrend.
* **momentum** — strength of recent moves: RSI(14), MACD(12,26,9) histogram sign and
  volatility-adjusted 12-1 momentum. ``+1`` strongly bullish, ``-1`` strongly bearish.
* **entry** — timing for opening a position on ``side`` (+1 long, -1 short): not stretched from
  the 10w MA, RSI room to run, MACD histogram turning our way. ``+1`` good time to enter.

:func:`assess_position` judges an open position: chandelier trailing stop anchored at entry,
trend/momentum from the position's side, and a hold/review/exit action.

Level-based indicators use raw ``close``/``high``/``low`` (split-adjusted, not dividend-adjusted,
so stops are real price levels); return-based momentum uses ``adj_close``.
Parameters are documented in ``docs/decisions/0003-technical-model.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

FAST_MA = 10  # weeks, ~50 trading days
SLOW_MA = 40  # weeks, ~200 trading days
SLOPE_WEEKS = 4  # slow-MA slope measured over this many weeks
FULL_SLOPE = 0.005  # slow-MA rise per week (0.5%) that scores a full +1
WILDER = 14  # RSI, ATR, ADX period
ADX_TREND = 20  # ADX below this = no clear trend; trend score halved
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
MOM_LOOKBACK, MOM_SKIP = 52, 4  # 12-1 momentum: 12-month return skipping the last month
STOP_ATR = 3.0  # chandelier stop distance in ATRs (LeBeau)
RSI_OVERBOUGHT = 70


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = WILDER) -> pd.Series:
    """Wilder's RSI, 0-100."""
    delta = close.diff()
    gain = _wilder(delta.clip(lower=0), n)
    loss = _wilder(-delta.clip(upper=0), n)
    return 100 - 100 / (1 + gain / loss)


def macd(close: pd.Series) -> pd.DataFrame:
    """MACD line, signal line and histogram (12, 26, 9)."""
    line = ema(close, MACD_FAST) - ema(close, MACD_SLOW)
    signal = ema(line, MACD_SIGNAL)
    return pd.DataFrame({"macd": line, "macd_signal": signal, "macd_hist": line - signal})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift()
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = WILDER) -> pd.Series:
    """Wilder's average true range, in price units."""
    return _wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = WILDER) -> pd.Series:
    """Wilder's ADX, 0-100: trend strength regardless of direction."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = _wilder(true_range(df), n)
    plus_di = 100 * _wilder(plus_dm, n) / tr
    minus_di = 100 * _wilder(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return _wilder(dx, n)


def indicators(weekly: pd.DataFrame) -> pd.DataFrame:
    """All raw indicators, one row per week."""
    close, adj = weekly["close"], weekly["adj_close"]
    out = pd.DataFrame(index=weekly.index)
    out["close"] = close
    out["sma_fast"] = sma(close, FAST_MA)
    out["sma_slow"] = sma(close, SLOW_MA)
    out["slow_slope"] = out["sma_slow"].pct_change(SLOPE_WEEKS) / SLOPE_WEEKS  # per week
    out["adx"] = adx(weekly)
    out["rsi"] = rsi(close)
    out = out.join(macd(close))
    out["atr"] = atr(weekly)
    # 12-1 momentum, divided by annualised vol over the same window (a Sharpe-like t-stat).
    mom = adj.shift(MOM_SKIP) / adj.shift(MOM_LOOKBACK) - 1
    vol = np.log(adj).diff().rolling(MOM_LOOKBACK, min_periods=MOM_LOOKBACK).std() * np.sqrt(52)
    out["mom_12_1"] = mom
    out["mom_sharpe"] = mom / (vol * np.sqrt((MOM_LOOKBACK - MOM_SKIP) / 52))
    out["extension"] = (close - out["sma_fast"]) / out["atr"]  # ATRs above the 10w MA
    return out


def _mean(cols: list[pd.Series]) -> pd.Series:
    return pd.concat(cols, axis=1).mean(axis=1, skipna=False)


def scores(weekly: pd.DataFrame, side: int = 1) -> pd.DataFrame:
    """Indicators plus sub-scores and the three pillar scores (``trend``, ``momentum``,
    ``entry``), each in [-1, 1]. Trend and momentum are market views (+ bullish); ``entry``
    is timing for a new position on ``side`` (+1 long, -1 short). NaN during warm-up."""
    if side not in (1, -1):
        raise ValueError("side must be 1 (long) or -1 (short)")
    ind = indicators(weekly)
    s = pd.DataFrame(index=ind.index)

    s["t_price_vs_slow"] = np.sign(ind["close"] - ind["sma_slow"])
    s["t_fast_vs_slow"] = np.sign(ind["sma_fast"] - ind["sma_slow"])
    s["t_slow_slope"] = (ind["slow_slope"] / FULL_SLOPE).clip(-1, 1)
    damp = ind["adx"].ge(ADX_TREND).map({True: 1.0, False: 0.5}).where(ind["adx"].notna())
    s["trend"] = _mean([s["t_price_vs_slow"], s["t_fast_vs_slow"], s["t_slow_slope"]]) * damp

    s["m_rsi"] = ((ind["rsi"] - 50) / 20).clip(-1, 1)
    s["m_macd"] = np.sign(ind["macd_hist"])
    s["m_12_1"] = ind["mom_sharpe"].clip(-1, 1)
    s["momentum"] = _mean([s["m_rsi"], s["m_macd"], s["m_12_1"]])

    # Best entry is within 1 ATR of the 10w MA; score falls to -1 at 3 ATRs away either side.
    s["e_extension"] = (1 - (ind["extension"].abs() - 1).clip(lower=0)).clip(-1, 1)
    side_rsi = ind["rsi"] if side == 1 else 100 - ind["rsi"]
    s["e_rsi_room"] = ((RSI_OVERBOUGHT - side_rsi) / 20).clip(-1, 1)
    s["e_macd_turn"] = np.sign(side * ind["macd_hist"].diff())
    s["entry"] = _mean([s["e_extension"], s["e_rsi_room"], s["e_macd_turn"]])

    s["stop"] = ind["close"] - side * STOP_ATR * ind["atr"]  # initial stop if entering now
    s["risk_pct"] = STOP_ATR * ind["atr"] / ind["close"]
    return ind.join(s)


@dataclass(frozen=True)
class Scorecard:
    """Latest-week technical view for opening a position on ``side``."""

    date: pd.Timestamp
    side: int
    trend: float  # [-1, 1], + bullish
    momentum: float  # [-1, 1], + bullish
    entry: float  # [-1, 1], + good timing for ``side``
    close: float
    stop: float  # initial stop if entering now
    risk_pct: float  # distance to that stop as a fraction of price


@dataclass(frozen=True)
class Position:
    """Assessment of an open position; trend/momentum/score are oriented to its side."""

    date: pd.Timestamp
    side: int
    entry_week: pd.Timestamp
    entry_price: float
    weeks_held: int
    close: float
    ret: float  # price return in the position's favour (excludes dividends)
    r_multiple: float  # gain in units of initial risk
    initial_stop: float
    stop: float  # stop to use for the coming week
    stop_distance_atr: float
    stopped_on: pd.Timestamp | None
    trend: float
    momentum: float
    action: str  # hold / review / exit
    score: float  # [-1, 1]


def assess_position(
    weekly: pd.DataFrame,
    entry_date: str | pd.Timestamp,
    side: int = 1,
    entry_price: float | None = None,
) -> Position:
    """Assess a position opened on ``entry_date`` (``side`` +1 long, -1 short).

    ``entry_price`` defaults to the close of the week containing ``entry_date``. The stop is a
    chandelier anchored at entry: the best high (long) / low (short) since entry minus/plus
    ``STOP_ATR`` x ATR, never loosened, and never looser than the initial stop set from the ATR
    of the last week completed before entry. Breaches are judged on weekly closes.

    ``action``: ``exit`` if a weekly close has breached the stop; ``review`` if trend or
    momentum has turned against the position; else ``hold``. ``score`` is in [-1, 1]
    (-1 when stopped out), oriented to the position: + means the case for holding is strong.
    """
    sc = scores(weekly, side)
    entry_ts = pd.Timestamp(entry_date)
    entry_ts = entry_ts.tz_localize("UTC") if entry_ts.tzinfo is None else entry_ts
    held = sc.index[sc.index >= entry_ts]
    before = sc.index[sc.index < entry_ts]
    if held.empty or before.empty or pd.isna(sc.loc[before[-1], "atr"]):
        raise ValueError(f"need weekly data both before and after {entry_ts:%Y-%m-%d}")

    price = float(entry_price if entry_price is not None else sc.loc[held[0], "close"])
    initial_stop = price - side * STOP_ATR * float(sc.loc[before[-1], "atr"])
    w = weekly.loc[held]
    if side == 1:
        trail = w["high"].cummax() - STOP_ATR * sc.loc[held, "atr"]
        stop = trail.clip(lower=initial_stop).cummax()
    else:
        trail = w["low"].cummin() + STOP_ATR * sc.loc[held, "atr"]
        stop = trail.clip(upper=initial_stop).cummin()
    # A week's close is judged against the stop set at the end of the previous week.
    live_stop = stop.shift().fillna(initial_stop)
    breached = side * (w["close"] - live_stop) < 0

    last = sc.iloc[-1]
    close = float(last["close"])
    trend, momentum = side * float(last["trend"]), side * float(last["momentum"])
    stopped_on = breached.index[breached.to_numpy()][0] if breached.any() else None
    if stopped_on is not None:
        action, score = "exit", -1.0
    else:
        action = "review" if trend < 0 or momentum < 0 else "hold"
        score = (trend + momentum) / 2
    return Position(
        date=sc.index[-1],
        side=side,
        entry_week=held[0],
        entry_price=price,
        weeks_held=len(held),
        close=close,
        ret=side * (close / price - 1),
        r_multiple=side * (close - price) / abs(price - initial_stop),
        initial_stop=initial_stop,
        stop=float(stop.iloc[-1]),
        stop_distance_atr=side * (close - float(stop.iloc[-1])) / float(last["atr"]),
        stopped_on=stopped_on,
        trend=trend,
        momentum=momentum,
        action=action,
        score=score,
    )


def scorecard(weekly: pd.DataFrame, side: int = 1) -> Scorecard:
    """Latest-week pillar scores for a new position on ``side``, for the overall scorecard."""
    last = scores(weekly, side).iloc[-1]
    return Scorecard(
        date=last.name,  # type: ignore[arg-type]
        side=side,
        trend=float(last["trend"]),
        momentum=float(last["momentum"]),
        entry=float(last["entry"]),
        close=float(last["close"]),
        stop=float(last["stop"]),
        risk_pct=float(last["risk_pct"]),
    )
