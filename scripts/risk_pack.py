"""The risk manager's pack for a daily run (docs/decisions/0036).

uv run python scripts/risk_pack.py --run DAILY_RUN_ID [--out FILE] [--db PATH]

Prints (and with --out writes) the book's risk card from that run in plain markdown: VaR and CVaR
(1 day and the headline horizon; historical, EWMA-filtered and with the pending entries filled)
against the limit, heat, stress, drawdown, beta, volatility, correlation, the 1-day VaR backtest,
the flags, where the day's P&L and the VaR change came from, each position; then the trend, the
changes applied today, the market regime, scheduled events on held stocks and the risk manager's
previous report. Then `/risk`. Exits 3 when the run has no risk card (no book).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solaraalpha.portfolio import book
from solaraalpha.portfolio.riskreport import build_pack


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--run", type=int, required=True, help="daily run id (daily.py --save)")
    p.add_argument("--out", type=Path, default=None, help="also write the pack here")
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    conn = book.connect(args.db)
    try:
        pack = build_pack(conn, args.run)
    except ValueError as e:
        print(e)
        sys.exit(3)
    finally:
        conn.close()
    print(pack)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(pack)


if __name__ == "__main__":
    main()
