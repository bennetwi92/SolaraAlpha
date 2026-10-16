"""Historical daily prices for a single stock from Yahoo Finance's public chart endpoint.

Free, no key. Unofficial endpoint: Yahoo's terms allow personal, non-commercial use only and
the format can change without notice — see ``docs/data-sources.md``.

Index is the trading-session date (midnight UTC, tz-aware). A bar's close is only known after
that session ends (LSE: ~16:35 London), so a signal using it can trade the next session at best.
Prices are in the listing currency (LSE stocks: pence, ``GBp``), recorded in ``df.attrs``.

``adj_close`` is back-adjusted for dividends and splits, so its *level* on a past date uses
later information; use it for returns only, and raw ``close`` for anything level-based.
Older history can be padded filler (e.g. LSE before ~2006: holidays, zero volume, stale
closes) — run :func:`solaraalpha.data.clean.check_prices` and
:func:`solaraalpha.data.clean.trim_to_consistent` before use.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from solaraalpha.data.http import DEFAULT_TIMEOUT, session

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
# Yahoo returns 429 to non-browser user agents.
_HEADERS = {"User-Agent": "Mozilla/5.0"}
COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]
ACTION_COLUMNS = ["date", "kind", "value", "ratio"]


LOOKBACK_YEARS = 5


def fetch_daily(ticker: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Daily OHLCV for a Yahoo ticker (e.g. ``"JET2.L"``) between ``start`` and ``end`` (dates).

    ``start`` defaults to ``LOOKBACK_YEARS`` before now, ``end`` to now. A bar for the
    current, possibly unfinished, session may be included; :func:`solaraalpha.data.clean.to_weekly` drops incomplete periods.
    """
    stop = pd.Timestamp(end, tz=UTC) if end else pd.Timestamp(datetime.now(UTC))
    begin = pd.Timestamp(start, tz=UTC) if start else stop - pd.DateOffset(years=LOOKBACK_YEARS)
    period1, period2 = int(begin.timestamp()), int(stop.timestamp())
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


def fetch_corporate_actions(
    ticker: str, start: str | None = None, end: str | None = None
) -> pd.DataFrame:
    """Dividends and splits for a Yahoo ticker between ``start`` and ``end`` (same endpoint as
    :func:`fetch_daily`, with ``events=div,split``). See :func:`parse_events` for the frame."""
    stop = pd.Timestamp(end, tz=UTC) if end else pd.Timestamp(datetime.now(UTC))
    begin = pd.Timestamp(start, tz=UTC) if start else stop - pd.DateOffset(years=LOOKBACK_YEARS)
    resp = session().get(
        CHART_URL.format(ticker=ticker),
        params={
            "period1": str(int(begin.timestamp())),
            "period2": str(int(stop.timestamp())),
            "interval": "1d",
            "events": "div,split",
        },
        headers=_HEADERS,
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return parse_events(resp.json())


def parse_events(payload: dict) -> pd.DataFrame:
    """Tidy ``date, kind, value, ratio`` frame from a chart payload's ``events``.

    ``kind`` is ``dividend`` (``value``: amount per share in the quote currency, e.g. pence for
    LSE stocks; the date is the ex-dividend date) or ``split`` (``value``: new shares per old
    share, e.g. 2.0 for a 2:1 split and 0.1 for a 1:10 consolidation; ``ratio`` the text
    ``new:old``). Sorted by date; ``attrs`` carry ``ticker`` and ``currency``.
    """
    chart = payload["chart"]
    if chart.get("error"):
        raise ValueError(f"Yahoo chart error: {chart['error']}")
    result = chart["result"][0]
    meta = result["meta"]
    tz = meta["exchangeTimezoneName"]
    events = result.get("events") or {}
    rows: list[dict[str, object]] = []
    for e in (events.get("dividends") or {}).values():
        rows.append(
            {"date": e["date"], "kind": "dividend", "value": float(e["amount"]), "ratio": ""}
        )
    for e in (events.get("splits") or {}).values():
        num, den = float(e["numerator"]), float(e["denominator"])
        rows.append(
            {
                "date": e["date"],
                "kind": "split",
                "value": num / den if den else float("nan"),
                "ratio": str(e.get("splitRatio") or f"{e['numerator']}:{e['denominator']}"),
            }
        )
    df = pd.DataFrame(rows, columns=ACTION_COLUMNS)
    ts = pd.to_datetime(df["date"].astype("int64"), unit="s", utc=True) if len(df) else df["date"]
    if len(df):
        df["date"] = ts.dt.tz_convert(tz).dt.normalize().dt.tz_localize(None).dt.tz_localize(UTC)
    df = df.sort_values(["date", "kind"]).reset_index(drop=True)
    df.attrs = {"ticker": meta["symbol"], "currency": meta.get("currency")}
    return df
