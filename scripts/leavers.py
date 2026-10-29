"""The AIM leavers ledger (issue #291): every company that left AIM since 2022, from the NSM.

uv run python scripts/leavers.py              # print the summary
uv run python scripts/leavers.py --write      # write config/aim_leavers.csv

The London Stock Exchange's own AIM notices (filer "AIM" on the NSM) name every cancellation
with the company's LEI: "Cancellation - X", and "Readmission - X" for a transfer to the Main
Market or the Specialist Fund Segment (``data.leavers``). How each ended (takeover with the cash
offer price, administration, suspension, cancellation by choice, other) comes from the company's
own filings in the year before. Research only: it bounds the survivorship bias of the research
universe (research 0012). Run by hand, never in CI; reads ~700 NSM documents (about 10 minutes).
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd

from solaraalpha.data.leavers import (
    CONTINUING,
    Notice,
    classify,
    fetch_aim_notices,
    filings_for,
    leaver_notices,
    takeover_price,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "aim_leavers.csv"
START = "2022-01-01"
URL = "https://data.fca.org.uk/artefacts/NSM/RNS/{}.html"


def row(n: Notice) -> dict:
    f = filings_for(n)
    if n.reason:
        how, src = "main_market", n.disclosure_id
    else:
        how, src = classify(n, f)
    price, psrc = takeover_price(f, n) if how == "takeover" else (None, "")
    before = f[f["published"] <= n.published + pd.Timedelta(days=1)]
    urls = dict(zip(f["id"], f["url"], strict=True))
    return {
        "lei": n.lei,
        "name": n.company,
        "isins": " ".join(n.isins),
        "last_filing": before["published"].max().date().isoformat() if len(before) else "",
        "cancelled": n.published.date().isoformat(),
        "how": how,
        "offer_pence": price,
        "source": urls.get(psrc or src, URL.format(src) if src else ""),
        "notice": URL.format(n.disclosure_id),
        "aim_rule": n.rule,
        "filings": len(before),
    }


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--write", action="store_true", help="write config/aim_leavers.csv")
    args = p.parse_args(argv)

    notices = leaver_notices(fetch_aim_notices(START, date.today().isoformat()))
    with ThreadPoolExecutor(2) as ex:
        rows = list(ex.map(row, notices))
    df = pd.DataFrame(rows)
    back = df[df["how"] == CONTINUING]
    df = df[df["how"] != CONTINUING].sort_values(["cancelled", "name"], ignore_index=True)
    for r in back.itertuples():
        print(f"not a leaver (back on AIM): {r.name} {r.cancelled}", file=sys.stderr)
    year = df["cancelled"].str[:4]
    print(pd.crosstab(year, df["how"], margins=True).to_string(), file=sys.stderr)
    t = df[df["how"] == "takeover"]
    print(
        f"takeovers with a cash offer price: {t['offer_pence'].notna().sum()} of {len(t)}",
        file=sys.stderr,
    )
    if args.write:
        df.to_csv(OUT, index=False)
        print(f"wrote {OUT.relative_to(ROOT)} ({len(df)} leavers)", file=sys.stderr)
    else:
        print(df.to_csv(index=False))


if __name__ == "__main__":
    main()
