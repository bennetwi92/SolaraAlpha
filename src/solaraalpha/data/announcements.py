"""Regulatory announcements for one UK-listed issuer from the FCA's National Storage Mechanism.

The NSM is the UK's official archive of regulated information: every RNS (results, trading
updates, AGM statements, director dealings, major holdings, ...) is filed there, with its
publication time. Free, no key. The search endpoint is the one the NSM website itself calls
(``api.data.fca.org.uk``): it is not documented, so the format can change. NSM terms allow use
"for any lawful purpose" — see ``docs/data-sources.md``.

The issuer is identified by its **LEI**, which survives renames. The AIM issuer register is gone
(ADR 0043: no AIM data), so :func:`resolve_issuer` resolves nothing until SEC EDGAR replaces this
module for the Nasdaq 100 (ADR 0041).

``published`` is the publication time (tz-aware UTC). That is when the market could first act
on the announcement; see :func:`solaraalpha.analysis.events.reactions` for the session it lands in.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser

import pandas as pd

from solaraalpha.data.http import DEFAULT_TIMEOUT, session
from solaraalpha.data.prices import LOOKBACK_YEARS
from solaraalpha.log import get_logger

log = get_logger(__name__)

NSM_SEARCH_URL = "https://api.data.fca.org.uk/search?index=nsm-search"
# Seconds between NSM searches. The NSM sits behind AWS API Gateway, whose WAF refuses (403) a
# caller over a request rate in a rolling window, and keeps refusing while the caller keeps
# asking: an unpaced scan got refused within minutes (2026-10-29, docs/decisions/0038), and at
# one a second the 2026-11-01 Sunday scan was refused after ~3 minutes, then 310 of 557 stocks
# while it retried into the block. ~17 a minute (85 per 5 minutes) stays under it.
NSM_GAP = 3.5
NSM_GAP_MAX = 15.0
_gap = NSM_GAP
_last_search = 0.0
# A refusal (403 / 429) is waited out with no requests for longer than the WAF's 5-minute
# window, and the gap doubles (up to NSM_GAP_MAX) for the rest of the process; retried while
# the process's NSM_RETRY_BUDGET of seconds lasts, then raised at once (#303).
NSM_RETRY_BUDGET = 1200.0
NSM_RETRY_WAIT = 330.0
_retry_left = NSM_RETRY_BUDGET
# Each issuer's filings (the default LOOKBACK_YEARS window) fetched in this process, by LEI: the
# weekly scan's catalyst pass and its events stage share one NSM search per stock (#303).
_FILINGS: dict[str, pd.DataFrame] = {}
NSM_DOC_BASE = "https://data.fca.org.uk/artefacts/"
COLUMNS = [
    "published",
    "type_code",
    "type",
    "category",
    "headline",
    "company",
    "source",
    "id",
    "url",
]
PAGE = 1000  # the NSM's search index caps from + size at 10,000


@dataclass(frozen=True)
class Issuer:
    ticker: str
    isin: str
    lei: str
    name: str  # legal name (as filed), or the research universe's short name
    market: str  # "AIM" for a research-universe stock, else ""


def resolve_issuer(ticker: str) -> Issuer:
    """The issuer behind a ticker. The AIM issuer register (``config/issuers.toml``, the research
    universe) was removed with the AIM strategy (ADR 0040, 0043), and the Nasdaq 100 files with
    the SEC, not the NSM, so no ticker resolves until SEC EDGAR replaces this module (ADR 0041)."""
    raise ValueError(f"no filings source for {ticker}: SEC EDGAR is not built yet (ADR 0041)")


def fetch_announcements(lei: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Every current NSM filing for an issuer's ``lei`` published between ``start`` and ``end``.

    ``start`` defaults to ``LOOKBACK_YEARS`` before now (enough history to see each yearly
    reporting cycle several times), ``end`` to now. Superseded versions are excluded.
    """
    stop = pd.Timestamp(end, tz=UTC) if end else pd.Timestamp(datetime.now(UTC))
    begin = pd.Timestamp(start, tz=UTC) if start else stop - pd.DateOffset(years=LOOKBACK_YEARS)
    frames = []
    offset = 0
    while True:
        body = {
            "from": offset,
            "size": PAGE,
            "sort": "publication_date",
            "sortorder": "desc",
            "criteriaObj": {
                "criteria": [
                    {"name": "lei", "value": lei},
                    {"name": "latest_flag", "value": "Y"},
                ],
                "dateCriteria": [
                    {
                        "name": "publication_date",
                        "value": {"from": _iso(begin), "to": _iso(stop)},
                    }
                ],
            },
        }
        page = parse_nsm(_search(body))
        frames.append(page)
        offset += PAGE
        if len(page) < PAGE:
            break
    df = pd.concat(frames, ignore_index=True).drop_duplicates("id")
    df = df.sort_values("published", ignore_index=True)
    df.attrs["lei"] = lei
    return df


def filings(lei: str) -> pd.DataFrame:
    """:func:`fetch_announcements` for ``lei`` (default window), fetched once per process."""
    if lei not in _FILINGS:
        _FILINGS[lei] = fetch_announcements(lei)
    return _FILINGS[lei]


def cached(lei: str) -> pd.DataFrame | None:
    """``lei``'s filings if this process already fetched them, else None (never fetches)."""
    return _FILINGS.get(lei)


def remember(lei: str, df: pd.DataFrame) -> None:
    """Keep ``df`` (a current, default-window fetch) as ``lei``'s filings for :func:`filings`."""
    _FILINGS[lei] = df


def _search(body: dict) -> dict:
    """One NSM search, at most one every :data:`NSM_GAP` seconds; a 403 / 429 refusal is waited
    out (:data:`NSM_RETRY_WAIT`, then a slower pace) while the process's
    :data:`NSM_RETRY_BUDGET` lasts."""
    global _last_search, _retry_left, _gap
    while True:
        time.sleep(max(0.0, _last_search + _gap - time.monotonic()))
        try:
            resp = session().post(NSM_SEARCH_URL, json=body, timeout=DEFAULT_TIMEOUT)
        finally:
            _last_search = time.monotonic()
        if resp.status_code in (403, 429) and _retry_left >= NSM_RETRY_WAIT:
            _gap = min(NSM_GAP_MAX, _gap * 2)
            log.warning(
                "NSM refused the search (%s): retrying in %.0fs, then one every %.1fs",
                resp.status_code,
                NSM_RETRY_WAIT,
                _gap,
            )
            _retry_left -= NSM_RETRY_WAIT
            time.sleep(NSM_RETRY_WAIT)
            continue
        resp.raise_for_status()
        return dict(resp.json())


def _iso(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")  # the NSM rejects "+00:00" offsets


def parse_nsm(payload: dict) -> pd.DataFrame:
    """Turn an NSM search JSON payload into a frame with :data:`COLUMNS`, oldest first."""
    rows = [
        (
            s["publication_date"],
            s.get("type_code") or "",
            s.get("type") or "",
            s.get("category_group") or "",
            (s.get("headline") or "").strip(),
            s.get("company") or "",
            s.get("source") or "",
            s["disclosure_id"],
            NSM_DOC_BASE + s["download_link"],
        )
        for hit in payload["hits"]["hits"]
        for s in [hit["_source"]]
    ]
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["published"] = pd.to_datetime(df["published"], utc=True, format="ISO8601")
    return df.sort_values("published", ignore_index=True)


def fetch_text(url: str) -> str:
    """Plain text of one announcement (its NSM ``url``), tables flattened to ``a | b`` rows."""
    resp = session().get(url, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return html_to_text(resp.text)


_BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
_SKIP = {"script", "style", "head"}
_CELL = {"td", "th"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0
        self.cell = 0  # inside a table cell, blocks don't break the line

    def _block(self) -> None:
        self.parts.append(" " if self.cell else "\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self.skip += 1
        elif tag in _CELL:
            self.cell += 1
            self.parts.append(" | ")
        elif tag in _BLOCK:
            self._block()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in _CELL:
            self.cell = max(0, self.cell - 1)
        elif tag in _BLOCK:
            self._block()

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data.replace("\n", " "))  # source line breaks are not breaks


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line.strip(" |") for line in lines if line.strip(" |"))
