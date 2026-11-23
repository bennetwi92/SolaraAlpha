"""Rank the watchlist (or some stocks) by crowd momentum on daily bars (ADR 0043).

uv run python scripts/momentum.py                 # the whole watchlist, best first
uv run python scripts/momentum.py NVDA PLTR TSLA  # these stocks
uv run python scripts/momentum.py --top 15

Prices are fetched fresh (one Yahoo call per stock, plus the market and sector ETFs once). The
score is unvalidated until the pre-registered test (docs/decisions/0029 protocol).
"""

from __future__ import annotations

import argparse
import math

from solaraalpha.pipeline import universe
from solaraalpha.portfolio.fit import RANK_NOTE
from solaraalpha.toolkits import Inputs, get


def _f(x: float, pct: bool = False) -> str:
    if math.isnan(x):
        return "  -  "
    return f"{x:+.0%}" if pct else f"{x:+.2f}"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("tickers", nargs="*", help="Yahoo tickers (default: the watchlist)")
    p.add_argument("--top", type=int, default=0, help="show only the best N")
    args = p.parse_args(argv)
    run = get("momentum").run
    cards, failed = [], []
    for t in args.tickers or universe():
        try:
            cards.append((t, run(Inputs(t))["momentum"]))
        except Exception as e:  # one stock's bad data never stops the ranking
            failed.append(f"{t} ({type(e).__name__}: {e})")
    cards.sort(key=lambda tc: -tc[1].crowd if not math.isnan(tc[1].crowd) else math.inf)
    shown = cards[: args.top] if args.top else cards
    if shown:
        print(f"Crowd momentum as of {shown[0][1].date:%Y-%m-%d}. {RANK_NOTE}\n")
    head = f"{'#':>3} {'ticker':<6} {'crowd':>6} {'trend':>6} {'high':>6} {'vol':>6} {'str':>6}"
    print(head + f" {'from hi':>8} {'rvol':>5} {'vs mkt 20d':>10} {'vs sector':>9}  flags")
    for i, (t, c) in enumerate(shown, 1):
        flags = [n for n, on in (("new high", c.new_high), ("breakout", c.breakout)) if on]
        if c.extended:
            flags.append(f"extended {c.extension_atr:.1f} ATR")
        print(
            f"{i:>3} {t:<6} {_f(c.crowd):>6} {_f(c.trend):>6} {_f(c.high):>6} {_f(c.volume):>6}"
            f" {_f(c.strength):>6} {_f(c.pct_from_high, True):>8} {c.rvol:>5.1f}"
            f" {_f(c.rs_market_20, True):>10} {_f(c.rs_sector_20, True):>9}  {', '.join(flags)}"
        )
    if failed:
        print("\nNot scored: " + "; ".join(failed))


if __name__ == "__main__":
    main()
