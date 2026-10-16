"""Build the monitor agent's pack for the positions a daily run flagged for review.

uv run python scripts/monitor_pack.py --run DAILY_RUN_ID [--position ID] [--db PATH] [--no-text]

For each open position with an agent trigger (gap, filing) in that daily run (or the one given
with --position), writes data/processed/monitor/<position_id>/<run_id>/pack.json: state,
orders and plan; the entry conditions (then); fresh technical / levels / relative-strength
cards (now) and a then -> now diff; the thesis conditions with machine ones evaluated now; the
triggers with the filing's full text; the latest research verdict. Then `/monitor`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solaraalpha.monitoring import build_pack, positions_to_review, write_pack
from solaraalpha.portfolio import book


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--run", type=int, required=True, help="daily run id (daily.py --save)")
    p.add_argument(
        "--position", type=int, default=None, help="one position instead of the triggered ones"
    )
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    p.add_argument("--no-text", action="store_true", help="skip fetching filing texts")
    args = p.parse_args(argv)
    conn = book.connect(args.db)
    try:
        ids = [args.position] if args.position is not None else positions_to_review(conn, args.run)
        if not ids:
            print(f"daily run {args.run}: no position needs the monitor")
        for pid in ids:
            try:
                pack = build_pack(conn, pid, args.run, text=not args.no_text)
            except ValueError as e:
                print(f"position {pid}: {e}")
                sys.exit(2)
            out = write_pack(pack)
            hits = [t["condition"] for t in pack["thesis"] if t["hit"]]
            print(
                f"position {pid} {pack['ticker']}: {len(pack['triggers'])} trigger(s); thesis hits: {', '.join(hits) or 'none'};"
                f" now errors: {', '.join(pack['now_errors']) or 'none'}; written to {out}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
