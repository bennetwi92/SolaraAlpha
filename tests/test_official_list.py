from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from solaraalpha.data import official_list
from solaraalpha.data.trading212 import parse_instruments

FIXTURE = Path(__file__).parent / "fixtures" / "fca_official_list.csv"


def test_parse_official_list() -> None:
    df = official_list.parse_official_list(FIXTURE.read_text())
    assert len(df) == 6
    assert list(df.columns) == ["company", "country", "category", "venue", "isins"]
    informa = df[df["company"] == "Informa plc"].iloc[0]
    assert informa["isins"] == ["GB00BMJ6DW54"]
    assert informa["category"] == "Equity shares (commercial companies)"
    assert "GB00B012TP20" in official_list.listed_isins(df)


def test_isins_split_from_one_field() -> None:
    assert official_list.ISIN.findall("GB00BMJ6DW54, GB00B012TP20") == [
        "GB00BMJ6DW54",
        "GB00B012TP20",
    ]


def _inst(ticker: str, short: str, isin: str, name: str, cur: str = "GBX", typ: str = "STOCK"):
    return {
        "ticker": ticker,
        "shortName": short,
        "isin": isin,
        "currencyCode": cur,
        "type": typ,
        "name": name,
    }


def test_aim_lines_drop_listed_non_lse_and_noise() -> None:
    inst = parse_instruments(
        [
            _inst("ABDPl_EQ", "ABDP", "GB00B9GQVG73", "AB Dynamics"),  # AIM: kept
            _inst("INFl_EQ", "INF", "GB00BMJ6DW54", "Informa"),  # Official List
            _inst("ANPl_EQ", "ANP", "GB00B3NWT178", "Anpario", cur="GBP"),  # not pence
            _inst("XNASl_EQ", "XNAS", "IE00BMFKG444", "Xtrackers NASDAQ", typ="ETF"),
            _inst("STN_US_EQ", "STN", "CA85472N1096", "Stantec"),  # not London
            _inst("GACAl_EQ", "GACA", "GB0003692737", "General Accident 8.875% PRF"),
            _inst("SLFRl_EQ", "SLFR", "GG00BN56JF17", "SLF Realisation Fund"),
            _inst("TWOl_EQ", "TWO", "GB0000000011", "Two Lines A"),  # same TIDM twice
            _inst("TWOl1_EQ", "TWO", "GB0000000029", "Two Lines B"),
            _inst("AAZl_EQ", "AAZ", "GB00B0C18177", "Anglo Asian Mining"),  # AIM: kept
            _inst("SNRl_EQ", "SNR", "GB0007958233", "Senior"),  # NOT_AIM: off the live list
        ]
    )
    out = official_list.aim_lines(inst, {"GB00BMJ6DW54"})
    assert list(out["ticker"]) == ["AAZ.L", "ABDP.L"]
    assert list(out["t212"]) == ["AAZl_EQ", "ABDPl_EQ"]


def test_fetch_follows_the_redirect_page(monkeypatch: pytest.MonkeyPatch) -> None:
    redirect = (
        "<script>window.location.replace('https://files.example/OfficialListLive.csv');</script>"
    )
    pages = {
        official_list.OFFICIAL_LIST_URL: redirect,
        "https://files.example/OfficialListLive.csv": FIXTURE.read_text(),
    }
    calls: list[str] = []

    class Session:
        def get(self, url: str, timeout: float) -> SimpleNamespace:
            calls.append(url)
            return SimpleNamespace(text=pages[url], raise_for_status=lambda: None)

    monkeypatch.setattr(official_list, "session", Session)
    df = official_list.fetch_official_list()
    assert len(df) == 6
    assert calls == [official_list.OFFICIAL_LIST_URL, "https://files.example/OfficialListLive.csv"]
