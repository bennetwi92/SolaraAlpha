"""Build the execution agent's pack (ADR 0031): DB and Yahoo only, no broker call.

uv run python scripts/execution_pack.py [--out FILE] [--db PATH]

Writes data/processed/execution/<UTC time>/pack.json (or --out) and prints its path: the London
session and the exit deadline; what the agent has to decide (new orders waiting for it, orders
Trading212 rejected, stops the code sent since its last turn); every approved order not yet sent
with why it waits, the research plan, the last close / price and the liquidity card; the orders
at the broker; what the code did and what filled since the agent's last turn; the guards. Then
`/execute`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from solaraalpha import store
from solaraalpha.config import settings
from solaraalpha.portfolio import book, execution


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    now = pd.Timestamp.now(tz="UTC")
    conn = book.connect(args.db)
    try:
        liq = store.latest("liquidity", path=args.db)
        data = execution.pack(conn, now, liquidity=liq)
    finally:
        conn.close()
    out = (
        args.out or settings.processed_dir / "execution" / now.strftime("%Y%m%dT%H%M") / "pack.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, default=str))
    print(out)


if __name__ == "__main__":
    main()
