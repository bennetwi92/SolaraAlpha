"""Fundamentals model for a single stock, aimed at long trades held ~2-16 weeks.

Input is the tidy frame from :func:`solaraalpha.data.fundamentals.fetch_fundamentals` plus the
weekly price frame from :func:`solaraalpha.data.clean.to_weekly` (for valuation). Three
pillars, each scored in ``[-1, +1]`` (+ = favourable for a long):

* **growth** — earnings momentum, the fundamental driver of multi-week bursts: TTM EPS growth
  year on year and its acceleration, revenue growth, the growth analysts expect (trailing P/E
  over forward P/E) and recent revisions to that forward estimate.
* **value** — re-rating room: earnings and free-cash-flow yields, and today's earnings yield
  against the stock's own history.
* **quality** — what can blow up a trade: return on equity, net debt / EBITDA, cash conversion
  and share-count dilution.

**No look-ahead.** Statement figures are dated at the period end but published later, so
:func:`known` makes them usable only once announced: from the period's results announcement
when :func:`stamp_published` has matched one from the issuer's filings (FCA NSM), else after
the statutory reporting lag (``ANNUAL_LAG``, ``INTERIM_LAG``). Market fields (P/E, forward
P/E, market cap) are known on their observation date. :func:`fundamentals_card` with ``as_of``
applies those dates; without it (the live view) everything fetched counts, since it has all
been published by now.

Unlike the technical and levels models this one is stock-specific in use (accounting quirks
differ by company), but the scoring here is generic; per-stock adjustments are added only when
needed. Parameters are documented in ``docs/decisions/0005-fundamentals.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from solaraalpha.log import get_logger

log = get_logger(__name__)

ANNUAL_LAG = pd.DateOffset(months=6)  # AIM rule 19 (Main Market: 4 months)
INTERIM_LAG = pd.DateOffset(months=3)  # AIM rule 18 / DTR 4.2 half-yearly reports
YEAR_TOL = pd.Timedelta(days=20)  # "a year earlier" may be off by a few days (52/53-week years)
FRESH_WEEKS = 13  # market observations older than this are ignored
REVISION_WEEKS = (4, 26)  # forward-estimate revision measured over this many weeks
MIN_HISTORY = 52  # weeks of earnings-yield history needed for the own-history percentile
FULL_GROWTH = 0.20  # 20% growth (or acceleration) scores +1, -20% scores -1
FULL_REVISION = 0.10  # forward earnings revised up 10% scores +1
NEUTRAL_YIELD = 0.05  # earnings / FCF yield scoring 0 (P/E 20); 10% scores +1, 0% scores -1
FULL_ROE = 0.15  # ROE of 15% scores +1, 0% scores -1
MAX_LEVERAGE = 3.0  # net debt / EBITDA scoring -1; net cash scores +1
FULL_DILUTION = 0.05  # 5% more shares in a year scores -1 (5% bought back scores +1)
# Price quoted in a minor unit: (major currency, units per major).
MINOR_UNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}
MARKET_FIELDS = ("trailingPeRatio", "trailingForwardPeRatio", "trailingMarketCap")
# NSM type codes of the announcement that publishes a period's figures, and how long after the
# period end to look for it (past the statutory lag, so a late reporter is matched, not assumed).
ANNUAL_RESULTS = frozenset({"FR", "PRE", "ACS"})  # final / preliminary results, annual report
INTERIM_RESULTS = frozenset({"IR"})  # half-year (interim) report
ANNUAL_WINDOW = pd.DateOffset(months=12)
INTERIM_WINDOW = pd.DateOffset(months=6)


def series(fund: pd.DataFrame, field: str) -> pd.Series:
    """One field as a date-indexed series (empty if absent)."""
    f = fund[fund["field"] == field]
    return pd.Series(f["value"].to_numpy(), index=pd.DatetimeIndex(f["date"]), name=field)


def _annual(fund: pd.DataFrame) -> pd.Series:
    """Rows dated at a fiscal-year end (``annual*`` fields and trailing items on those dates)."""
    annual_ends = set(fund.loc[fund["field"].str.startswith("annual"), "date"])
    return fund["field"].str.startswith("annual") | fund["date"].isin(annual_ends)


def usable_from(fund: pd.DataFrame) -> pd.Series:
    """Date from which each row may be used: observation date for market fields; for statement
    figures the ``published`` date when :func:`stamp_published` found one, else period end plus
    ``ANNUAL_LAG`` for fiscal-year figures, ``INTERIM_LAG`` for interim ones."""
    market = fund["field"].isin(MARKET_FIELDS)
    annual = _annual(fund)
    lagged = fund["date"].where(~annual, fund["date"] + ANNUAL_LAG)
    lagged = lagged.where(annual, fund["date"] + INTERIM_LAG)
    if "published" in fund:
        lagged = fund["published"].where(fund["published"].notna(), lagged)
    return fund["date"].where(market, lagged)


def stamp_published(fund: pd.DataFrame, filings: pd.DataFrame) -> pd.DataFrame:
    """``fund`` with a ``published`` column: when each statement period's figures were announced.

    ``filings`` is the issuer's NSM frame (:func:`solaraalpha.data.announcements.
    fetch_announcements`). A fiscal-year period is matched to the first final / preliminary
    results or annual report (``ANNUAL_RESULTS``) published within ``ANNUAL_WINDOW`` after the
    period end, an interim one to the first half-year report within ``INTERIM_WINDOW``. Market
    fields and unmatched periods stay NaT, so :func:`usable_from` falls back to the lag.
    """
    out = fund.copy()
    out["published"] = pd.Series(pd.NaT, index=out.index, dtype=out["date"].dtype)
    if filings.empty:
        return out
    statements = ~out["field"].isin(MARKET_FIELDS)
    annual_ends = set(out.loc[_annual(out), "date"])
    for end in out.loc[statements, "date"].unique():
        codes, window = (
            (ANNUAL_RESULTS, ANNUAL_WINDOW)
            if end in annual_ends
            else (INTERIM_RESULTS, INTERIM_WINDOW)
        )
        pub = filings.loc[filings["type_code"].isin(codes), "published"]
        pub = pub[(pub > end) & (pub <= end + window)]
        if len(pub):
            out.loc[statements & (out["date"] == end), "published"] = pub.min()
    return out


def known(fund: pd.DataFrame, as_of: str | pd.Timestamp) -> pd.DataFrame:
    """Rows of ``fund`` that were public by ``as_of`` (see :func:`usable_from`)."""
    return fund[usable_from(fund) <= _utc(as_of)]


def _utc(ts: str | pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t


def _growth(new: float, old: float) -> float:
    return (new - old) / abs(old) if old else np.nan


def yoy(s: pd.Series, i: int = -1) -> float:
    """Growth of ``s`` at position ``i`` over the value about a year earlier (NaN if none)."""
    if len(s) < 2:
        return np.nan
    date = s.index[i]
    gap = abs(s.index - (date - pd.DateOffset(years=1)))
    j = int(gap.argmin())
    return _growth(float(s.iloc[i]), float(s.iloc[j])) if gap[j] <= YEAR_TOL else np.nan


def _last(fund: pd.DataFrame, field: str) -> float:
    s = series(fund, field)
    return float(s.iloc[-1]) if len(s) else np.nan


def eps_ttm(fund: pd.DataFrame) -> pd.Series:
    """Trailing-twelve-month diluted EPS at each period end: the ``trailingDilutedEPS`` series,
    filled in with fiscal-year EPS where it has no point."""
    trailing, annual = series(fund, "trailingDilutedEPS"), series(fund, "annualDilutedEPS")
    return pd.concat([trailing, annual[~annual.index.isin(trailing.index)]]).sort_index()


def price_in(currency: str, close: float, price_currency: str | None) -> float:
    """``close`` converted to the reporting ``currency`` (pence to pounds etc.); NaN if the
    price is in a different currency altogether (no FX conversion)."""
    major, per = MINOR_UNITS.get(price_currency or "", (price_currency, 1))
    if major != currency:
        log.warning("price in %s, accounts in %s: valuation skipped", price_currency, currency)
        return np.nan
    return close / per


def reporting_currency(fund: pd.DataFrame) -> str:
    cur = fund.loc[fund["currency"] != "", "currency"]
    return str(cur.mode().iloc[0]) if len(cur) else ""


def earnings_yield_history(fund: pd.DataFrame, weekly: pd.DataFrame) -> pd.Series:
    """Weekly TTM earnings yield (EPS / price), using only EPS public by each week (lagged)."""
    eps = eps_ttm(fund)
    if eps.empty:
        return pd.Series(np.nan, index=weekly.index, name="earnings_yield")
    rows = fund[fund["field"].isin(["trailingDilutedEPS", "annualDilutedEPS"])]
    avail = pd.Series(usable_from(fund).loc[rows.index].to_numpy(), index=rows["date"])
    avail = avail.groupby(level=0).min().reindex(eps.index)
    pit = pd.Series(eps.to_numpy(), index=pd.DatetimeIndex(avail.to_numpy())).sort_index()
    pos = pit.index.searchsorted(weekly.index, side="right") - 1
    eps_w = np.where(pos >= 0, pit.to_numpy()[pos.clip(0)], np.nan)
    price = price_in(reporting_currency(fund), 1.0, weekly.attrs.get("currency"))
    return pd.Series(
        eps_w / (weekly["close"].to_numpy() * price), weekly.index, name="earnings_yield"
    )


def metrics(fund: pd.DataFrame, weekly: pd.DataFrame) -> dict[str, float]:
    """Raw fundamental metrics from all rows of ``fund``, valued at the last weekly close."""
    as_of = weekly.index[-1]
    m: dict[str, float] = {}

    eps = eps_ttm(fund)
    m["eps_ttm"] = float(eps.iloc[-1]) if len(eps) else np.nan
    m["eps_growth"] = yoy(eps)
    m["eps_accel"] = m["eps_growth"] - yoy(eps, -2) if len(eps) > 2 else np.nan
    m["rev_growth"] = yoy(series(fund, "annualTotalRevenue"))

    ratios = pd.concat(
        [series(fund, f) for f in MARKET_FIELDS], axis=1, keys=["pe", "fwd_pe", "mcap"], sort=True
    )
    ratios = ratios[ratios.index <= as_of]
    both = ratios.dropna(subset=["pe", "fwd_pe"])
    fresh = len(both) > 0 and as_of - both.index[-1] <= pd.Timedelta(weeks=FRESH_WEEKS)
    m["fwd_eps_growth"] = (
        float(both["pe"].iloc[-1] / both["fwd_pe"].iloc[-1] - 1) if fresh else np.nan
    )
    m["eps_revision"] = np.nan
    fwd = (ratios["mcap"] / ratios["fwd_pe"]).dropna()  # forward earnings, in money
    if fresh and len(fwd):
        lo, hi = (fwd.index[-1] - pd.Timedelta(weeks=w) for w in REVISION_WEEKS[::-1])
        base = fwd[(fwd.index >= lo) & (fwd.index <= hi)]
        if len(base):
            m["eps_revision"] = _growth(float(fwd.iloc[-1]), float(base.iloc[0]))

    price = price_in(
        reporting_currency(fund), float(weekly["close"].iloc[-1]), weekly.attrs.get("currency")
    )
    ey = earnings_yield_history(fund, weekly).dropna()
    m["earnings_yield"] = m["eps_ttm"] / price
    m["pe"] = price / m["eps_ttm"] if m["eps_ttm"] > 0 else np.nan
    hist = ey.iloc[:-1]
    m["ey_percentile"] = (
        float((hist < m["earnings_yield"]).mean()) if len(hist) >= MIN_HISTORY else np.nan
    )
    fcf, ni = _last(fund, "annualFreeCashFlow"), _last(fund, "annualNetIncome")
    m["market_cap"] = price * _last(fund, "annualOrdinarySharesNumber")
    m["fcf_yield"] = fcf / m["market_cap"]

    ebitda = _last(fund, "annualEBITDA")
    net_debt = _last(fund, "annualTotalDebt") - _last(fund, "annualCashAndCashEquivalents")
    m["roe"] = ni / _last(fund, "annualStockholdersEquity")
    m["net_debt_ebitda"] = net_debt / ebitda if ebitda > 0 else (np.inf if net_debt > 0 else np.nan)
    m["fcf_conversion"] = fcf / ni if ni > 0 else np.nan
    shares = series(fund, "trailingDilutedAverageShares")
    m["share_change"] = yoy(shares if len(shares) else series(fund, "annualOrdinarySharesNumber"))
    return m


def _score(parts: list[float]) -> float:
    x = np.clip([p for p in parts if not np.isnan(p)], -1, 1)
    return float(x.mean()) if len(x) else np.nan


def pillar_scores(m: dict[str, float]) -> dict[str, float]:
    """Growth / value / quality in [-1, 1] from :func:`metrics`; each is the mean of whichever
    sub-signals are available (fundamental data is patchy), NaN if none are."""
    nd = m["net_debt_ebitda"]
    return {
        "growth": _score(
            [
                m["eps_growth"] / FULL_GROWTH,
                m["eps_accel"] / FULL_GROWTH,
                m["rev_growth"] / FULL_GROWTH,
                m["fwd_eps_growth"] / FULL_GROWTH,
                m["eps_revision"] / FULL_REVISION,
            ]
        ),
        "value": _score(
            [
                (m["earnings_yield"] - NEUTRAL_YIELD) / NEUTRAL_YIELD,
                (m["fcf_yield"] - NEUTRAL_YIELD) / NEUTRAL_YIELD,
                2 * m["ey_percentile"] - 1,
            ]
        ),
        "quality": _score(
            [
                (m["roe"] - FULL_ROE / 2) / (FULL_ROE / 2),
                1 - 2 * max(nd, 0) / MAX_LEVERAGE if not np.isnan(nd) else np.nan,
                2 * m["fcf_conversion"] - 1,
                -m["share_change"] / FULL_DILUTION,
            ]
        ),
    }


@dataclass(frozen=True)
class FundamentalsCard:
    """Fundamental view at ``date`` for the overall scorecard; missing inputs are NaN."""

    date: pd.Timestamp
    currency: str  # reporting currency of the accounts
    last_period: pd.Timestamp  # latest reporting period end used
    next_period: pd.Timestamp  # expected end of the next reporting period (results follow)
    close: float  # last weekly close, in the listing currency
    eps_ttm: float
    eps_growth: float  # TTM EPS vs a year earlier
    eps_accel: float  # change in that growth since the previous period
    rev_growth: float  # fiscal-year revenue vs the year before
    fwd_eps_growth: float  # analysts' forward EPS vs TTM (trailing P/E / forward P/E - 1)
    eps_revision: float  # change in forward earnings over the last 4-26 weeks
    pe: float  # NaN when EPS <= 0
    earnings_yield: float
    ey_percentile: float  # today's earnings yield vs its own weekly history (1 = cheapest)
    fcf_yield: float
    market_cap: float  # in the reporting currency
    roe: float
    net_debt_ebitda: float  # negative = net cash
    fcf_conversion: float  # free cash flow / net income
    share_change: float  # diluted share count vs a year earlier
    growth: float  # [-1, 1]
    value: float  # [-1, 1]
    quality: float  # [-1, 1]


def fundamentals_card(
    fund: pd.DataFrame, weekly: pd.DataFrame, as_of: str | pd.Timestamp | None = None
) -> FundamentalsCard:
    """Fundamental metrics and pillar scores as of the last week in ``weekly``.

    With ``as_of`` (a past date), prices are cut to weeks ending on or before it and only
    fundamentals public by then are used (:func:`known`). Without it, all of ``fund`` is used.
    """
    if as_of is not None:
        weekly = weekly[weekly.index <= _utc(as_of)]
        fund = known(fund, _utc(as_of))
    if weekly.empty:
        raise ValueError("no weekly prices on or before as_of")
    statements = fund[~fund["field"].isin(MARKET_FIELDS)]
    if statements.empty:
        raise ValueError("no published fundamentals")
    m = metrics(fund, weekly)
    eps = eps_ttm(fund)
    step = eps.index[-1] - eps.index[-2] if len(eps) > 1 else pd.Timedelta(days=365)
    months = max(1, round(step.days / 30.4))
    last = statements["date"].max()
    return FundamentalsCard(
        date=weekly.index[-1],
        currency=reporting_currency(fund),
        last_period=last,
        next_period=last + pd.DateOffset(months=months) + pd.offsets.MonthEnd(0),
        close=float(weekly["close"].iloc[-1]),
        **m,
        **pillar_scores(m),
    )
