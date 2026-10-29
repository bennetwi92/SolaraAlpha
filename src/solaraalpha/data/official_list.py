"""The FCA Official List, and the AIM research universe built from it (issue #290).

The Official List is every security the FCA has admitted to listing (Main Market shares, funds,
debt, ...). AIM shares are **not** on it: AIM is an LSE market, not a listing. So the London
share lines Trading212 offers, minus every ISIN on the Official List, are AIM companies (plus a
little noise: preference shares and specialist funds, removed by name; delisted lines Trading212
still carries, removed when Yahoo has no recent prices).

The list is a public CSV: the download link on ``marketsecurities.fca.org.uk/officiallist`` answers
with a page that redirects (in script) to the file, so :func:`fetch_official_list` follows it.
"""

from __future__ import annotations

import io
import re

import pandas as pd

from solaraalpha.data.http import DEFAULT_TIMEOUT, session
from solaraalpha.data.trading212 import LSE

OFFICIAL_LIST_URL = "https://marketsecurities.fca.org.uk/downloadOfficiallist/live"
ISIN = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")
REDIRECT = re.compile(r"window\.location\.replace\('([^']+)'\)")
# share lines on the LSE that are not AIM companies: preference / debt-like lines and funds
NOT_COMPANY = re.compile(r"%|\bprf\b|\bpref\b|preference|\bfund\b|realisation", re.IGNORECASE)
# London lines that pass the Official List test but are not AIM companies (hand check of every
# line's filing text, 2026-10-29, #290): Main Market shares suspended for a takeover or
# cancellation drop off the live list, Specialist Fund Segment vehicles were never on it, and
# suspended standard-list shells. ISIN -> what it is.
NOT_AIM = {
    "JE00BKVHVW88": "BAY Capital: Main Market (standard), acquisition company",
    "GG00BMWWJM28": "Castelnau: Specialist Fund Segment",
    "JE00BF0D1M25": "EJF Investments: Specialist Fund Segment",
    "JE00BYZT0R68": "Golden Rock Global: Main Market (standard)",
    "GB0004170089": "Headlam: Main Market, listing suspended",
    "GB00BRXCFB77": "Hellenic Dynamics: Main Market (standard)",
    "GG00BPNZ7G17": "JZ Capital Partners: Main Market investment company",
    "GB00BYQCS703": "Kanabo: Main Market (standard)",
    "AU0000368748": "New Frontier Minerals (Castillo Copper): Main Market (standard) and ASX",
    "GB00BSB7BS06": "Pinewood Technologies: Main Market (ESCC), in an offer",
    "GB00BF2K5076": "Pennpetro Energy: Main Market (standard)",
    "GB00BMWKKM32": "Pristine Capital: Main Market (standard)",
    "AU0000078487": "PYX Resources: Main Market (standard)",
    "GB0007958233": "Senior: Main Market, listing being cancelled",
    "GB00BYWYZ460": "Sure Ventures: Specialist Fund Segment",
    "GB00BFMDJC60": "Supply@Me Capital: Main Market (standard)",
    "GG00B1RMC548": "Tetragon: Specialist Fund Segment",
    "BMG917071026": "UIL: Main Market investment company",
    "JE00BLB56J12": "Apertura Energy (Red Capital): Main Market acquisition company",
    "GB00BMXTZ463": "Adalan Ventures: Main Market (standard), suspended",
}


def fetch_official_list() -> pd.DataFrame:
    """Every security on the FCA Official List today (the live list), as :func:`parse_official_list`."""
    resp = session().get(OFFICIAL_LIST_URL, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    if m := REDIRECT.search(resp.text):
        resp = session().get(m.group(1), timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
    return parse_official_list(resp.text)


def parse_official_list(text: str) -> pd.DataFrame:
    """The Official List CSV: one row per listed security (``company``, ``country``,
    ``category``, ``venue``, ``isins``: the ISINs in the row's ISIN(S) field)."""
    raw = pd.read_csv(io.StringIO(text), index_col=False, dtype=str).fillna("")
    return pd.DataFrame(
        {
            "company": raw["Company Name"],
            "country": raw["Country of Inc."],
            "category": raw["Listing Category"],
            "venue": raw["Trading Venue"],
            "isins": raw["ISIN(S)"].map(ISIN.findall),
        }
    )


def listed_isins(official: pd.DataFrame) -> set[str]:
    """Every ISIN on the Official List."""
    return {i for isins in official["isins"] for i in isins}


def aim_lines(instruments: pd.DataFrame, listed: set[str]) -> pd.DataFrame:
    """The AIM share lines among Trading212's ``instruments`` (as ``trading212.parse_instruments``):
    LSE stocks quoted in pence whose ISIN is not on the Official List nor in :data:`NOT_AIM` and
    whose name is not a preference line or a fund. Adds ``ticker`` (Yahoo: TIDM + ``.L``); a TIDM on two lines is
    left out (ambiguous), as in ``trading212.lse_lines``. One row per ticker, by ticker."""
    lines = instruments[
        instruments["t212"].map(lambda t: bool(LSE.match(str(t))))
        & (instruments["type"] == "STOCK")
        & (instruments["currency"] == "GBX")
        & ~instruments["isin"].isin(listed)
        & ~instruments["isin"].isin(NOT_AIM)
        & ~instruments["name"].map(lambda n: bool(NOT_COMPANY.search(str(n))))
    ]
    counts = lines["short"].value_counts()
    lines = lines[lines["short"].map(counts) == 1].copy()
    lines.insert(0, "ticker", lines["short"] + ".L")
    return lines.sort_values("ticker", ignore_index=True)
