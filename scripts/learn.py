"""Learn from the record: are ACT labels paying, and what do closed trades say?

uv run python scripts/learn.py scorecard [--since DATE] [--db PATH]   # forward returns by label
uv run python scripts/learn.py journal   [--since DATE] [--db PATH]   # closed trades by exit / set-up
uv run python scripts/learn.py agents    [--since DATE] [--db PATH]   # agent cost, tokens, minutes
uv run python scripts/learn.py agents-value [--since DATE] [--db PATH]  # did downgrades avoid losers?

scorecard scores every saved label (from #111 on; older pass/fail steps are excluded) against
the 2 / 4 / 8 / 16-week forward return of the stock and its excess over the market benchmark
(config/benchmarks.toml), per stage x label and per toolkit x band. Prices come from the saved
snapshots; fetch_daily fills in tickers with no saved history. No look-ahead: horizons without a
complete later bar stay NaN. journal groups closed positions by exit reason and set-up type.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from solaraalpha import store
from solaraalpha.analysis.relative_strength import comparisons
from solaraalpha.analysis.scorecard import (
    MIN_GROUP,
    agent_value,
    calibration,
    forward_returns,
    label_scorecard,
    toolkit_scorecard,
)
from solaraalpha.data.clean import clean_prices
from solaraalpha.data.prices import fetch_daily
from solaraalpha.portfolio import book
from solaraalpha.portfolio.journal import closed_trades, journal
from solaraalpha.store import Conn, query


def labels_with_dates(conn: Conn, since: str | None) -> pd.DataFrame:
    """Labels joined with the screen card's week (``date``) for the run and ticker."""
    sql = (
        "SELECT l.run_id, l.ticker, l.stage, l.label, l.confidence, (SELECT MIN(x.date) FROM results x"
        " WHERE x.run_id = l.run_id AND x.ticker = l.ticker"
        " AND x.model IN ('technical', 'levels', 'relative_strength', 'fundamentals')) AS date"
        " FROM labels l ORDER BY l.run_id, l.rowid"
    )
    df = query(sql, conn)
    df = df.dropna(subset=["date"])
    if since:
        df = df[df["date"] >= since]
    return df.reset_index(drop=True)


def prices_for(conn: Conn, tickers: list[str], fetch: object = None) -> dict[str, pd.DataFrame]:
    fetch = fetch or fetch_daily
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        px = store.load_prices(t, conn=conn)
        if px.empty:
            try:
                px = clean_prices(fetch(t))  # type: ignore[operator]
            except Exception as e:
                print(f"NOTE: {t}: no saved history and the fetch failed ({e})")
                continue
        out[t] = px
    return out


def scorecard(db: Path | None, since: str | None) -> str:
    conn = store.connect(db)
    try:
        labels = labels_with_dates(conn, since)
        if labels.empty:
            return "No labels to score yet (labels are saved by scan.py --save)."
        tickers = sorted(set(labels["ticker"]))
        market_of = {t: comparisons(t).market or "" for t in tickers}
        prices = prices_for(conn, tickers)
        bench = prices_for(conn, sorted({m for m in market_of.values() if m}))
        fwd = forward_returns(labels, prices, bench=bench, market_of=market_of)
        steps = query(
            "SELECT run_id, ticker, toolkit, status FROM steps WHERE stage = 'screen' OR stage = 'fundamentals'",
            conn,
        )
    finally:
        conn.close()
    pd.set_option("display.width", 200)
    fmt = {c: "{:.1%}".format for c in fwd.columns if c.startswith(("ret_", "exc_"))}
    lines = [f"{len(fwd)} labels scored" + (f" since {since}" if since else "")]
    by_label = label_scorecard(fwd)
    lines += [
        "",
        "By stage x label:",
        by_label.to_string(index=False, float_format=lambda v: f"{v:.3f}")
        if not by_label.empty
        else "  (none)",
    ]
    by_toolkit = toolkit_scorecard(steps, fwd)
    lines += [
        "",
        "By toolkit x band:",
        by_toolkit.to_string(index=False, float_format=lambda v: f"{v:.3f}")
        if not by_toolkit.empty
        else "  (none)",
    ]
    lines += [
        "",
        "Rows without enough future data stay NaN (no look-ahead); n_Nw counts the scored ones.",
        "",
    ]
    del fmt
    return "\n".join(lines)


def journal_text(db: Path | None, since: str | None) -> str:
    conn = book.connect(db)
    try:
        closed = closed_trades(conn)
    finally:
        conn.close()
    if since:
        closed = closed[closed["closed_at"] >= since]
    if closed.empty:
        return "No closed trades yet."
    j = journal(closed)
    lines = [f"{len(closed)} closed trades" + (f" since {since}" if since else ""), ""]
    lines.append(
        closed[
            [
                "position_id",
                "ticker",
                "closed_at",
                "exit_reason",
                "setup",
                "net_pnl",
                "r_multiple",
                "holding_weeks",
            ]
        ].to_string(index=False, float_format=lambda v: f"{v:.2f}")
    )
    lines += ["", j.to_string(index=False, float_format=lambda v: f"{v:.2f}")]
    return "\n".join(lines)


def agents_text(db: Path | None, since: str | None) -> str:
    from solaraalpha.agents import agent_usage

    runs = store.load_agent_runs(since, path=db)
    if runs.empty:
        return "No agent runs recorded yet (record.py usage writes them from agents.yml)."
    use = agent_usage(runs)
    fmt = lambda v: f"{v:.2f}"  # noqa: E731
    lines = [f"{len(runs)} agent invocations" + (f" since {since}" if since else ""), ""]
    lines += ["Per week:", use["by_week"].to_string(index=False, float_format=fmt), ""]
    lines += ["Per stage:", use["by_stage"].to_string(index=False, float_format=fmt), ""]
    lines += [
        "Average per ticker / invocation:",
        use["per_ticker"].to_string(index=False, float_format=fmt),
    ]
    failures = runs[runs["status"] != "success"]
    lines += ["", f"failures: {len(failures)}"]
    return "\n".join(lines)


def agents_value_text(db: Path | None, since: str | None) -> str:
    conn = store.connect(db)
    try:
        labels = labels_with_dates(conn, since)
        if labels.empty:
            return "No labels to score yet."
        tickers = sorted(set(labels["ticker"]))
        prices = prices_for(conn, tickers)
        market_of = {t: comparisons(t).market or "" for t in tickers}
        bench = prices_for(conn, sorted({m for m in market_of.values() if m}))
        fwd = forward_returns(labels, prices, bench=bench, market_of=market_of)
    finally:
        conn.close()
    av = agent_value(labels, fwd)
    if av.empty:
        return "No agent verdicts (validate / research) to compare with the screen yet."
    pct = lambda v: "-" if pd.isna(v) else f"{v:+.1%}"  # noqa: E731
    lines = [
        f"{len(labels)} labels" + (f" since {since}" if since else ""),
        "",
        "Kept (screen ACT -> agent ACT) vs downgraded (screen ACT -> agent WATCH / PASS):",
    ]
    for r in av.to_dict("records"):
        verdict = (
            "" if r["conclusive"] else f"  (too few to conclude: fewer than {MIN_GROUP} in a group)"
        )
        lines.append(
            f"  {r['stage']:<9} {r['horizon']:>2}w  kept n={r['n_kept']} mean {pct(r['kept_mean'])} hit {pct(r['kept_hit'])}"
            f" | downgraded n={r['n_downgraded']} mean {pct(r['downgraded_mean'])} hit {pct(r['downgraded_hit'])}"
            f" | diff {pct(r['diff_mean'])} [90%: {pct(r['diff_lo90'])}, {pct(r['diff_hi90'])}]{verdict}"
        )
    cal = calibration(labels, fwd)
    lines += ["", "Calibration (ACT verdicts by confidence, 8-week hit rate):"]
    lines += [
        f"  {r['stage']:<9} {r['bucket']}  n={r['n']}  hit {pct(r['hit_rate'])}  mean {pct(r['mean_return'])}"
        for r in cal.to_dict("records")
    ] or ["  (none)"]
    lines += [
        "",
        "A positive diff means the agent kept the better stocks; the interval is a seeded bootstrap.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("scorecard", "journal", "agents", "agents-value"):
        sp = sub.add_parser(name)
        sp.add_argument("--since", default=None, help="YYYY-MM-DD")
        sp.add_argument(
            "--db", type=Path, default=None, help="local DB file (default: configured DB)"
        )
    args = p.parse_args(argv)
    if args.cmd == "scorecard":
        print(scorecard(args.db, args.since))
    elif args.cmd == "journal":
        print(journal_text(args.db, args.since))
    elif args.cmd == "agents-value":
        print(agents_value_text(args.db, args.since))
    else:
        print(agents_text(args.db, args.since))


if __name__ == "__main__":
    main()
