"""Run the weekly pipeline: the watchlist, some stocks, or one toolkit on one stock.

uv run python scripts/scan.py                          # every stock in config/watchlist.toml
uv run python scripts/scan.py JET2.L                   # these stocks through the pipeline
uv run python scripts/scan.py JET2.L --toolkit technical [--param entry_date=2025-03-14]
uv run python scripts/scan.py --list                   # stages and toolkits

Stages run in order over every stock; a stock moves on only if all of a stage's toolkits pass
(see solaraalpha.pipeline). Add --save to record each stock's cards and outcomes in the
results DB, and --report FILE to append a markdown summary (GitHub Actions:
"$GITHUB_STEP_SUMMARY"). Exits 1 if any toolkit errored (after reporting and saving).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from solaraalpha.pipeline import WEEKLY, Step, run_pipeline, run_toolkit, watchlist
from solaraalpha.store import save_run
from solaraalpha.toolkits import REGISTRY, Inputs

MARK = {"pass": "PASS", "fail": "fail", "skip": "-", "error": "ERROR"}


def _value(raw: str) -> Any:
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def _save(args: argparse.Namespace, x: Inputs, steps: list[Step]) -> int:
    cards = {model: card for s in steps for model, card in s.cards.items()}
    rows = [(s.toolkit, s.toolkit, s.status, s.reason) for s in steps]
    daily, fund = x.fetched("daily"), x.fetched("fund")
    return save_run(
        "scan", vars(args), x.ticker, cards, daily=daily, fund=fund, steps=rows, others=x.others
    )


def _report(results: dict[str, list[Step]]) -> str:
    cols = [s.toolkit for s in next(iter(results.values()))]
    lines = ["## Weekly pipeline", ""]
    lines += ["| ticker | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for ticker, steps in results.items():
        lines.append(f"| {ticker} | " + " | ".join(MARK[s.status] for s in steps) + " |")
    through = [t for t, steps in results.items() if all(s.status == "pass" for s in steps)]
    lines += ["", f"**Through every stage:** {', '.join(through) or 'none'}", ""]
    for ticker, steps in results.items():
        lines += [f"- {ticker} `{s.toolkit}` {s.status}: {s.reason}" for s in steps if s.reason]
    return "\n".join(lines) + "\n"


def _list() -> None:
    print("Weekly pipeline:")
    for i, st in enumerate(WEEKLY, 1):
        print(f"  {i}. {st.name:<14} {' + '.join(st.toolkits)}")
    print("\nToolkits:")
    for t in REGISTRY.values():
        print(f"  {t.id:<18} {t.doc}")
        print(f"  {'':<18} passes: {', '.join(t.passes) or '(always, once run)'}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("tickers", nargs="*", help="Yahoo tickers (default: config/watchlist.toml)")
    p.add_argument("--toolkit", help="run just this toolkit, with its default verdict")
    p.add_argument("--param", action="append", default=[], help="toolkit param key=value")
    p.add_argument("--list", action="store_true", help="show stages and toolkits")
    p.add_argument("--save", action="store_true", help="record runs in the results DB")
    p.add_argument("--report", type=Path, help="append a markdown summary to this file")
    args = p.parse_args()
    if args.list:
        _list()
        return
    if args.param and not args.toolkit:
        sys.exit("--param needs --toolkit")

    inputs = [Inputs(t) for t in args.tickers or watchlist()]
    if args.toolkit:
        params = {k: _value(v) for k, v in (kv.split("=", 1) for kv in args.param)}
        results = {x.ticker: [run_toolkit(args.toolkit, x, params)] for x in inputs}
    else:
        results = run_pipeline(inputs)

    for x in inputs:
        steps = results[x.ticker]
        print(f"{x.ticker:<10} " + "  ".join(f"{s.toolkit}:{MARK[s.status]}" for s in steps))
        if args.save:
            print(f"{'':<10} saved run {_save(args, x, steps)}")

    report = _report(results)
    print("\n" + report)
    if args.report:
        with args.report.open("a") as f:
            f.write(report + "\n")
    if any(s.status == "error" for steps in results.values() for s in steps):
        sys.exit(1)


if __name__ == "__main__":
    main()
