"""Run the weekly pipeline: the watchlist, some stocks, or one toolkit on one stock.

uv run python scripts/scan.py                          # every stock in config/watchlist.toml
uv run python scripts/scan.py JET2.L                   # these stocks through the pipeline
uv run python scripts/scan.py JET2.L --toolkit technical [--param entry_date=2025-03-14]
uv run python scripts/scan.py --list                   # stages, toolkits and their bands

Every toolkit labels its stock ACT / WATCH / PASS (ERROR if it raised) from its act / watch
bands (information). The screen then ranks every stock past a sanity floor (no ERROR, liquidity
>= min_adv_gbp) by the combined score and shortlists the top shortlist_size (config/
portfolio.toml), plus up to shortlist_catalysts more with a catalyst: shortlisted stocks get the
screen label ACT and go to the agents, the rest keep their band label capped at WATCH, floor
failures PASS. See solaraalpha.pipeline, docs/decisions/0014 (amendment #192) and 0022.
After the pipeline, every **open position** in the book is reviewed (portfolio.review): all
toolkits re-run with the position's entry, the trailing stop (context for the daily monitor,
which sets stops: ADR 0024, never drafted here), deterioration since entry and the horizon rule
judged into a `review` card; an exit is reported, never drafted. Add --save to record the scan as one run in
the results DB (every stock's cards, steps, a `labels` row per stock and input snapshots),
--report FILE to append a markdown summary (GitHub Actions: "$GITHUB_STEP_SUMMARY") and
--run-id-file FILE to write the saved run id. Exits 1 if any toolkit errored (after reporting
and saving).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha.catalysts import fresh_catalysts
from solaraalpha.pipeline import (
    ALL_LABELS,
    NOT_ERROR,
    SCREEN_TOOLKITS,
    WEEKLY,
    Screened,
    Step,
    cards_of,
    rank_act,
    run_pipeline,
    run_toolkit,
    screen,
    stale_watchlist,
    watch_on,
    watchlist,
    watchlist_snapshot,
)
from solaraalpha.portfolio import book
from solaraalpha.portfolio.review import Reviewed, review_positions, review_report
from solaraalpha.portfolio.rules import load_rules
from solaraalpha.store import save_label, save_ticker, start_run
from solaraalpha.toolkits import REGISTRY, Inputs

MARK = {"act": "ACT", "watch": "WATCH", "pass": "pass", "skip": "-", "error": "ERROR"}


def _value(raw: str) -> Any:
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def _save(run_id: int, x: Inputs, steps: list[Step], got: Screened | None) -> None:
    rows = [(s.stage, s.toolkit, s.status, s.reason) for s in steps]
    daily, fund = x.fetched("daily"), x.fetched("fund")
    save_ticker(run_id, x.ticker, cards_of(steps), daily, fund, steps=rows, others=x.others)
    if got is not None:
        save_label(
            run_id,
            x.ticker,
            "screen",
            got.label,
            got.reason,
            watch_on=watch_on(steps) if got.label == "WATCH" else None,
            rank=got.rank,
            shortlist=got.shortlist,
        )


def _report(
    results: dict[str, list[Step]],
    labels: dict[str, Screened],
    reviewed: list[Reviewed] | None = None,
) -> str:
    cols = [s.toolkit for s in next(iter(results.values()))]
    lines = ["## Weekly pipeline", ""]
    stale = stale_watchlist(watchlist_snapshot(), pd.Timestamp.now(tz="UTC"))
    if stale:
        lines += [f"> **WARNING:** {stale}", ""]
    lines += ["| ticker | " + " | ".join(cols) + " | label |", "|---" * (len(cols) + 2) + "|"]
    for ticker, steps in results.items():
        marks = " | ".join(MARK[s.status] for s in steps)
        lines.append(f"| {ticker} | {marks} | **{labels[ticker].label}** |")
    picks = sorted(
        (t for t, got in labels.items() if got.label == "ACT"),
        key=lambda t: (labels[t].rank is None, labels[t].rank or 0, t),
    )
    lines += ["", f"**Shortlist ({len(picks)}, to the agents):**"]
    lines += [f"- {t}: {labels[t].shortlist or 'ACT'}" for t in picks] or ["- none"]
    lines += ["", "**WATCH (why):**"]
    watch = [f"- {t}: {got.reason}" for t, got in labels.items() if got.label == "WATCH"]
    lines += watch or ["- none"]
    lines += ["", "**ERROR:**"]
    errors = [
        f"- {t} `{s.toolkit}`: {s.reason}"
        for t, steps in results.items()
        for s in steps
        if s.status == "error"
    ]
    lines += errors or ["- none"]
    if reviewed is not None:
        lines += ["", review_report(reviewed)]
    lines += ["", "<details><summary>Every toolkit's reason</summary>", ""]
    for ticker, steps in results.items():
        lines += [f"- {ticker} `{s.toolkit}` {s.label}: {s.reason}" for s in steps if s.reason]
    lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def _list() -> None:
    print("Weekly pipeline (a stage runs on tickers whose running label is in runs_on):")
    for i, st in enumerate(WEEKLY, 1):
        if st.on_shortlist:
            on = "the shortlist"
        elif st.runs_on == ALL_LABELS:
            on = "all"
        elif st.runs_on == NOT_ERROR:
            on = "all but ERROR"
        else:
            on = "/".join(sorted(st.runs_on, reverse=True))
        print(f"  {i}. {st.name:<14} {' + '.join(st.toolkits):<42} runs on {on}")
    rules = load_rules()
    print(
        f"\nShortlist: past the floor (no ERROR, adv_gbp_20 >= {rules.min_adv_gbp:,.0f}), ranked by"
        f" rank_score; top {rules.shortlist_size} + up to {rules.shortlist_catalysts} on a"
        " catalyst -> screen ACT (to the agents); the rest keep their band label, capped at WATCH."
    )
    print(f"Band label = combine({', '.join(SCREEN_TOOLKITS)}); ERROR > PASS > WATCH > ACT.")
    print("\nToolkits (bands are information and placeholders, docs/decisions/0014):")
    for t in REGISTRY.values():
        print(f"  {t.id:<18} {t.doc}")
        print(f"  {'':<18} act:   {' | '.join(t.act) or '(always, once run)'}")
        if t.watch:
            print(f"  {'':<18} watch: {' | '.join(t.watch)}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("tickers", nargs="*", help="Yahoo tickers (default: config/watchlist.toml)")
    p.add_argument("--toolkit", help="run just this toolkit and print its label")
    p.add_argument("--param", action="append", default=[], help="toolkit param key=value")
    p.add_argument("--list", action="store_true", help="show stages, toolkits and bands")
    p.add_argument("--save", action="store_true", help="record the scan in the results DB")
    p.add_argument("--report", type=Path, help="append a markdown summary to this file")
    p.add_argument("--run-id-file", type=Path, help="write the saved run id to this file")
    args = p.parse_args(argv)
    if args.list:
        _list()
        return
    if args.param and not args.toolkit:
        sys.exit("--param needs --toolkit")

    inputs = [Inputs(t) for t in args.tickers or watchlist()]
    run_id = start_run("scan", vars(args)) if args.save else None
    catalysts: dict[str, str] | None = None  # ticker -> one-line reason (#193)
    if args.toolkit:
        params = {k: _value(v) for k, v in (kv.split("=", 1) for kv in args.param)}
        results = {x.ticker: [run_toolkit(args.toolkit, x, params)] for x in inputs}
        ranks = rank_act({t: cards_of(s) for t, s in results.items() if s[0].label == "ACT"})
        labels = {t: Screened(s[0].label, s[0].reason, ranks.get(t)) for t, s in results.items()}
    else:
        catalysts = fresh_catalysts([x.ticker for x in inputs])
        results = run_pipeline(inputs, catalysts=catalysts)
        labels = screen(results, catalysts=catalysts)

    for x in inputs:
        steps, got = results[x.ticker], labels[x.ticker]
        marks = "  ".join(f"{s.toolkit}:{MARK[s.status]}" for s in steps)
        rk = f" #{got.rank}" if got.rank is not None else ""
        print(f"{x.ticker:<10} {got.label + rk:<6} {marks}")
        if run_id is not None:
            _save(run_id, x, steps, None if args.toolkit else got)
    reviewed: list[Reviewed] | None = None
    if not args.toolkit:
        conn = book.connect()
        try:
            shared = {x.ticker: x for x in inputs}
            reviewed = review_positions(conn, load_rules(), shared, run_id)
        finally:
            conn.close()
        for it in reviewed:
            print(f"{it.state.ticker:<10} review {it.card.action:<6} {it.card.reason}")
    if run_id is not None:
        print(f"saved run {run_id}")
        if args.run_id_file:
            args.run_id_file.write_text(f"{run_id}\n")

    report = _report(results, labels, reviewed)
    print("\n" + report)
    if args.report:
        with args.report.open("a") as f:
            f.write(report + "\n")
    # Fail only when every ticker errored (as the agents do, ADR 0019): a per-ticker ERROR is
    # saved, reported and notified, and failing the run on it would skip the agents (#189).
    failed = [t for t, steps in results.items() if any(s.status == "error" for s in steps)]
    if results and len(failed) == len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
