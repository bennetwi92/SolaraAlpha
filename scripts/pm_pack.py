"""The portfolio manager's pack for a scan run: the week's research-ACT ideas and the whole book.

uv run python scripts/pm_pack.py --run N [--out DIR] [--db PATH]

Writes data/processed/pm/<run_id>/pack.json (DB only): candidates (research verdict, trade
levels, what each risk in the band buys after the caps), research WATCH names, open positions
(P&L, R, stop, next event, latest monitor / review view), pending entries, money (equity, cash,
heat, exposure, drawdown, breaker, free slots), regime, macro notes, correlations, the limits,
and what the rules alone would fund. The /pm skill reads it (docs/decisions/0027).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from solaraalpha import pm
from solaraalpha.portfolio import book


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--run", type=int, required=True, help="scan run id")
    p.add_argument("--out", type=Path, default=None, help="directory (default data/processed/pm/N)")
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    conn = book.connect(args.db)
    try:
        pack = pm.build_pack(conn, args.run)
    finally:
        conn.close()
    out = pm.write_pack(pack, args.out)
    m = pack["money"]
    print(
        f"run {args.run}: {len(pack['candidates'])} research-ACT candidates,"
        f" {len(pack['open_positions'])} open, {len(pack['pending_entries'])} pending;"
        f" equity £{m['equity']:,.0f}, deployable cash £{m['deployable_cash']:,.0f},"
        f" heat {m['heat']:.1%}, free slots {m['slots_free']}"
    )
    print(f"written to {out}")


if __name__ == "__main__":
    main()
