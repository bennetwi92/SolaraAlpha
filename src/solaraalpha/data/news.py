"""Press coverage headlines from the Google News RSS search feed.

Free, no key. Google publishes no terms specific to the RSS feed, so treat it as personal,
research use only — see ``docs/data-sources.md``. It is a **discovery** feed: headlines and
links only, ranked by Google, at most 100 items, with syndicated duplicates. Anything material
should be confirmed against a primary source (the company's own announcements) before use.

``published`` is the article time Google reports (tz-aware UTC).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pandas as pd

from solaraalpha.data.http import DEFAULT_TIMEOUT, session

RSS_URL = "https://news.google.com/rss/search"
# Google redirects non-browser user agents to a consent page.
_HEADERS = {"User-Agent": "Mozilla/5.0"}
COLUMNS = ["published", "source", "headline", "url"]


def fetch_news(query: str, days: int = 7) -> pd.DataFrame:
    """Headlines matching ``query`` (Google News syntax) from the last ``days`` days, UK edition."""
    resp = session().get(
        RSS_URL,
        params={"q": f"{query} when:{days}d", "hl": "en-GB", "gl": "GB", "ceid": "GB:en"},
        headers=_HEADERS,
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    df = parse_rss(resp.text)
    df.attrs["query"] = query
    return df


def parse_rss(xml: str) -> pd.DataFrame:
    """Turn a Google News RSS document into a frame with :data:`COLUMNS`, oldest first."""
    rows = []
    for item in ET.fromstring(xml).iterfind("./channel/item"):
        source = item.findtext("source") or ""
        title = item.findtext("title") or ""
        # Titles end with " - <source>"; drop it, the source has its own column.
        if source and title.endswith(f" - {source}"):
            title = title.removesuffix(f" - {source}")
        rows.append((item.findtext("pubDate"), source, title.strip(), item.findtext("link")))
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["published"] = pd.to_datetime(df["published"], utc=True, format="mixed")
    return df.sort_values("published", ignore_index=True)
