from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from solaraalpha.analysis.levels import level_map, levels_card, pivots, trendline, zones


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


def test_errors() -> None:
    with pytest.raises(ValueError):
        levels_card(_weekly(_range(n=10)))
    with pytest.raises(ValueError):
        levels_card(_weekly(_range()), side=0)
