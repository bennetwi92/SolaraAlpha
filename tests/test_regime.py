from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from solaraalpha.analysis.regime import RegimeCard, market_regime


def _index(rets: np.ndarray, start: str = "2025-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(rets), tz="UTC", name="date")
    close = 4000 * np.exp(np.cumsum(rets))
    df = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 1e6,
        },
        index=idx,
    )
    df.attrs = {"ticker": "^FTAS", "currency": "GBP"}
    return df


def _quiet(n: int = 400, seed: int = 0, sigma: float = 0.006) -> np.ndarray:
    return sigma * np.random.default_rng(seed).standard_normal(n)


def test_calm_market() -> None:
    c = market_regime(_index(_quiet()))
    assert isinstance(c, RegimeCard) and c.regime == "calm" and c.index == "^FTAS"
    assert -0.08 <= c.drawdown_20d <= 0 and abs(c.ret_1d_z) < 3 and 0 <= c.vol_percentile_1y <= 1
    assert "no drawdown" in c.reason and c.date == pd.Timestamp(
        "2025-01-01", tz="UTC"
    ) + pd.offsets.BDay(399)


def test_risk_off_on_a_drawdown_boundary() -> None:
    rets = _quiet()
    # slide 7.9% from the 20-day high over the last ten sessions: not risk_off; 8.1%: risk_off
    for drop, expected in ((0.079, "calm"), (0.081, "risk_off")):
        r = rets.copy()
        r[-20:] = 0.0  # flat at the high for ten sessions, then the slide
        r[-10:] = math.log(1 - drop) / 10
        c = market_regime(_index(r))
        assert c.regime == expected, (drop, c)
        assert c.drawdown_20d == pytest.approx(-drop, abs=1e-3)
    assert "below its 20-day high" in market_regime(_index(r)).reason


def test_risk_off_on_a_three_sigma_day() -> None:
    rets = _quiet()
    sigma = rets[-61:-1].std()
    r = rets.copy()
    r[-1] = -2.9 * sigma
    assert market_regime(_index(r)).regime == "calm"
    r[-1] = -3.1 * sigma
    c = market_regime(_index(r))
    assert (
        c.regime == "risk_off"
        and "sigma day" in c.reason
        and c.ret_1d_z == pytest.approx(-3.1, abs=0.05)
    )
    assert c.ret_1d == pytest.approx(math.exp(-3.1 * sigma) - 1)


def test_stressed_on_a_vol_spike() -> None:
    rets = _quiet()
    r = rets.copy()
    r[-20:] = 0.02 * np.random.default_rng(3).standard_normal(20)  # vol more than triples, no trend
    c = market_regime(_index(r))
    assert c.regime in ("stressed", "risk_off") and c.vol_percentile_1y > 0.8
    if c.regime == "stressed":
        assert "percentile" in c.reason


def test_short_history_degrades_gracefully() -> None:
    c = market_regime(_index(_quiet(10)))
    assert c.regime == "calm" and math.isnan(c.vol_percentile_1y)
    with pytest.raises(ValueError, match="at least two sessions"):
        market_regime(_index(_quiet(1)))
