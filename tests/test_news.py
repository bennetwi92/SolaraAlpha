from __future__ import annotations

from pathlib import Path

import pytest

from solaraalpha.data.news import COLUMNS, fetch_news, parse_rss

FIXTURE = Path(__file__).parent / "fixtures" / "google_news_jet2.xml"


def test_parse_rss() -> None:
    df = parse_rss(FIXTURE.read_text())
    assert list(df.columns) == COLUMNS
    assert len(df) == 3
    assert str(df["published"].dt.tz) == "UTC"
    assert df["published"].is_monotonic_increasing
    assert not any(h.endswith(f" - {s}") for h, s in zip(df["headline"], df["source"], strict=True))
    assert df["url"].str.startswith("https://").all()


@pytest.mark.network
def test_fetch_news_live() -> None:
    assert not fetch_news('"Jet2"', days=30).empty
