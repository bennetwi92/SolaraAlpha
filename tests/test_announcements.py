from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha.data import announcements
from solaraalpha.data.announcements import (
    COLUMNS,
    NSM_DOC_BASE,
    fetch_announcements,
    filers,
    html_to_text,
    known_issuers,
    match_filer,
    parse_nsm,
    resolve_issuer,
)
from solaraalpha.pipeline import watchlist

FIXTURES = Path(__file__).parent / "fixtures"
JET2_LEI = "I72RQ3PNLDPW8R7JIJ56"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_nsm() -> None:
    df = parse_nsm(_load("nsm_jet2.json"))
    assert list(df.columns) == COLUMNS
    assert str(df["published"].dt.tz) == "UTC"
    assert df["published"].is_monotonic_increasing
    pre = df[df["type_code"] == "PRE"].iloc[0]
    assert pre["headline"] == "Final Results"
    assert pre["category"] == "Financial Results"
    assert pre["published"] == pd.Timestamp("2026-07-08 06:00:04", tz="UTC")
    assert pre["url"].startswith(NSM_DOC_BASE + "NSM/RNS/")
    assert df["id"].is_unique


def test_resolve_issuer_rejects_non_lse() -> None:
    with pytest.raises(ValueError, match="LSE"):
        resolve_issuer("AAPL")


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network call for a ticker with an LEI on file")

    monkeypatch.setattr(announcements, "search_issuers", blocked)


def test_resolve_issuer_reads_config_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    iss = resolve_issuer("JET2.L")
    assert (iss.lei, iss.name) == (JET2_LEI, "JET2 PLC")


def test_resolve_issuer_reads_the_research_universe(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_network(monkeypatch)
    iss = resolve_issuer("AXL.L")  # not on the watchlist; GLEIF has no LEI for its ISIN
    assert (iss.lei, iss.isin, iss.market) == ("9845000FDF0856QD9031", "CA04274P1053", "AIM")


def test_resolve_issuer_by_nsm_name(monkeypatch: pytest.MonkeyPatch) -> None:
    universe = {"XYZ.L": {"name": "Xyz Mining", "isin": "CA0000000000", "lei": "", "t212": ""}}
    monkeypatch.setattr(announcements, "research_issuers", lambda: universe)
    found = [("XYZ MINING CORP.", "LEI-XYZ", 3)]
    monkeypatch.setattr(announcements, "search_issuers", lambda name: found)
    announcements.lei_by_name.cache_clear()
    iss = resolve_issuer("XYZ.L")
    assert (iss.lei, iss.name, iss.isin) == ("LEI-XYZ", "XYZ MINING CORP.", "CA0000000000")


def test_resolve_issuer_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(announcements, "research_issuers", lambda: {})
    with pytest.raises(ValueError, match=r"no LEI for NOPE\.L"):
        resolve_issuer("NOPE.L")


def test_match_filer_needs_the_same_name() -> None:
    found = [("QUILTER PLC", "LEI-Q", 263), ("UIL LIMITED", "LEI-U", 243)]
    assert match_filer("UIL", found) == ("UIL LIMITED", "LEI-U")  # not the top substring hit
    assert match_filer("Fever-Tree Drinks", [("FEVERTREE DRINKS PLC", "L", 1)]) is not None
    assert match_filer("Yu Group", [("YÜ GROUP PLC", "L", 1)]) is not None
    assert match_filer("Gooch & Housego", [("GOOCH & HOUSEGO PLC", "L", 1)]) is not None
    assert match_filer("Arrow", [("ARROW EXPLORATION CORP.", "L", 1)]) is None


def test_issuers_cover_the_watchlist() -> None:
    issuers = known_issuers()
    missing = [t for t in watchlist() if t not in issuers]
    assert not missing, f"add to config/issuers.toml (scripts/issuers.py --write): {missing}"
    leis = [v["lei"] for v in issuers.values()]
    assert len(set(leis)) == len(leis)
    assert all(len(lei) == 20 and v["name"] for lei, v in zip(leis, issuers.values(), strict=True))


def test_filers() -> None:
    rows = filers(_load("nsm_jet2.json"))
    assert rows[0][:2] == ("JET2 PLC", JET2_LEI)
    assert rows[0][2] == len(_load("nsm_jet2.json")["hits"]["hits"])


def test_html_to_text() -> None:
    text = html_to_text((FIXTURES / "rns_sample.html").read_text())
    lines = text.splitlines()
    assert lines[0] == "RNS Number : 3911L"
    assert "Revenue | £7,482.1m | £7,173.5m | 4%" in lines
    assert "Results in line with expectations." in lines
    assert "margin" not in text and "var x" not in text and "National Storage" not in text


@pytest.mark.network
def test_fetch_announcements_live() -> None:
    df = fetch_announcements(JET2_LEI, start="2025-07-01", end="2025-07-31")
    assert "Final Results" in set(df["headline"])
    assert resolve_issuer("JET2.L").lei == JET2_LEI
