"""Regulatory announcements for one UK-listed issuer from the FCA's National Storage Mechanism.

The NSM is the UK's official archive of regulated information: every RNS (results, trading
updates, AGM statements, director dealings, major holdings, ...) is filed there, with its
publication time. Free, no key. The search endpoint is the one the NSM website itself calls
(``api.data.fca.org.uk``): it is not documented, so the format can change. NSM terms allow use
"for any lawful purpose" — see ``docs/data-sources.md``.

The issuer is identified by its **LEI**, which survives renames (Jet2 plc filed as Dart Group
plc until 2020). :func:`resolve_issuer` maps an LSE ticker to its LEI from ``config/issuers.toml``
(looked up once per stock: an LEI never changes); a ticker not listed there goes TIDM → ISIN via
the London Stock Exchange instrument endpoint, then ISIN → LEI via GLEIF (the official LEI
registry). The LSE endpoint answers 403 from cloud runners, so the pipeline relies on the file.

``published`` is the publication time (tz-aware UTC). That is when the market could first act
on the announcement; see :func:`solaraalpha.analysis.events.reactions` for the session it lands in.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from html.parser import HTMLParser

import pandas as pd

from solaraalpha.config import REPO_ROOT
from solaraalpha.data.http import DEFAULT_TIMEOUT, get_json, session
from solaraalpha.data.prices import LOOKBACK_YEARS

NSM_SEARCH_URL = "https://api.data.fca.org.uk/search?index=nsm-search"
NSM_DOC_BASE = "https://data.fca.org.uk/artefacts/"
LSE_INSTRUMENT_URL = "https://api.londonstockexchange.com/api/gw/lse/instruments/alldata/{tidm}"
GLEIF_URL = "https://api.gleif.org/api/v1/lei-records"
ISSUERS = REPO_ROOT / "config" / "issuers.toml"
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
    name: str  # legal name (GLEIF)
    market: str  # e.g. "AIM", "MAINMARKET" (LSE); "" when read from config/issuers.toml


@cache
def known_issuers() -> dict[str, dict[str, str]]:
    """``config/issuers.toml``: ticker -> ``{"lei", "name"}``."""
    with ISSUERS.open("rb") as f:
        return tomllib.load(f)["issuers"]


def resolve_issuer(ticker: str) -> Issuer:
    """Identify the issuer behind an LSE Yahoo ticker (e.g. ``"JET2.L"``)."""
    if not ticker.endswith(".L"):
        raise ValueError(f"only LSE tickers (*.L) are supported, got {ticker!r}")
    if known := known_issuers().get(ticker):
        return Issuer(ticker, "", known["lei"], known["name"], "")
    tidm = ticker.removesuffix(".L")
    lse = get_json(LSE_INSTRUMENT_URL.format(tidm=tidm))
    assert isinstance(lse, dict)
    gleif = get_json(GLEIF_URL, **{"filter[isin]": lse["isin"]})
    assert isinstance(gleif, dict)
    return parse_issuer(ticker, lse, gleif)


def parse_issuer(ticker: str, lse: dict, gleif: dict) -> Issuer:
    if not gleif["data"]:
        raise ValueError(f"no LEI found for ISIN {lse['isin']}")
    rec = gleif["data"][0]["attributes"]
    return Issuer(
        ticker=ticker,
        isin=lse["isin"],
        lei=rec["lei"],
        name=rec["entity"]["legalName"]["name"],
        market=lse.get("market") or "",
    )


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
        resp = session().post(NSM_SEARCH_URL, json=body, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        page = parse_nsm(resp.json())
        frames.append(page)
        offset += PAGE
        if len(page) < PAGE:
            break
    df = pd.concat(frames, ignore_index=True).drop_duplicates("id")
    df = df.sort_values("published", ignore_index=True)
    df.attrs["lei"] = lei
    return df


def search_issuers(name: str, days: int = 365) -> list[tuple[str, str, int]]:
    """NSM filers whose company name matches ``name`` in the last ``days``: ``(company, lei,
    filings)``, most filings first (one page). For filling ``config/issuers.toml`` by hand."""
    stop = pd.Timestamp(datetime.now(UTC))
    body = {
        "from": 0,
        "size": PAGE,
        "sort": "publication_date",
        "sortorder": "desc",
        "criteriaObj": {
            "criteria": [
                {"name": "company_lei", "value": [name, "", "disclose_org", "related_org"]},
                {"name": "latest_flag", "value": "Y"},
            ],
            "dateCriteria": [
                {
                    "name": "publication_date",
                    "value": {"from": _iso(stop - pd.Timedelta(days=days)), "to": _iso(stop)},
                }
            ],
        },
    }
    resp = session().post(NSM_SEARCH_URL, json=body, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return filers(resp.json())


def filers(payload: dict) -> list[tuple[str, str, int]]:
    """``(company, lei, filings)`` per LEI in an NSM search payload, most filings first; the
    company name is the one on the latest filing. Filings without an LEI are left out."""
    seen: dict[str, list] = {}
    for hit in payload["hits"]["hits"]:
        s = hit["_source"]
        if lei := s.get("lei"):
            row = seen.setdefault(lei, [s.get("company") or "", s["publication_date"], 0])
            if s["publication_date"] > row[1]:
                row[:2] = [s.get("company") or "", s["publication_date"]]
            row[2] += 1
    rows = [(co, lei, n) for lei, (co, _, n) in seen.items()]
    return sorted(rows, key=lambda r: -r[2])


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
