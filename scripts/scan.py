"""Run toolkits through their dependency graph: a whole universe, one stock, or one toolkit.

uv run python scripts/scan.py --universe watchlist        # every instrument, its own graph
uv run python scripts/scan.py --universe all              # every universe in config/universes
uv run python scripts/scan.py JET2.L --graph long_swing   # one stock through a graph
uv run python scripts/scan.py JET2.L --toolkit technical [--param entry_date=2025-03-14]
uv run python scripts/scan.py --list                      # toolkits, graphs, universes

Add --save to record every ticker's cards and node outcomes in the results DB, and
--report FILE to append a markdown summary (GitHub Actions: "$GITHUB_STEP_SUMMARY").
Exits 1 if any node errored (after reporting and saving), so a failed fetch is visible.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from solaraalpha.graph import (
    CONFIG_DIR,
    Graph,
    Step,
    load_graph,
    load_universe,
    run_graph,
    single,
    universes,
)
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


def _jobs(args: argparse.Namespace) -> list[tuple[str, str, Graph]]:
    """(label, ticker, graph) for every ticker to run."""
    if args.universe:
        ids = universes() if args.universe == "all" else [args.universe]
        jobs = []
        for uid in ids:
            u = load_universe(uid)
            jobs += [(uid, t, load_graph(u.graph_for(t))) for t in u.tickers]
        return jobs
    if not args.tickers or bool(args.graph) == bool(args.toolkit):
        sys.exit("give --universe, or tickers with exactly one of --graph / --toolkit")
    if args.graph:
        graph = load_graph(args.graph)
    else:
        params = dict(p.split("=", 1) for p in args.param)
        graph = single(args.toolkit, {k: _value(v) for k, v in params.items()})
    return [("adhoc", t, graph) for t in args.tickers]


def _save(args: argparse.Namespace, label: str, graph: Graph, x: Inputs, steps: list[Step]) -> int:
    cards = {}
    for s in steps:
        for model, card in s.cards.items():
            cards[model if s.node == s.toolkit else f"{s.node}.{model}"] = card
    meta = vars(args) | {"universe": label, "graph": graph.id}
    rows = [(s.node, s.toolkit, s.status, s.reason) for s in steps]
    daily, fund = x.fetched("daily"), x.fetched("fund")
    return save_run("scan", meta, x.ticker, cards, daily=daily, fund=fund, steps=rows)


def _report(results: list[tuple[str, str, Graph, list[Step]]]) -> str:
    lines = ["## Scan", ""]
    by_graph: dict[tuple[str, str], list[tuple[str, list[Step]]]] = {}
    for label, ticker, graph, steps in results:
        by_graph.setdefault((label, graph.id), []).append((ticker, steps))
    for (label, gid), rows in by_graph.items():
        nodes = [s.node for s in rows[0][1]]
        lines += [f"### {label} — graph `{gid}`", ""]
        lines += ["| ticker | " + " | ".join(nodes) + " |", "|---" * (len(nodes) + 1) + "|"]
        for ticker, steps in rows:
            lines.append(f"| {ticker} | " + " | ".join(MARK[s.status] for s in steps) + " |")
        lines.append("")
        for ticker, steps in rows:
            for s in steps:
                if s.status != "skip":
                    lines.append(f"- {ticker} `{s.node}` {s.status}: {s.reason}")
        lines.append("")
    return "\n".join(lines)


def _list() -> None:
    print("Toolkits:")
    for t in REGISTRY.values():
        print(f"  {t.id:<14} {t.doc}")
        print(f"  {'':<14} passes: {', '.join(t.passes) or '(always, once run)'}")
    print("\nGraphs:")
    for p in sorted((CONFIG_DIR / "graphs").glob("*.toml")):
        g = load_graph(p.stem)
        print(f"  {g.id:<14} {g.description}")
        for n in g.nodes.values():
            after = f"  after {', '.join(n.needs)}" if n.needs else ""
            print(f"  {'':<16}{n.id} [{n.toolkit}]{after}: {', '.join(map(str, n.passes)) or '-'}")
    print("\nUniverses:")
    for uid in universes():
        u = load_universe(uid)
        print(f"  {u.id:<14} graph {u.graph}, {len(u.tickers)} tickers")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("tickers", nargs="*", help="Yahoo tickers, e.g. JET2.L")
    p.add_argument("--universe", help="universe id (config/universes/<id>.toml) or 'all'")
    p.add_argument("--graph", help="graph id (config/graphs/<id>.toml)")
    p.add_argument("--toolkit", help="run just this toolkit, with its default verdict")
    p.add_argument("--param", action="append", default=[], help="toolkit param key=value")
    p.add_argument("--list", action="store_true", help="show toolkits, graphs and universes")
    p.add_argument("--save", action="store_true", help="record runs in the results DB")
    p.add_argument("--report", type=Path, help="append a markdown summary to this file")
    args = p.parse_args()
    if args.list:
        _list()
        return

    results = []
    for label, ticker, graph in _jobs(args):
        x = Inputs(ticker)
        steps = run_graph(graph, ticker, x)
        results.append((label, ticker, graph, steps))
        print(f"{ticker:<10} " + "  ".join(f"{s.node}:{MARK[s.status]}" for s in steps))
        if args.save:
            print(f"{'':<10} saved run {_save(args, label, graph, x, steps)}")

    report = _report(results)
    print("\n" + report)
    if args.report:
        with args.report.open("a") as f:
            f.write(report + "\n")
    if any(s.status == "error" for *_, steps in results for s in steps):
        sys.exit(1)


if __name__ == "__main__":
    main()
