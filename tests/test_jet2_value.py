from __future__ import annotations

from dataclasses import replace
from typing import Any

import pandas as pd
import pytest

from solaraalpha.analysis import jet2_value as m


def test_fy26_reproduces_reported_accounts() -> None:
    p = m.pnl(m.FY26)
    assert p["revenue"] == pytest.approx(7482.1, abs=0.1)
    assert p["operating_costs"] == pytest.approx(7042.5, abs=0.1)
    assert p["ebit"] == pytest.approx(439.6, abs=0.1)
    assert p["pbt"] == pytest.approx(551.0, abs=0.1)
    assert p["eps"] == pytest.approx(208.2, abs=0.05)
    assert p["fuel"] + 32.0 == pytest.approx(728.9, abs=0.1)  # fuel line incl. SAF premium


def test_base_years_hit_consensus() -> None:
    y27, y28 = m.base_years()
    assert m.pnl(y27)["eps"] == pytest.approx(m.CONSENSUS["FY27"])
    assert m.pnl(y28)["eps"] == pytest.approx(m.CONSENSUS["FY28"])


def test_reference_market_is_the_calibrated_base() -> None:
    w = m.fy27_remaining(m.REFERENCE.date)
    y27, y28, pe = m.scenario(m.REFERENCE, [], w)
    assert m.pnl(y27)["eps"] == pytest.approx(m.CONSENSUS["FY27"])
    assert m.pnl(y28)["eps"] == pytest.approx(m.CONSENSUS["FY28"])
    assert pe == 0.0


def test_fy27_remaining_follows_seat_profile() -> None:
    assert m.fy27_remaining(pd.Timestamp("2026-04-01", tz="UTC")) == pytest.approx(1.0)
    assert m.fy27_remaining(pd.Timestamp("2027-04-01", tz="UTC")) == pytest.approx(0.0)
    after_summer = m.fy27_remaining(pd.Timestamp("2026-11-01", tz="UTC"))
    assert after_summer == pytest.approx(m.WINTER_SEATS / (m.SUMMER_SEATS + m.WINTER_SEATS))


@pytest.mark.parametrize(
    ("spec", "expected"), [("+5%", 105.0), ("-10", 90.0), ("*2", 200.0), ("42", 42.0)]
)
def test_target_spec(spec: str, expected: float) -> None:
    assert m._target(100.0, spec) == pytest.approx(expected)


def test_apply_scopes() -> None:
    years = m.base_years()
    y27, y28, _ = m.apply(years, ["seats=+10%"], fy27_weight=0.25)
    assert y28.seats == pytest.approx(years[1].seats * 1.10)
    assert y27.seats == pytest.approx(years[0].seats * 1.025)  # a quarter of FY27 still to fly
    y27, y28, _ = m.apply(years, ["fy27.seats=+10%"], fy27_weight=0.25)
    assert y27.seats == pytest.approx(years[0].seats * 1.10)
    assert y28.seats == years[1].seats
    y27, y28, pe = m.apply(years, ["fy28.seats=+10%", "pe=+0.5"], fy27_weight=0.25)
    assert y27.seats == years[0].seats
    assert pe == 0.5


@pytest.mark.parametrize("bad", ["nonsense=1", "fy29.seats=1", "seats=", "seats"])
def test_apply_rejects_bad_shocks(bad: str) -> None:
    with pytest.raises(ValueError):
        m.apply(m.base_years(), [bad], 0.5)


def test_directions() -> None:
    """Each lever moves FY28 EPS the way the business says it should."""
    _, y28 = m.base_years()
    e = m.pnl(y28)["eps"]

    def eps(**kw: Any) -> float:
        return m.pnl(replace(y28, **kw))["eps"]

    assert eps(fuel_market_usd=y28.fuel_market_usd * 1.2) < e  # half unhedged, 30% recovered
    assert eps(fuel_hedged=0.8) > e  # hedges are below the market
    assert eps(usd_market=y28.usd_market * 0.95) < e  # weaker pound: dearer fuel
    assert eps(eur_market=y28.eur_market * 0.95) < e  # weaker pound: dearer hotels
    assert eps(load_factor=y28.load_factor + 0.01) > e
    assert eps(base_rate=y28.base_rate + 0.005) > e  # net cash earns more
    assert eps(extra_revenue=20.0, extra_costs=20.0) == pytest.approx(e)


def test_full_pass_through_neutralises_unhedged_fuel() -> None:
    _, y28 = m.base_years()
    spike = replace(y28, fuel_hedged=0.0, pass_through=1.0, fuel_market_usd=2000.0)
    assert m.pnl(spike)["ebit"] == pytest.approx(m.pnl(replace(y28, fuel_hedged=0.0))["ebit"])


def test_value_reverts_pe_and_counts_fy27_once() -> None:
    y27, y28 = m.base_years()
    price = m.REFERENCE.price
    v0 = m.value(y27, y28, price, weeks=0)
    assert v0.fair_value == pytest.approx(price)  # no time, no news: today's price
    v52 = m.value(y27, y28, price, weeks=52, fair_pe=10.0)
    assert v52.horizon_pe == pytest.approx((v0.implied_pe + 10.0) / 2)  # one half-life
    up27 = replace(y27, extra_revenue=100.0)
    gain = m.value(up27, y28, price, weeks=0).fair_value - price
    assert gain == pytest.approx(100 * 0.75 / y27.shares * 100)  # FY27 cash, no multiple


def test_value_floor() -> None:
    y27, y28 = m.base_years()
    crash = replace(y28, load_factor=0.6)
    assert m.value(y27, crash, m.REFERENCE.price, 12).fair_value == pytest.approx(m.FLOOR)


def test_diluted_shares_fall_with_buyback() -> None:
    s27, s28 = m.diluted_shares(1413.0)
    assert s28 < s27 < m.SHARES_IN_ISSUE
    assert m.diluted_shares(2000.0)[1] > s28  # a higher price buys fewer shares


def test_market_from_curve() -> None:
    months = pd.date_range("2026-11-01", "2028-03-01", freq="MS")
    flat = pd.Series(4.0, index=months)
    mk = m.market_from(pd.Timestamp("2026-10-16", tz="UTC"), 1400.0, flat, 1.3, 1.2)
    jet = 4.0 * m.GAL_PER_TONNE * (1 + m.JET_PREMIUM)
    assert mk.fuel_fy27_rest == pytest.approx(jet)
    assert mk.fuel_fy28 == pytest.approx(jet)
    empty = m.market_from(mk.date, 1400.0, pd.Series(dtype=float), 1.3, 1.2)
    assert empty.fuel_fy28 == m.REFERENCE.fuel_fy28


def test_card_and_monte_carlo() -> None:
    c, _ = m.card(m.REFERENCE, ["fuel_market_usd=-20%"], weeks=12, sims=300)
    base, _ = m.card(m.REFERENCE, weeks=12, sims=300)
    assert c.fair_value > base.fair_value
    assert c.fv_p10 < c.fv_p50 < c.fv_p90
    assert 0 <= c.p_up <= 1 and -1 <= c.score <= 1
    again, _ = m.card(m.REFERENCE, ["fuel_market_usd=-20%"], weeks=12, sims=300)
    assert again.fv_p50 == c.fv_p50  # seeded


def test_scenarios_parse() -> None:
    w = m.fy27_remaining(m.REFERENCE.date)
    for _, shocks in m.SCENARIOS.values():
        m.scenario(m.REFERENCE, shocks, w)


def test_catalysts_window() -> None:
    got = m.catalysts(pd.Timestamp("2026-11-01", tz="UTC"), pd.Timestamp("2026-11-30", tz="UTC"))
    assert [d for d, _ in got] == ["2026-11-05", "2026-11-18"]
