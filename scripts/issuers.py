"""Watchlist stocks missing from config/issuers.toml, with their LEI candidates from the FCA NSM.

The pipeline finds a stock's filings by LEI, read from config/issuers.toml (the LSE lookup it
used before answers 403 from cloud runners). After scripts/universe.py adds stocks, run this:
it searches the NSM by each new stock's watchlist name and prints the filers found, best first.
--write appends the filer whose name matches (case, punctuation and legal form aside; a stock
with no such filer is left for you to add by hand); check the names before committing.

uv run python scripts/issuers.py            # print the candidates
uv run python scripts/issuers.py --write    # append the best one per stock
"""

from __future__ import annotations

import argparse
import re

from solaraalpha.data.announcements import ISSUERS, known_issuers, match_filer, search_issuers
from solaraalpha.pipeline import WATCHLIST


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--write", action="store_true", help="append the best candidates to the file")
    args = p.parse_args()

    names = dict(re.findall(r'^\s*"([^"]+)",\s*#\s*(.+?)\s*$', WATCHLIST.read_text(), flags=re.M))
    missing = [t for t in names if t not in known_issuers()]
    if not missing:
        print("every watchlist stock is in config/issuers.toml")
        return
    lines = []
    for t in missing:
        found = search_issuers(names[t])
        print(f"{t} ({names[t]}):", "; ".join(f"{c} {lei} ({n})" for c, lei, n in found[:4]) or "-")
        if match := match_filer(names[t], found):
            company, lei = match
            lines.append(f'"{t}" = {{ lei = "{lei}", name = "{company}" }}  # {names[t]}\n')
    if args.write and lines:
        with ISSUERS.open("a") as f:
            f.writelines(lines)
        print(f"appended {len(lines)} to {ISSUERS}")


if __name__ == "__main__":
    main()
