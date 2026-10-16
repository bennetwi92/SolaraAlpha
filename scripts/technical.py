"""Weekly technical scorecard for one stock, optionally assessing an open position.

uv run python scripts/technical.py JET2.L
uv run python scripts/technical.py JET2.L --entry-date 2025-03-14 [--entry-price 1450] [--short]
"""

from __future__ import annotations

import argparse
from dataclasses import asdict

from solaraalpha.analysis.technical import assess_position, scorecard, scores
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent
from solaraalpha.data.prices import fetch_daily
from solaraalpha.store import save_run


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ticker", help="Yahoo ticker, e.g. JET2.L")
    p.add_argument("--entry-date", default=None, help="assess a position opened on this date")
    p.add_argument("--entry-price", type=float, default=None)
    p.add_argument("--short", action="store_true", help="short side (default long)")
    p.add_argument("--save", action="store_true", help="record this run in the results DB")
    args = p.parse_args()
    side = -1 if args.short else 1

    daily = fetch_daily(args.ticker)
    issues = check_prices(daily)
    if not issues.empty:
        print(f"{len(issues)} data-quality issues; run scripts/stock_prices.py to review.")
    weekly = to_weekly(trim_to_consistent(clean_prices(daily), issues))
    print(f"{args.ticker}: {len(weekly)} complete weeks to {weekly.index[-1]:%Y-%m-%d}\n")

    cols = ["close", "sma_fast", "sma_slow", "adx", "rsi", "macd_hist", "mom_12_1", "extension"]
    cols += ["trend", "momentum", "entry"]
    print(scores(weekly, side)[cols].tail(8).round(2).to_string(), "\n")

    print("Scorecard (new position):")
    card = scorecard(weekly, side)
    for k, v in asdict(card).items():
        print(f"  {k:>18}: {v:.2f}" if isinstance(v, float) else f"  {k:>18}: {v}")

    pos = None
    if args.entry_date:
        print("\nOpen position:")
        pos = assess_position(weekly, args.entry_date, side, args.entry_price)
        for k, v in asdict(pos).items():
            print(f"  {k:>18}: {v:.2f}" if isinstance(v, float) else f"  {k:>18}: {v}")

    if args.save:
        cards = {"technical": card} | ({"position": pos} if pos else {})
        run_id = save_run("technical", vars(args), args.ticker, cards, daily=daily)
        print(f"\nSaved run {run_id}.")


if __name__ == "__main__":
    main()
