"""Fundamentals scorecard for one stock: growth / value / quality, aimed at 2-16 week longs.

uv run python scripts/fundamentals.py JET2.L [--as-of 2025-06-30]
"""

from __future__ import annotations

import argparse
from dataclasses import asdict

import pandas as pd

from solaraalpha.analysis.fundamentals import (
    fundamentals_card,
    fx_ticker,
    known,
    reporting_currency,
)
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent
from solaraalpha.data.fundamentals import fetch_fundamentals
from solaraalpha.data.prices import fetch_daily
from solaraalpha.store import save_run


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ticker", help="Yahoo ticker, e.g. JET2.L")
    p.add_argument("--as-of", default=None, help="past date: only data public by then")
    p.add_argument("--save", action="store_true", help="record this run in the results DB")
    args = p.parse_args()

    daily = fetch_daily(args.ticker)
    issues = check_prices(daily)
    if not issues.empty:
        print(f"{len(issues)} data-quality issues; run scripts/stock_prices.py to review.")
    weekly = to_weekly(trim_to_consistent(clean_prices(daily), issues))
    fund = fetch_fundamentals(args.ticker)
    shown = known(fund, args.as_of) if args.as_of else fund
    print(f"{args.ticker}: {len(fund)} fundamental observations, {len(weekly)} weeks of prices\n")

    table = shown.pivot_table(index="date", columns="field", values="value")
    table.index = pd.DatetimeIndex(table.index).strftime("%Y-%m-%d")
    with pd.option_context("display.float_format", "{:,.3g}".format, "display.width", 200):
        print(table.T.iloc[:, -10:].to_string(), "\n")

    print("Fundamentals card:")
    pair = fx_ticker(reporting_currency(fund), weekly.attrs.get("currency"))
    fx = fetch_daily(pair)["close"] if pair else None
    card = fundamentals_card(fund, weekly, args.as_of, fx)
    for k, v in asdict(card).items():
        print(f"  {k:>15}: {v:.3f}" if isinstance(v, float) else f"  {k:>15}: {v}")

    if args.save:
        cards = {"fundamentals": card}
        run_id = save_run("fundamentals", vars(args), args.ticker, cards, daily=daily, fund=fund)
        print(f"\nSaved run {run_id}.")


if __name__ == "__main__":
    main()
