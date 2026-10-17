"""Notify the owner only when something needs them (ADR 0021).

uv run python scripts/notify.py --mode daily --run N [--only actions,revisions,errors] [--channel stdout|github-issue] [--db PATH]
uv run python scripts/notify.py --mode weekly --run N [--summary-url URL] [--channel ...] [--db PATH]

daily: owner-action triggers of the daily run, revision proposals since it started, ERRORs;
weekly: the weekly pack's headline sections plus the job-summary link. Writes the markdown to
the channel (default stdout) and exits 3 when there is nothing to send, so a workflow step can
skip posting. The github-issue channel comments on the open issue labelled `inbox` with `gh`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solaraalpha import notify
from solaraalpha.portfolio import book

NOTHING = 3


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--mode", choices=("daily", "weekly"), required=True)
    p.add_argument("--run", type=int, required=True, help="the daily or scan run id")
    p.add_argument(
        "--only", default="actions,revisions,errors", help="daily: item types to include"
    )
    p.add_argument("--summary-url", default=None, help="weekly: link to the job summary")
    p.add_argument("--channel", choices=tuple(notify.CHANNELS), default="stdout")
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    conn = book.connect(args.db)
    try:
        if args.mode == "daily":
            only = tuple(x.strip() for x in args.only.split(",") if x.strip())
            text = notify.daily_message(conn, args.run, only)
        else:
            text = notify.weekly_message(conn, args.run, args.summary_url)
    finally:
        conn.close()
    if text is None:
        print(f"nothing to send for {args.mode} run {args.run}", file=sys.stderr)
        sys.exit(NOTHING)
    notify.send(text, args.channel)


if __name__ == "__main__":
    main()
