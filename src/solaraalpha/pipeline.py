"""The weekly pipeline: every stock through the toolkits, one stage at a time.

A stage runs each of its toolkits on every ticker still in; a ticker moves on to the next stage
only if all of them pass. Toolkits within a stage are independent (neither sees the other's
result), so technical and levels are judged side by side and neither gates the other::

    screen        technical + levels    every ticker in the watchlist
    fundamentals  fundamentals          tickers that passed the screen
    events        events                tickers that passed fundamentals

Running stage by stage (not stock by stock) puts each stage's results for all stocks side by
side before the next starts, which the later agent stage needs to work from a shortlist.

Each toolkit ends as ``pass``, ``fail`` (ran, conditions not met), ``skip`` (ticker dropped
out at an earlier stage) or ``error`` (raised; logged and treated as a fail, so one bad stock
or source never stops the run).

Pass conditions are the toolkit's own (``solaraalpha.toolkits``), ``"<field> <op> <value>"``
on its verdict card, op one of ``>= <= > < == !=``; value a number, ``true`` / ``false``, or a
bare word (strings). A NaN or missing value fails the condition.
"""

from __future__ import annotations

import math
import operator
import re
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from solaraalpha.config import REPO_ROOT
from solaraalpha.log import get_logger
from solaraalpha.toolkits import Cards, Inputs, get

log = get_logger(__name__)

WATCHLIST = REPO_ROOT / "config" / "watchlist.toml"
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
class Stage:
    name: str
    toolkits: tuple[str, ...]


WEEKLY: tuple[Stage, ...] = (
    Stage("screen", ("technical", "levels")),
    Stage("fundamentals", ("fundamentals",)),
    Stage("events", ("events",)),
)


@dataclass(frozen=True)
class Step:
    """One toolkit's outcome for one ticker."""

    stage: str
    toolkit: str
    status: str  # pass / fail / skip / error
    reason: str = ""
    cards: Cards = field(default_factory=dict)


def watchlist(path: Path = WATCHLIST) -> list[str]:
    with path.open("rb") as f:
        return list(tomllib.load(f)["tickers"])


def verdict(cards: Cards, toolkit: str) -> tuple[bool, str]:
    """(passed, reason) for ``toolkit``'s pass conditions on its verdict card."""
    conds = [Condition.parse(c) for c in get(toolkit).passes]
    if not conds:
        return True, "ran"
    card = asdict(cards[toolkit])
    failed = [c for c in conds if not c.check(card)]
    shown = ", ".join(f"{c.field}={_fmt(card[c.field])}" for c in conds)
    if failed:
        return False, f"failed {'; '.join(map(str, failed))} ({shown})"
    return True, shown


def _fmt(v: Any) -> str:
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def run_toolkit(
    toolkit: str, x: Inputs, params: dict[str, Any] | None = None, stage: str = ""
) -> Step:
    """Run one toolkit for one ticker and judge it; errors are caught into the Step."""
    try:
        cards = get(toolkit).run(x, **(params or {}))
        ok, reason = verdict(cards, toolkit)
    except Exception as e:
        log.exception("%s: %s failed", x.ticker, toolkit)
        return Step(stage or toolkit, toolkit, "error", f"{type(e).__name__}: {e}")
    step = Step(stage or toolkit, toolkit, "pass" if ok else "fail", reason, cards)
    log.info("%s: %s %s (%s)", x.ticker, toolkit, step.status, reason)
    return step


def run_pipeline(inputs: list[Inputs], stages: tuple[Stage, ...] = WEEKLY) -> dict[str, list[Step]]:
    """Run ``stages`` in order over every ticker; a ticker stays in while it passes."""
    steps: dict[str, list[Step]] = {x.ticker: [] for x in inputs}
    out_at: dict[str, str] = {}  # ticker -> stage it dropped out at
    for stage in stages:
        alive = [x for x in inputs if x.ticker not in out_at]
        log.info("stage %s: %d of %d tickers", stage.name, len(alive), len(inputs))
        for x in inputs:
            if x.ticker in out_at:
                why = f"out at {out_at[x.ticker]}"
                steps[x.ticker] += [Step(stage.name, k, "skip", why) for k in stage.toolkits]
                continue
            new = [run_toolkit(k, x, stage=stage.name) for k in stage.toolkits]
            steps[x.ticker] += new
            if any(s.status != "pass" for s in new):
                out_at[x.ticker] = stage.name
    return steps
