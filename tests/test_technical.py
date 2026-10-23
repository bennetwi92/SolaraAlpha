from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from solaraalpha.analysis.technical import (
    assess_position,
    atr,
    indicators,
    rsi,
    scorecard,
    scores,
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


def _wiggle(n: int, drift: float) -> np.ndarray:
    rng = np.random.default_rng(0)
    return 100 * np.exp(np.cumsum(drift + 0.02 * rng.standard_normal(n)))


def test_rsi_and_atr_basics() -> None:
    up = pd.Series(np.arange(1.0, 40.0))
    assert rsi(up).iloc[-1] == pytest.approx(100)
    df = _weekly(np.full(30, 100.0))
    assert atr(df).iloc[-1] == pytest.approx(4.0)  # high-low = 4, no gaps


def test_no_look_ahead() -> None:
    df = _weekly(_wiggle(150, 0.003))
    full = scores(df)
    cut = scores(df.iloc[:100])
    pd.testing.assert_frame_equal(full.iloc[:100], cut)


def test_uptrend_scores_bullish_and_downtrend_bearish() -> None:
    up = scorecard(_weekly(_wiggle(150, 0.01)))
    down = scorecard(_weekly(_wiggle(150, -0.01)))
    assert up.trend > 0.5 and up.momentum > 0
    assert down.trend < -0.5 and down.momentum < 0
    for card in (up, down):
        for v in (card.trend, card.momentum, card.entry):
            assert -1 <= v <= 1


def test_warm_up_is_nan() -> None:
    sc = scores(_weekly(_wiggle(60, 0.0)))
    assert sc["trend"].iloc[:39].isna().all()
    assert sc["momentum"].iloc[:51].isna().all()
    assert sc[["trend", "momentum", "entry"]].iloc[-1].notna().all()


def test_overextended_rally_is_poor_entry_for_long() -> None:
    close = np.concatenate([np.full(80, 100.0) * _wiggle(80, 0.0) / 100, np.linspace(100, 200, 8)])
    sc = scores(_weekly(close))
    assert sc["extension"].iloc[-1] > 3
    assert sc["entry"].iloc[-1] < 0
    short = scores(_weekly(close), side=-1)
    assert short["stop"].iloc[-1] > short["close"].iloc[-1]


def test_assess_position_hold_then_exit() -> None:
    rise = _wiggle(120, 0.01)
    df = _weekly(rise)
    pos = assess_position(df, df.index[80])
    assert pos.action == "hold" and pos.stopped_on is None
    assert pos.stop > pos.initial_stop
    assert pos.r_multiple > 0

    crash = np.concatenate([rise, rise[-1] * np.linspace(0.95, 0.5, 6)])
    pos = assess_position(_weekly(crash), df.index[80])
    assert pos.action == "exit" and pos.score == -1


def test_the_trail_ignores_the_entry_weeks_high_before_the_entry() -> None:
    df = _weekly(_wiggle(81, 0.0))
    entry = df.index[80]  # the last week: the position has seen only its entry week
    df.loc[entry, "high"] = df.loc[entry, "close"] * 1.5  # a spike before the entry that week
    pos = assess_position(df, entry)
    assert pos.stop < df.loc[entry, "close"] and pos.action == "hold"  # was a stop above the close


def test_assess_position_short_and_errors() -> None:
    df = _weekly(_wiggle(120, -0.01))
    pos = assess_position(df, "2021-06-01", side=-1, entry_price=float(df["close"].iloc[75]))
    assert pos.side == -1 and pos.ret > 0
    with pytest.raises(ValueError):
        assess_position(df, "2030-01-01")
    with pytest.raises(ValueError):
        scores(df, side=0)


def test_indicator_columns() -> None:
    ind = indicators(_weekly(_wiggle(60, 0.0)))
    assert {"sma_fast", "sma_slow", "adx", "rsi", "macd_hist", "atr", "mom_sharpe"} <= set(
        ind.columns
    )
