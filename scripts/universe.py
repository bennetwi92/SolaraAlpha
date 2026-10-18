"""Quarterly watchlist refresh: FTSE AIM 100 + FTSE AIM UK 50 constituents from the LSE website.

Prints config/watchlist.toml (or writes it with --write), then to stderr: tickers added /
removed vs the current watchlist, and new tickers without a config/benchmarks.toml entry with
their LSE ICB sector (add `sector` / `industry` by hand; see that file's header). Run by hand
after each quarterly FTSE review (Mar / Jun / Sep / Dec), never in CI (docs/data-sources.md).

uv run python scripts/universe.py            # print, review the diff
uv run python scripts/universe.py --write    # overwrite config/watchlist.toml
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

from solaraalpha.data.http import DEFAULT_TIMEOUT, get_json, session

ROOT = Path(__file__).resolve().parents[1]
WATCHLIST = ROOT / "config" / "watchlist.toml"
BENCHMARKS = ROOT / "config" / "benchmarks.toml"
# The universe is the union (the owner keeps AIM UK 50 names that are outside the AIM 100).
INDEXES = ("ftse-aim-100-index", "ftse-aim-uk-50-index")
REFRESH_URL = "https://api.londonstockexchange.com/api/v1/components/refresh"
INSTRUMENT_URL = "https://api.londonstockexchange.com/api/gw/lse/instruments/alldata/{tidm}"
# the constituents table on londonstockexchange.com/indices/<index>/constituents/table
TABLE_TAB = "1602cf04-c25b-4ea0-a9d6-64040d217877"
TABLE_COMPONENT = "block_content%3Aafe540a2-2a0c-46af-8497-407dc4c7fd71"
PAGE_SIZE = 20  # the API caps a page at 20 rows

# FTSE Russell ICB sectors (6-digit codes) -> names as used in config/benchmarks.toml
ICB_SECTORS = {
    "101010": "Software & Computer Services",
    "101020": "Technology Hardware & Equipment",
    "151010": "Telecommunications Equipment",
    "151020": "Telecommunications Service Providers",
    "201010": "Health Care Providers",
    "201020": "Medical Equipment & Services",
    "201030": "Pharmaceuticals & Biotechnology",
    "301010": "Banks",
    "302010": "Finance & Credit Services",
    "302020": "Investment Banking & Brokerage Services",
    "302030": "Mortgage Real Estate Investment Trusts",
    "302040": "Closed End Investments",
    "302050": "Open End & Miscellaneous Investment Vehicles",
    "303010": "Life Insurance",
    "303020": "Non-life Insurance",
    "351010": "Real Estate Investment & Services",
    "351020": "Real Estate Investment Trusts",
    "401010": "Automobiles & Parts",
    "402010": "Consumer Services",
    "402020": "Household Goods & Home Construction",
    "402030": "Leisure Goods",
    "402040": "Personal Goods",
    "403010": "Media",
    "404010": "Retailers",
    "405010": "Travel & Leisure",
    "451010": "Beverages",
    "451020": "Food Producers",
    "451030": "Tobacco",
    "452010": "Personal Care, Drug & Grocery Stores",
    "501010": "Construction & Materials",
    "502010": "Aerospace & Defense",
    "502020": "Electronic & Electrical Equipment",
    "502030": "General Industrials",
    "502040": "Industrial Engineering",
    "502050": "Industrial Support Services",
    "502060": "Industrial Transportation",
    "551010": "Industrial Materials",
    "551020": "Industrial Metals & Mining",
    "551030": "Precious Metals & Mining",
    "552010": "Chemicals",
    "601010": "Oil, Gas & Coal",
    "601020": "Alternative Energy",
    "651010": "Electricity",
    "651020": "Gas, Water & Multi-utilities",
    "651030": "Waste & Disposal Services",
}

HEADER = """\
# Stocks the weekly pipeline runs (scripts/scan.py, .github/workflows/scan.yml).
#
# Universe: FTSE AIM 100 plus FTSE AIM UK 50 constituents as listed on the London Stock Exchange
# website on {day} (londonstockexchange.com/indices/<index>/constituents/table; refresh with
# scripts/universe.py). Hand-run snapshot, not point-in-time: refresh after each quarterly FTSE
# review (Mar / Jun / Sep / Dec). Yahoo ticker = TIDM + ".L". `snapshot` is the copy date:
# scan.py warns once a review month has passed since it (solaraalpha.pipeline.stale_watchlist).
snapshot = {day}
tickers = [
"""


def parse_rows(payload: list[dict]) -> tuple[list[dict], int]:
    """Constituent rows and the index's total count from one components/refresh response."""
    for item in payload[0]["content"]:
        value = item.get("value")
        if item.get("name") == "ftseindexconstituents" and isinstance(value, dict):
            return list(value["content"]), int(value["totalElements"])
    raise ValueError("no constituents table in the response")


def fetch_constituents(index: str = INDEXES[0]) -> list[dict]:
    """Every constituent row (tidm, issuername, marketcapitalization, ...) of a FTSE index."""
    rows: list[dict] = []
    page, total = 0, 1
    while len(rows) < total:
        body = {
            "path": "ftse-constituents",
            "parameters": f"indexname%3D{index}%26tab%3Dtable%26tabId%3D{TABLE_TAB}",
            "components": [
                {"componentId": TABLE_COMPONENT, "parameters": f"page={page}&size={PAGE_SIZE}"}
            ],
        }
        resp = session().post(REFRESH_URL, json=body, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        got, total = parse_rows(resp.json())
        if not got:
            break
        rows += got
        page += 1
    if len(rows) != total:
        raise ValueError(f"got {len(rows)} of {total} constituents")
    return rows


def current_names(text: str) -> dict[str, str]:
    """Ticker -> name comment from an existing watchlist file (keeps hand-edited names)."""
    return dict(re.findall(r'^\s*"([^"]+)",\s*#\s*(.+?)\s*$', text, flags=re.M))


def yahoo(tidm: str) -> str:
    """Yahoo ticker for a TIDM: ``"JET2"`` -> ``"JET2.L"``, ``"YU."`` -> ``"YU.L"``."""
    return f"{tidm.rstrip('.')}.L"


def tidy(issuer: str) -> str:
    """A readable default name from the LSE issuer name (hand-edit it in the file after)."""
    name, suffix = issuer.strip(), r",?\s+(PLC|LIMITED|LTD|INC\.?|CORPORATION|\(THE\))$"
    while re.search(suffix, name):
        name = re.sub(suffix, "", name)
    return name.title()


def render(rows: list[dict], day: date, names: dict[str, str]) -> str:
    """The watchlist TOML: one `"TIDM.L",  # name` line per constituent, sorted by ticker."""
    entries = {yahoo(r["tidm"]): r["issuername"] for r in rows}
    lines = [
        "    " + f'"{t}",'.ljust(13) + f"# {names.get(t) or tidy(entries[t])}"
        for t in sorted(entries)
    ]
    return HEADER.format(day=day.isoformat()) + "\n".join(lines) + "\n]\n"


def sector(tidm: str) -> str:
    """The LSE's ICB sector for a TIDM, named, with its code."""
    data = get_json(INSTRUMENT_URL.format(tidm=tidm))
    code = str(data.get("sectorcode")) if isinstance(data, dict) else "?"
    return f"{ICB_SECTORS.get(code, '?')} ({code})"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--write", action="store_true", help="overwrite config/watchlist.toml")
    args = p.parse_args(argv)

    old_text = WATCHLIST.read_text()
    old = set(tomllib.loads(old_text)["tickers"])
    rows = list({r["tidm"]: r for i in INDEXES for r in fetch_constituents(i)}.values())
    text = render(rows, date.today(), current_names(old_text))
    tidms = {yahoo(r["tidm"]): r["tidm"] for r in rows}
    new = set(tidms)
    if args.write:
        WATCHLIST.write_text(text)
    else:
        print(text, end="")
    with BENCHMARKS.open("rb") as f:
        known = set(tomllib.load(f).get("stocks", {}))
    print(f"{len(new)} constituents; added {sorted(new - old)}", file=sys.stderr)
    print(f"removed {sorted(old - new)}", file=sys.stderr)
    for t in sorted(new - known):
        print(f"no benchmarks entry: {t}  LSE sector {sector(tidms[t])}", file=sys.stderr)


if __name__ == "__main__":
    main()
