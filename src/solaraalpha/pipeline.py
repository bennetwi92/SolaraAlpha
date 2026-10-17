"""The weekly pipeline: every stock through the toolkits, one stage at a time, to a label.

Each toolkit judges its own verdict card against two **bands** (``solaraalpha.toolkits``):
``act`` clauses and ``watch`` clauses. Its label is **ACT** if every ``act`` clause holds, else
**WATCH** if every ``watch`` clause holds, else **PASS**; a toolkit that raises is **ERROR**. A
toolkit with no clauses (``events``) is ACT whenever it runs. Labels **combine** as "a stock is
only as good as its weakest toolkit": ERROR if any errored, else PASS if any is PASS, else WATCH
if any is WATCH, else ACT (:func:`combine`).

A stage runs each of its toolkits on every ticker whose *running* label (the combination of
everything judged so far) is in the stage's ``runs_on``; the others get ``skip`` steps::

    screen        technical + levels + relative_strength    every ticker in the watchlist
                  (+ liquidity: no bands, information only)
    fundamentals  fundamentals                              running label ACT or WATCH
    events        events                                    running label ACT (input to research)

so a WATCH stock still gets a full four-toolkit label. The **screen label** of a ticker is
:func:`combine` over technical, levels, relative_strength and fundamentals
(:func:`screen_label`); events is not a gate. Running stage by stage (not stock by stock) puts
each stage's results for all stocks side by side before the next starts.

Clause grammar: a clause is one or more conditions joined by `` or ``; a condition is
``<field> <op> <value>`` (op one of ``>= <= > < == !=``; value a number, ``true`` / ``false``, or
a bare word) or ``<field> is nan``. A NaN or missing value fails an ordinary condition. The bands
are placeholders until backtested; see docs/decisions/0014.
"""

from __future__ import annotations

import math
import operator
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha.config import REPO_ROOT
from solaraalpha.log import get_logger
from solaraalpha.toolkits import Cards, Inputs, get

log = get_logger(__name__)

WATCHLIST = REPO_ROOT / "config" / "watchlist.toml"
# FTSE reviews its UK indices quarterly; constituent changes take effect in these months. A
# hand-copied snapshot is stale once a review month has passed (plus a week for the changes
# to land) since it was taken.
REVIEW_MONTHS = (3, 6, 9, 12)
REVIEW_GRACE_DAYS = 7
LABELS = ("ACT", "WATCH", "PASS", "ERROR")
ALL_LABELS = frozenset(LABELS)
# Toolkits whose labels make the screen label (events is the input to research, not a gate).
SCREEN_TOOLKITS = ("technical", "levels", "relative_strength", "fundamentals")
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}
_COND = re.compile(r"^\s*(\w+)\s*(>=|<=|==|!=|>|<)\s*(\S+)\s*$")
_IS_NAN = re.compile(r"^\s*(\w+)\s+is\s+nan\s*$", re.I)


def _is_nan(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


@dataclass(frozen=True)
class Condition:
    field: str
    op: str  # one of _OPS, or "is nan"
    value: float | bool | str | None = None

    @classmethod
    def parse(cls, text: str) -> Condition:
        if m_nan := _IS_NAN.match(text):
            return cls(m_nan.group(1), "is nan")
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
        if self.op == "is nan":
            return _is_nan(v)
        if _is_nan(v):
            return False
        return bool(_OPS[self.op](v, self.value))

    def __str__(self) -> str:
        if self.op == "is nan":
            return f"{self.field} is nan"
        v = str(self.value).lower() if isinstance(self.value, bool) else self.value
        return f"{self.field} {self.op} {v}"


@dataclass(frozen=True)
class Clause:
    """Conditions joined by ``or``: holds when any of them does."""

    conditions: tuple[Condition, ...]

    @classmethod
    def parse(cls, text: str) -> Clause:
        parts = re.split(r"\s+or\s+", text.strip())
        return cls(tuple(Condition.parse(p) for p in parts))

    @property
    def fields(self) -> list[str]:
        return list(dict.fromkeys(c.field for c in self.conditions))

    def check(self, card: dict[str, Any]) -> bool:
        return any(c.check(card) for c in self.conditions)

    def __str__(self) -> str:
        return " or ".join(map(str, self.conditions))


@dataclass(frozen=True)
class Stage:
    name: str
    toolkits: tuple[str, ...]
    runs_on: frozenset[str] = ALL_LABELS  # running labels a ticker must hold to run the stage


WEEKLY: tuple[Stage, ...] = (
    Stage("screen", ("technical", "levels", "relative_strength", "liquidity")),
    Stage("fundamentals", ("fundamentals",), frozenset({"ACT", "WATCH"})),
    Stage("events", ("events",), frozenset({"ACT"})),
)


@dataclass(frozen=True)
class Step:
    """One toolkit's outcome for one ticker."""

    stage: str
    toolkit: str
    status: str  # act / watch / pass / error / skip (the toolkit's label, lowercase)
    reason: str = ""
    cards: Cards = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.status.upper()


def watchlist(path: Path = WATCHLIST) -> list[str]:
    with path.open("rb") as f:
        return list(tomllib.load(f)["tickers"])


def watchlist_snapshot(path: Path = WATCHLIST) -> pd.Timestamp | None:
    """The ``snapshot`` date in the watchlist file, if any."""
    with path.open("rb") as f:
        raw = tomllib.load(f).get("snapshot")
    return pd.Timestamp(raw) if raw is not None else None


def stale_watchlist(snapshot: pd.Timestamp | None, today: pd.Timestamp) -> str | None:
    """A warning when ``today`` is more than :data:`REVIEW_GRACE_DAYS` past the end of the first
    FTSE review month (:data:`REVIEW_MONTHS`) after ``snapshot``'s month; None when current."""
    if snapshot is None:
        return "watchlist has no `snapshot` date; add one so staleness can be checked"
    snap, now = pd.Timestamp(snapshot).tz_localize(None), pd.Timestamp(today).tz_localize(None)
    year, month = snap.year, snap.month
    later = [m for m in REVIEW_MONTHS if m > month]
    review_month = (year, later[0]) if later else (year + 1, REVIEW_MONTHS[0])
    end_of_review = pd.Timestamp(
        year=review_month[0], month=review_month[1], day=1
    ) + pd.offsets.MonthEnd(0)
    due = end_of_review + pd.Timedelta(days=REVIEW_GRACE_DAYS)
    if now.normalize() > due:
        return (
            f"watchlist snapshot {snap:%Y-%m-%d} predates the {end_of_review:%B %Y} FTSE review:"
            " refresh config/watchlist.toml from the FTSE AIM UK 50 constituents"
        )
    return None


def band(cards: Cards, toolkit: str) -> tuple[str, str]:
    """``(label, reason)`` for ``toolkit``'s act / watch bands on its verdict card."""
    tk = get(toolkit)
    act = [Clause.parse(c) for c in tk.act]
    watch = [Clause.parse(c) for c in tk.watch]
    if not act and not watch:
        return "ACT", "ran"
    card = asdict(cards[toolkit])
    failed_act = [c for c in act if not c.check(card)]
    if not failed_act:
        return "ACT", _shown(card, act)
    failed_watch = [c for c in watch if not c.check(card)]
    failed = failed_watch or failed_act
    label = "WATCH" if not failed_watch else "PASS"
    return label, f"failed {'; '.join(map(str, failed))} ({_shown(card, failed)})"


def _shown(card: dict[str, Any], clauses: list[Clause]) -> str:
    names = list(dict.fromkeys(f for c in clauses for f in c.fields))
    return ", ".join(f"{n}={_fmt(card[n])}" for n in names)


def _fmt(v: Any) -> str:
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def combine(labels: list[str]) -> str:
    """ERROR if any errored; else PASS if any is PASS; else WATCH if any is WATCH; else ACT."""
    for lab in ("ERROR", "PASS", "WATCH"):
        if lab in labels:
            return lab
    return "ACT"


def screen_label(steps: list[Step]) -> tuple[str, str]:
    """The ticker's screen label and reason: :func:`combine` over the :data:`SCREEN_TOOLKITS`
    that ran (skipped ones are left out), e.g. ``technical WATCH: failed entry >= 0 (entry=-0.41)``."""
    judged = [s for s in steps if s.toolkit in SCREEN_TOOLKITS and s.status != "skip"]
    label = combine([s.label for s in judged]) if judged else "ERROR"
    reason = "; ".join(
        f"{s.toolkit} {s.label}" + (f": {s.reason}" if s.label != "ACT" else "") for s in judged
    )
    return label, reason or "no toolkit ran"


def _field(cards: Cards, model: str, field: str) -> Any:
    card = cards.get(model)
    if card is None:
        return None
    v = card.get(field) if isinstance(card, dict) else getattr(card, field, None)
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def watch_on(steps: list[Step]) -> list[dict[str, Any]] | None:
    """What each WATCH toolkit is waiting for, in machine form (None when no toolkit is WATCH):
    technical extended → ``pullback`` to the 10-week MA ``level``; levels coiled → ``break`` of
    ``trigger``; levels thin → ``reward_risk``; relative strength → ``rs``; fundamentals →
    ``revisions`` (``eps_revision``). Anything else keeps the toolkit's reason text."""
    out: list[dict[str, Any]] = []
    for s in steps:
        if s.label != "WATCH" or s.toolkit not in SCREEN_TOOLKITS:
            continue
        c = s.cards
        item: dict[str, Any] = {"toolkit": s.toolkit}
        if s.toolkit == "technical" and (_field(c, "technical", "entry") or 0) < 0:
            item |= {
                "kind": "pullback",
                "level": _field(c, "technical", "ma10"),
                "entry": _field(c, "technical", "entry"),
            }
        elif s.toolkit == "levels" and _field(c, "levels", "coil_pending"):
            item |= {"kind": "break", "trigger": _field(c, "levels", "trigger")}
        elif s.toolkit == "levels":
            item |= {"kind": "reward_risk", "reward_risk": _field(c, "levels", "reward_risk")}
        elif s.toolkit == "relative_strength":
            item |= {"kind": "rs", "rs": _field(c, "relative_strength", "rs")}
        elif s.toolkit == "fundamentals":
            item |= {"kind": "revisions", "eps_revision": _field(c, "fundamentals", "eps_revision")}
        else:
            item |= {"kind": s.toolkit, "reason": s.reason}
        out.append(item)
    return out or None


def rank_act(cards_by_ticker: Mapping[str, Cards]) -> dict[str, int]:
    """``ticker -> rank`` (1 = best) by :func:`solaraalpha.portfolio.fit.rank_score` over each
    ticker's cards; NaN scores last, ties broken by ticker, so the order is deterministic."""
    from solaraalpha.portfolio.fit import rank_score

    scored = []
    for t, cards in cards_by_ticker.items():
        v = rank_score(cards)
        scored.append((math.isnan(v), -v if not math.isnan(v) else 0.0, t))
    return {t: i for i, (_, _, t) in enumerate(sorted(scored), 1)}


def run_toolkit(
    toolkit: str, x: Inputs, params: dict[str, Any] | None = None, stage: str = ""
) -> Step:
    """Run one toolkit for one ticker and label it; errors are caught into the Step."""
    try:
        cards = get(toolkit).run(x, **(params or {}))
        label, reason = band(cards, toolkit)
    except Exception as e:
        log.exception("%s: %s failed", x.ticker, toolkit)
        return Step(stage or toolkit, toolkit, "error", f"{type(e).__name__}: {e}")
    step = Step(stage or toolkit, toolkit, label.lower(), reason, cards)
    log.info("%s: %s %s (%s)", x.ticker, toolkit, step.label, reason)
    return step


def run_pipeline(inputs: list[Inputs], stages: tuple[Stage, ...] = WEEKLY) -> dict[str, list[Step]]:
    """Run ``stages`` in order over every ticker; a stage runs on a ticker only while its running
    label (the combination of every toolkit judged so far) is in the stage's ``runs_on``."""
    steps: dict[str, list[Step]] = {x.ticker: [] for x in inputs}
    running: dict[str, str] = {x.ticker: "ACT" for x in inputs}  # nothing judged yet
    for stage in stages:
        alive = [x for x in inputs if running[x.ticker] in stage.runs_on]
        log.info("stage %s: %d of %d tickers", stage.name, len(alive), len(inputs))
        for x in inputs:
            if running[x.ticker] not in stage.runs_on:
                why = f"running label {running[x.ticker]}"
                steps[x.ticker] += [Step(stage.name, k, "skip", why) for k in stage.toolkits]
                continue
            new = [run_toolkit(k, x, stage=stage.name) for k in stage.toolkits]
            steps[x.ticker] += new
            running[x.ticker] = combine([s.label for s in steps[x.ticker] if s.status != "skip"])
    return steps
