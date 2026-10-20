"""Relative strength of one stock vs its market, industry and peers (config/benchmarks.toml).

uv run python scripts/relative_strength.py JET2.L
uv run python scripts/relative_strength.py JET2.L --market ^FTMC --industry EXV9.DE --peers EZJ.L IAG.L
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace

import pandas as pd

from solaraalpha.analysis.relative_strength import (
    HORIZONS,
    RANK_WEEKS,
    compare,
    comparisons,
    rs_card,
)
from solaraalpha.analysis.technical import scorecard
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent
from solaraalpha.data.prices import fetch_daily
from solaraalpha.store import save_run


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("ticker", help="Yahoo ticker, e.g. JET2.L")
    p.add_argument("--market", help="override the market index")
    p.add_argument("--industry", help="override the industry index / ETF")
    p.add_argument("--peers", nargs="+", help="override the peers")
    p.add_argument("--save", action="store_true", help="record this run in the results DB")
    args = p.parse_args()

    comp = comparisons(args.ticker)
    comp = replace(
        comp,
        market=args.market or comp.market,
        industry=args.industry or comp.industry,
        peers=tuple(args.peers) if args.peers else comp.peers,
    )
    others: dict[str, pd.DataFrame] = {}

    def fetch(ticker: str) -> pd.DataFrame:
        if ticker not in others:
            others[ticker] = fetch_daily(ticker)
        return others[ticker]

    daily = fetch_daily(args.ticker)
    issues = check_prices(daily)
    if not issues.empty:
        print(f"{len(issues)} data-quality issues; run scripts/stock_prices.py to review.")
    weekly = to_weekly(trim_to_consistent(clean_prices(daily), issues))
    c = compare(weekly, comp, fetch)
    print(f"{args.ticker}: {len(weekly)} complete weeks to {weekly.index[-1]:%Y-%m-%d}\n")

    cols = [f"x_{h}" for h in HORIZONS] + [f"z_{h}" for h in HORIZONS] + ["score", "rs_high"]
    for kind, frame in c.frames.items():
        print(f"vs {kind} ({c.used[kind]}): log excess return x_h, t-stat z_h over h weeks")
        print(frame[cols].tail(6).round(2).to_string(), "\n")

    if c.peers:
        rets = {
            t: s.reindex(c.stock.index).pct_change(RANK_WEEKS, fill_method=None).iloc[-1]
            for t, s in c.peers.items()
        }
        rets[f"{args.ticker} *"] = c.stock.pct_change(RANK_WEEKS, fill_method=None).iloc[-1]
        ranked = pd.Series(rets).sort_values(ascending=False)
        print(f"{RANK_WEEKS}-week return, in the stock's currency:")
        print(ranked.map(lambda r: f"{r:+.1%}").to_string(), "\n")

    print("Scorecard:")
    card = rs_card(c, scorecard(weekly).trend)  # trend feeds the leadership / spike flags
    for k, v in asdict(card).items():
        print(f"  {k:>13}: {v:.2f}" if isinstance(v, float) else f"  {k:>13}: {v}")

    if args.save:
        cards = {"relative_strength": card}
        run_id = save_run(
            "relative_strength", vars(args), args.ticker, cards, daily=daily, others=others
        )
        print(f"\nSaved run {run_id}.")


if __name__ == "__main__":
    main()
