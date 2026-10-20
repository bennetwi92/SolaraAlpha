from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from solaraalpha.analysis import levels
from solaraalpha.analysis.levels import (
    coil,
    compression,
    describe,
    level_map,
    levels_card,
    pivots,
    trendline,
    zones,
)


def _weekly(close: np.ndarray, start: str = "2020-01-03") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(close), freq="W-FRI", tz="UTC", name="date")
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "adj_close": close,
            "volume": 1e6,
        },
        index=idx,
    )


def _range(n: int = 80, period: int = 10, lo: float = 90.0, hi: float = 110.0) -> np.ndarray:
    """Price oscillating between ``lo`` and ``hi`` (a trading range)."""
    t = np.arange(n)
    return lo + (hi - lo) * (1 - np.cos(2 * np.pi * t / period)) / 2


def test_pivots_are_swings_and_only_confirmed() -> None:
    df = _weekly(_range())
    piv = pivots(df)
    assert set(piv["kind"]) == {"high", "low"}
    highs = piv[piv["kind"] == "high"]
    assert np.allclose(highs["price"], 110 * 1.02)
    assert (piv["confirmed"] <= df.index[-1]).all()
    # No look-ahead: pivots from a truncated frame are exactly the full frame's pivots that
    # were already confirmed by then.
    cut = df.iloc[:47]
    known = piv[piv["confirmed"] <= cut.index[-1]].reset_index(drop=True)
    pd.testing.assert_frame_equal(pivots(cut), known)


def test_range_gives_support_and_resistance_zones() -> None:
    df = _weekly(_range(n=76))  # ends mid-range, rising
    z = zones(df, tol=1.0)
    assert len(z) == 2 and (z["touches"] >= 6).all()
    lm = level_map(df)
    zone = lm[lm["source"] == "zone"]
    assert set(zone["role"]) == {"support", "resistance"}
    assert lm["price"].is_monotonic_decreasing
    card = levels_card(df)
    assert card.support == pytest.approx(90 * 0.98)
    assert card.resistance == pytest.approx(110 * 1.02)
    assert card.stop < card.support < card.close < card.target
    assert not card.blue_sky and not card.new_low
    assert not card.coil_pending and np.isnan(card.trigger)


def test_trendlines_follow_a_rising_channel() -> None:
    t = np.arange(120)
    close = 100 * np.exp(0.01 * t) * (1 + 0.05 * np.sin(2 * np.pi * t / 12))
    df = _weekly(close)
    lower, upper = trendline(df, "lower"), trendline(df, "upper")
    assert lower is not None and upper is not None
    assert lower.slope == pytest.approx(0.01, abs=0.002)
    assert upper.slope == pytest.approx(0.01, abs=0.002)
    assert lower.touches >= 3 and lower.value < df["close"].iloc[-1] * 1.05 < upper.value
    with pytest.raises(ValueError):
        trendline(df, "middle")
    assert levels_card(df).structure > 0.5


def test_broken_trendline_is_dropped() -> None:
    t = np.arange(100)
    close = 100 * np.exp(0.01 * t) * (1 + 0.05 * np.sin(2 * np.pi * t / 12))
    crash = np.concatenate([close, close[-1] * np.linspace(0.9, 0.6, 5)])
    lower = trendline(_weekly(crash), "lower")
    assert lower is None or lower.slope < 0.005


def test_blue_sky_breakout_long_vs_short() -> None:
    close = np.concatenate([_range(n=76), np.linspace(112, 140, 6)])
    df = _weekly(close)
    long = levels_card(df)
    assert long.blue_sky and long.close > long.high_5y
    assert long.new_high and long.from_high_52w > 0 and "blue sky" in long.context
    assert np.isnan(long.target) and long.reward_risk == np.inf and long.setup == 1
    short = levels_card(df, side=-1)
    assert short.stop > short.close > short.target
    for card in (long, short):
        assert -1 <= card.structure <= 1 and -1 <= card.setup <= 1


def test_breakout_flag() -> None:
    close = np.concatenate([_range(n=76), [100.0, 104.0, 107.0, 125.0]])
    assert levels_card(_weekly(close)).breakout == 1
    close = np.concatenate([_range(n=76), [100.0, 96.0, 93.0, 75.0]])
    card = levels_card(_weekly(close))
    assert card.breakout == -1 and card.new_low


def _triangle(top: float = 109.5, floor: float = 100.0, volume: float = 1e6) -> pd.DataFrame:
    """A trading range 90-110 that ends squeezing into an ascending triangle under ``top``."""
    df = _weekly(_range(n=72))
    tail = pd.DataFrame(
        {
            "open": [104.0, 106.0, 107.0, 108.0],
            "high": [top - 0.5, top, top - 0.1, top],
            "low": [floor, floor + 2, floor + 3.5, floor + 5],
            "close": [106.0, 107.0, 108.0, 108.5],
            "adj_close": [106.0, 107.0, 108.0, 108.5],
            "volume": volume,
        },
        index=pd.date_range(df.index[-1] + pd.Timedelta(weeks=1), periods=4, freq="W-FRI"),
    )
    return pd.concat([df, tail])


def test_coil_is_tight_and_converging() -> None:
    c = coil(_triangle())
    assert c.coiled and c.span_atr <= levels.COIL_ATR
    assert c.upper_slope < c.lower_slope and c.lower_slope > 0
    assert c.lower < c.upper and 0 < c.apex_weeks < 20
    assert not coil(_weekly(_range(n=76))).coiled  # mid-swing in a wide range


def test_compressed_long_plans_a_breakout_from_the_trigger() -> None:
    df = _triangle(volume=5e5)  # the year's tightest weeks on drying-up volume: compressed
    card = levels_card(df)
    assert card.compressed and card.trigger > max(card.close, card.coil_upper)
    assert card.coil_pending  # compressed and the trigger is above the close
    assert card.trigger < card.resistance == card.target  # box top (~112) is above the coil
    assert card.box_atr > 0
    assert card.stop < card.support < card.close < card.trigger
    risk = card.trigger - card.stop
    assert card.reward_risk == pytest.approx((card.target - card.trigger) / risk)
    assert card.risk_pct == pytest.approx(risk / card.trigger)
    inside = levels_card(
        _triangle(top=112.5, floor=103.0, volume=5e5)
    )  # coil reaches the box top: clear it to enter
    assert inside.compressed and inside.trigger > inside.resistance
    assert inside.trigger > inside.high_5y  # nothing overhead once it clears the box
    assert np.isnan(inside.target) and inside.reward_risk == np.inf
    short = levels_card(df, side=-1)
    assert short.trigger < min(short.close, short.coil_lower)
    assert short.stop > short.close > short.trigger


def test_coiled_but_not_compressed_enters_at_the_close() -> None:
    # ADR 0004 amendment #210: a tight coil on usual volume is not a squeeze, so no trigger
    card = levels_card(_triangle())
    assert card.coiled and not card.compressed
    assert np.isnan(card.trigger) and not card.coil_pending
    assert card.reward_risk == pytest.approx((card.target - card.close) / (card.close - card.stop))


def test_uncoiled_plan_enters_at_the_close() -> None:
    card = levels_card(_weekly(_range(n=76)))
    assert not card.coiled and np.isnan(card.trigger)
    assert card.reward_risk == pytest.approx((card.target - card.close) / (card.close - card.stop))


def test_far_trendline_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    t = np.arange(100)
    close = 100 * np.exp(0.01 * t) * (1 + 0.05 * np.sin(2 * np.pi * t / 12))
    df = _weekly(np.concatenate([close, np.full(15, close[-1] * 0.8)]))
    monkeypatch.setattr(levels, "TREND_MAX_ATR", np.inf)
    far = trendline(df, "upper")
    assert far is not None  # the rising line over the old highs is still unbroken...
    monkeypatch.setattr(levels, "TREND_MAX_ATR", 3.0)
    near = trendline(df, "upper")
    a = levels._atr_now(df)
    assert abs(far.value - df["close"].iloc[-1]) > 3 * a  # ...but far above price
    assert near is None or abs(near.value - df["close"].iloc[-1]) <= 3 * a


def test_errors() -> None:
    with pytest.raises(ValueError):
        levels_card(_weekly(_range(n=10)))
    with pytest.raises(ValueError):
        levels_card(_weekly(_range()), side=0)


def test_compression_is_against_the_stocks_own_year() -> None:
    quiet = compression(_triangle(volume=5e5))  # the year's tightest weeks, on half the volume
    assert quiet.range_pctile == 0 and quiet.volume_ratio == pytest.approx(0.5)
    assert quiet.compressed and quiet.score == pytest.approx(1.0)
    busy = compression(_triangle())  # as tight, but volume as usual: not drying up
    assert busy.range_pctile == 0 and busy.volume_ratio == pytest.approx(1.0)
    assert not busy.compressed and busy.score == pytest.approx(0.5)
    wide = compression(_weekly(_range(n=76)))  # mid-swing in a wide range
    assert wide.range_pctile > levels.COMPRESS_PCTILE and not wide.compressed
    short = compression(_weekly(_range(n=20)))  # under half a year to compare with
    assert np.isnan(short.range_pctile) and np.isnan(short.volume_ratio) and not short.compressed
    # point-in-time: what comes after a week doesn't change that week's reading
    df = _triangle(volume=5e5)
    after = _weekly(np.full(6, 150.0), start=str((df.index[-1] + pd.Timedelta(weeks=1)).date()))
    assert compression(pd.concat([df, after]).iloc[: len(df)]) == quiet
    card = levels_card(df)
    assert card.compressed and card.compression == quiet.score
    assert card.context.startswith("compressed (0% of the past year's 4-week ranges")
    assert "forming: no breakout yet" in card.context


def test_wedge_from_converging_trendlines() -> None:
    t = np.arange(90)
    amp = np.linspace(0.3, 0.04, 90)  # swings shrink: lower highs, higher lows
    card = levels_card(_weekly(100 * (1 + amp * np.sin(2 * np.pi * t / 10))))
    assert card.upper_slope < 0 < card.lower_slope
    assert card.wedge and 0 < card.wedge_apex_weeks <= levels.WEDGE_APEX_WEEKS
    assert card.wedge_width_atr > 0 and "wedge: trendlines converging" in card.context
    t = np.arange(120)  # a parallel rising channel is not a wedge
    channel = 100 * np.exp(0.01 * t) * (1 + 0.05 * np.sin(2 * np.pi * t / 12))
    assert not levels_card(_weekly(channel)).wedge


def test_describe_reads_stored_cards() -> None:
    # cards come back from the DB with booleans as 1.0 / 0.0; older cards lack the new fields
    old = {"breakout": 1, "blue_sky": 0.0, "coil_pending": 1.0, "trigger": 1520.0}
    assert describe(**old) == (
        "broke out above last week's resistance; breakout trigger 1520.00 not yet hit"
    )
    assert (
        describe(compressed=0.0, volume_ratio=1.5)
        == "not compressed (volume 1.50x the year's median)"
    )
    new = {"compressed": 1.0, "range_pctile": 0.08, "volume_ratio": 0.62, "wedge": 1.0}
    text = describe(**new, wedge_apex_weeks=11.8, new_high=0.0, from_high_52w=-0.12)
    assert text == (
        "compressed (8% of the past year's 4-week ranges were tighter, volume 0.62x the year's"
        " median); wedge: trendlines converging, apex in ~12 weeks; forming: no breakout yet;"
        " 12% below the 52-week high"
    )
    assert describe() == ""
