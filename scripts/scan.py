"""Run the weekly pipeline: the watchlist, some stocks, or one toolkit on one stock.

uv run python scripts/scan.py                          # every stock in config/watchlist.toml
uv run python scripts/scan.py JET2.L                   # these stocks through the pipeline
uv run python scripts/scan.py JET2.L --toolkit technical [--param entry_date=2025-03-14]
uv run python scripts/scan.py --list                   # stages, toolkits and their bands

Every toolkit labels its stock ACT / WATCH / PASS (ERROR if it raised) from its act / watch
bands, and the stock's screen label combines technical, levels, relative strength and
fundamentals (ERROR > PASS > WATCH > ACT); see solaraalpha.pipeline and docs/decisions/0014.
After the pipeline, every **open position** in the book is reviewed (portfolio.review): all
toolkits re-run with the position's entry, the stop ratcheted (a raised stop is drafted as an
approved order with --save), deterioration since entry and the horizon rule judged into a
`review` card; an exit is reported, never drafted. Add --save to record the scan as one run in
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

from solaraalpha.pipeline import (
    SCREEN_TOOLKITS,
    WEEKLY,
    Step,
    run_pipeline,
    run_toolkit,
    screen_label,
    watchlist,
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


def _save(run_id: int, x: Inputs, steps: list[Step], label: tuple[str, str] | None) -> None:
    cards = {model: card for s in steps for model, card in s.cards.items()}
    rows = [(s.stage, s.toolkit, s.status, s.reason) for s in steps]
    daily, fund = x.fetched("daily"), x.fetched("fund")
    save_ticker(run_id, x.ticker, cards, daily, fund, steps=rows, others=x.others)
    if label is not None:
        save_label(run_id, x.ticker, "screen", label[0], label[1])


def _report(
    results: dict[str, list[Step]],
    labels: dict[str, tuple[str, str]],
    reviewed: list[Reviewed] | None = None,
) -> str:
    cols = [s.toolkit for s in next(iter(results.values()))]
    lines = ["## Weekly pipeline", ""]
    lines += ["| ticker | " + " | ".join(cols) + " | label |", "|---" * (len(cols) + 2) + "|"]
    for ticker, steps in results.items():
        marks = " | ".join(MARK[s.status] for s in steps)
        lines.append(f"| {ticker} | {marks} | **{labels[ticker][0]}** |")
    by = {
        lab: [t for t, (got, _) in labels.items() if got == lab]
        for lab in ("ACT", "WATCH", "ERROR")
    }
    lines += ["", f"**ACT:** {', '.join(by['ACT']) or 'none'}", ""]
    lines += ["**WATCH (why):**"]
    lines += [f"- {t}: {labels[t][1]}" for t in by["WATCH"]] or ["- none"]
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
        on = "all" if len(st.runs_on) == 4 else "/".join(sorted(st.runs_on, reverse=True))
        print(f"  {i}. {st.name:<14} {' + '.join(st.toolkits):<42} runs on {on}")
    print(f"\nScreen label = combine({', '.join(SCREEN_TOOLKITS)}); ERROR > PASS > WATCH > ACT.")
    print("\nToolkits (bands are placeholders, docs/decisions/0014):")
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
    if args.toolkit:
        params = {k: _value(v) for k, v in (kv.split("=", 1) for kv in args.param)}
        results = {x.ticker: [run_toolkit(args.toolkit, x, params)] for x in inputs}
        labels = {t: (steps[0].label, steps[0].reason) for t, steps in results.items()}
    else:
        results = run_pipeline(inputs)
        labels = {t: screen_label(steps) for t, steps in results.items()}

    for x in inputs:
        steps = results[x.ticker]
        marks = "  ".join(f"{s.toolkit}:{MARK[s.status]}" for s in steps)
        print(f"{x.ticker:<10} {labels[x.ticker][0]:<6} {marks}")
        if run_id is not None:
            _save(run_id, x, steps, None if args.toolkit else labels[x.ticker])
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
    errored = any(s.status == "error" for steps in results.values() for s in steps)
    errored |= any(s.status == "error" for it in reviewed or [] for s in it.steps)
    if errored:
        sys.exit(1)


if __name__ == "__main__":
    main()
