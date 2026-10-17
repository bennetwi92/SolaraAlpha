"""Where the validator disagreed with the screen, and which rules it blamed.

uv run python scripts/validator_stats.py [--since DATE] [--db PATH]

A disagreement is a run / ticker whose validate label is worse than its screen label. The
report counts the toolkits the validator marked "not warranted" and the checklist items it
flagged "concern" (from the verdict's checks), and the disagreement rate per run: a toolkit
that is often not warranted has a rule worth revisiting (story map S2.6, A11).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.store import query
from solaraalpha.validation import disagreement_stats, disagreements


def validate_cards(conn: store.Conn) -> dict[tuple[int, str], dict[str, Any]]:
    df = query(
        "SELECT run_id, ticker, field, value, text FROM results WHERE model = 'validate'"
        " ORDER BY run_id, rowid",
        conn,
    )
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for r in df.to_dict("records"):
        card = out.setdefault((int(r["run_id"]), str(r["ticker"])), {})
        card[str(r["field"])] = r["text"] if pd.notna(r["text"]) else r["value"]
    return out


def report(db: Path | None, since: str | None) -> str:
    conn = store.connect(db)
    try:
        labels = query(
            "SELECT l.run_id, l.ticker, l.stage, l.label, r.started_at FROM labels l"
            " JOIN runs r USING (run_id) WHERE l.stage IN ('screen', 'validate')"
            + (" AND r.started_at >= ?" if since else "")
            + " ORDER BY l.run_id, l.rowid",
            conn,
            [since] if since else [],
        )
        cards = validate_cards(conn)
    finally:
        conn.close()
    dis = disagreements(labels, cards)
    stats = disagreement_stats(dis, labels)
    n_val = int((labels["stage"] == "validate").sum()) if not labels.empty else 0
    lines = [
        f"{n_val} validate labels, {stats['total']} disagreements"
        + (f" since {since}" if since else "")
    ]
    if n_val == 0:
        return lines[0] + " (nothing validated yet)."
    lines += ["", "Disagreements:"]
    lines += [
        f"  run {r['run_id']} {r['ticker']}: screen {r['screen']} -> validate {r['validate']};"
        f" not warranted: {r['not_warranted'] or '-'}; concerns: {r['concerns'] or '-'}"
        for r in dis.to_dict("records")
    ] or ["  (none)"]
    lines += ["", "Toolkits marked not warranted:"]
    lines += [f"  {k:<20} {v}" for k, v in stats["by_toolkit"].items()]
    lines += ["", "Checklist items flagged concern:"]
    lines += [f"  {k:<24} {v}" for k, v in stats["by_check"].items()]
    lines += ["", "Rate per run:"]
    lines += [
        f"  run {r['run_id']}: {r['disagreements']} of {r['validated']} ({r['rate']:.0%})"
        for r in stats["per_run"]
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--since", default=None, help="runs started on or after this date")
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    print(report(args.db, args.since))


if __name__ == "__main__":
    main()
