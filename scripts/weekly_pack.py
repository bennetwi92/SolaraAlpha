"""Build the weekly pack for a saved scan run: new plans, watch list, open positions, money.

uv run python scripts/weekly_pack.py --run RUN_ID [--out FILE] [--report FILE] [--db PATH]

Writes data/processed/packs/<run_id>.md by default; --report appends to a file (GitHub
Actions: "$GITHUB_STEP_SUMMARY"). Run after `plan.py --run N` so the drafted positions appear;
the fit is re-run here (as `plan.py --dry-run`) so the pack does not depend on stored
rejections.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from solaraalpha.config import settings
from solaraalpha.portfolio import book
from solaraalpha.portfolio.rules import load_rules
from solaraalpha.portfolio.weekly_pack import build_pack, render_pack


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--run", type=int, required=True, help="scan run id")
    p.add_argument("--out", type=Path, default=None, help="default data/processed/packs/<run>.md")
    p.add_argument("--report", type=Path, default=None, help="append the pack to this file")
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    conn = book.connect(args.db)
    try:
        text = render_pack(build_pack(conn, args.run, load_rules()))
    finally:
        conn.close()
    out = args.out or settings.processed_dir / "packs" / f"{args.run}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    if args.report:
        with args.report.open("a") as f:
            f.write(text + "\n")
    print(text)
    print(f"written to {out}")


if __name__ == "__main__":
    main()
