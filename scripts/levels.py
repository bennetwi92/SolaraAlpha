"""Weekly support/resistance, trendlines and significant levels for one stock, with a
level-based trade plan (stop, targets, reward:risk).

uv run python scripts/levels.py JET2.L [--short] [--n 6]
"""

from __future__ import annotations

import argparse
from dataclasses import asdict

from solaraalpha.analysis.levels import level_map, levels_card, trendline
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent
from solaraalpha.data.prices import fetch_daily


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ticker", help="Yahoo ticker, e.g. JET2.L")
    p.add_argument("--short", action="store_true", help="plan a short (default long)")
    p.add_argument("--n", type=int, default=6, help="levels to show each side of the close")
    args = p.parse_args()
    side = -1 if args.short else 1

    daily = fetch_daily(args.ticker)
    issues = check_prices(daily)
    if not issues.empty:
        print(f"{len(issues)} data-quality issues; run scripts/stock_prices.py to review.")
    weekly = to_weekly(trim_to_consistent(clean_prices(daily), issues))
    print(f"{args.ticker}: {len(weekly)} complete weeks to {weekly.index[-1]:%Y-%m-%d}\n")

    lm = level_map(weekly)
    res, sup = lm[lm["role"] == "resistance"], lm[lm["role"] == "support"]
    shown = lm.loc[res.index[-args.n :].union(sup.index[: args.n])]
    shown = shown.assign(last=shown["last"].dt.strftime("%Y-%m-%d"))
    print(f"Levels around close {weekly['close'].iloc[-1]:.2f}:")
    print(shown.round(2).to_string(index=False), "\n")

    for kind in ("upper", "lower"):
        t = trendline(weekly, kind)
        print(
            f"{kind} trendline: none intact"
            if t is None
            else f"{kind} trendline: {t.start:%Y-%m-%d} {t.start_price:.2f} -> {t.end:%Y-%m-%d} "
            f"{t.end_price:.2f}, {t.slope:+.2%}/wk, {t.touches} touches, next week {t.value:.2f}"
        )

    print(f"\nLevels card ({'short' if args.short else 'long'}):")
    for k, v in asdict(levels_card(weekly, side)).items():
        print(f"  {k:>14}: {v:.2f}" if isinstance(v, float) else f"  {k:>14}: {v}")


if __name__ == "__main__":
    main()
