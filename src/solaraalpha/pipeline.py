"""The weekly pipeline: every stock through the toolkits, one stage at a time, to a shortlist.

Each toolkit judges its own verdict card against two **bands** (``solaraalpha.toolkits``):
``act`` clauses and ``watch`` clauses. Its label is **ACT** if every ``act`` clause holds, else
**WATCH** if every ``watch`` clause holds, else **PASS**; a toolkit that raises is **ERROR**. A
toolkit with no clauses (``events``) is ACT whenever it runs. Labels **combine** as "a stock is
only as good as its weakest toolkit": ERROR if any errored, else PASS if any is PASS, else WATCH
if any is WATCH, else ACT (:func:`combine`, :func:`screen_label` over technical, levels,
relative_strength and fundamentals). Since #192 the bands are **information**, not the gate:
what reaches the agents is the ranked shortlist (ADR 0022; ADR 0014, amendment #192).

A stage runs each of its toolkits on every ticker whose *running* label (the combination of
everything judged so far) is in the stage's ``runs_on``, or, for an ``on_shortlist`` stage, on
the shortlisted tickers; the others get ``skip`` steps::

    screen        technical + levels + relative_strength    every ticker in the watchlist
                  (+ liquidity: no bands; the floor, sizing)
    fundamentals  fundamentals                              every ticker without an ERROR
    events        events                                    the shortlist (input to research)

The **shortlist** (:func:`screen`): a minimal sanity floor (no screening toolkit ERROR, a
liquidity card with ``adv_gbp_20 >= min_adv_gbp``, a combined score), then the tickers past it
ranked by :func:`solaraalpha.portfolio.fit.rank_score` (:func:`rank_act`). The top
``shortlist_size`` are shortlisted ("rank N"); tickers with a catalyst (``catalysts``: ticker ->
one-line reason, tagged with its tier) join outside the top N, best tier then best rank, up to
``shortlist_catalysts`` more ("catalyst: tier 1: <reason>"). Shortlisted tickers get the screen label ACT (the agents can only downgrade it);
the others keep their band label capped at WATCH; floor failures are PASS. Volatility is never
a gate. Running stage by stage (not stock by stock) puts each stage's results for all stocks
side by side before the next starts.

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
# Every AIM company Trading212 offers in GBX (#290); the weekly scan runs it with the watchlist
# (owner, 2026-10-29: docs/decisions/0038).
RESEARCH_UNIVERSE = REPO_ROOT / "config" / "research_universe.toml"
# FTSE reviews its UK indices quarterly; constituent changes take effect in these months. A
# hand-copied snapshot is stale once a review month has passed (plus a week for the changes
# to land) since it was taken.
REVIEW_MONTHS = (3, 6, 9, 12)
REVIEW_GRACE_DAYS = 7
LABELS = ("ACT", "WATCH", "PASS", "ERROR")
ALL_LABELS = frozenset(LABELS)
# Toolkits whose labels make the screen label (events is the input to research, not a gate).
# Fewer clean weekly bars than this (after trim_to_consistent) and the toolkits are not run: the
# stock is PASS "too little clean history", not an ERROR. Thin AIM lines with stale closes trim to
# a few weeks (ADR 0038); the toolkits need 14+ weeks for an ATR.
MIN_WEEKS = 26
THIN = "too little clean history"

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
    on_shortlist: bool = False  # run on the shortlist (:func:`screen`) instead of ``runs_on``


NOT_ERROR = frozenset({"ACT", "WATCH", "PASS"})
WEEKLY: tuple[Stage, ...] = (
    Stage("screen", ("technical", "levels", "relative_strength", "liquidity")),
    Stage("fundamentals", ("fundamentals",), NOT_ERROR),
    Stage("events", ("events",), on_shortlist=True),
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


def universe(path: Path = WATCHLIST, research: Path = RESEARCH_UNIVERSE) -> list[str]:
    """The weekly scan's stocks: the watchlist, then the research universe's other tickers."""
    with research.open("rb") as f:
        wide = list(tomllib.load(f)["stocks"])
    names = watchlist(path)
    return names + [t for t in wide if t not in set(names)]


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
            " refresh config/watchlist.toml (scripts/universe.py --write)"
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
    that ran (skipped ones are left out), e.g. ``technical WATCH: failed entry >= 0 (entry=-0.41)``.
    Liquidity has no bands but sizing needs it, so a liquidity ERROR makes the label ERROR."""
    judged = [
        s
        for s in steps
        if (s.toolkit in SCREEN_TOOLKITS or (s.toolkit == "liquidity" and s.label == "ERROR"))
        and s.status != "skip"
    ]
    if not judged:
        thin = next((s.reason for s in steps if s.reason.startswith(THIN)), None)
        return ("PASS", thin) if thin else ("ERROR", "no toolkit ran")
    label = combine([s.label for s in judged])
    reason = "; ".join(
        f"{s.toolkit} {s.label}" + (f": {s.reason}" if s.label != "ACT" else "") for s in judged
    )
    return label, reason


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
    technical extended → ``pullback`` to the 10-week MA ``level``; levels with a pending
    trigger (compressed) → ``break`` of ``trigger``; levels otherwise → ``compression`` (not yet
    compressed or at a high); relative strength → ``rs`` (no longer WATCH since #210: no bands);
    fundamentals → ``revisions`` (``eps_revision``). Anything else keeps the toolkit's reason text."""
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
            item |= {"kind": "compression", "compression": _field(c, "levels", "compression")}
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


@dataclass(frozen=True)
class Screened:
    """One ticker's screen outcome: the ``labels`` row the scan saves."""

    label: str  # ACT = shortlisted; else the band label capped at WATCH; PASS = below the floor
    reason: str
    rank: int | None = None  # combined-score rank among the tickers past the floor (1 = best)
    shortlist: str | None = None  # why it is shortlisted: "rank N" / "catalyst: <reason>"


def cards_of(steps: list[Step]) -> Cards:
    """Every card the ticker's steps produced, by model."""
    return {model: card for s in steps for model, card in s.cards.items()}


def floor(steps: list[Step], min_adv_gbp: float) -> str | None:
    """Why the ticker fails the sanity floor (None: it passes): a screening toolkit errored, no
    liquidity card, ``adv_gbp_20 < min_adv_gbp``, or no combined score at all."""
    from solaraalpha.portfolio.fit import rank_score

    if screen_label(steps)[0] == "ERROR":
        return "a screening toolkit errored"
    cards = cards_of(steps)
    adv = _field(cards, "liquidity", "adv_gbp_20")
    if adv is None:
        return "no liquidity (adv_gbp_20)"
    if adv < min_adv_gbp:
        return f"adv_gbp_20 {adv:,.0f} < min_adv_gbp {min_adv_gbp:,.0f}"
    if math.isnan(rank_score(cards)):
        return "no combined score"
    return None


def screen(
    steps: Mapping[str, list[Step]],
    size: int | None = None,
    catalysts: Mapping[str, str] | None = None,
    catalyst_cap: int | None = None,
    min_adv_gbp: float | None = None,
    untradable: Mapping[str, str] | None = None,
) -> dict[str, Screened]:
    """Rank the screened tickers into the shortlist (ADR 0022; ADR 0014, amendment #192).

    Tickers past the :func:`floor` are ranked by ``rank_score`` (:func:`rank_act`); the top
    ``size`` are shortlisted as ``rank N``. A ticker in ``catalysts`` (ticker -> one-line
    reason) past the floor but outside the top ``size`` joins as ``catalyst: <reason>``,
    best tier first (:func:`solaraalpha.catalysts.tier_of`: information, scheduled, discovery,
    warnings only), then best-ranked, at most ``catalyst_cap`` of them; a top-N ticker with a catalyst carries
    both reasons. Shortlisted -> ACT; past the floor but not shortlisted -> its band label
    (:func:`screen_label`) capped at WATCH; below the floor -> PASS; ERROR stays ERROR.
    ``None`` arguments default to ``config/portfolio.toml`` (``shortlist_size``,
    ``shortlist_catalysts``, ``min_adv_gbp``). A ticker in ``untradable`` (ticker -> why, e.g.
    not offered by Trading212, ADR 0030) fails the floor."""
    if size is None or catalyst_cap is None or min_adv_gbp is None:
        from solaraalpha.portfolio.rules import load_rules

        rules = load_rules()
        size = rules.shortlist_size if size is None else size
        catalyst_cap = rules.shortlist_catalysts if catalyst_cap is None else catalyst_cap
        min_adv_gbp = rules.min_adv_gbp if min_adv_gbp is None else min_adv_gbp
    catalysts = catalysts or {}
    bands = {t: screen_label(s) for t, s in steps.items()}
    untradable = untradable or {}
    below = {t: untradable.get(t) or floor(s, min_adv_gbp) for t, s in steps.items()}
    ranks = rank_act({t: cards_of(s) for t, s in steps.items() if below[t] is None})
    picks = {t: f"rank {r}" for t, r in ranks.items() if r <= size}
    extra = 0
    from solaraalpha.catalysts import tier_of

    by_tier = sorted(
        (t for t in catalysts if t in ranks), key=lambda t: (tier_of(catalysts[t]), ranks[t])
    )
    for t in by_tier:
        why = f"catalyst: {catalysts[t]}"
        if t in picks:
            picks[t] += f"; {why}"
        elif extra < catalyst_cap:
            picks[t], extra = why, extra + 1
    out: dict[str, Screened] = {}
    for t in steps:
        label, reason = bands[t]
        note = f" (catalyst: {catalysts[t]})" if t in catalysts and t not in picks else ""
        if label == "ERROR":
            out[t] = Screened(label, reason + note)
        elif below[t] is not None:
            out[t] = Screened("PASS", f"floor: {below[t]}{note}; {reason}")
        elif t in picks:
            out[t] = Screened("ACT", f"shortlist {picks[t]}; {reason}", ranks[t], picks[t])
        else:
            capped = "WATCH" if label == "ACT" else label
            out[t] = Screened(capped, f"rank {ranks[t]}, not shortlisted{note}; {reason}", ranks[t])
    return out


def thin_history(x: Inputs) -> str | None:
    """Why ``x`` has too little clean history to judge (:data:`MIN_WEEKS`), or None (enough, or
    the prices failed: the toolkits report that as an ERROR)."""
    try:
        n = len(x.weekly)
    except Exception:
        return None
    return f"{THIN}: {n} weekly bars < {MIN_WEEKS}" if n < MIN_WEEKS else None


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


def run_pipeline(
    inputs: list[Inputs],
    stages: tuple[Stage, ...] = WEEKLY,
    catalysts: Mapping[str, str] | None = None,
    **shortlist: Any,
) -> dict[str, list[Step]]:
    """Run ``stages`` in order over every ticker; a stage runs on a ticker only while its running
    label (the combination of every toolkit judged so far) is in the stage's ``runs_on`` or,
    for an ``on_shortlist`` stage, while it is on :func:`screen`'s shortlist of the steps so
    far (``catalysts`` and the ``size`` / ``catalyst_cap`` / ``min_adv_gbp`` / ``untradable`` keywords go to
    :func:`screen`)."""
    steps: dict[str, list[Step]] = {x.ticker: [] for x in inputs}
    running: dict[str, str] = {x.ticker: "ACT" for x in inputs}  # nothing judged yet
    thin = {x.ticker: why for x in inputs if (why := thin_history(x))}
    for stage in stages:
        picked: set[str] = set()
        if stage.on_shortlist:
            got = screen(steps, catalysts=catalysts, **shortlist)
            picked = {t for t, s in got.items() if s.shortlist}

        def runs(t: str, stage: Stage = stage, picked: set[str] = picked) -> bool:
            return t in picked if stage.on_shortlist else running[t] in stage.runs_on

        alive = [x for x in inputs if runs(x.ticker) and x.ticker not in thin]
        log.info("stage %s: %d of %d tickers", stage.name, len(alive), len(inputs))
        for x in inputs:
            if x.ticker in thin:
                steps[x.ticker] += [
                    Step(stage.name, k, "skip", thin[x.ticker]) for k in stage.toolkits
                ]
                continue
            if not runs(x.ticker):
                why = (
                    "not shortlisted"
                    if stage.on_shortlist
                    else f"running label {running[x.ticker]}"
                )
                steps[x.ticker] += [Step(stage.name, k, "skip", why) for k in stage.toolkits]
                continue
            new = [run_toolkit(k, x, stage=stage.name) for k in stage.toolkits]
            steps[x.ticker] += new
            running[x.ticker] = combine([s.label for s in steps[x.ticker] if s.status != "skip"])
    return steps
