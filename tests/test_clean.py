from __future__ import annotations

import numpy as np
import pandas as pd

from solaraalpha.data.clean import (
    check_prices,
    clean_prices,
    log_returns,
    to_weekly,
    trim_to_consistent,
)


def _daily(n: int = 10, start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n, tz="UTC", name="date")
    close = 100 + np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "adj_close": close,
            "volume": 1000.0,
        },
        index=idx,
    )


def _checks(df: pd.DataFrame) -> set[str]:
    return set(check_prices(df)["check"])


def test_clean_data_has_no_issues() -> None:
    assert check_prices(_daily()).empty


def test_detects_each_issue() -> None:
    df = _daily(20)
    df.loc[df.index[2], "close"] = np.nan
    df.loc[df.index[3], "low"] = -1
    df.loc[df.index[4], "high"] = 50  # below open/close
    df.loc[df.index[5], "volume"] = 0
    df.loc[df.index[8], "close"] = 1000  # +900% then -90%
    found = _checks(df)
    assert {"missing", "non_positive_price", "ohlc_inconsistent", "zero_volume"} <= found
    assert "large_move" in found


def test_detects_gap_stale_duplicates() -> None:
    df = _daily(20)
    df = df.drop(df.index[5:12])  # ~9-day gap
    assert "gap" in _checks(df)

    stale = _daily(25)
    stale[["open", "high", "low", "close", "adj_close"]] = 100.0
    assert "stale" in _checks(stale)

    dup = pd.concat([_daily(3), _daily(1)])
    assert {"duplicate_date", "unsorted"} <= _checks(dup)


def test_empty() -> None:
    assert _checks(_daily().iloc[:0]) == {"empty"}


def test_clean_prices() -> None:
    df = pd.concat([_daily(3), _daily(1)])
    df.loc[df.index[1], "close"] = np.nan
    out = clean_prices(df)
    assert out.index.is_monotonic_increasing
    assert not out.index.duplicated().any()
    assert len(out) == 2


def test_to_weekly() -> None:
    df = _daily(10, "2024-01-01")  # Mon 1 Jan .. Fri 12 Jan
    w = to_weekly(df, as_of=pd.Timestamp("2024-01-13", tz="UTC"))
    assert list(w.index) == [
        pd.Timestamp("2024-01-05", tz="UTC"),
        pd.Timestamp("2024-01-12", tz="UTC"),
    ]
    first = w.iloc[0]
    assert first["open"] == 100 and first["close"] == 104
    assert first["high"] == 105 and first["low"] == 99
    assert first["volume"] == 5000 and first["sessions"] == 5


def test_to_weekly_drops_incomplete_week() -> None:
    df = _daily(10, "2024-01-01")
    w = to_weekly(df, as_of=pd.Timestamp("2024-01-12", tz="UTC"))  # Friday, maybe mid-session
    assert list(w.index) == [pd.Timestamp("2024-01-05", tz="UTC")]


def test_log_returns() -> None:
    r = log_returns(pd.Series([100.0, 110.0, 99.0]))
    assert np.isnan(r.iloc[0])
    assert np.isclose(r.iloc[1], np.log(1.1))


def test_trim_to_consistent() -> None:
    df = _daily(20)
    df.loc[df.index[4], "low"] = -1.0  # non-positive price: blocking
    df.loc[df.index[8], "volume"] = 0  # no trades: not blocking
    df.loc[df.index[10], "close"] = 1000  # large move: not blocking
    out = trim_to_consistent(df, check_prices(df))
    assert out.index[0] == df.index[5]


def test_trim_to_consistent_no_blocking() -> None:
    df = _daily()
    assert trim_to_consistent(df, check_prices(df)).equals(df)


def test_stale_flags_whole_run() -> None:
    df = _daily(10)
    df[["open", "high", "low", "close", "adj_close"]] = 100.0
    stale = check_prices(df, max_stale_days=5).query("check == 'stale'")
    assert list(stale["date"]) == list(df.index[5:])


def test_trim_ignores_dropped_rows() -> None:
    df = _daily(20)
    df.loc[df.index[-1], ["open", "high", "low", "close", "adj_close"]] = np.nan  # no-trade bar
    out = trim_to_consistent(clean_prices(df), check_prices(df))
    assert out.index[0] == df.index[0]
    assert out.index[-1] == df.index[-2]


def test_a_bad_newest_bar_does_not_cut_the_history() -> None:
    df = _daily(20)
    dup = pd.concat([df, df.iloc[[-1]] * 1.001])  # Yahoo repeats the live session
    out = trim_to_consistent(clean_prices(dup), check_prices(dup))
    assert len(out) == 20 and out["close"].iloc[-1] == df["close"].iloc[-1] * 1.001
    nv = df.copy()
    nv.loc[nv.index[-1], "volume"] = np.nan  # a null volume beside the close
    assert len(trim_to_consistent(clean_prices(nv), check_prices(nv))) == 20
    mid = df.copy()
    mid.loc[mid.index[10], "volume"] = np.nan  # mid-history it still cuts
    assert trim_to_consistent(clean_prices(mid), check_prices(mid)).index[0] == df.index[11]
