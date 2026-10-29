"""The AIM research universe (issue #290): every AIM company Trading212 offers, from free sources.

The LSE's own lists answer 403 from the cloud (bot protection), so the universe is built without
them: the London share lines Trading212 offers in pence, minus every ISIN on the FCA Official List
(AIM shares are not on it), minus preference lines and funds (by name). Each line is then checked
on Yahoo (a bar in the last 10 days, else excluded as stale or delisted) and its ISIN looked up on
GLEIF for the LEI the NSM files under (else the NSM filer whose name matches: GLEIF does not link
many non-UK issuers' ISINs). Writes config/research_universe.toml (with --write).

This is a research universe, not the live watchlist: today's companies only (the leavers ledger,
#291, bounds the survivorship bias), and only those Trading212 offers (what we could trade). The
£10k-a-day research floor is applied point-in-time in each study, not here. Needs the Trading212
keys (read-only instruments call). Run by hand, never in CI.

uv run python scripts/research_universe.py            # print the summary
uv run python scripts/research_universe.py --write    # write config/research_universe.toml
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from solaraalpha.data.announcements import GLEIF_URL, fetch_announcements, lei_by_name
from solaraalpha.data.http import get_json
from solaraalpha.data.official_list import aim_lines, fetch_official_list, listed_isins
from solaraalpha.data.prices import fetch_daily
from solaraalpha.data.trading212 import fetch_instruments

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "research_universe.toml"
STALE_DAYS = 10  # no Yahoo bar within this many days: stale or delisted
HEADER = """\
# The AIM research universe (issue #290; owner's decision on #288, 2026-10-28): every AIM company
# Trading212 offers in pence, built by scripts/research_universe.py from free sources: Trading212's
# instruments (London share lines in GBX) minus every ISIN on the FCA Official List (AIM shares are
# not on it), minus preference lines and funds, minus the hand-checked non-AIM lines
# (`official_list.NOT_AIM`), minus lines with no Yahoo bar in the last {stale} days. `lei` from
# GLEIF by ISIN when the NSM has filings under it, else the NSM filer of the same name ("" = none
# found). No liquidity cut here: studies apply the £10k research floor point-in-time. The weekly
# scan runs it too (ADR 0038); today's companies only
# (survivor snapshot: see the leavers ledger, #291). Refresh by hand:
# `uv run python scripts/research_universe.py --write`.
snapshot = {day}

[stocks]
"""


def check_prices(ticker: str, day: date) -> tuple[str, float]:
    """``("", adv60)`` for a line with a recent Yahoo bar, else ``(reason, 0)``."""
    try:
        bars = fetch_daily(ticker, start=(day - timedelta(days=120)).isoformat())
    except Exception as e:
        return f"no Yahoo prices ({str(e)[:40]})", 0.0
    if bars.empty:
        return "no Yahoo prices", 0.0
    last = bars.index.max().date()
    if (day - last).days > STALE_DAYS:
        return f"stale: last Yahoo bar {last}", 0.0
    value = (bars["close"] * bars["volume"] / 100).tail(60)  # pence -> GBP
    return "", float(value.median())


def lei_for(isin: str, name: str, day: date) -> str:
    """The LEI GLEIF holds for an ISIN when the NSM has filings under it in the last two years,
    else the NSM filer named ``name`` (GLEIF can hold a different entity's LEI: hVIVO, Invinity),
    else GLEIF's, or "" when neither."""
    try:
        data = get_json(GLEIF_URL, **{"filter[isin]": isin})
        assert isinstance(data, dict)
        gleif = str(data["data"][0]["attributes"]["lei"]) if data["data"] else ""
        since = (day - timedelta(days=730)).isoformat()
        if gleif and not fetch_announcements(gleif, start=since).empty:
            return gleif
        found = lei_by_name(name)
    except Exception:
        return ""
    return found[1] if found else gleif


def toml_str(s: str) -> str:
    return '"' + re.sub(r'(["\\])', r"\\\1", s) + '"'


def render(kept: pd.DataFrame, day: date) -> str:
    lines = [
        f"{toml_str(r.ticker)} = {{ name = {toml_str(r.name)}, isin = {toml_str(r.isin)}, "
        f"lei = {toml_str(r.lei)}, t212 = {toml_str(r.t212)} }}"
        for r in kept.itertuples()
    ]
    return HEADER.format(stale=STALE_DAYS, day=day.isoformat()) + "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--write", action="store_true", help="write config/research_universe.toml")
    args = p.parse_args(argv)

    day = date.today()
    lines = aim_lines(fetch_instruments(), listed_isins(fetch_official_list()))
    print(f"{len(lines)} AIM lines on Trading212 (not on the Official List)", file=sys.stderr)
    reasons, adv = [], []
    for t in lines["ticker"]:
        why, v = check_prices(t, day)
        reasons.append(why)
        adv.append(v)
    lines["why"], lines["adv60"] = reasons, adv
    dropped = lines[lines["why"] != ""]
    kept = lines[lines["why"] == ""].copy()
    kept["lei"] = [lei_for(i, n, day) for i, n in zip(kept["isin"], kept["name"], strict=True)]

    for r in dropped.itertuples():
        print(f"excluded {r.ticker} ({r.name}): {r.why}", file=sys.stderr)
    floor = (kept["adv60"] >= 10_000).sum()
    print(
        f"kept {len(kept)}; excluded {len(dropped)}; LEI found {(kept['lei'] != '').sum()}; "
        f"60-day median traded value >= £10k today: {floor}",
        file=sys.stderr,
    )
    for r in kept[kept["lei"] == ""].itertuples():
        print(f"no LEI: {r.ticker} ({r.name}, {r.isin})", file=sys.stderr)
    text = render(kept, day)
    if args.write:
        OUT.write_text(text)
        print(f"wrote {OUT.relative_to(ROOT)}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
