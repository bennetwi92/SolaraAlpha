"""Fetch the last 5 years of daily prices for one stock, report data-quality issues, cut to
the consistent span, and show weekly bars.

    uv run python scripts/stock_prices.py JET2.L
"""

from __future__ import annotations

import argparse

from solaraalpha.data.clean import (
    BLOCKING,
    check_prices,
    clean_prices,
    log_returns,
    to_weekly,
    trim_to_consistent,
)
from solaraalpha.data.prices import fetch_daily


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ticker", help="Yahoo ticker, e.g. JET2.L")
    p.add_argument("--start", default=None, help="default: 5 years ago")
    p.add_argument("--end", default=None)
    args = p.parse_args()

    daily = fetch_daily(args.ticker, args.start, args.end)
    print(
        f"{args.ticker}: {len(daily)} daily bars {daily.index[0]:%Y-%m-%d} → "
        f"{daily.index[-1]:%Y-%m-%d}, currency {daily.attrs.get('currency')}"
    )

    issues = check_prices(daily)
    if issues.empty:
        print("No data-quality issues found.")
    else:
        print("\nData-quality issues:")
        print(issues.to_string(index=False))

    blocking = issues[issues["check"].isin(BLOCKING)]
    usable = trim_to_consistent(clean_prices(daily), issues)
    if blocking.empty:
        print("\nNo blocking issues — using the full window.")
    else:
        last = blocking.iloc[blocking["date"].argmax()]
        print(f"\nCutoff after {last['date']:%Y-%m-%d} ({last['check']}).")
    print(f"Usable: {len(usable)} daily bars from {usable.index[0]:%Y-%m-%d}.")

    weekly = to_weekly(usable)
    weekly["log_return"] = log_returns(weekly["adj_close"])
    print(f"\n{len(weekly)} complete weekly bars; last 5:")
    print(weekly.tail().round(2).to_string())


if __name__ == "__main__":
    main()
