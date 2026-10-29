from __future__ import annotations

import numpy as np
import pandas as pd

from solaraalpha.analysis.universe import (
    research_tickers,
    shares_at,
    size_group,
    weekly_adv,
    weekly_universe,
)


def _daily(volume: float = 10_000.0, close: float = 200.0) -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-02", "2023-06-30", tz="UTC", name="date")
    df = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": volume}, index=idx
    )
    df.attrs = {"currency": "GBp"}
    return df


def _fund(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "field": "annualOrdinarySharesNumber",
            "date": pd.to_datetime([d for d, _ in rows]).tz_localize("UTC"),
            "value": [v for _, v in rows],
            "currency": "",
        }
    )


FRIDAYS = pd.date_range("2023-01-06", "2023-06-30", freq="W-FRI", tz="UTC")


def test_weekly_adv_point_in_time_in_gbp() -> None:
    d = _daily()
    d.loc[d.index >= "2023-05-01", "volume"] = 100_000.0  # jump after May 1
    adv = weekly_adv(d, FRIDAYS)
    assert np.isnan(adv.iloc[0])  # 5 sessions < min_periods
    assert adv.loc["2023-04-28"] == 20_000.0  # 200p x 10k shares = £20k
    assert adv.loc["2023-05-05"] == 20_000.0  # 5 new sessions of 20: median unchanged
    assert adv.loc["2023-06-30"] == 200_000.0


def test_shares_at_uses_published_count_then_falls_back_to_earliest() -> None:
    # annual period ends; known ANNUAL_LAG later (no publication column)
    fund = _fund([("2021-12-31", 1e6), ("2022-12-31", 2e6)])
    sh = shares_at(fund, FRIDAYS)
    assert sh["pit"].iloc[-1] and sh["shares"].iloc[-1] == 2e6
    early = shares_at(fund, pd.DatetimeIndex([pd.Timestamp("2021-01-01", tz="UTC")]))
    assert not early["pit"].iloc[0] and early["shares"].iloc[0] == 1e6
    assert shares_at(None, FRIDAYS)["shares"].isna().all()


def test_size_group() -> None:
    assert size_group(True, 1e6) == "aim100"
    assert size_group(False, 49e6) == "micro"
    assert size_group(False, 51e6) == "rest"
    assert size_group(False, float("nan")) == "unknown"


def test_weekly_universe_floor_and_size() -> None:
    d = _daily(volume=4_000.0)  # £8k a day: under the £10k floor
    fund = _fund([("2021-12-31", 10e6)])
    u = weekly_universe(d, fund, FRIDAYS, control=False)
    assert not u["floor"].any()
    assert u["mcap"].iloc[-1] == 2.0 * 10e6  # £2 x 10m shares
    assert (u["size"] == "micro").all()
    early = weekly_universe(d, fund, pd.DatetimeIndex([pd.Timestamp("2022-06-03", tz="UTC")]), True)
    assert early.empty  # before the first bar


def test_research_tickers_reads_the_snapshot() -> None:
    t = research_tickers()
    assert len(t) > 500 and t == sorted(t) and all(x.endswith(".L") for x in t)


def test_patient_retries_403_then_raises_other_errors() -> None:
    import pytest
    import requests

    from solaraalpha.data.http import patient

    def err(code: int) -> requests.HTTPError:
        r = requests.Response()
        r.status_code = code
        return requests.HTTPError(response=r)

    calls = []

    def flaky() -> int:
        calls.append(1)
        if len(calls) < 3:
            raise err(403)
        return 7

    assert patient(flaky, wait=0) == 7 and len(calls) == 3
    with pytest.raises(requests.HTTPError):
        patient(lambda: (_ for _ in ()).throw(err(404)), wait=0)
    with pytest.raises(requests.HTTPError):
        patient(lambda: (_ for _ in ()).throw(err(403)), tries=2, wait=0)
