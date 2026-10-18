from __future__ import annotations

import json
import sys
import tomllib
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import universe

FIXTURE = Path(__file__).parent / "fixtures" / "lse_constituents.json"


def test_parse_rows() -> None:
    rows, total = universe.parse_rows(json.loads(FIXTURE.read_text()))
    assert total == 3 and [r["tidm"] for r in rows] == ["GGP", "JET2", "YU."]
    with pytest.raises(ValueError, match="no constituents"):
        universe.parse_rows([{"content": [{"name": "other", "value": {}}]}])


def test_render_keeps_hand_names_and_loads() -> None:
    rows, _ = universe.parse_rows(json.loads(FIXTURE.read_text()))
    text = universe.render(rows, date(2026, 10, 18), {"JET2.L": "Jet2"})
    data = tomllib.loads(text)
    assert data["tickers"] == ["GGP.L", "JET2.L", "YU.L"]  # sorted; "YU." -> "YU.L"
    assert str(data["snapshot"]) == "2026-10-18"
    assert '"JET2.L",    # Jet2\n' in text  # existing name kept
    assert "# Greatland Resources\n" in text and "# Yu Group\n" in text  # suffixes dropped
    assert universe.current_names(text)["YU.L"] == "Yu Group"


def test_tidy() -> None:
    assert universe.tidy("PROPERTY FRANCHISE GROUP PLC (THE)") == "Property Franchise Group"
    assert universe.tidy("BOKU, INC.") == "Boku"


def test_checked_in_watchlist_is_the_aim_100() -> None:
    text = universe.WATCHLIST.read_text()
    tickers = tomllib.loads(text)["tickers"]
    assert len(tickers) == 100 and tickers == sorted(tickers) and "JET2.L" in tickers
    assert "FTSE AIM 100" in text
    with universe.BENCHMARKS.open("rb") as f:
        stocks = tomllib.load(f)["stocks"]
    assert all(stocks[t].get("sector") and stocks[t].get("industry") for t in tickers)
    assert all(s["sector"] in universe.ICB_SECTORS.values() for s in stocks.values())
