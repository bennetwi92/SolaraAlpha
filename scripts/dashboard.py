"""Render the static HTML dashboard over the results DB.

uv run python scripts/dashboard.py [--out data/processed/dashboard.html] [--run N] [--db PATH]

One self-contained file (inline CSS, base64 PNG charts, no JS): holdings, portfolio (equity,
cash, heat vs limit, sector exposure), equity curve and drawdown from the stored snapshots,
this week's pack (the latest saved scan run, or --run), the watch list, closed trades and the
label scorecard. No network. The weekly and daily workflows upload it as `dashboard-<run>`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from solaraalpha.config import settings
from solaraalpha.dashboard import render_dashboard
from solaraalpha.portfolio import book


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--out", type=Path, default=None, help="default data/processed/dashboard.html")
    p.add_argument(
        "--run", type=int, default=None, help="scan run for 'This week' (default latest)"
    )
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    conn = book.connect(args.db)
    try:
        page = render_dashboard(conn, args.run)
    finally:
        conn.close()
    out = args.out or settings.processed_dir / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(f"dashboard written to {out} ({len(page.encode()) / 1024:.0f} kB)")


if __name__ == "__main__":
    main()
