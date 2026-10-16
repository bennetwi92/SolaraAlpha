"""Company fundamentals for a single stock from Yahoo Finance's fundamentals-timeseries endpoint.

Free, no key, no cookie/crumb (unlike Yahoo's ``quoteSummary``). Unofficial endpoint: Yahoo's
terms allow personal, non-commercial use only — see ``docs/data-sources.md``.

Returns a tidy long frame, one row per (field, date): ``field`` (Yahoo's name, e.g.
``annualNetIncome``), ``date`` (tz-aware UTC), ``value``, ``currency`` (reporting currency,
e.g. ``GBP``; empty for ratios). Two kinds of date:

* **statement fields** (``annual*``, ``trailing*`` items) — ``date`` is the *period end*. The
  figures are published weeks to months later, so they must not be used from ``date``; see
  :func:`solaraalpha.analysis.fundamentals.known` for the publication lag.
* **market fields** (``trailingPeRatio``, ``trailingForwardPeRatio``, ``trailingMarketCap``) —
  ``date`` is the day Yahoo observed the value, so it is known on that date. These are sparse
  and irregular (a handful per year).

``trailing*`` statement items are trailing-twelve-month figures at each reporting period end
(half-years for most UK companies, quarters for US); ``annual*`` items are fiscal years.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from solaraalpha.data.http import DEFAULT_TIMEOUT, session
from solaraalpha.data.prices import LOOKBACK_YEARS

TIMESERIES_URL = (
    "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker}"
)
_HEADERS = {"User-Agent": "Mozilla/5.0"}
ANNUAL = [
    "TotalRevenue",
    "EBITDA",
    "NetIncome",
    "DilutedEPS",
    "FreeCashFlow",
    "TotalDebt",
    "CashAndCashEquivalents",
    "StockholdersEquity",
    "OrdinarySharesNumber",
]
TRAILING = ["DilutedEPS", "DilutedAverageShares"]
MARKET = ["trailingPeRatio", "trailingForwardPeRatio", "trailingMarketCap"]
FIELDS = [f"annual{f}" for f in ANNUAL] + [f"trailing{f}" for f in TRAILING] + MARKET
COLUMNS = ["field", "date", "value", "currency"]


def fetch_fundamentals(ticker: str, start: str | None = None) -> pd.DataFrame:
    """All :data:`FIELDS` for a Yahoo ticker (e.g. ``"JET2.L"``) dated from ``start`` to now.

    ``start`` defaults to ``LOOKBACK_YEARS`` + 1 years ago, so year-on-year comparisons are
    available at the start of the price history.
    """
    now = pd.Timestamp(datetime.now(UTC))
    begin = pd.Timestamp(start, tz=UTC) if start else now - pd.DateOffset(years=LOOKBACK_YEARS + 1)
    resp = session().get(
        TIMESERIES_URL.format(ticker=ticker),
        params={
            "type": ",".join(FIELDS),
            "period1": str(int(begin.timestamp())),
            "period2": str(int(now.timestamp())),
        },
        headers=_HEADERS,
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    df = parse_timeseries(resp.json())
    df.attrs["ticker"] = ticker
    return df


def parse_timeseries(payload: dict) -> pd.DataFrame:
    """Turn a Yahoo fundamentals-timeseries JSON payload into the tidy long frame."""
    ts = payload["timeseries"]
    if ts.get("error"):
        raise ValueError(f"Yahoo timeseries error: {ts['error']}")
    rows = [
        (field, obs["asOfDate"], obs["reportedValue"]["raw"], obs.get("currencyCode") or "")
        for result in ts["result"] or []
        for field in result["meta"]["type"]
        for obs in result.get(field) or []
        if obs
    ]
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(UTC)
    df["value"] = df["value"].astype("float64")
    return df.sort_values(["field", "date"], ignore_index=True)
