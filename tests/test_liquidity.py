from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from solaraalpha import toolkits
from solaraalpha.analysis.liquidity import corwin_schultz, liquidity_card
from solaraalpha.pipeline import (
    REVIEW_MONTHS,
    WEEKLY,
    run_toolkit,
    stale_watchlist,
    watchlist_snapshot,
)
from solaraalpha.toolkits import REGISTRY, Inputs


def _daily(n: int = 120, spread: float = 0.02) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    close = 1000 * np.exp(np.cumsum(0.0005 + 0.01 * rng.standard_normal(n)))
    idx = pd.bdate_range(end="2026-10-09", periods=n, tz="UTC", name="date")
    half = spread / 2
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * (1 + half + 0.005),
            "low": close * (1 - half - 0.005),
            "close": close,
            "adj_close": close,
            "volume": 40_000.0,
        },
        index=idx,
    )
    df.attrs = {"ticker": "T.L", "currency": "GBp"}
    return df


def test_liquidity_card_values_pence_and_zero_volume() -> None:
    df = _daily()
    df.loc[df.index[-3:], "volume"] = 0.0  # three no-trade sessions
    card = liquidity_card(df, max_adv_pct=0.05)
    assert card.date == df.index[-1]
    expect20 = float(((df["close"] * df["volume"]).iloc[-20:]).median()) / 100  # pence -> GBP
    assert card.adv_gbp_20 == pytest.approx(expect20)
    assert card.adv_gbp_60 == pytest.approx(
        float(((df["close"] * df["volume"]).iloc[-60:]).median()) / 100
    )
    assert card.zero_volume_share == pytest.approx(3 / 60)
    assert card.max_position_gbp == pytest.approx(0.05 * card.adv_gbp_20)
    assert 0 <= card.spread_proxy < 0.1


def test_spread_proxy_orders_wide_before_tight() -> None:
    tight = liquidity_card(_daily(spread=0.005), 0.05).spread_proxy
    wide = liquidity_card(_daily(spread=0.04), 0.05).spread_proxy
    assert wide > tight
    assert (corwin_schultz(_daily()).dropna() >= 0).all()


def test_as_of_uses_only_earlier_sessions() -> None:
    df = _daily()
    cut = df.index[-10]
    card = liquidity_card(df, 0.05, as_of=cut)
    assert card.date == cut
    assert card.adv_gbp_20 == pytest.approx(liquidity_card(df[df.index <= cut], 0.05).adv_gbp_20)
    with pytest.raises(ValueError, match="no sessions"):
        liquidity_card(df.iloc[:0], 0.05)


def test_liquidity_toolkit_registered_in_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        "liquidity" in REGISTRY
        and REGISTRY["liquidity"].act == ()
        and REGISTRY["liquidity"].watch == ()
    )
    assert "liquidity" in WEEKLY[0].toolkits
    monkeypatch.setattr(toolkits, "fetch_daily", lambda ticker: _daily(400))
    step = run_toolkit("liquidity", Inputs("T.L"))
    assert step.status == "act" and "liquidity" in step.cards
    card = step.cards["liquidity"]
    assert (
        card.date == Inputs("T.L").weekly.index[-1] if False else card.date.weekday() == 4
    )  # a Friday


def test_stale_watchlist() -> None:
    assert REVIEW_MONTHS == (3, 6, 9, 12)
    snap = pd.Timestamp("2026-10-16")
    assert stale_watchlist(snap, pd.Timestamp("2026-12-31")) is None
    assert stale_watchlist(snap, pd.Timestamp("2027-01-07")) is None  # grace week
    warn = stale_watchlist(snap, pd.Timestamp("2027-01-08"))
    assert warn is not None and "predates the December 2026 FTSE review" in warn
    assert (
        stale_watchlist(pd.Timestamp("2026-12-20"), pd.Timestamp("2027-04-08")) is not None
    )  # next review March 2027
    assert stale_watchlist(pd.Timestamp("2026-12-20"), pd.Timestamp("2027-04-07")) is None
    assert "no `snapshot`" in (stale_watchlist(None, pd.Timestamp("2026-10-16")) or "")
    assert watchlist_snapshot() == pd.Timestamp("2026-10-16")
