"""Daily check of open positions, the watch list and pending breakout entries.

uv run python scripts/daily.py [--save] [--report FILE] [--run-id-file FILE] [--db PATH]

Fetches daily prices (check / clean / trim), evaluates the triggers in portfolio.triggers
(stop closed through, stop touched, target hit, gap, coil trigger broken, waiting entry
broken), finds new filings and headlines since the last daily run, and prints the report:
Owner actions, Agent review needed, Watch breaks, Headlines, Errors. --save records a `daily`
run with a price snapshot per ticker and the triggers table (the monitor agent reads it).
Exits 1 only if no stock's prices could be fetched, after reporting; other errors are saved
(`error` triggers) for the desk errors. On Friday every open position and pending entry also
gets a weekly review (`daily_review`, ADR 0039). See docs/decisions/0017.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solaraalpha import desk_chat
from solaraalpha.portfolio import book
from solaraalpha.portfolio.daily import render_daily, run_daily


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--save", action="store_true", help="record the run, prices and triggers")
    p.add_argument("--report", type=Path, help="append the markdown report to this file")
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    p.add_argument("--run-id-file", type=Path, help="write the saved run id to this file")
    args = p.parse_args(argv)
    if args.save:
        desk_chat.pull(args.db)
    conn = book.connect(args.db)
    try:
        result = run_daily(conn, save=args.save, args=vars(args))
    finally:
        conn.close()
    report = render_daily(result)
    print(report)
    if args.run_id_file and result.run_id is not None:
        args.run_id_file.write_text(f"{result.run_id}\n")
    if args.report:
        with args.report.open("a") as f:
            f.write(report + "\n")
    # Fail only when no stock could be checked (as scan.py does, #189): a per-ticker error is
    # reported and saved for the desk errors, and failing the run on it would skip the monitor
    # for every position (ADR 0024).
    unpriced = {t for t, e in result.errors if e.startswith("prices:")}
    if result.tickers and unpriced >= set(result.tickers):
        sys.exit(1)


if __name__ == "__main__":
    main()
