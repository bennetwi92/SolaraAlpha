"""Notify the owner only when something needs them (ADR 0021).

uv run python scripts/notify.py --mode daily --run N [--only actions,revisions,errors] [--channel stdout|github-issue] [--db PATH]
uv run python scripts/notify.py --mode weekly --run N [--summary-url URL] [--channel ...] [--db PATH]
uv run python scripts/notify.py --mode errors --run N --part pipeline|agents [--channel github-errors]
uv run python scripts/notify.py --mode job-failed --workflow NAME --conclusion C --url URL [--channel ...]
uv run python scripts/notify.py --mode execute|execute-errors --since ISO [--channel ...]

daily: owner-action triggers of the daily run, revision proposals since it started, ERRORs;
weekly: the weekly pack's headline sections plus the job-summary link. Writes the markdown to
the channel (default stdout) and exits 3 when there is nothing to send, so a workflow step can
skip posting. The github-issue channel comments on the open issue labelled `inbox` with `gh`.
errors: the run's failures for the `desk-errors` issue (ADR 0032; channel github-errors);
job-failed: one line for a workflow that ended in failure (no DB read).
execute: what an execution turn sent, filled, cancelled or refused since --since (inbox);
execute-errors: its stops not at the broker, rejections and errors (desk-errors, ADR 0031).
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
    p.add_argument(
        "--mode",
        choices=("daily", "weekly", "errors", "job-failed", "execute", "execute-errors"),
        required=True,
    )
    p.add_argument("--since", default=None, help="execute: the turn's start (ISO)")
    p.add_argument("--run", type=int, default=None, help="the daily or scan run id")
    p.add_argument("--part", choices=("pipeline", "agents", "all"), default="all", help="errors")
    p.add_argument("--workflow", default="", help="job-failed: the workflow's name")
    p.add_argument("--conclusion", default="failure", help="job-failed: how it ended")
    p.add_argument("--url", default="", help="job-failed: the run's link")
    p.add_argument(
        "--only", default="actions,revisions,errors", help="daily: item types to include"
    )
    p.add_argument("--summary-url", default=None, help="weekly: link to the job summary")
    p.add_argument("--channel", choices=tuple(notify.CHANNELS), default="stdout")
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    if args.mode == "job-failed":
        notify.send(
            notify.job_failed_message(args.workflow, args.conclusion, args.url), args.channel
        )
        return
    if args.mode.startswith("execute"):
        if not args.since:
            p.error(f"--mode {args.mode} needs --since")
    elif args.run is None:
        p.error(f"--mode {args.mode} needs --run")
    conn = book.connect(args.db)
    try:
        if args.mode == "execute":
            text = notify.execute_message(conn, args.since)
        elif args.mode == "execute-errors":
            text = notify.execute_errors(conn, args.since)
        elif args.mode == "daily":
            only = tuple(x.strip() for x in args.only.split(",") if x.strip())
            text = notify.daily_message(conn, args.run, only)
        elif args.mode == "errors":
            text = notify.desk_errors(conn, args.run, args.part)
        else:
            text = notify.weekly_message(conn, args.run, args.summary_url)
    finally:
        conn.close()
    if text is None:
        what = f"since {args.since}" if args.since else f"run {args.run}"
        print(f"nothing to send for {args.mode} {what}", file=sys.stderr)
        sys.exit(NOTHING)
    notify.send(text, args.channel)


if __name__ == "__main__":
    main()
