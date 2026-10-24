"""An agent's own track record: what it decided, what happened next, where it was overruled.

uv run python scripts/track_record.py --agent NAME [--since DATE] [--out FILE] [--db PATH]

NAME is a desk area (senior-quant, fundamental-strategist, portfolio-manager, execution-trader),
one stage (screen, validate, research, macro, pm, monitor, execute, postmortem) or ``all``.
Read-only: built from the results DB (ADR 0035, step 1). Forward returns use the saved price
snapshots; a ticker with none is fetched.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from solaraalpha import store, track_record
from solaraalpha.analysis.relative_strength import comparisons
from solaraalpha.analysis.scorecard import forward_returns
from solaraalpha.store import Conn


def inputs(conn: Conn, since: str | None, fetch: object = None) -> track_record.Inputs:
    labels = track_record.labels_with_dates(conn, since)
    if labels.empty:
        return track_record.Inputs(
            labels, pd.DataFrame(columns=["run_id", "ticker", "stage", "label"]), since
        )
    tickers = sorted(set(labels["ticker"]))
    market_of = {t: comparisons(t).market or "" for t in tickers}
    prices = track_record.prices_for(conn, tickers, fetch)
    bench = track_record.prices_for(conn, sorted({m for m in market_of.values() if m}), fetch)
    fwd = forward_returns(labels, prices, bench=bench, market_of=market_of)
    return track_record.Inputs(labels, fwd, since)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--agent", required=True, help="desk area, stage or 'all'")
    p.add_argument("--since", default=None, help="YYYY-MM-DD")
    p.add_argument("--out", type=Path, default=None, help="write the markdown here too")
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    track_record.stages_for(args.agent)  # fail fast on a bad name
    conn = store.connect(args.db)
    try:
        text = track_record.report(conn, args.agent, inputs(conn, args.since))
    finally:
        conn.close()
    if args.out:
        args.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
