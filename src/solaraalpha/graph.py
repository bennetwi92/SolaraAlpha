"""Dependency graphs of toolkits, and universes of instruments that run them.

Defined in TOML under ``config/`` and referred to by id (the file name):

* ``config/graphs/<id>.toml`` — a DAG of nodes. Each node runs one registered toolkit (default:
  the node's own id), with optional ``params`` and ``passes`` conditions (default: the
  toolkit's), and ``needs`` — the nodes that must *pass* before it runs::

      [nodes.technical]
      passes = ["trend >= 0.25", "momentum > 0"]

      [nodes.levels]
      needs = ["technical"]

* ``config/universes/<id>.toml`` — the instruments and the graph each one runs::

      graph = "long_swing"
      tickers = ["JET2.L"]
      [graphs]            # optional per-ticker override
      "XYZ.L" = "other_graph"

:func:`run_graph` walks a graph for one ticker in dependency order. Each node ends as ``pass``,
``fail`` (ran, conditions not met), ``skip`` (a need did not pass) or ``error`` (raised; logged,
and treated like a fail downstream so one bad stock or source never stops a scan).

Conditions are ``"<field> <op> <value>"`` on the verdict card, op one of
``>= <= > < == !=``; value a number, ``true`` / ``false``, or a bare word (strings). A NaN or
missing value fails the condition.
"""

from __future__ import annotations

import math
import operator
import re
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

from solaraalpha.config import REPO_ROOT
from solaraalpha.log import get_logger
from solaraalpha.toolkits import Cards, Inputs, get

log = get_logger(__name__)

CONFIG_DIR = REPO_ROOT / "config"
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}
_COND = re.compile(r"^\s*(\w+)\s*(>=|<=|==|!=|>|<)\s*(\S+)\s*$")


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: float | bool | str

    @classmethod
    def parse(cls, text: str) -> Condition:
        m = _COND.match(text)
        if not m:
            raise ValueError(f"bad condition {text!r}; expected '<field> <op> <value>'")
        name, op, raw = m.groups()
        value: float | bool | str
        if raw.lower() in ("true", "false"):
            value = raw.lower() == "true"
        else:
            try:
                value = float(raw)
            except ValueError:
                value = raw
        return cls(name, op, value)

    def check(self, card: dict[str, Any]) -> bool:
        if self.field not in card:
            raise KeyError(f"card has no field {self.field!r}")
        v = card[self.field]
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return False
        return bool(_OPS[self.op](v, self.value))

    def __str__(self) -> str:
        return f"{self.field} {self.op} {self.value}"


@dataclass(frozen=True)
class Node:
    id: str
    toolkit: str
    needs: tuple[str, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)
    passes: tuple[Condition, ...] = ()


@dataclass(frozen=True)
class Graph:
    id: str
    nodes: dict[str, Node]  # in dependency order
    description: str = ""


@dataclass(frozen=True)
class Universe:
    id: str
    graph: str
    tickers: tuple[str, ...]
    graphs: dict[str, str] = field(default_factory=dict)  # per-ticker override
    description: str = ""

    def graph_for(self, ticker: str) -> str:
        return self.graphs.get(ticker, self.graph)


@dataclass(frozen=True)
class Step:
    """One node's outcome for one ticker."""

    node: str
    toolkit: str
    status: str  # pass / fail / skip / error
    reason: str = ""
    cards: Cards = field(default_factory=dict)


def node(id: str, toolkit: str | None = None, **spec: Any) -> Node:
    """Build a node; ``passes`` defaults to the toolkit's own conditions."""
    tk = get(toolkit or id)
    passes = spec.get("passes", tk.passes)
    unknown = set(spec) - {"needs", "params", "passes"}
    if unknown:
        raise ValueError(f"node {id!r}: unknown keys {sorted(unknown)}")
    return Node(
        id,
        tk.id,
        tuple(spec.get("needs", ())),
        dict(spec.get("params", {})),
        tuple(Condition.parse(c) for c in passes),
    )


def make_graph(id: str, nodes: dict[str, dict[str, Any]], description: str = "") -> Graph:
    """Validate a graph spec (``{node_id: {toolkit, needs, params, passes}}``) into a Graph."""
    built = {k: node(k, **v) for k, v in nodes.items()}
    for n in built.values():
        missing = set(n.needs) - set(built)
        if missing:
            raise ValueError(f"graph {id!r}: node {n.id!r} needs unknown {sorted(missing)}")
    try:
        order = list(TopologicalSorter({n.id: n.needs for n in built.values()}).static_order())
    except CycleError as e:
        raise ValueError(f"graph {id!r} has a cycle: {e.args[1]}") from None
    return Graph(id, {k: built[k] for k in order}, description)


def single(toolkit: str, params: dict[str, Any] | None = None) -> Graph:
    """A one-node graph running just ``toolkit`` with its default verdict."""
    return make_graph(toolkit, {toolkit: {"params": params or {}}})


def _toml(kind: str, id: str, root: Path | None) -> dict[str, Any]:
    path = (root or CONFIG_DIR) / kind / f"{id}.toml"
    if not path.exists():
        have = sorted(p.stem for p in path.parent.glob("*.toml"))
        raise FileNotFoundError(f"no {kind[:-1]} {id!r} in {path.parent}; have {have}")
    with path.open("rb") as f:
        return tomllib.load(f)


def load_graph(id: str, root: Path | None = None) -> Graph:
    spec = _toml("graphs", id, root)
    return make_graph(id, spec.get("nodes", {}), spec.get("description", ""))


def load_universe(id: str, root: Path | None = None) -> Universe:
    spec = _toml("universes", id, root)
    u = Universe(
        id,
        spec["graph"],
        tuple(spec["tickers"]),
        dict(spec.get("graphs", {})),
        spec.get("description", ""),
    )
    for t in u.graphs:
        if t not in u.tickers:
            raise ValueError(f"universe {id!r}: graph override for {t!r}, not in tickers")
    for g in {u.graph, *u.graphs.values()}:
        load_graph(g, root)  # fail fast on a bad reference
    return u


def universes(root: Path | None = None) -> list[str]:
    return sorted(p.stem for p in ((root or CONFIG_DIR) / "universes").glob("*.toml"))


def verdict(cards: Cards, n: Node) -> tuple[bool, str]:
    """(passed, reason) for node ``n``'s conditions on its toolkit's verdict card."""
    if not n.passes:
        return True, "ran"
    card = asdict(cards[n.toolkit])
    failed = [c for c in n.passes if not c.check(card)]
    shown = ", ".join(f"{c.field}={_fmt(card[c.field])}" for c in n.passes)
    if failed:
        return False, f"failed {'; '.join(map(str, failed))} ({shown})"
    return True, shown


def _fmt(v: Any) -> str:
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def run_graph(graph: Graph, ticker: str, inputs: Inputs | None = None) -> list[Step]:
    """Walk ``graph`` for ``ticker``: each node runs only if every node it needs passed."""
    x = inputs or Inputs(ticker)
    steps: dict[str, Step] = {}
    for n in graph.nodes.values():
        blocked = [d for d in n.needs if steps[d].status != "pass"]
        if blocked:
            steps[n.id] = Step(n.id, n.toolkit, "skip", f"needs {', '.join(blocked)}")
            continue
        try:
            cards = get(n.toolkit).run(x, **n.params)
            ok, reason = verdict(cards, n)
        except Exception as e:
            log.exception("%s: node %s (%s) failed", ticker, n.id, n.toolkit)
            steps[n.id] = Step(n.id, n.toolkit, "error", f"{type(e).__name__}: {e}")
            continue
        steps[n.id] = Step(n.id, n.toolkit, "pass" if ok else "fail", reason, cards)
        log.info("%s: %s %s (%s)", ticker, n.id, steps[n.id].status, reason)
    return list(steps.values())
