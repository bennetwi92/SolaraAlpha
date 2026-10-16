from __future__ import annotations

import pandas as pd
import pytest
import requests

from solaraalpha.data import futures


def test_contract_ticker() -> None:
    assert futures.contract_ticker("HO", pd.Timestamp("2027-06-01"), "NYM") == "HOM27.NYM"
    assert futures.contract_ticker("BZ", pd.Timestamp("2026-12-01"), "NYM") == "BZZ26.NYM"


def test_fetch_curve_skips_missing_months(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(ticker: str, start: str | None = None) -> pd.DataFrame:
        if ticker == "HOF27.NYM":
            raise requests.HTTPError("404")
        idx = pd.DatetimeIndex(["2026-10-15", "2026-10-16"], tz="UTC")
        return pd.DataFrame({"close": [3.0, 4.0 if ticker == "HOZ26.NYM" else 3.5]}, index=idx)

    monkeypatch.setattr(futures, "fetch_daily", fake)
    curve = futures.fetch_curve(
        "HO", "NYM", pd.Timestamp("2026-12-01", tz="UTC"), 3, pd.Timestamp("2026-10-16", tz="UTC")
    )
    assert curve.to_dict() == {pd.Timestamp("2026-12-01"): 4.0, pd.Timestamp("2027-02-01"): 3.5}


@pytest.mark.network
def test_fetch_curve_live() -> None:
    now = pd.Timestamp.now(tz="UTC")
    curve = futures.fetch_curve("HO", "NYM", now + pd.offsets.MonthBegin(2), 3)
    assert len(curve) >= 2 and (curve > 0).all()
