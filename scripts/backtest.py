"""Replay the screen point-in-time over the watchlist and sweep the band thresholds.

uv run python scripts/backtest.py [TICKERS...] [--years 5] [--step 1] [--out FILE]

Fresh fetches (last --years years, check / clean / trim, weekly bars), each Friday's cards
computed from the bars up to that week (fundamentals via known() lags; benchmarks sliced the
same way), labelled with the current bands; then forward 4 / 8 / 16-week returns (excess vs the
market index) and a sweep changing one ACT threshold at a time. Writes a markdown report with
the survivorship caveat and a *suggested* band table to data/processed/backtest/report.md (or
--out); the bands in code are not changed. --step N replays every Nth week when 50 stocks x 5
years is too slow. Caches nothing but the report. See ADR 0014, issue #138.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from solaraalpha.analysis.backtest import forward, replay, report, sweep
from solaraalpha.analysis.relative_strength import comparisons
from solaraalpha.config import settings
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent
from solaraalpha.data.fundamentals import fetch_fundamentals
from solaraalpha.data.prices import fetch_daily
from solaraalpha.pipeline import watchlist


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("tickers", nargs="*", help="Yahoo tickers (default: config/watchlist.toml)")
    p.add_argument("--years", type=float, default=5.0)
    p.add_argument("--step", type=int, default=1, help="replay every Nth week")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    tickers = args.tickers or watchlist()
    start = (pd.Timestamp.now(tz="UTC") - pd.DateOffset(years=args.years)).strftime("%Y-%m-%d")
    cache: dict[str, pd.DataFrame] = {}

    def fetch(t: str) -> pd.DataFrame:
        if t not in cache:
            cache[t] = fetch_daily(t, start=start)
        return cache[t]

    replays, fwds = [], []
    t0 = time.time()
    for t in tickers:
        try:
            raw = fetch(t)
            weekly = to_weekly(trim_to_consistent(clean_prices(raw), check_prices(raw)))
        except Exception as e:
            print(f"{t}: prices failed ({e})")
            continue
        try:
            fund = fetch_fundamentals(t)
        except Exception as e:
            print(f"{t}: fundamentals unavailable ({e})")
            fund = None
        comp = comparisons(t)
        bench = None
        if comp.market:
            try:
                b = fetch(comp.market)
                bench = to_weekly(clean_prices(b))
            except Exception as e:
                print(f"{t}: market {comp.market} unavailable ({e})")
        rep = replay(t, weekly, fund, comp, step=args.step, fetch=fetch)
        replays.append(rep)
        fwds.append(forward(weekly, bench=bench).assign(ticker=t))
        print(f"{t}: {len(rep)} weeks replayed ({time.time() - t0:.0f}s so far)")
    replayed = pd.concat(replays, ignore_index=True) if replays else pd.DataFrame()
    fwd = pd.concat(fwds, ignore_index=True) if fwds else pd.DataFrame()
    sw = sweep(replayed, fwd) if not replayed.empty else pd.DataFrame()
    text = report(replayed, sw, tickers, args.years)
    out = args.out or settings.processed_dir / "backtest" / "report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"\n{text}\nwritten to {out}")


if __name__ == "__main__":
    main()
