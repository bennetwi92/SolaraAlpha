"""Cleanliness checks for daily price data, and resampling to weekly bars.

``check_prices`` only *reports*; it never changes data. Decide what to do about each issue
after looking at it. ``clean_prices`` applies only fixes that are always safe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from solaraalpha.data.prices import COLUMNS

PRICE_COLS = ["open", "high", "low", "close", "adj_close"]
# Issues that mean the data isn't real trading data. Large moves (often real), OHLC
# inconsistencies (close outside high/low; harmless for close-to-close returns) and zero-volume
# days (normal for thinly traded AIM stocks) don't count — see docs/decisions/0012.
BLOCKING = frozenset({"missing", "non_positive_price", "gap", "stale", "duplicate_date"})
# For series used only for close-to-close returns (indices, ETFs, FX as comparisons): volume
# doesn't matter, and a missing or duplicated day is dropped / de-duplicated by clean_prices.
CLOSE_ONLY_BLOCKING = frozenset({"non_positive_price", "gap", "stale"})
# On the newest bar these are the live / partial session Yahoo sometimes sends (a repeated date,
# a null volume beside the close): clean_prices keeps the last copy, and they never cut the
# whole history (the cut would leave nothing).
LATEST_BAR_OK = frozenset({"duplicate_date", "missing"})


def check_prices(
    df: pd.DataFrame,
    max_abs_return: float = 0.25,
    max_gap_days: int = 7,
    max_stale_days: int = 20,
) -> pd.DataFrame:
    """Return one row per issue found: ``date``, ``check``, ``detail``. Empty means clean.

    Checks: duplicate/unsorted dates, missing values, non-positive prices, inconsistent OHLC,
    zero volume, calendar gaps longer than ``max_gap_days``, daily close-to-close moves larger
    than ``max_abs_return`` (often unit or split errors, sometimes real), and runs of more
    than ``max_stale_days`` identical closes (every day of the run beyond that is flagged).
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
    add(run > max_stale_days, "stale", f"close unchanged > {max_stale_days} days")

    return _frame(issues)


def _frame(issues: list[tuple[pd.Timestamp | None, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(issues, columns=["date", "check", "detail"])


def clean_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Apply only always-safe fixes: sort, drop duplicate dates (keep last), drop rows with no
    close. Everything else flagged by :func:`check_prices` needs a human decision."""
    out = df.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out.dropna(subset=["close"])


def trim_to_consistent(
    df: pd.DataFrame, issues: pd.DataFrame, blocking: frozenset[str] = BLOCKING
) -> pd.DataFrame:
    """Keep only the data after the last ``blocking`` issue, so the series is consistent
    from its first date on. The cutoff differs per stock; check how much is left.

    Issues on rows no longer in ``df`` (e.g. a no-trade bar with no close, dropped by
    :func:`clean_prices`) don't cut: they aren't in the data being used, nor do
    :data:`LATEST_BAR_OK` issues on the newest bar."""
    block = issues[issues["check"].isin(blocking)].dropna(subset=["date"])
    if not df.empty:
        block = block[~((block["date"] == df.index.max()) & block["check"].isin(LATEST_BAR_OK))]
    dates = block["date"]
    dates = dates[dates.isin(df.index)]
    return df if dates.empty else df[df.index > dates.max()]


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
