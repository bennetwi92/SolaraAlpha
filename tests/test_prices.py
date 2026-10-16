from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha.data.prices import COLUMNS, fetch_daily, parse_chart

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_chart_jet2.json"


def test_parse_chart() -> None:
    df = parse_chart(json.loads(FIXTURE.read_text()))
    assert isinstance(df.index, pd.DatetimeIndex)
    assert list(df.columns) == COLUMNS
    assert str(df.index.tz) == "UTC"
    assert df.index[0] == pd.Timestamp("2025-10-01", tz="UTC")
    assert (df.index == df.index.normalize()).all()
    assert df.attrs == {"ticker": "JET2.L", "currency": "GBp"}
    assert df["close"].notna().all()


def test_parse_chart_error() -> None:
    with pytest.raises(ValueError, match="Not Found"):
        parse_chart({"chart": {"result": None, "error": {"code": "Not Found"}}})


@pytest.mark.network
def test_fetch_daily_live() -> None:
    df = fetch_daily("JET2.L", "2024-01-01", "2024-02-01")
    assert 15 < len(df) < 25
