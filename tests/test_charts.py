from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha.analysis.charts import weekly_chart
from solaraalpha.data.clean import to_weekly


def _daily(n: int = 800) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    close = 1000 * np.exp(np.cumsum(0.001 + 0.015 * rng.standard_normal(n)))
    idx = pd.bdate_range(end="2026-10-09", periods=n, tz="UTC", name="date")
    df = pd.DataFrame(
        {
            "open": close * (1 + 0.003 * rng.standard_normal(n)),
            "high": close * 1.012,
            "low": close * 0.988,
            "close": close,
            "adj_close": close,
            "volume": rng.integers(50_000, 200_000, n).astype("float64"),
        },
        index=idx,
    )
    df.attrs = {"ticker": "JET2.L", "currency": "GBp"}
    return df


def test_weekly_chart_writes_a_png(tmp_path: Path) -> None:
    weekly = to_weekly(_daily(), as_of=pd.Timestamp("2026-10-12", tz="UTC"))
    last = float(weekly["close"].iloc[-1])
    levels = {
        "support": last * 0.93,
        "resistance": last * 1.08,
        "lower_trend": last * 0.9,
        "lower_slope": last * 0.002,
        "upper_trend": last * 1.1,
        "upper_slope": last * 0.001,
        "coiled": 1.0,
        "coil_upper": last * 1.03,
        "coil_upper_slope": -last * 0.001,
        "coil_lower": last * 0.97,
        "coil_lower_slope": last * 0.001,
        "trigger": last * 1.035,
        "stop": last * 0.9,
        "target": last * 1.2,
        "target_2": math.nan,  # missing levels are skipped
        "atr": last * 0.04,
    }
    tech = {"trend": 0.6, "momentum": 0.2, "entry": -0.1}
    out = weekly_chart(weekly, levels, tech, tmp_path / "x" / "chart.png", title="JET2.L")
    assert out == tmp_path / "x" / "chart.png" and out.exists()
    assert out.stat().st_size > 40_000  # a real picture, not a blank canvas
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # no cards, no MAs precomputed, a short history: still a chart
    short = weekly_chart(weekly.tail(20).drop(columns=["sessions"]), None, None, tmp_path / "s.png")
    assert short.stat().st_size > 20_000
    with pytest.raises(ValueError, match="no weekly bars"):
        weekly_chart(weekly.iloc[0:0], None, None, tmp_path / "e.png")
