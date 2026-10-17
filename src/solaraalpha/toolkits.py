"""Toolkit register: every model the weekly pipeline can run, by id.

A toolkit is a function ``(Inputs, **params) -> {model: card}`` registered with
:func:`toolkit`. The card stored under the toolkit's own id is its *verdict card*: the toolkit's
``act`` and ``watch`` bands (clauses such as ``"trend >= 0.25"``, see
:mod:`solaraalpha.pipeline`) are checked against it to label the stock ACT / WATCH / PASS. With
no clauses, a toolkit is ACT whenever it runs. Other cards (e.g. ``position``) are saved but not
judged. The bands below are placeholders until backtested (docs/decisions/0014).

:class:`Inputs` fetches each ticker's data once, on first use, and shares it across every
toolkit run for that ticker, so a pipeline run costs one price fetch per stock. Other
instruments a toolkit compares against (indices, ETFs, peers, FX) are fetched once per process
and shared across stocks (:meth:`Inputs.other`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from functools import cached_property
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha.analysis.events import build_pack
from solaraalpha.analysis.fundamentals import FundamentalsCard, fundamentals_card
from solaraalpha.analysis.levels import LevelsCard, levels_card
from solaraalpha.analysis.liquidity import LiquidityCard, liquidity_card
from solaraalpha.analysis.relative_strength import RelativeStrength, compare, comparisons, rs_card
from solaraalpha.analysis.technical import Position, Scorecard, assess_position, scorecard
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent
from solaraalpha.data.fundamentals import fetch_fundamentals
from solaraalpha.data.prices import fetch_daily

Cards = dict[str, Any]
_OTHERS: dict[str, pd.DataFrame] = {}  # other instruments' daily prices, fetched this process


class Inputs:
    """Data for one ticker, fetched lazily (once) and shared by the toolkits that need it."""

    def __init__(self, ticker: str, daily: pd.DataFrame | None = None) -> None:
        self.ticker = ticker
        self.others: dict[str, pd.DataFrame] = {}  # other instruments this ticker's runs used
        if daily is not None:  # already fetched by the caller (the daily job): no second fetch
            self.__dict__["daily"] = daily

    @cached_property
    def daily(self) -> pd.DataFrame:
        """Daily prices as fetched (before cleaning) — what the results DB snapshots."""
        return fetch_daily(self.ticker)

    @cached_property
    def issues(self) -> pd.DataFrame:
        return check_prices(self.daily)

    @cached_property
    def weekly(self) -> pd.DataFrame:
        """Checked, cleaned, trimmed, completed weekly bars."""
        return to_weekly(trim_to_consistent(clean_prices(self.daily), self.issues))

    @cached_property
    def fund(self) -> pd.DataFrame:
        return fetch_fundamentals(self.ticker)

    def other(self, ticker: str) -> pd.DataFrame:
        """Daily prices (as fetched) of another instrument this ticker is compared with."""
        if ticker not in _OTHERS:
            _OTHERS[ticker] = fetch_daily(ticker)
        self.others[ticker] = _OTHERS[ticker]
        return self.others[ticker]

    def fetched(self, name: str) -> pd.DataFrame | None:
        """``name`` if it has been fetched in this run, else None (never triggers a fetch)."""
        return self.__dict__.get(name)


@dataclass(frozen=True)
class Toolkit:
    id: str
    run: Callable[..., Cards]
    act: tuple[str, ...]  # clauses that must all hold for ACT
    watch: tuple[str, ...]  # clauses that must all hold for WATCH (when not ACT)
    doc: str


REGISTRY: dict[str, Toolkit] = {}


def toolkit(
    id: str, act: tuple[str, ...] = (), watch: tuple[str, ...] = ()
) -> Callable[[Callable[..., Cards]], Any]:
    """Register ``fn`` as toolkit ``id`` with its ``act`` and ``watch`` bands."""

    def register(fn: Callable[..., Cards]) -> Callable[..., Cards]:
        if id in REGISTRY:
            raise ValueError(f"toolkit {id!r} registered twice")
        doc = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
        REGISTRY[id] = Toolkit(id, fn, act, watch, doc)
        return fn

    return register


def get(id: str) -> Toolkit:
    try:
        return REGISTRY[id]
    except KeyError:
        raise KeyError(f"unknown toolkit {id!r}; registered: {', '.join(REGISTRY)}") from None


# Bands are fixed long-side screens from the story map §4 A1 (docs/decisions/0014): "a stage-2
# uptrend with positive momentum, priced near support rather than stretched above it,
# outperforming its peers, with earnings growing or being revised up, and at least 2:1 to the
# next resistance (or nothing overhead)". Placeholders until backtested.


@toolkit(
    "technical",
    act=("trend >= 0.25", "momentum > 0", "entry >= 0", "risk_pct <= 0.15"),
    watch=("trend >= 0", "momentum >= -0.25"),
)
def _technical(
    x: Inputs, side: int = 1, entry_date: str | None = None, entry_price: float | None = None
) -> Cards:
    """Weekly trend / momentum / entry scores (+ open-position assessment with entry_date)."""
    cards: Cards = {"technical": scorecard(x.weekly, side)}
    if entry_date:
        cards["position"] = assess_position(x.weekly, entry_date, side, entry_price)
    return cards


@toolkit(
    "levels",
    act=("structure > 0", "reward_risk >= 2"),  # inf (blue sky) counts
    watch=("reward_risk >= 1.5 or coil_pending == true",),
)
def _levels(x: Inputs, side: int = 1) -> Cards:
    """Support / resistance, trendlines, 52w and 5y highs, coil; stop, target, reward:risk
    (from the breakout trigger when coiled)."""
    return {"levels": levels_card(x.weekly, side)}


@toolkit(
    "relative_strength",
    act=("rs > 0",),
    watch=("rs >= -0.25", "rs_high == true or peer_top_half == true"),
)
def _relative_strength(x: Inputs) -> Cards:
    """Excess return vs market, industry and peers (config/benchmarks.toml), vol-scaled."""
    return {"relative_strength": rs_card(compare(x.weekly, comparisons(x.ticker), x.other))}


@toolkit(
    "fundamentals",
    act=("growth > 0", "quality > -0.5", "eps_revision >= 0 or eps_revision is nan"),
    watch=("quality > -0.5", "eps_revision > 0"),
)
def _fundamentals(x: Inputs, as_of: str | None = None) -> Cards:
    """Growth / value / quality scores, point-in-time via publication lags."""
    return {"fundamentals": fundamentals_card(x.fund, x.weekly, as_of)}


@toolkit("liquidity")  # no bands: information for sizing (adv_gbp_20) and the validator
def _liquidity(x: Inputs) -> Cards:
    """Median daily traded value (20 / 60 sessions), zero-volume share, spread proxy, max
    position value at max_adv_pct."""
    from solaraalpha.portfolio.rules import load_rules

    daily = trim_to_consistent(clean_prices(x.daily), x.issues)
    return {"liquidity": liquidity_card(daily, load_rules().max_adv_pct, x.weekly.index[-1])}


@dataclass(frozen=True)
class EventsCard:
    """Summary of an event pack; interpretation is the `/events` agent's job."""

    date: pd.Timestamp  # week (Friday) of the last completed weekly bar, as the other cards
    generated: pd.Timestamp  # when the pack was built (news can run past ``date``)
    announcements: int  # material filings in the window
    max_abs_z: float  # largest reaction among them (NaN: none / no prices)
    next_event: pd.Timestamp | None  # likeliest date of the next expected scheduled event
    weeks_to_next: float
    pack: str  # directory holding pack.json, for the agent
    next_event_status: str = ""  # confirmed (a fixed calendar slot) / estimated (projection), #129


CONFIRMED_WINDOW_DAYS = 2  # past filings all within this many days of each other = a fixed slot


@toolkit("events")  # no bands: always ACT once run; it is the input to research, not a gate
def _events(x: Inputs, days: int = 7, horizon: int = 16, out: str | None = None) -> Cards:
    """Event pack: recent filings with market reactions, expected events, headlines."""
    pack, path = build_pack(
        x.ticker, days=days, horizon=horizon, out=Path(out) if out else None, daily=x.daily
    )
    now = pd.Timestamp(pack["generated"])
    zs = [abs(a["z"]) for a in pack["announcements"] if a.get("z") is not None]
    nxt = min((pd.Timestamp(e["likely"]) for e in pack["expected_events"]), default=None)
    status = ""
    if nxt is not None:
        ev = min(pack["expected_events"], key=lambda e: pd.Timestamp(e["likely"]))
        width = (pd.Timestamp(ev["window_end"]) - pd.Timestamp(ev["window_start"])).days
        status = "confirmed" if width <= CONFIRMED_WINDOW_DAYS else "estimated"
    return {
        "events": EventsCard(
            date=x.weekly.index[-1],
            generated=now,
            announcements=len(pack["announcements"]),
            max_abs_z=max(zs, default=float("nan")),
            next_event=nxt,
            weeks_to_next=(nxt - now).days / 7 if nxt is not None else float("nan"),
            pack=str(path),
            next_event_status=status,
        )
    }


# The card dataclasses each toolkit produces, in the order a field is looked up (#131): a thesis
# condition on ``trend`` belongs to technical, on ``support`` to levels, on ``rs`` to relative
# strength ... ``close`` is technical's, though the daily job reads it from the latest session.
CARDS: dict[str, tuple[type, ...]] = {
    "technical": (Scorecard, Position),
    "levels": (LevelsCard,),
    "relative_strength": (RelativeStrength,),
    "fundamentals": (FundamentalsCard,),
    "liquidity": (LiquidityCard,),
    "events": (EventsCard,),
}


def owner_of(field: str) -> str | None:
    """The toolkit whose card carries ``field`` (None: no toolkit does)."""
    for tk, types in CARDS.items():
        for t in types:
            if field in {f.name for f in fields(t)}:
                return tk
    return None
