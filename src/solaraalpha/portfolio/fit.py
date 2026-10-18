"""Portfolio fit: does a good set-up make a good addition to the book?

Pure functions. Candidates (trade plans) are taken in rank order, and each accepted plan is
added to the running totals before the next is checked, so when the slots run out the
lowest-ranked candidates are the ones left out. Breaches reject a plan (``rejected_by_fit``
with the reason); correlation with a holding and an event clash only flag it. See
docs/decisions/0015 "Portfolio fit".
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from solaraalpha.portfolio.plan import TradePlan
from solaraalpha.portfolio.risk import Equity, sector_of
from solaraalpha.portfolio.rules import Rules

# ADR 0022: reward:risk (levels setup) is not an estimate we rank on and relative strength is
# context for the agents (docs/research/0001), so the score is trend, compression and growth.
RANK_FIELDS = (
    ("technical", "trend"),
    ("levels", "compression"),
    ("fundamentals", "growth"),
)
CORRELATION_WEEKS = 26
CORRELATION_FLAG = 0.7  # placeholder: weekly-return correlation with a holding above this flags


@dataclass(frozen=True)
class FitReport:
    accepted: list[TradePlan]
    rejected: list[tuple[TradePlan, str]]
    flags: dict[str, list[str]]  # ticker -> flags (accepted and rejected alike)
    ranked: list[str]  # candidates in the order they were considered
    drawdown: float | None = None  # equity vs its high when known (<= 0), #143
    breaker: bool = False  # the circuit breaker rejected every plan


def rank_score(cards: Mapping[str, Any]) -> float:
    """Placeholder rank: mean of technical ``trend``, levels ``compression`` and fundamentals
    ``growth`` over the cards present, ignoring NaN (NaN if none)."""
    vals = []
    for model, field in RANK_FIELDS:
        card = cards.get(model)
        if card is None:
            continue
        v = card.get(field) if isinstance(card, Mapping) else getattr(card, field, None)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            vals.append(float(v))
    return sum(vals) / len(vals) if vals else math.nan


def _sum(df: pd.DataFrame, col: str, where: pd.Series | None = None) -> float:
    if df is None or df.empty or col not in df:
        return 0.0
    s = df[col] if where is None else df.loc[where, col]
    return float(s.fillna(0).sum())


def fit(
    plans: list[TradePlan],
    holdings: pd.DataFrame,
    pending: pd.DataFrame,
    equity: Equity,
    rules: Rules,
    scores: Mapping[str, float],
    returns: Mapping[str, pd.Series] | None = None,
    sectors: Mapping[str, str] | None = None,
    drawdown: float | None = None,
) -> FitReport:
    """Check ``plans`` against the book in rank order (highest ``scores`` first, ties by ticker).

    ``holdings`` is :func:`solaraalpha.portfolio.risk.holdings` (open positions: ticker, value,
    risk, sector); ``pending`` holds draft / approved positions not yet open with the same
    columns (planned value and risk). ``returns`` are weekly returns per ticker (candidates and
    holdings) for the correlation flag; ``sectors`` overrides :func:`sector_of`.

    Checks: **slots** (open + pending + accepted < ``max_positions``), **sector** (sector value
    after adding <= ``max_sector_pct`` x equity), **heat** ((open + pending + accepted + plan
    risk) / equity <= ``max_heat``); a plan sized to zero shares is rejected too. **Correlation**
    (26-week weekly-return correlation with a holding > 0.7) and the plan's **event** flag only
    flag. **Circuit breaker** (#143): with ``rules.circuit_breaker`` on and ``drawdown`` (equity
    vs its high, <= 0) below ``-rules.max_drawdown_new_entries``, every plan is rejected with
    ``circuit breaker: drawdown X% > Y%``; open positions are untouched.
    """
    tripped = (
        rules.circuit_breaker
        and drawdown is not None
        and not math.isnan(drawdown)
        and drawdown < -rules.max_drawdown_new_entries
    )

    def sector(ticker: str) -> str:
        return (sectors or {}).get(ticker) or sector_of(ticker)

    order = sorted(
        plans,
        key=lambda p: (
            -(
                scores.get(p.ticker, math.nan)
                if not math.isnan(scores.get(p.ticker, math.nan))
                else -math.inf
            ),
            p.ticker,
        ),
    )
    n_open = len(holdings) if holdings is not None else 0
    n_pending = len(pending) if pending is not None else 0
    risk_total = _sum(holdings, "risk") + _sum(pending, "risk")
    by_sector: dict[str, float] = {}
    for df in (holdings, pending):
        if df is not None and not df.empty:
            for sec, val in df.groupby("sector")["value"].sum().items():
                by_sector[str(sec)] = by_sector.get(str(sec), 0.0) + float(val)

    accepted: list[TradePlan] = []
    rejected: list[tuple[TradePlan, str]] = []
    flags: dict[str, list[str]] = {}
    for plan in order:
        fl = [f for f in plan.flags if f.startswith("event within")]
        for held, corr in _correlations(plan.ticker, holdings, returns):
            fl.append(f"correlated with {held} ({corr:.2f})")
        if fl:
            flags[plan.ticker] = fl
        sec = sector(plan.ticker)
        reason = None
        if tripped:
            reason = (
                f"circuit breaker: drawdown {-float(drawdown or 0):.0%} >"
                f" {rules.max_drawdown_new_entries:.0%}"
            )
        elif plan.shares <= 0:
            reason = "size zero"
        elif n_open + n_pending + len(accepted) >= rules.max_positions:
            reason = (
                f"no free slot: {n_open} open + {n_pending} pending + {len(accepted)} accepted"
                f" >= max_positions {rules.max_positions}"
            )
        elif (
            equity.equity > 0
            and (by_sector.get(sec, 0.0) + plan.position_value) / equity.equity
            > rules.max_sector_pct
        ):
            share = (by_sector.get(sec, 0.0) + plan.position_value) / equity.equity
            reason = (
                f"sector {sec} would be {share:.0%} > max_sector_pct {rules.max_sector_pct:.0%}"
            )
        elif equity.equity > 0 and (risk_total + plan.risk_amount) / equity.equity > rules.max_heat:
            heat = (risk_total + plan.risk_amount) / equity.equity
            reason = f"heat would be {heat:.1%} > max_heat {rules.max_heat:.1%}"
        if reason:
            rejected.append((plan, f"rejected_by_fit: {reason}"))
            continue
        accepted.append(plan)
        risk_total += plan.risk_amount
        by_sector[sec] = by_sector.get(sec, 0.0) + plan.position_value
    return FitReport(accepted, rejected, flags, [p.ticker for p in order], drawdown, bool(tripped))


def _correlations(
    ticker: str, holdings: pd.DataFrame, returns: Mapping[str, pd.Series] | None
) -> list[tuple[str, float]]:
    if not returns or ticker not in returns or holdings is None or holdings.empty:
        return []
    own = returns[ticker].dropna().iloc[-CORRELATION_WEEKS:]
    out = []
    for held in holdings["ticker"]:
        other = returns.get(str(held))
        if other is None or held == ticker:
            continue
        both = pd.concat([own, other.dropna().iloc[-CORRELATION_WEEKS:]], axis=1, join="inner")
        if len(both) < 8:
            continue
        corr = float(both.iloc[:, 0].corr(both.iloc[:, 1]))
        if corr > CORRELATION_FLAG:
            out.append((str(held), corr))
    return out
