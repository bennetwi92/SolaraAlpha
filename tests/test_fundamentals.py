from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha.analysis.fundamentals import (
    eps_ttm,
    fundamentals_card,
    fx_ticker,
    known,
    usable_from,
    with_publication,
    yoy,
)
from solaraalpha.data.fundamentals import parse_timeseries

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_timeseries_jet2.json"
SCORES = ("growth", "value", "quality")


def _fund() -> pd.DataFrame:
    return parse_timeseries(json.loads(FIXTURE.read_text()))


def _weekly(close: float = 1400.0, currency: str = "GBp", end: str = "2026-10-09") -> pd.DataFrame:
    idx = pd.date_range(end=end, periods=260, freq="W-FRI", tz="UTC", name="date")
    c = close * np.exp(0.1 * np.sin(np.arange(len(idx)) / 8))
    df = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "adj_close": c}, index=idx)
    df.attrs = {"ticker": "TEST", "currency": currency}
    return df


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _synthetic(eps: list[float], revenue: list[float]) -> pd.DataFrame:
    """Annual accounts for fiscal years ending 31 March, plus nothing else."""
    rows = []
    for i, (e, r) in enumerate(zip(eps, revenue, strict=True)):
        d = _ts(f"{2022 + i}-03-31")
        rows += [("annualDilutedEPS", d, e), ("annualTotalRevenue", d, r)]
    df = pd.DataFrame(rows, columns=["field", "date", "value"])
    df["currency"] = "GBP"
    return df


def test_publication_lags() -> None:
    fund = _fund()
    lag = (usable_from(fund) - fund["date"]).dt.days
    by = fund.assign(lag=lag).set_index(["field", "date"])["lag"]
    assert by["annualNetIncome", _ts("2025-03-31")] == 183  # 6 months
    assert by["trailingDilutedEPS", _ts("2025-03-31")] == 183  # fiscal year end: annual lag
    assert by["trailingDilutedEPS", _ts("2025-09-30")] == 91  # interim: 3 months
    assert by["trailingPeRatio", _ts("2025-08-20")] == 0  # market observation
    k = known(fund, "2025-09-29")
    assert _ts("2025-03-31") not in set(k.loc[k["field"] == "annualNetIncome", "date"])


def test_no_look_ahead() -> None:
    fund, weekly = _fund(), _weekly()
    as_of = "2025-06-30"
    card = fundamentals_card(fund, weekly, as_of)
    future = usable_from(fund) > _ts(as_of)
    assert future.any()
    altered = fund.assign(value=fund["value"].where(~future, fund["value"] * 10))
    later = weekly.assign(close=weekly["close"].where(weekly.index <= _ts(as_of), 1.0))
    later.attrs = weekly.attrs
    assert repr(fundamentals_card(altered, later, as_of)) == repr(card)  # NaN-safe equality
    assert card.last_period == _ts("2024-09-30")


def test_live_card_on_jet2_fixture() -> None:
    card = fundamentals_card(_fund(), _weekly())
    assert card.currency == "GBP"
    assert card.last_period == _ts("2026-03-31")
    assert card.next_period == _ts("2026-09-30")
    assert card.pe == pytest.approx(card.close / 100 / card.eps_ttm)  # pence -> pounds
    assert card.fwd_eps_growth < 0  # forward P/E above trailing: earnings expected to fall
    assert card.share_change < 0  # buybacks
    for s in SCORES:
        assert -1 <= getattr(card, s) <= 1


def test_currency_mismatch_skips_valuation() -> None:
    card = fundamentals_card(_fund(), _weekly(currency="USD"))
    assert np.isnan(card.pe) and np.isnan(card.fcf_yield)
    assert not np.isnan(card.growth) and not np.isnan(card.quality)


def _usd(fund: pd.DataFrame) -> pd.DataFrame:
    return fund.assign(currency=fund["currency"].where(fund["currency"] == "", "USD"))


def test_fx_converts_a_usd_reporter_priced_in_pence() -> None:
    fund, weekly = _usd(_fund()), _weekly()
    assert fx_ticker("USD", "GBp") == "GBPUSD=X" and fx_ticker("GBP", "GBp") is None
    fx = pd.Series(1.25, index=pd.date_range("2020-01-01", "2026-10-09", freq="D", tz="UTC"))
    usd = fundamentals_card(fund, weekly, fx=fx)
    gbp = fundamentals_card(_fund(), weekly)
    assert usd.pe == pytest.approx(gbp.pe * 1.25)  # price in USD = pence / 100 * 1.25
    assert usd.market_cap == pytest.approx(gbp.market_cap * 1.25)
    assert usd.ey_percentile == pytest.approx(gbp.ey_percentile)  # a constant rate
    assert np.isnan(fundamentals_card(fund, weekly).pe)  # no FX: skipped, not wrong


def test_fx_is_point_in_time() -> None:
    fund, weekly = _usd(_fund()), _weekly()
    idx = pd.date_range("2020-01-01", "2026-10-09", freq="D", tz="UTC")
    fx = pd.Series(np.where(idx <= _ts("2025-06-30"), 1.25, 9.0), index=idx)
    then = fundamentals_card(fund, weekly, "2025-06-30", fx)
    flat = fundamentals_card(fund, weekly, "2025-06-30", pd.Series(1.25, index=idx))
    assert repr(then) == repr(flat)  # a later rate changes nothing


def test_growth_n_counts_the_inputs() -> None:
    weekly = _weekly()
    thin = fundamentals_card(_synthetic([1.0, 1.2], [100, 100]), weekly)
    assert thin.growth_n == 2  # eps growth and revenue growth only
    assert 1 <= fundamentals_card(_fund(), weekly).growth_n <= 5


def test_zero_equity_and_negative_ebitda() -> None:
    fund = _fund()
    eq = fund["field"] == "annualStockholdersEquity"
    last = fund.loc[eq, "date"].max()
    fund.loc[eq & (fund["date"] == last), "value"] = 0.0
    eb = fund["field"] == "annualEBITDA"
    fund.loc[eb, "value"] = -1e6
    card = fundamentals_card(fund, _weekly())  # #213: used to raise ZeroDivisionError
    assert np.isnan(card.roe) and np.isnan(card.net_debt_ebitda)


def test_a_loss_over_negative_equity_is_no_return_on_equity() -> None:
    fund = _fund()
    last = fund.loc[fund["field"] == "annualStockholdersEquity", "date"].max()
    at = fund["date"] == last
    fund.loc[at & (fund["field"] == "annualStockholdersEquity"), "value"] = -10e6
    fund.loc[at & (fund["field"] == "annualNetIncome"), "value"] = -5e6
    card = fundamentals_card(fund, _weekly())
    assert np.isnan(card.roe)  # was +0.5: a top quality input for a distressed company


def test_a_loss_turning_to_a_profit_is_forward_growth() -> None:
    fund = _fund()
    last = fund.loc[fund["field"] == "trailingPeRatio", "date"].max()
    at = fund["date"] == last
    fund.loc[at & (fund["field"] == "trailingPeRatio"), "value"] = -20.0  # a loss now
    fund.loc[at & (fund["field"] == "trailingForwardPeRatio"), "value"] = 15.0  # a profit ahead
    card = fundamentals_card(fund, _weekly())
    assert card.fwd_eps_growth == pytest.approx(7 / 3)  # (15/20 + 1): was -7/3, the most bearish


def test_growth_direction() -> None:
    weekly = _weekly()
    up = fundamentals_card(_synthetic([1.0, 1.2, 1.5], [100, 120, 150]), weekly)
    down = fundamentals_card(_synthetic([1.5, 1.2, 0.9], [150, 120, 100]), weekly)
    assert up.eps_growth == pytest.approx(0.25)
    assert up.growth > 0.5 and down.growth < -0.5
    assert np.isnan(up.quality)  # no balance-sheet data -> no score, not zero


def test_yoy_and_eps_series() -> None:
    s = pd.Series([1.0, 2.0], index=pd.DatetimeIndex([_ts("2024-03-31"), _ts("2025-03-29")]))
    assert yoy(s) == pytest.approx(1.0)  # 52-week year tolerated
    s.index = pd.DatetimeIndex([_ts("2024-09-30"), _ts("2025-03-31")])
    assert np.isnan(yoy(s))
    eps = eps_ttm(_fund())
    assert eps.index.is_monotonic_increasing and eps.index.is_unique
    assert eps.index[0] == _ts("2023-03-31")  # annual fills in before trailing history


def test_errors() -> None:
    with pytest.raises(ValueError):
        fundamentals_card(_fund(), _weekly(), "2010-01-01")


def _filings(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame({"published": [_ts(d) for _, d in rows], "type_code": [c for c, _ in rows]})


def test_real_publication_dates() -> None:
    fund = _fund()  # fiscal years to 31 March, half-years to 30 September
    ann = _filings(
        [
            ("TST", "2025-04-20 07:00"),  # pre-close trading update: not the statements
            ("PRE", "2025-07-10 07:00"),  # FY25 results
            ("ACS", "2025-08-01 07:00"),  # annual report: later, the prelims count
            ("IR", "2025-11-20 07:00"),  # H1 FY26
        ]
    )
    pub = with_publication(fund, ann)
    use = usable_from(pub).set_axis(pd.MultiIndex.from_frame(fund[["field", "date"]]))
    assert use["annualNetIncome", _ts("2025-03-31")] == _ts("2025-07-10 07:00")
    assert use["trailingDilutedEPS", _ts("2025-09-30")] == _ts("2025-11-20 07:00")
    assert use["trailingPeRatio", _ts("2025-08-20")] == _ts("2025-08-20")  # market: own date
    # the trading update never makes FY25 known: not on 2025-05-01, but by the results
    assert _ts("2025-03-31") not in set(known(pub, "2025-05-01")["date"])
    assert _ts("2025-03-31") in set(known(pub, "2025-07-11")["date"])
    # ...and earlier than the statutory 6-month lag
    assert _ts("2025-03-31") not in set(known(fund, "2025-07-11")["date"])


def test_publication_falls_back_to_statutory_lag() -> None:
    fund = _fund()
    lag = usable_from(fund)
    assert usable_from(with_publication(fund, None)).equals(lag)
    only_tst = _filings([("TST", "2025-04-20"), ("NOR", "2025-06-01")])
    assert usable_from(with_publication(fund, only_tst)).equals(lag)
    fy25 = (fund["field"] == "annualNetIncome") & (fund["date"] == _ts("2025-03-31"))
    late = _filings([("FR", "2026-01-15")])  # a late filer: its real date, past the 6-month lag
    assert usable_from(with_publication(fund, late))[fy25].iloc[0] == _ts("2026-01-15")
    stray = _filings([("FR", "2026-05-01")])  # over a year on: another period's, not FY25's
    assert usable_from(with_publication(fund, stray))[fy25].iloc[0] == _ts("2025-09-30")
