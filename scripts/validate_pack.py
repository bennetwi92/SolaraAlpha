"""Build the validator's pack for one ticker (or every screen-ACT ticker) of a saved run.

uv run python scripts/validate_pack.py JET2.L --run RUN_ID [--out DIR] [--db PATH]
uv run python scripts/validate_pack.py --run RUN_ID --all

Writes pack.json to data/processed/validate/<ticker>/<run_id>/ from the DB only: the cards,
steps and screen label, the price snapshot as seen at the run (issues, span kept, weekly bars
with 10w / 40w MAs, daily bars, largest moves), the fundamentals known at the card date, and the
recent filings' headlines (the one live fetch; "unavailable" on failure), plus chart.png (the
weekly picture with the levels and the plan drawn on). Never includes
events, research or other agents' notes: the validator is independent. Then `/validate`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solaraalpha import store
from solaraalpha.validation import build_pack, write_chart, write_pack


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("ticker", nargs="?", help="Yahoo ticker; omit with --all")
    p.add_argument("--run", type=int, required=True, help="scan run id")
    p.add_argument("--all", action="store_true", help="every screen-ACT ticker of the run")
    p.add_argument("--out", type=Path, default=None, help="output directory (one ticker only)")
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    if bool(args.ticker) == args.all:
        sys.exit("give one TICKER, or --all")
    conn = store.connect(args.db)
    try:
        tickers = (
            store.shortlist(args.run, "screen", ("ACT",), path=args.db)
            if args.all
            else [args.ticker]
        )
        if not tickers:
            print(f"run {args.run}: no screen-ACT tickers")
        for ticker in tickers:
            pack = build_pack(conn, ticker, args.run)
            out = write_pack(pack, args.out if not args.all else None)
            chart = write_chart(conn, pack, out)
            n_issues = (
                len(pack["prices"].get("issues", [])) if isinstance(pack["prices"], dict) else 0
            )
            filings = pack["filings"]
            n_fil = len(filings["items"]) if isinstance(filings, dict) else filings
            lab = pack["screen_label"]["label"] if pack["screen_label"] else "-"
            print(
                f"{ticker}: screen {lab}; {len(pack['cards'])} cards; {n_issues} price issues; filings: {n_fil};"
                f" chart: {chart.name if chart else 'none (no prices)'}; written to {out}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
