"""Commodity futures curves from Yahoo Finance's chart endpoint (one ticker per contract month).

Free, no key; same unofficial endpoint and terms as :mod:`solaraalpha.data.prices`. A contract
ticker is root + month code + two-digit year + exchange, e.g. ``HOM27.NYM`` (NYMEX heating oil,
June 2027). Expired or unlisted months return no data and are skipped.

The curve is the last settled close of each contract, so it is known after that session ends.
"""

from __future__ import annotations

import pandas as pd
import requests

from solaraalpha.data.prices import fetch_daily
from solaraalpha.log import get_logger

log = get_logger(__name__)

MONTH_CODES = "FGHJKMNQUVXZ"  # Jan..Dec
LOOKBACK_DAYS = 14  # a settle older than this is treated as missing


def contract_ticker(root: str, month: pd.Timestamp, exchange: str) -> str:
    """Yahoo ticker of the ``root`` contract delivering in ``month``, e.g. ``HOM27.NYM``."""
    return f"{root}{MONTH_CODES[month.month - 1]}{month.year % 100:02d}.{exchange}"


def fetch_curve(
    root: str, exchange: str, first: pd.Timestamp, months: int, as_of: pd.Timestamp | None = None
) -> pd.Series:
    """Last close of each contract month from ``first`` for ``months`` months, indexed by the
    delivery month start. Months with no recent settle are left out (with a warning)."""
    as_of = as_of or pd.Timestamp.now(tz="UTC")
    start = (as_of - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    out: dict[pd.Timestamp, float] = {}
    first = first.tz_localize(None) if first.tzinfo else first
    for m in pd.date_range(first.to_period("M").to_timestamp(), periods=months, freq="MS"):
        ticker = contract_ticker(root, m, exchange)
        try:
            close = fetch_daily(ticker, start=start)["close"].dropna()
        except (requests.RequestException, ValueError, KeyError, IndexError) as e:
            log.warning("no data for %s: %s", ticker, e)
            continue
        if close.empty:
            log.warning("no recent settle for %s", ticker)
            continue
        out[m] = float(close.iloc[-1])
    return pd.Series(out, name=f"{root}.{exchange}", dtype="float64")
