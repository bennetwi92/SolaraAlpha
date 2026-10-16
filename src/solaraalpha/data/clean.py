"""Cleanliness checks for daily price data, and resampling to weekly bars.

``check_prices`` only *reports*; it never changes data. Decide what to do about each issue
after looking at it. ``clean_prices`` applies only fixes that are always safe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from solaraalpha.data.prices import COLUMNS

PRICE_COLS = ["open", "high", "low", "close", "adj_close"]


def check_prices(
    df: pd.DataFrame,
    max_abs_return: float = 0.25,
    max_gap_days: int = 7,
    max_stale_days: int = 5,
) -> pd.DataFrame:
    """Return one row per issue found: ``date``, ``check``, ``detail``. Empty means clean.

    Checks: duplicate/unsorted dates, missing values, non-positive prices, inconsistent OHLC,
    zero volume, calendar gaps longer than ``max_gap_days``, daily close-to-close moves larger
    than ``max_abs_return`` (often unit or split errors, sometimes real), and runs of more
    than ``max_stale_days`` identical closes.
    """
    issues: list[tuple[pd.Timestamp | None, str, str]] = []

    def add(mask: pd.Series, check: str, detail: pd.Series | str) -> None:
        details = detail if isinstance(detail, pd.Series) else pd.Series(detail, index=mask.index)
        for date in mask.index[mask.to_numpy()]:
            issues.append((date, check, str(details.loc[date])))

    if df.empty:
        issues.append((None, "empty", "no rows"))
        return _frame(issues)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        issues.append((None, "columns", f"missing {missing}"))
        return _frame(issues)

    if not df.index.is_monotonic_increasing:
        issues.append((None, "unsorted", "index not increasing"))
    add(pd.Series(df.index.duplicated(), index=df.index), "duplicate_date", "")

    nan = df[COLUMNS].isna()
    add(nan.any(axis=1), "missing", nan.apply(lambda r: ",".join(r.index[r]), axis=1))

    add((df[PRICE_COLS] <= 0).any(axis=1), "non_positive_price", "")
    bad_hl = (df["high"] < df[["open", "close", "low"]].max(axis=1)) | (
        df["low"] > df[["open", "close", "high"]].min(axis=1)
    )
    add(bad_hl, "ohlc_inconsistent", df[["open", "high", "low", "close"]].apply(list, axis=1))
    add(df["volume"] == 0, "zero_volume", "")

    gap = df.index.to_series().diff().dt.days
    add(gap > max_gap_days, "gap", gap.map(lambda d: f"{d:.0f} calendar days since prior bar"))

    ret = df["close"].dropna().pct_change()
    add(ret.abs() > max_abs_return, "large_move", ret.map(lambda r: f"{r:+.1%}"))

    close = df["close"].dropna()
    run = close.groupby((close != close.shift()).cumsum()).cumcount() + 1
    add(run == max_stale_days + 1, "stale", f"close unchanged > {max_stale_days} days")

    return _frame(issues)


def _frame(issues: list[tuple[pd.Timestamp | None, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(issues, columns=["date", "check", "detail"])


def clean_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Apply only always-safe fixes: sort, drop duplicate dates (keep last), drop rows with no
    close. Everything else flagged by :func:`check_prices` needs a human decision."""
    out = df.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out.dropna(subset=["close"])


def to_weekly(df: pd.DataFrame, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """Resample daily bars to weekly bars labelled by the week's Friday.

    Each weekly bar uses only that week's sessions, so its close is known at the end of the
    last session of the week. Weeks whose Friday is on or after ``as_of`` (default: today, UTC)
    are dropped as possibly incomplete. Adds ``sessions`` (trading days in the week).
    """
    as_of = as_of if as_of is not None else pd.Timestamp.now(tz="UTC").normalize()
    weekly = df.resample("W-FRI").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "adj_close": "last",
            "volume": "sum",
        }
    )
    weekly["sessions"] = df["close"].resample("W-FRI").count()
    weekly = weekly[(weekly["sessions"] > 0) & (weekly.index < as_of)]
    weekly.attrs = dict(df.attrs)
    return weekly


def log_returns(prices: pd.Series) -> pd.Series:
    """Period log returns; the first period is NaN."""
    return pd.Series(np.log(prices).diff(), name=prices.name)
