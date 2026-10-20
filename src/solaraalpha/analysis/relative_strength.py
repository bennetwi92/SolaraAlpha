"""Relative strength of one stock against its market, industry and peers, on weekly bars.

What to compare against comes from ``config/benchmarks.toml`` (:func:`comparisons`):

* **market** — the broad index of the stock's home market; defaults by listing suffix
  (``.L`` → FTSE All-Share), overridable per stock.
* **industry** — the stock's sector index, or the closest sector ETF when Yahoo has no history
  for the index. Optional.
* **peers** — listed companies in the same business, any exchange, compared as an
  equal-weight basket (rebalanced weekly) and ranked one by one. Optional.

Every comparison is scored the same way. Weekly log excess return ``e`` of the stock over the
benchmark (total return via ``adj_close``; both in the stock's currency — other currencies are
converted at weekly FX closes). Over each horizon ``h`` in :data:`HORIZONS` the summed excess
is divided by the tracking error expected over ``h`` weeks (``std(e)`` over the last
:data:`TE_WEEKS`, times √h): an information-ratio t-stat, clipped to [-1, +1], so one standard
deviation of outperformance scores a full +1 (as the technical model's 12-1 momentum). A
comparison's score is the mean over horizons; ``rs`` is the mean over the comparisons
available. Each week uses only that week's and earlier closes.

The card also carries each comparison's per-horizon z (``market_z4`` ... ``peers_z26``,
unclipped), so the agents can tell a 4-week spike (which tends to reverse) from 26-week
leadership, and two flags for the agents, never for the rank (docs/research/0001; #210):
``leadership`` (RS line at its 52-week high in an uptrend, or the 26-week market leg at its
cap) and ``rs_spike`` (a short-term RS spike outside an uptrend). Both read the technical
model's ``trend``, which the toolkit passes in.

Parameters and choices: ``docs/decisions/0011-relative-strength.md``.
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from solaraalpha.config import REPO_ROOT
from solaraalpha.data.clean import (
    CLOSE_ONLY_BLOCKING,
    check_prices,
    clean_prices,
    to_weekly,
    trim_to_consistent,
)
from solaraalpha.log import get_logger

log = get_logger(__name__)

BENCHMARKS = REPO_ROOT / "config" / "benchmarks.toml"
HORIZONS = (4, 13, 26)  # weeks of excess return scored: ~1, 3 and 6 months
TE_WEEKS = 52  # tracking-error window
HIGH_WEEKS = 52  # RS line "new high" lookback
RANK_WEEKS = 13  # peer ranking horizon
KINDS = ("market", "industry", "peers")
# Agent flags (docs/research/0001, #210 phase 0); placeholders like every threshold here.
UPTREND = 0.25  # technical trend at or above this = an uptrend (as the technical ACT band)
SPIKE_Z = 0.5  # 4-week market z at or above this, outside an uptrend = rs_spike
Z_CAP = 1.0  # a leg's z is clipped here in the score; at or above it = "at its cap"
# Minor currency units -> major; the scale cancels in returns, only the currency matters.
_MAJOR = {"GBp": "GBP", "GBX": "GBP", "ILA": "ILS", "ZAc": "ZAR"}

Fetch = Callable[[str], pd.DataFrame]  # Yahoo ticker -> daily OHLCV, as fetch_daily


@dataclass(frozen=True)
class Comparisons:
    """What one stock is compared against (Yahoo tickers)."""

    market: str | None
    industry: str | None = None
    peers: tuple[str, ...] = ()


def comparisons(ticker: str, path: Path = BENCHMARKS) -> Comparisons:
    """The stock's comparisons from ``path``: its own entry, market defaulted by suffix."""
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    own = cfg.get("stocks", {}).get(ticker, {})
    suffix = "." + ticker.rsplit(".", 1)[1] if "." in ticker else ""
    market = own.get("market", cfg.get("market", {}).get(suffix))
    return Comparisons(market or None, own.get("industry") or None, tuple(own.get("peers", ())))


def weekly_close(daily: pd.DataFrame) -> pd.Series:
    """Weekly ``adj_close`` of a comparison series: checked, cleaned and trimmed on the issues
    that matter for close-to-close returns (:data:`CLOSE_ONLY_BLOCKING`)."""
    issues = check_prices(daily)
    weekly = to_weekly(trim_to_consistent(clean_prices(daily), issues, CLOSE_ONLY_BLOCKING))
    return weekly["adj_close"]


def major(currency: str | None) -> str | None:
    return _MAJOR.get(currency, currency) if currency else None


def in_currency(ticker: str, fetch: Fetch, to: str | None) -> pd.Series:
    """Weekly prices of ``ticker`` converted to currency ``to`` (Yahoo FX ``<FROM><TO>=X``)."""
    daily = fetch(ticker)
    prices = weekly_close(daily)
    src = major(daily.attrs.get("currency"))
    if src and to and src != to:
        fx = weekly_close(fetch(f"{src}{to}=X"))
        prices = (prices * fx).dropna()
    elif not src or not to:
        log.warning("%s: currency unknown; compared unconverted", ticker)
    return prices.rename(ticker)


def basket(prices: list[pd.Series]) -> pd.Series:
    """Equal-weight basket of ``prices``, rebalanced weekly, as an index starting at 1. Each
    week averages the members with a return that week, so members may start late."""
    wide = pd.concat(prices, axis=1).sort_index()
    rets = wide.pct_change(fill_method=None).mean(axis=1)
    start = wide.dropna(how="all").index[0]
    return (1 + rets.loc[start:].fillna(0)).cumprod().rename("basket")


def relative_strength(stock: pd.Series, bench: pd.Series) -> pd.DataFrame:
    """Weekly relative strength of ``stock`` against ``bench`` (same currency), on the weeks
    both have.

    Columns: ``excess`` (weekly log excess return), ``rs`` (RS line, stock / bench rebased to 1),
    ``te`` (weekly tracking error over :data:`TE_WEEKS`), ``x_h`` (log excess over ``h`` weeks),
    ``z_h`` (``x_h / (te·√h)``), ``score`` (mean of the clipped ``z_h``, [-1, 1]; NaN during
    warm-up) and ``rs_high`` (RS line at its :data:`HIGH_WEEKS` high).
    """
    both = pd.concat([stock, bench], axis=1, join="inner").dropna()
    logret = both.apply(np.log).diff()
    out = pd.DataFrame(index=both.index)
    out["excess"] = logret.iloc[:, 0] - logret.iloc[:, 1]
    out["rs"] = np.exp(out["excess"].fillna(0).cumsum())
    out["te"] = out["excess"].rolling(TE_WEEKS, min_periods=TE_WEEKS).std()
    zs = []
    for h in HORIZONS:
        out[f"x_{h}"] = out["excess"].rolling(h, min_periods=h).sum()
        out[f"z_{h}"] = out[f"x_{h}"] / (out["te"] * np.sqrt(h))
        zs.append(out[f"z_{h}"].clip(-1, 1))
    out["score"] = pd.concat(zs, axis=1).mean(axis=1, skipna=False)
    out["rs_high"] = out["rs"] >= out["rs"].rolling(HIGH_WEEKS, min_periods=HIGH_WEEKS).max()
    return out


@dataclass(frozen=True)
class Compared:
    """A stock's prices and its relative strength against each comparison."""

    stock: pd.Series  # weekly adj_close, stock's currency
    frames: dict[str, pd.DataFrame]  # kind -> relative_strength(); kinds that loaded only
    used: dict[str, str]  # kind -> what it was compared against
    peers: dict[str, pd.Series]  # peer -> weekly prices in the stock's currency


def compare(weekly: pd.DataFrame, comp: Comparisons, fetch: Fetch) -> Compared:
    """Relative strength of the stock (``weekly`` from ``to_weekly``) against ``comp``.

    A comparison that fails to load is logged and left out, so one bad source doesn't stop
    the rest.
    """
    stock = weekly["adj_close"]
    cur = major(weekly.attrs.get("currency"))

    def load(ticker: str) -> pd.Series | None:
        try:
            return in_currency(ticker, fetch, cur)
        except Exception as e:
            log.warning("%s: comparison not loaded (%s: %s)", ticker, type(e).__name__, e)
            return None

    benches: dict[str, pd.Series] = {}
    used: dict[str, str] = {}
    for kind, ticker in (("market", comp.market), ("industry", comp.industry)):
        if ticker and (s := load(ticker)) is not None:
            benches[kind], used[kind] = s, ticker
    peers = {p: s for p in comp.peers if (s := load(p)) is not None}
    if peers:
        benches["peers"], used["peers"] = basket(list(peers.values())), ",".join(peers)
    frames = {k: relative_strength(stock, b) for k, b in benches.items()}
    return Compared(stock, frames, used, peers)


@dataclass(frozen=True)
class RelativeStrength:
    """Latest-week relative strength; scores in [-1, 1], + outperforming."""

    date: pd.Timestamp
    rs: float  # mean of the available comparison scores
    market: float  # NaN: comparison not available
    industry: float
    peers: float  # vs the equal-weight peer basket
    market_13w: float  # 13-week excess return over the market, e.g. 0.05 = 5% ahead
    industry_13w: float
    peers_13w: float
    rs_high: bool  # RS line vs the market at its 52-week high (leadership)
    peer_rank: int | None  # 1 = best 13-week return among the stock and its peers
    peer_count: int  # peers ranked (excluding the stock)
    peer_top_half: bool  # peer_rank <= ceil((peer_count + 1) / 2); False without a rank
    compared: str  # e.g. "market=^FTAS industry=EXV9.DE peers=EZJ.L,IAG.L"
    # Per-horizon z per comparison (x_h / (te * sqrt(h)), unclipped; NaN: not available)
    market_z4: float = float("nan")
    market_z13: float = float("nan")
    market_z26: float = float("nan")
    industry_z4: float = float("nan")
    industry_z13: float = float("nan")
    industry_z26: float = float("nan")
    peers_z4: float = float("nan")
    peers_z13: float = float("nan")
    peers_z26: float = float("nan")
    # Agent context, not in the rank (docs/research/0001). False when trend is unknown.
    leadership: bool = False  # (rs_high and trend >= UPTREND) or market_z26 >= Z_CAP
    rs_spike: bool = False  # market_z4 >= SPIKE_Z and trend < UPTREND
    context: str = ""  # the flags in plain words, for the agents


def flags(rs_high: bool, z4: float, z26: float, trend: float) -> tuple[bool, bool, str]:
    """``(leadership, rs_spike, context)`` from the market comparison and technical ``trend``.

    Leadership: the RS line at its 52-week high in an uptrend (``trend >= UPTREND``), or the
    26-week market leg at its cap (``z26 >= Z_CAP``). Spike: ``z4 >= SPIKE_Z`` with ``trend <
    UPTREND``. NaN inputs fail their condition, so an unknown trend sets neither trend clause."""
    up = bool(trend >= UPTREND)  # NaN compares False
    lead = bool((rs_high and up) or z26 >= Z_CAP)
    spike = bool(z4 >= SPIKE_Z and trend < UPTREND)
    words = []
    if lead:
        words.append(
            "leadership: sustained outperformance of the market (RS line high in an"
            " uptrend, or 26-week leg at its cap)"
        )
    if spike:
        words.append(
            "short-term RS spike outside an uptrend: recent outperformance without a"
            " trend tends to give some back over 4-8 weeks; check the catalyst and"
            " whether it is priced in"
        )
    return lead, spike, "; ".join(words)


def rs_card(c: Compared, trend: float = float("nan")) -> RelativeStrength:
    """Summarise :func:`compare` at the stock's latest week (a comparison without that week,
    e.g. a stale source, counts as unavailable). ``trend`` is the technical model's trend score
    for the same week (it feeds only the agent flags; NaN leaves their trend clauses false)."""
    date = c.stock.index[-1]
    last = {k: f.loc[date] for k, f in c.frames.items() if date in f.index}
    score = {k: float(last[k]["score"]) if k in last else float("nan") for k in KINDS}
    x13 = {k: float(np.exp(last[k]["x_13"]) - 1) if k in last else float("nan") for k in KINDS}
    z = {
        f"{k}_z{h}": float(last[k][f"z_{h}"]) if k in last else float("nan")
        for k in KINDS
        for h in HORIZONS
    }
    rs_high = bool("market" in last and last["market"]["rs_high"])
    lead, spike, context = flags(rs_high, z["market_z4"], z["market_z26"], trend)
    found = [v for v in score.values() if not np.isnan(v)]

    rets = {
        t: s.reindex(c.stock.index).pct_change(RANK_WEEKS, fill_method=None).iloc[-1]
        for t, s in c.peers.items()
    }
    rets = {t: r for t, r in rets.items() if not np.isnan(r)}
    own = c.stock.pct_change(RANK_WEEKS, fill_method=None).iloc[-1]
    rank = 1 + int(sum(r > own for r in rets.values())) if rets and not np.isnan(own) else None

    return RelativeStrength(
        date=date,
        rs=float(np.mean(found)) if found else float("nan"),
        market=score["market"],
        industry=score["industry"],
        peers=score["peers"],
        market_13w=x13["market"],
        industry_13w=x13["industry"],
        peers_13w=x13["peers"],
        rs_high=rs_high,
        peer_rank=rank,
        peer_count=len(rets),
        peer_top_half=rank is not None and rank <= math.ceil((len(rets) + 1) / 2),
        compared=" ".join(f"{k}={v}" for k, v in c.used.items()),
        market_z4=z["market_z4"],
        market_z13=z["market_z13"],
        market_z26=z["market_z26"],
        industry_z4=z["industry_z4"],
        industry_z13=z["industry_z13"],
        industry_z26=z["industry_z26"],
        peers_z4=z["peers_z4"],
        peers_z13=z["peers_z13"],
        peers_z26=z["peers_z26"],
        leadership=lead,
        rs_spike=spike,
        context=context,
    )
