"""Fetch daily prices for one stock, report data-quality issues, and show weekly bars.

uv run python scripts/stock_prices.py JET2.L --start 2006-01-01
"""

from __future__ import annotations

import argparse

from solaraalpha.data.clean import check_prices, clean_prices, log_returns, to_weekly
from solaraalpha.data.prices import fetch_daily


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ticker", help="Yahoo ticker, e.g. JET2.L")
    p.add_argument("--start", default="1990-01-01")
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
        print("\nData-quality issues by year:")
        by_year = issues.assign(year=issues["date"].dt.year)
        print(by_year.pivot_table(index="year", columns="check", aggfunc="size", fill_value=0))
        print("\nLarge moves (verify against news before trusting):")
        print(issues[issues["check"] == "large_move"].to_string(index=False))

    weekly = to_weekly(clean_prices(daily))
    weekly["log_return"] = log_returns(weekly["adj_close"])
    print(f"\n{len(weekly)} complete weekly bars; last 5:")
    print(weekly.tail().round(2).to_string())


if __name__ == "__main__":
    main()
