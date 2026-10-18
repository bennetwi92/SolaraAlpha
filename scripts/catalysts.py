"""Fresh catalysts over the watchlist (or some stocks): new filings, headlines, scheduled events.

The catalyst route onto the weekly shortlist (#193, ADR 0022): a stock listed here can reach the
agents even when it ranks outside the top N. Finding only; the agents judge what counts.

uv run python scripts/catalysts.py [TICKERS...] [--days 7] [--horizon 10] [--no-news]
"""

from __future__ import annotations

import argparse
import time

import pandas as pd

from solaraalpha.catalysts import fresh_catalysts
from solaraalpha.pipeline import watchlist


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("tickers", nargs="*", help="Yahoo LSE tickers (default: the watchlist)")
    p.add_argument("--days", type=int, default=7, help="look-back for new filings / headlines")
    p.add_argument("--horizon", type=int, default=10, help="weeks ahead for scheduled events")
    p.add_argument("--no-news", action="store_true", help="skip press headlines")
    args = p.parse_args()

    tickers = args.tickers or watchlist()
    now = pd.Timestamp.now(tz="UTC")
    t0 = time.monotonic()
    found = fresh_catalysts(
        tickers,
        since=now - pd.Timedelta(days=args.days),
        horizon_weeks=args.horizon,
        now=now,
        news=not args.no_news,
    )
    for ticker, reason in found.items():
        print(f"{ticker:<8} {reason}")
    print(f"\n{len(found)} of {len(tickers)} with a fresh catalyst in {time.monotonic() - t0:.0f}s")


if __name__ == "__main__":
    main()
