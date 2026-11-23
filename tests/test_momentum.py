from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from solaraalpha import toolkits
from solaraalpha.analysis.momentum import EXTENDED_ATR, MIN_SESSIONS, momentum_card
from solaraalpha.pipeline import WEEKLY, run_toolkit
from solaraalpha.portfolio.fit import rank_score
from solaraalpha.toolkits import REGISTRY, Inputs


def _bars(
    close: np.ndarray, volume: np.ndarray | None = None, end: str = "2026-11-20"
) -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=len(close), tz="UTC", name="date")
    vol = np.full(len(close), 1e6) if volume is None else volume
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.99,
            "close": close,
            "adj_close": close,
            "volume": vol,
        },
        index=idx,
    )


def _market(n: int, drift: float = 0.0) -> pd.Series:
    idx = pd.bdate_range(end="2026-11-20", periods=n, tz="UTC", name="date")
    return pd.Series(100 * np.exp(drift * np.arange(n)), index=idx)


def test_a_steady_leader_on_heavy_volume_scores_high() -> None:
    n = 300
    close = 100 * np.exp(0.004 * np.arange(n))  # +0.4% a session, at its high every day
    volume = np.full(n, 1e6)
    volume[-5:] = 3e6  # three times the usual volume this week
    card = momentum_card(_bars(close, volume), market=_market(n))
    assert card.trend == 1.0 and card.high == 1.0 and card.volume == 1.0
    assert card.strength == 1.0  # ~8% and ~27% ahead of a flat market
    assert card.crowd == 1.0 and card.new_high and card.breakout
    assert card.rvol == pytest.approx(3.0) and card.pct_from_high == 0.0
    assert card.sessions == n and card.date == pd.Timestamp("2026-11-20", tz="UTC")


def test_a_falling_stock_on_thin_volume_scores_low() -> None:
    n = 300
    close = 100 * np.exp(-0.003 * np.arange(n))
    volume = np.full(n, 1e6)
    volume[-5:] = 4e5
    card = momentum_card(_bars(close, volume), market=_market(n, 0.001))
    assert card.trend == -1.0 and card.high == -1.0 and card.volume == -1.0
    assert card.strength == -1.0 and card.crowd == -1.0
    assert not card.new_high and not card.breakout


def test_no_market_leaves_strength_out_of_the_mean() -> None:
    close = 100 * np.exp(0.004 * np.arange(300))
    card = momentum_card(_bars(close))
    assert math.isnan(card.strength) and math.isnan(card.rs_market_20)
    assert card.crowd == pytest.approx((1.0 + 1.0 + 0.0) / 3)  # trend, high, flat volume


def test_as_of_uses_only_earlier_sessions() -> None:
    n = 400
    close = np.r_[100 * np.exp(0.004 * np.arange(300)), np.full(100, 100 * np.exp(1.2) * 0.7)]
    bars, market = _bars(close), _market(n)
    cut = bars.index[299]
    then = momentum_card(bars, market, as_of=cut)
    assert repr(then) == repr(momentum_card(bars[bars.index <= cut], market[market.index <= cut]))
    assert then.high == 1.0 and momentum_card(bars, market).high < 0  # the later drop not seen


def test_a_young_listing_gets_three_trend_votes_and_a_short_history_raises() -> None:
    card = momentum_card(_bars(100 * np.exp(0.004 * np.arange(120))))
    assert card.trend == 1.0 and card.sessions == 120
    with pytest.raises(ValueError, match="needs"):
        momentum_card(_bars(np.full(MIN_SESSIONS - 1, 100.0)))


def test_stretched_close_is_flagged_extended() -> None:
    close = np.r_[np.full(299, 100.0), 130.0]  # a 30% jump on a flat range (ATR ~2 + the gap)
    card = momentum_card(_bars(close))
    assert card.extension_atr > EXTENDED_ATR and card.extended


def test_toolkit_is_the_screen_rank(monkeypatch: pytest.MonkeyPatch) -> None:
    assert REGISTRY["momentum"].act == ("crowd >= 0.5",)
    assert "momentum" in WEEKLY[0].toolkits
    up = _bars(100 * np.exp(0.004 * np.arange(300)), end=pd.Timestamp.now().strftime("%Y-%m-%d"))
    monkeypatch.setattr(toolkits, "fetch_daily", lambda ticker: up.copy())
    step = run_toolkit("momentum", Inputs("NVDA"))
    card = step.cards["momentum"]
    # every instrument is the same series here: no excess return, flat volume
    assert step.status == "act" and card.crowd == 0.5 and card.strength == 0.0
    assert rank_score(step.cards) == card.crowd
    assert toolkits.owner_of("crowd") == "momentum" and toolkits.owner_of("trend") == "technical"


def _random_bars(n: int, seed: int) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(0.0005 + 0.02 * rng.standard_normal(n)))
    volume = 1e6 * np.exp(0.5 * rng.standard_normal(n))
    bars = _bars(close, volume)
    idx = pd.bdate_range(end="2026-11-20", periods=n + 30, tz="UTC", name="date")
    market = pd.Series(100 * np.exp(np.cumsum(0.01 * rng.standard_normal(n + 30))), index=idx)
    return bars, market.drop(market.index[[n + 5, n + 9]])  # gaps: the ffill is exercised


def test_crowd_panel_equals_the_card_on_every_date() -> None:
    from solaraalpha.analysis.momentum import crowd_panel

    for n, seed in ((330, 1), (150, 2)):  # with and without the 200-day vote
        bars, market = _random_bars(n, seed)
        panel = crowd_panel(bars, market)
        assert panel.iloc[: MIN_SESSIONS - 1].isna().all().all()
        for i in (MIN_SESSIONS - 1, 120, 199, 200, n - 1):
            if i >= n:
                continue
            day = bars.index[i]
            card = momentum_card(bars, market, as_of=day)
            row = panel.loc[day]
            for f in ("trend", "high", "volume", "strength", "crowd"):
                assert row[f] == pytest.approx(getattr(card, f), abs=1e-12), (n, i, f)


def test_crowd_beta_takes_the_market_out() -> None:
    from solaraalpha.analysis.momentum import crowd_panel

    rng = np.random.default_rng(3)
    n = 300
    idx = pd.bdate_range(end="2026-11-20", periods=n, tz="UTC", name="date")
    rm = 0.01 * rng.standard_normal(n)
    market = pd.Series(100 * np.exp(np.cumsum(rm)), index=idx)
    close = 100 * np.exp(np.cumsum(2 * rm))  # a pure beta-2 stock, no stock-specific return
    panel = crowd_panel(_bars(close), market)
    tail = panel.iloc[-100:]
    raw, net = tail["strength"].abs().mean(), tail["strength_beta"].abs().mean()
    assert net < raw / 3  # leverage alone moves the raw score; net of beta little is left
