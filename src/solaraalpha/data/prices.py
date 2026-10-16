"""Historical daily prices for a single stock from Yahoo Finance's public chart endpoint.

Free, no key. Unofficial endpoint: Yahoo's terms allow personal, non-commercial use only and
the format can change without notice — see ``docs/data-sources.md``.

Index is the trading-session date (midnight UTC, tz-aware). A bar's close is only known after
that session ends (LSE: ~16:35 London), so a signal using it can trade the next session at best.
Prices are in the listing currency (LSE stocks: pence, ``GBp``), recorded in ``df.attrs``.

``adj_close`` is back-adjusted for dividends and splits, so its *level* on a past date uses
later information; use it for returns only, and raw ``close`` for anything level-based.
Yahoo's history before ~2006 for LSE stocks is padded filler (holidays, zero volume, stale
closes) — run :func:`solaraalpha.data.clean.check_prices` before using any range.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from solaraalpha.data.http import DEFAULT_TIMEOUT, session

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
# Yahoo returns 429 to non-browser user agents.
_HEADERS = {"User-Agent": "Mozilla/5.0"}
COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]


def fetch_daily(ticker: str, start: str = "1990-01-01", end: str | None = None) -> pd.DataFrame:
    """Daily OHLCV for a Yahoo ticker (e.g. ``"JET2.L"``) between ``start`` and ``end`` (dates).

    ``end`` defaults to now. A bar for the current, possibly unfinished, session may be included;
    :func:`solaraalpha.data.clean.to_weekly` drops incomplete periods.
    """
    period1 = int(pd.Timestamp(start, tz=UTC).timestamp())
    period2 = int(
        (pd.Timestamp(end, tz=UTC) if end else pd.Timestamp(datetime.now(UTC))).timestamp()
    )
    resp = session().get(
        CHART_URL.format(ticker=ticker),
        params={"period1": str(period1), "period2": str(period2), "interval": "1d"},
        headers=_HEADERS,
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return parse_chart(resp.json())


def parse_chart(payload: dict) -> pd.DataFrame:
    """Turn a Yahoo ``/v8/finance/chart`` JSON payload into a daily OHLCV frame."""
    chart = payload["chart"]
    if chart.get("error"):
        raise ValueError(f"Yahoo chart error: {chart['error']}")
    result = chart["result"][0]
    meta = result["meta"]
    quote = result["indicators"]["quote"][0]
    adj = result["indicators"].get("adjclose", [{}])[0].get("adjclose")

    # Timestamps are session open times in exchange time; take the local calendar date.
    ts = pd.to_datetime(result.get("timestamp", []), unit="s", utc=True)
    dates = ts.tz_convert(meta["exchangeTimezoneName"]).normalize().tz_localize(None)
    df = pd.DataFrame(
        {
            "open": quote["open"],
            "high": quote["high"],
            "low": quote["low"],
            "close": quote["close"],
            "adj_close": adj if adj is not None else quote["close"],
            "volume": quote["volume"],
        },
        index=pd.DatetimeIndex(dates, name="date").tz_localize(UTC),
        dtype="float64",
    )
    df.attrs = {"ticker": meta["symbol"], "currency": meta.get("currency")}
    return df
