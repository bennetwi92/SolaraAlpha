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
    html_to_text,
    parse_nsm,
    resolve_issuer,
)

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


def test_resolve_issuer_has_no_source_until_edgar() -> None:
    with pytest.raises(ValueError, match="EDGAR"):
        resolve_issuer("AAPL")


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


class _Resp:
    def __init__(self, code: int) -> None:
        self.status_code = code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}", response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return {"hits": {"hits": []}}


def _fake_post(monkeypatch: pytest.MonkeyPatch, codes: list[int]) -> list[int]:
    sent: list[int] = []

    class _Session:
        def post(self, *args: object, **kwargs: object) -> _Resp:
            code = codes.pop(0)
            sent.append(code)
            return _Resp(code)

    monkeypatch.setattr(announcements, "session", lambda: _Session())
    monkeypatch.setattr(announcements.time, "sleep", lambda s: None)
    return sent


def test_search_waits_out_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _fake_post(monkeypatch, [403, 429, 200])
    monkeypatch.setattr(announcements, "_retry_left", 1000.0)
    monkeypatch.setattr(announcements, "_gap", 3.5)
    assert announcements._search({}) == {"hits": {"hits": []}}
    assert sent == [403, 429, 200]
    assert announcements._retry_left == 1000.0 - 2 * announcements.NSM_RETRY_WAIT
    assert announcements._gap == 14.0  # the pace slows after each refusal


def test_search_raises_once_the_retry_budget_is_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    sent = _fake_post(monkeypatch, [403, 403])
    monkeypatch.setattr(announcements, "_retry_left", 400.0)  # one wait, not two
    monkeypatch.setattr(announcements, "_gap", 3.5)
    with pytest.raises(requests.HTTPError):
        announcements._search({})
    assert sent == [403, 403]


def test_filings_fetch_once_per_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fetch(lei: str) -> pd.DataFrame:
        calls.append(lei)
        return pd.DataFrame(columns=COLUMNS)

    monkeypatch.setattr(announcements, "_FILINGS", {})
    monkeypatch.setattr(announcements, "fetch_announcements", fetch)
    announcements.filings("A")
    announcements.filings("A")
    seeded = pd.DataFrame(columns=COLUMNS)
    announcements.remember("B", seeded)
    assert announcements.filings("B") is seeded
    assert calls == ["A"]
