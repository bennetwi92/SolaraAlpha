from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha.data.fundamentals import COLUMNS, FIELDS, fetch_fundamentals, parse_timeseries

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_timeseries_jet2.json"


def test_parse_timeseries() -> None:
    df = parse_timeseries(json.loads(FIXTURE.read_text()))
    assert list(df.columns) == COLUMNS
    assert set(df["field"]) <= set(FIELDS)
    assert str(df["date"].dt.tz) == "UTC"
    assert df["value"].notna().all()
    ni = df[df["field"] == "annualNetIncome"].set_index("date")["value"]
    assert ni[pd.Timestamp("2025-03-31", tz="UTC")] == pytest.approx(446.8e6)
    assert (df.loc[df["field"] == "annualNetIncome", "currency"] == "GBP").all()
    assert (df.loc[df["field"] == "trailingPeRatio", "currency"] == "").all()


def test_parse_timeseries_error() -> None:
    with pytest.raises(ValueError, match="Bad"):
        parse_timeseries({"timeseries": {"result": None, "error": {"code": "Bad Request"}}})


@pytest.mark.network
def test_fetch_fundamentals_live() -> None:
    df = fetch_fundamentals("JET2.L")
    assert {"annualNetIncome", "trailingDilutedEPS"} <= set(df["field"])
