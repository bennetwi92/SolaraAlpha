"""Price levels for any single stock on weekly bars: support/resistance, trendlines, and
significant highs/lows, turned into trade-plan numbers (stop, targets, reward:risk).

Input is a weekly OHLCV frame from :func:`solaraalpha.data.clean.to_weekly`. Everything is
computed as of the last week in the frame using only that frame, so to look back in time pass a
truncated frame. Swing pivots need ``PIVOT_WEEKS`` later weeks to confirm, so a pivot is only
used once it is confirmed.

* **pivots** — swing highs/lows: the high (low) of a week beats the ``PIVOT_WEEKS`` weeks either
  side.
* **zones** — pivot highs and lows clustered by price (within ``ZONE_ATR`` x ATR); a zone needs
  ``MIN_TOUCHES`` pivots. Highs and lows are pooled because broken resistance tends to become
  support and vice versa.
* **trendlines** — upper (through swing highs) and lower (through swing lows) lines in log price,
  not broken by any weekly close since their first anchor and projected within ``TREND_MAX_ATR``
  x ATR of the close; the one with most touches wins.
* **coil** — the last ``COIL_WEEKS`` weeks: their high-low span in ATRs, and least-squares lines
  through their highs and lows (converging lines = a triangle squeezing into its apex). A tight
  span (``COIL_ATR``) means the stock is coiled, and the plan becomes a breakout plan.
* **significant levels** — prior 52-week and 5-year highs/lows (5-year = as far back as the
  supplied history goes, up to 5 years). ``blue_sky`` = closed above the 5-year high, so no
  overhead supply.

:func:`levels_card` turns the level map into a plan for a position on ``side`` and two scores in
[-1, +1]: ``structure`` (market view, + bullish) and ``setup`` (reward:risk for ``side``).
When coiled the plan is entered on a stop order beyond the coil (``trigger``) rather than at the
close, so reward:risk is measured from where the trade would actually start.
All levels use raw ``high``/``low``/``close`` so they are real price levels.
Parameters are documented in ``docs/decisions/0004-levels.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from solaraalpha.analysis.technical import FULL_SLOPE, STOP_ATR, atr

PIVOT_WEEKS = 3  # a swing high/low beats this many weeks either side
ZONE_ATR = 0.5  # pivots within this many ATRs of a zone's lowest member join the zone
MIN_TOUCHES = 2  # zones with fewer swing pivots are left out of the level map
TREND_PIVOTS = 6  # trendlines are drawn through pairs of the last N swing highs / lows
TREND_TOL_ATR = 0.25  # closes may poke this far through a trendline; pivots this near touch it
TREND_MAX_ATR = 3.0  # trendlines projected further than this from the close are ignored
COIL_WEEKS = 4  # recent weeks that make up the coil
COIL_ATR = 1.5  # coiled when those weeks span at most this many ATRs (high to low)
TRIGGER_ATR = 0.1  # breakout entry sits this far beyond the coil's line
STOP_BUFFER_ATR = 0.5  # stop sits this far beyond the nearest support / resistance
MIN_STOP_ATR = 1.0  # ...but at least this far from the close
YEAR = 52  # weeks
FIVE_YEARS = 5 * YEAR
FULL_RR = 3.0  # reward:risk that scores a full +1 on setup (1/3 scores -1, 1 scores 0)


def pivots(weekly: pd.DataFrame, k: int = PIVOT_WEEKS) -> pd.DataFrame:
    """Confirmed swing highs and lows: ``date`` (pivot week), ``confirmed`` (the week it became
    known, ``k`` weeks later), ``kind`` (``high``/``low``), ``price`` and ``pos`` (row number).
    A swing high is strictly above the ``k`` weeks before it and at least the ``k`` after."""
    out = []
    for kind, sign in (("high", 1), ("low", -1)):
        x = sign * weekly[kind]
        before = x.shift(1).rolling(k, min_periods=k).max()
        after = x.shift(-k).rolling(k, min_periods=k).max()
        pos = np.flatnonzero(((x > before) & (x >= after)).to_numpy())
        out.append(
            pd.DataFrame(
                {
                    "date": weekly.index[pos],
                    "confirmed": weekly.index[pos + k],
                    "kind": kind,
                    "price": weekly[kind].to_numpy()[pos],
                    "pos": pos,
                }
            )
        )
    return pd.concat(out, ignore_index=True).sort_values("pos", ignore_index=True)


def zones(weekly: pd.DataFrame, tol: float) -> pd.DataFrame:
    """Swing pivots clustered by price: a pivot joins the zone if within ``tol`` (price units) of
    the zone's lowest member. Columns: ``price`` (mean), ``low``, ``high``, ``touches``, ``last``
    (most recent pivot week)."""
    piv = pivots(weekly).sort_values("price", ignore_index=True)
    group = np.zeros(len(piv), dtype=int)
    floor = piv["price"].iloc[0] if len(piv) else 0.0
    for i, p in enumerate(piv["price"].iloc[1:], start=1):
        new = p - floor > tol
        group[i] = group[i - 1] + new
        floor = p if new else floor
    return (
        piv.groupby(group)
        .agg(
            price=("price", "mean"),
            low=("price", "min"),
            high=("price", "max"),
            touches=("price", "size"),
            last=("date", "max"),
        )
        .reset_index(drop=True)
    )


@dataclass(frozen=True)
class Trendline:
    kind: str  # upper (resistance, through swing highs) / lower (support, through swing lows)
    start: pd.Timestamp
    start_price: float
    end: pd.Timestamp
    end_price: float
    slope: float  # fractional change per week
    touches: int  # swing pivots on the line, anchors included
    value: float  # projected to the coming week


def trendline(weekly: pd.DataFrame, kind: str) -> Trendline | None:
    """Best ``upper`` or ``lower`` trendline still intact at the last close, or None.

    Candidates join pairs of the last ``TREND_PIVOTS`` confirmed swing highs (upper) or lows
    (lower) with a straight line in log price. A candidate is dropped if any weekly close since
    its first anchor is more than ``TREND_TOL_ATR`` x ATR through it, or if its projection to the
    coming week is more than ``TREND_MAX_ATR`` x ATR from the close (no longer in play). The
    survivor with the most pivots touching it wins; ties go to the most recent anchors."""
    if kind not in ("upper", "lower"):
        raise ValueError("kind must be 'upper' or 'lower'")
    sign = 1 if kind == "upper" else -1
    piv = pivots(weekly)
    piv = piv[piv["kind"] == ("high" if kind == "upper" else "low")].tail(TREND_PIVOTS)
    log_close = np.log(weekly["close"].to_numpy())
    n = len(log_close)
    atr_log = float(atr(weekly).iloc[-1]) / float(weekly["close"].iloc[-1])
    tol, far = TREND_TOL_ATR * atr_log, TREND_MAX_ATR * atr_log
    p_pos, p_log = piv["pos"].to_numpy(), np.log(piv["price"].to_numpy())

    best: tuple[tuple[int, int, int], Trendline] | None = None
    for a, b in combinations(range(len(piv)), 2):
        ia, ib = int(p_pos[a]), int(p_pos[b])
        slope = (p_log[b] - p_log[a]) / (ib - ia)
        line = p_log[a] + slope * (np.arange(ia, n) - ia)
        nxt = p_log[a] + slope * (n - ia)
        if (sign * (log_close[ia:] - line) > tol).any() or abs(nxt - log_close[-1]) > far:
            continue
        on = p_pos >= ia
        touches = int((np.abs(p_log[on] - p_log[a] - slope * (p_pos[on] - ia)) <= tol).sum())
        key = (touches, ib, ia)
        if best is None or key > best[0]:
            best = (
                key,
                Trendline(
                    kind=kind,
                    start=weekly.index[ia],
                    start_price=float(np.exp(p_log[a])),
                    end=weekly.index[ib],
                    end_price=float(np.exp(p_log[b])),
                    slope=float(np.expm1(slope)),
                    touches=touches,
                    value=float(np.exp(nxt)),
                ),
            )
    return best[1] if best else None


@dataclass(frozen=True)
class Coil:
    span_atr: float  # high-to-low span of the last ``COIL_WEEKS`` weeks, in ATRs
    coiled: bool  # span_atr <= COIL_ATR
    upper: float  # least-squares line through those weeks' highs, projected to the coming week
    upper_slope: float  # per week
    lower: float  # same through the lows
    lower_slope: float
    apex_weeks: float  # weeks until the lines meet (NaN unless converging)


def coil(weekly: pd.DataFrame, n: int = COIL_WEEKS) -> Coil:
    """The last ``n`` weeks as a coil: how tight they are and where their high/low lines go.
    Lines are fitted in log price; they converge when the upper falls faster than the lower."""
    w = weekly.iloc[-n:]
    x = np.arange(n)
    (su, iu), (sl, il) = (np.polyfit(x, np.log(w[c].to_numpy()), 1) for c in ("high", "low"))
    upper, lower = iu + su * n, il + sl * n  # log, projected to the coming week
    span = float(w["high"].max() - w["low"].min()) / _atr_now(weekly)
    apex = (upper - lower) / (sl - su) if su < sl and upper > lower else np.nan
    return Coil(
        span_atr=span,
        coiled=span <= COIL_ATR,
        upper=float(np.exp(upper)),
        upper_slope=float(np.expm1(su)),
        lower=float(np.exp(lower)),
        lower_slope=float(np.expm1(sl)),
        apex_weeks=float(apex),
    )


def _atr_now(weekly: pd.DataFrame) -> float:
    a = float(atr(weekly).iloc[-1])
    if np.isnan(a):
        raise ValueError("not enough weekly history for ATR")
    return a


def level_map(weekly: pd.DataFrame) -> pd.DataFrame:
    """Every level as of the last week, highest first: ``source`` (``zone``, ``trend_upper``,
    ``trend_lower``, ``high_52w``, ``low_52w``, ``high_5y``, ``low_5y``), ``price``,
    ``touches``, ``last`` (week it was last set or touched), ``role`` (``resistance`` above the
    close, ``support`` below), and distance from the close in ATRs and as a fraction.

    52-week and 5-year highs/lows exclude the latest week, so a close above them is a breakout
    (the old high becomes support). Trendline prices are projected to the coming week."""
    close, a = float(weekly["close"].iloc[-1]), _atr_now(weekly)
    rows: list[tuple[str, float, int, pd.Timestamp]] = [
        ("zone", z.price, z.touches, z.last)  # type: ignore[misc]
        for z in zones(weekly, ZONE_ATR * a).itertuples()
        if z.touches >= MIN_TOUCHES  # type: ignore[operator]
    ]
    for kind in ("upper", "lower"):
        t = trendline(weekly, kind)
        if t is not None:
            rows.append((f"trend_{kind}", t.value, t.touches, t.end))
    prior = weekly.iloc[:-1]
    for hi, lo, frame in (
        ("high_52w", "low_52w", prior.iloc[-YEAR:]),
        ("high_5y", "low_5y", prior.iloc[-FIVE_YEARS:]),
    ):
        rows.append((hi, *_extreme(frame, "high")))
        rows.append((lo, *_extreme(frame, "low")))
    df = pd.DataFrame(rows, columns=["source", "price", "touches", "last"])
    df["role"] = np.where(df["price"] > close, "resistance", "support")
    df["dist_atr"] = (df["price"] - close) / a
    df["dist_pct"] = df["price"] / close - 1
    return df.sort_values("price", ascending=False, ignore_index=True)


def _extreme(frame: pd.DataFrame, col: str) -> tuple[float, int, pd.Timestamp]:
    x = frame[col].to_numpy()
    i = int(x.argmax() if col == "high" else x.argmin())
    return float(x[i]), 1, frame.index[i]


@dataclass(frozen=True)
class LevelsCard:
    """Latest-week levels and a level-based plan for a new position on ``side``.
    Missing levels (no trendline, nothing above/below) are NaN."""

    date: pd.Timestamp
    side: int
    close: float
    atr: float
    support: float  # nearest level below the close
    resistance: float  # nearest level above the close
    lower_trend: float  # projected to the coming week
    lower_slope: float  # per week
    upper_trend: float
    upper_slope: float
    high_52w: float  # prior 52 weeks, excluding the latest
    low_52w: float
    high_5y: float  # prior 5 years (or all supplied history if shorter), excluding the latest
    low_5y: float
    start_5y: pd.Timestamp  # first week of that window; later than 5y ago if history is short
    blue_sky: bool  # closed above the 5-year high
    new_low: bool  # closed below the 5-year low
    breakout: int  # +1 closed above last week's nearest resistance, -1 below its support, else 0
    box_atr: float  # nearest resistance minus nearest support, in ATRs
    coil_atr: float  # span of the last COIL_WEEKS weeks, in ATRs
    coiled: bool  # coil_atr <= COIL_ATR: plan a breakout from ``trigger``
    coil_upper: float  # line through the coil's highs, projected to the coming week
    coil_upper_slope: float  # per week
    coil_lower: float  # line through the coil's lows
    coil_lower_slope: float
    apex_weeks: float  # weeks until the coil's lines meet (NaN unless converging)
    trigger: float  # breakout entry (stop order) beyond the coil; NaN = enter at the close
    coil_pending: bool  # coiled and the trigger is still above the close: wait for the break
    stop: float  # beyond the nearest level against ``side``
    target: float  # nearest level in favour of ``side`` beyond the entry (NaN: nothing)
    target_2: float  # the one after
    reward_risk: float  # from the entry to ``target``; inf when nothing is in the way
    risk_pct: float  # distance from the entry to the stop as a fraction of the entry
    structure: float  # [-1, 1], + bullish: trendline slopes and place in the 52-week range
    setup: float  # [-1, 1], + good reward:risk for ``side``


def _nearest(df: pd.DataFrame, close: float) -> tuple[pd.Series, pd.Series]:
    above = df.loc[df["price"] > close, "price"].sort_values()
    below = df.loc[df["price"] < close, "price"].sort_values(ascending=False)
    return above, below


def levels_card(weekly: pd.DataFrame, side: int = 1) -> LevelsCard:
    """Level map summarised into trade-plan numbers for a new position on ``side``.

    Stop: ``STOP_BUFFER_ATR`` beyond the nearest level against the position (support for a
    long), at least ``MIN_STOP_ATR`` from the close; ``STOP_ATR`` (technical model) if there is
    no such level. Targets: the next two levels in the position's favour.

    Coiled (the last ``COIL_WEEKS`` weeks span at most ``COIL_ATR`` ATRs): the entry is a stop
    order ``TRIGGER_ATR`` beyond the coil's line (upper for a long), or beyond the close if that
    is further, and beyond the nearest level ahead (resistance for a long) when that level lies
    inside the coil's range. The stop still sits beyond the nearest level against the position
    (the bottom of the box for a long), at least ``MIN_STOP_ATR`` from the entry, and targets,
    reward:risk and ``risk_pct`` are measured from the entry. ``setup`` is
    log(reward:risk) / log(``FULL_RR``), clipped: 3:1 scores +1, 1:1 scores 0, 1:3 scores -1;
    nothing in the way (e.g. blue sky for a long) scores +1.
    """
    if side not in (1, -1):
        raise ValueError("side must be 1 (long) or -1 (short)")
    close, a = float(weekly["close"].iloc[-1]), _atr_now(weekly)
    lm = level_map(weekly)
    above, below = _nearest(lm, close)
    c = coil(weekly)
    w = weekly.iloc[-COIL_WEEKS:]
    edge = [c.upper, close] if side == 1 else [c.lower, close]
    box = (above, w["high"].max()) if side == 1 else (below, w["low"].min())
    if not box[0].empty and side * (box[0].iloc[0] - box[1]) <= 0:
        edge.append(float(box[0].iloc[0]))  # the box edge lies inside the coil: clear it too
    trigger = side * max(side * e for e in edge) + side * TRIGGER_ATR * a if c.coiled else np.nan
    entry = trigger if c.coiled else close
    against = below if side == 1 else above
    favour = lm.loc[side * (lm["price"] - entry) > 0, "price"].sort_values(ascending=side == 1)

    if against.empty:
        stop = entry - side * STOP_ATR * a
    else:
        stop = float(against.iloc[0]) - side * STOP_BUFFER_ATR * a
        stop = entry - side * max(side * (entry - stop), MIN_STOP_ATR * a)
    targets = [float(x) for x in favour.iloc[:2]] + [np.nan, np.nan]
    risk = side * (entry - stop)
    reward_risk = side * (targets[0] - entry) / risk if not favour.empty else np.inf

    by = lm.set_index("source")["price"]
    upper, lower = trendline(weekly, "upper"), trendline(weekly, "lower")
    hi52, lo52 = float(by["high_52w"]), float(by["low_52w"])
    parts = [2 * (close - lo52) / (hi52 - lo52) - 1 if hi52 > lo52 else 0.0]
    parts += [t.slope / FULL_SLOPE for t in (upper, lower) if t is not None]

    breakout = 0
    if atr(weekly).notna().iloc[-2]:
        p_above, p_below = _nearest(level_map(weekly.iloc[:-1]), float(weekly["close"].iloc[-2]))
        if not p_above.empty and close > p_above.iloc[0]:
            breakout = 1
        elif not p_below.empty and close < p_below.iloc[0]:
            breakout = -1

    return LevelsCard(
        date=weekly.index[-1],
        side=side,
        close=close,
        atr=a,
        support=float(below.iloc[0]) if not below.empty else np.nan,
        resistance=float(above.iloc[0]) if not above.empty else np.nan,
        lower_trend=lower.value if lower else np.nan,
        lower_slope=lower.slope if lower else np.nan,
        upper_trend=upper.value if upper else np.nan,
        upper_slope=upper.slope if upper else np.nan,
        high_52w=hi52,
        low_52w=lo52,
        high_5y=float(by["high_5y"]),
        low_5y=float(by["low_5y"]),
        start_5y=weekly.index[max(len(weekly) - 1 - FIVE_YEARS, 0)],
        blue_sky=close > by["high_5y"],
        new_low=close < by["low_5y"],
        breakout=breakout,
        box_atr=float(above.iloc[0] - below.iloc[0]) / a
        if not (above.empty or below.empty)
        else np.nan,
        coil_atr=c.span_atr,
        coiled=c.coiled,
        coil_upper=c.upper,
        coil_upper_slope=c.upper_slope,
        coil_lower=c.lower,
        coil_lower_slope=c.lower_slope,
        apex_weeks=c.apex_weeks,
        trigger=float(trigger),
        coil_pending=bool(c.coiled and not np.isnan(trigger) and side * (trigger - close) > 0),
        stop=stop,
        target=targets[0],
        target_2=targets[1],
        reward_risk=float(reward_risk),
        risk_pct=risk / entry,
        structure=float(np.clip(parts, -1, 1).mean()),
        setup=float(np.clip(np.log(reward_risk) / np.log(FULL_RR), -1, 1)),
    )
