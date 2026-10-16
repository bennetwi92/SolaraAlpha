"""Build the post-mortem agent's pack for a closed position.

uv run python scripts/postmortem_pack.py POSITION_ID [--db PATH] [--out DIR]
uv run python scripts/postmortem_pack.py --since ISO [--db PATH]     # every position closed since

Writes data/processed/postmortem/<position_id>/pack.json: the close summary, the plan and
set-up type, the frozen entry conditions, every weekly review card and plan revision over the
trade, the daily triggers, the research and monitor verdicts with their evidence, the fills
and events, and weekly bars from the entry week - 8 to the exit week + 4 (stored prices as
seen at the close; later weeks labelled hindsight). Then `/postmortem POSITION_ID`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solaraalpha.data.prices import fetch_daily
from solaraalpha.portfolio import book
from solaraalpha.postmortem import build_pack, closed_since, write_pack


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("position_id", type=int, nargs="?", help="a closed position")
    p.add_argument("--since", help="instead: every position closed after this ISO time")
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    p.add_argument("--out", type=Path, default=None, help="directory for pack.json (one position)")
    args = p.parse_args(argv)
    if (args.position_id is None) == (args.since is None):
        p.error("give POSITION_ID or --since")
    conn = book.connect(args.db)
    try:
        ids = [args.position_id] if args.position_id is not None else closed_since(conn, args.since)
        if not ids:
            print(f"no position closed since {args.since}")
        for pid in ids:
            try:
                pack = build_pack(conn, pid, fetch=fetch_daily)
            except ValueError as e:
                print(f"position {pid}: {e}")
                sys.exit(2)
            out = write_pack(pack, args.out if args.position_id is not None else None)
            s = pack["close_summary"]
            hind = sum(1 for b in pack["bars"] if b["hindsight"])
            print(
                f"position {pid} {pack['ticker']}: {s.get('exit_reason')} {float(s.get('r_multiple') or 0):+.2f}R,"
                f" set-up {pack['setup']['type']}; {len(pack['reviews'])} review(s), {len(pack['revisions'])} revision(s),"
                f" {len(pack['triggers'])} trigger(s), {len(pack['bars'])} weekly bars ({hind} hindsight); written to {out}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
