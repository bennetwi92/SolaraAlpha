"""Financing risk for one stock: cash runway, dilution and equity raises (#218, #214 F4).

On AIM a discounted placing is the commonest way a good chart turns into a -20% day, and boards
time raises into strength. This card is **context** for the PM and the agents, never a gate or a
sizing rule (ADR 0022): the PM applies its own cap in ``/pm``.

* **Runway** (when free cash flow is negative): last reported annual cash / annual FCF burn, in
  months at the period end, and what is left of it today (``runway_now_months``). Both come from
  the last *annual* accounts, so they are out of date by construction: ``cash_period`` says how
  old, and ``raised_since_period`` says whether a raise has landed since.
* **Dilution**: share count now vs about 1 and 3 years earlier.
* **Raises**: equity raises in the last 3 years from the issuer's NSM filings, by headline
  (placing, fundraise, subscription, retail offer, ...), filings within :data:`RAISE_GAP_DAYS`
  counted as one raise; ``last_raise`` is the latest raise's first filing.
* **Warrants / convertibles**: any NSM headline in 3 years mentioning them.

Point-in-time: with ``as_of`` only statements public by then (:func:`fundamentals.known`) and
filings published by then are used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from solaraalpha.analysis.fundamentals import _last, _utc, eps_ttm, known, series

YEARS = 3  # raise / headline lookback
RAISE_GAP_DAYS = 14  # filings closer than this belong to the same raise
SHARE_TOL = pd.Timedelta(days=60)  # "about N years earlier" for the share count
RAISE = re.compile(
    r"placing|fund ?rais|capital raise|equity raise|subscription|retail ?offer|primarybid"
    r"|retailbook|open offer|rights issue|bookbuild",
    re.I,
)
INSTRUMENTS = re.compile(r"warrant|convertible|loan note", re.I)


@dataclass(frozen=True)
class FinancingCard:
    """Financing view at ``date``; NaN / None where the data doesn't say."""

    date: pd.Timestamp
    currency: str  # reporting currency of cash and fcf
    loss_making: bool | None  # TTM EPS <= 0 (None: no EPS)
    cash: float  # last annual cash and equivalents
    fcf: float  # last annual free cash flow
    cash_period: pd.Timestamp | None  # period end of that cash figure
    runway_months: float  # cash / annual burn * 12 at cash_period; NaN when FCF >= 0 or unknown
    runway_now_months: float  # runway_months less the months since cash_period
    dilution_12m: float  # share count vs ~1 year earlier
    dilution_36m: float  # share count vs ~3 years earlier
    raises_3y: int | None  # equity raises in 3 years (None: filings unavailable)
    last_raise: pd.Timestamp | None
    last_raise_headline: str
    raised_since_period: bool | None  # a raise after cash_period (runway understated)
    warrants_or_convertibles: bool | None  # mentioned in a filing headline in 3 years


def change_over(s: pd.Series, years: int) -> float:
    """Last value of ``s`` vs the value about ``years`` earlier (NaN if none within
    :data:`SHARE_TOL`)."""
    if len(s) < 2:
        return np.nan
    gap = abs(s.index - (s.index[-1] - pd.DateOffset(years=years)))
    j = int(gap.argmin())
    old = float(s.iloc[j])
    return float(s.iloc[-1]) / old - 1 if gap[j] <= SHARE_TOL and old else np.nan


def dilution(fund: pd.DataFrame, years: int) -> float:
    """Share-count change over ``years``: diluted average shares (half-yearly), else the
    year-end ordinary share count."""
    got = change_over(series(fund, "trailingDilutedAverageShares"), years)
    return (
        got if not np.isnan(got) else change_over(series(fund, "annualOrdinarySharesNumber"), years)
    )


def raises(ann: pd.DataFrame) -> pd.DataFrame:
    """One row per equity raise in ``ann`` (first filing's ``published`` and ``headline``)."""
    hits = ann[ann["headline"].str.contains(RAISE, na=False)].sort_values("published")
    if hits.empty:
        return hits[["published", "headline"]]
    new = hits["published"].diff() > pd.Timedelta(days=RAISE_GAP_DAYS)
    new.iloc[0] = True
    return hits.loc[new, ["published", "headline"]].reset_index(drop=True)


def financing_card(
    fund: pd.DataFrame,
    ann: pd.DataFrame | None,
    date: pd.Timestamp,
    as_of: str | pd.Timestamp | None = None,
) -> FinancingCard:
    """The financing card at ``date`` (the last weekly bar) from statements ``fund`` and the
    issuer's NSM filings ``ann`` (None: unavailable, filing fields None)."""
    now = _utc(as_of) if as_of is not None else _utc(date)
    if as_of is not None:
        fund = known(fund, now)
        ann = ann[ann["published"] <= now] if ann is not None else None
    eps = eps_ttm(fund)
    cash, fcf = _last(fund, "annualCashAndCashEquivalents"), _last(fund, "annualFreeCashFlow")
    period = series(fund, "annualCashAndCashEquivalents")
    cash_period = period.index[-1] if len(period) else None
    runway = cash / -fcf * 12 if fcf < 0 and cash >= 0 else np.nan
    elapsed = (now - cash_period).days / 30.4 if cash_period is not None else np.nan
    raised: pd.DataFrame | None = None
    recent: pd.DataFrame | None = None
    if ann is not None:
        recent = ann[ann["published"] > now - pd.DateOffset(years=YEARS)]
        raised = raises(recent)
    last = raised.iloc[-1] if raised is not None and len(raised) else None
    return FinancingCard(
        date=date,
        currency=_currency(fund),
        loss_making=bool(eps.iloc[-1] <= 0) if len(eps) else None,
        cash=cash,
        fcf=fcf,
        cash_period=cash_period,
        runway_months=runway,
        runway_now_months=runway - elapsed,
        dilution_12m=dilution(fund, 1),
        dilution_36m=dilution(fund, 3),
        raises_3y=len(raised) if raised is not None else None,
        last_raise=last["published"] if last is not None else None,
        last_raise_headline=str(last["headline"]) if last is not None else "",
        raised_since_period=None
        if raised is None
        else bool(cash_period is not None and (raised["published"] > cash_period).any()),
        warrants_or_convertibles=None
        if recent is None
        else bool(recent["headline"].str.contains(INSTRUMENTS, na=False).any()),
    )


def _currency(fund: pd.DataFrame) -> str:
    cur = fund.loc[fund["field"] == "annualCashAndCashEquivalents", "currency"]
    return str(cur.iloc[-1]) if len(cur) else ""
