"""Jet2 fair value at the trade horizon, with scenarios and news shocks.

uv run python scripts/jet2_value.py [--weeks 12] [--scenario fuel_spike] [--shock seats=+2%]
uv run python scripts/jet2_value.py --list          # named scenarios and shock fields

Shocks: [fy27.|fy28.]<field>=<spec>, spec "+5%" relative, "+10"/"-10" added, "*1.1" scaled,
"900" set. No prefix = going forward (FY28 in full, the rest of FY27 pro rata); "fy27." = all
of FY27 (news about the past); "fy28." = FY28 only; "pe=+0.5" shifts the horizon P/E.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, fields

import pandas as pd

from solaraalpha.analysis import jet2_value as m
from solaraalpha.data.clean import check_prices, clean_prices, trim_to_consistent
from solaraalpha.data.futures import fetch_curve
from solaraalpha.data.prices import fetch_daily
from solaraalpha.store import save_run

TICKER = "JET2.L"
FX = ("GBPUSD=X", "GBPEUR=X")
SHOWN = ("revenue", "ebit", "net_finance", "pbt", "eps")


def live_market(base_rate: float) -> tuple[m.Market, pd.DataFrame]:
    """Market inputs now (Jet2 close, heating-oil curve, FX) and the raw Jet2 daily frame."""
    daily = fetch_daily(TICKER)
    issues = check_prices(daily)
    if not issues.empty:
        print(f"{len(issues)} price data-quality issues; run scripts/stock_prices.py to review.")
    close = trim_to_consistent(clean_prices(daily), issues)["close"].dropna()
    date = close.index[-1]
    curve = fetch_curve("HO", "NYM", date + pd.offsets.MonthBegin(1), 18, as_of=date)
    start = f"{date - pd.Timedelta(days=10):%Y-%m-%d}"
    usd, eur = (float(fetch_daily(t, start=start)["close"].dropna().iloc[-1]) for t in FX)
    return m.market_from(date, float(close.iloc[-1]), curve, usd, eur, base_rate), daily


def table(cols: dict[str, dict[str, float]], rows: tuple[str, ...]) -> str:
    df = pd.DataFrame({k: {r: v[r] for r in rows} for k, v in cols.items()})
    return df.round(1).to_string()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--weeks", type=float, default=12, help="holding horizon in weeks")
    p.add_argument("--scenario", action="append", default=[], help="named scenario (repeatable)")
    p.add_argument("--shock", action="append", default=[], help="driver shock (repeatable)")
    p.add_argument("--fair-pe", type=float, default=m.FAIR_PE, help="fair P/E on FY28 EPS")
    p.add_argument("--base-rate", type=float, default=m.REFERENCE.base_rate, help="Bank Rate")
    p.add_argument("--offline", action="store_true", help="use reference-date market inputs")
    p.add_argument("--sims", type=int, default=2000, help="Monte Carlo draws")
    p.add_argument("--list", action="store_true", help="list scenarios and shock fields")
    p.add_argument("--save", action="store_true", help="record this run in the results DB")
    args = p.parse_args()

    if args.list:
        print("Scenarios:")
        for k, (desc, shocks) in m.SCENARIOS.items():
            print(f"  {k:16} {desc}\n  {'':16} {' '.join(shocks)}")
        print("\nShock fields (FY28 base value):")
        for f in fields(m.Year):
            if f.name != "name":
                print(f"  {f.name:18} {getattr(m.FY28, f.name):.4g}")
        return

    daily = None
    market = m.REFERENCE if args.offline else None
    if market is None:
        market, daily = live_market(args.base_rate)
    shocks = [s for name in args.scenario for s in m.SCENARIOS[name][1]] + args.shock
    w = m.fy27_remaining(market.date)

    print(f"Jet2 fair value — market as of {market.date:%Y-%m-%d}, horizon {args.weeks:g} weeks")
    print(
        f"Consensus EPS ({m.CONSENSUS_DATE}): FY27 {m.CONSENSUS['FY27']:.0f}p, "
        f"FY28 {m.CONSENSUS['FY28']:.0f}p. Share of FY27 still to fly: {w:.0%}\n"
    )

    ref, now = asdict(m.REFERENCE), asdict(market)
    print("Market inputs (reference = consensus date):")
    for k in ("price", "fuel_fy27_rest", "fuel_fy28", "usd", "eur", "base_rate"):
        print(f"  {k:>15}: {ref[k]:>9.4g} -> {now[k]:>9.4g}")

    base27, base28 = m.base_years()
    pi = m.priced_in(base27, base28)
    print("\nPriced in by consensus (at reference fuel/FX):")
    print(
        f"  package price/customer: FY27 £{pi['package_price_fy27']:.0f} "
        f"({pi['package_growth_fy27']:+.1%}), FY28 £{pi['package_price_fy28']:.0f} "
        f"({pi['package_growth_fy28']:+.1%})"
    )
    print(
        f"  flight-only yield: FY27 {pi['flight_only_growth_fy27']:+.1%}, "
        f"FY28 {pi['flight_only_growth_fy28']:+.1%}"
    )
    print(
        f"  P/E on consensus FY28: {market.price / m.CONSENSUS['FY28']:.1f}x "
        f"(fair {args.fair_pe:.1f}x)"
    )

    live27, live28, _ = m.scenario(market, [], w)
    c, (y27, y28) = m.card(market, shocks, args.weeks, args.fair_pe, args.sims)
    cols = {"FY26A": m.pnl(m.FY26), "FY27 live": m.pnl(live27), "FY28 live": m.pnl(live28)}
    if shocks:
        cols |= {"FY27 shock": m.pnl(y27), "FY28 shock": m.pnl(y28)}
    rows = m.REVENUE_LINES + m.COST_LINES + SHOWN
    print("\nP&L (£m, EPS p):")
    print(table(cols, rows))

    print(f"\nValuation at {c.horizon:%Y-%m-%d}:")
    print(
        f"  horizon P/E {c.horizon_pe:.2f}x x FY28 EPS {c.eps28:.1f}p "
        f"+ FY27 EPS vs consensus {c.eps27 - c.consensus27:+.1f}p"
    )
    print(
        f"  fair value {c.fair_value:.0f}p vs price {c.price:.0f}p: {c.upside:+.1%}  "
        f"score {c.score:+.2f}"
    )
    print(
        f"  Monte Carlo ({args.sims}): p10 {c.fv_p10:.0f}p  p50 {c.fv_p50:.0f}p  "
        f"p90 {c.fv_p90:.0f}p  P(fair value > price) {c.p_up:.0%}"
    )

    if shocks:
        plain = m.value(live27, live28, market.price, args.weeks, args.fair_pe)
        d27, d28 = c.eps27 - plain.eps27, c.eps28 - plain.eps28
        react = d28 * market.price / m.CONSENSUS["FY28"] + d27
        print(f"\nNews impact ({' '.join(shocks)}):")
        print(
            f"  EPS FY27 {d27:+.1f}p, FY28 {d28:+.1f}p; EBIT FY28 "
            f"{m.pnl(y28)['ebit'] - m.pnl(live28)['ebit']:+.0f}m"
        )
        print(
            f"  fair value {c.fair_value - plain.fair_value:+.0f}p "
            f"({c.fair_value / plain.fair_value - 1:+.1%}); "
            f"market reaction at today's multiple ~{react:+.0f}p ({react / market.price:+.1%})"
        )

    print("\nScenario sensitivities (fair value change vs live base, before your shocks):")
    plain_fv = m.value(live27, live28, market.price, args.weeks, args.fair_pe).fair_value
    for k, (desc, sh) in m.SCENARIOS.items():
        a27, a28, pe = m.scenario(market, sh, w)
        fv = m.value(a27, a28, market.price, args.weeks, args.fair_pe, pe).fair_value
        print(f"  {k:16} {fv - plain_fv:+6.0f}p {fv / plain_fv - 1:+6.1%}  {desc}")

    print("\nCatalysts within the horizon:")
    for d, t in m.catalysts(market.date, c.horizon) or [("-", "none listed")]:
        print(f"  {d}  {t}")

    if args.save:
        run_id = save_run("jet2_value", vars(args), TICKER, {"jet2_value": c}, daily=daily)
        print(f"\nSaved run {run_id}.")


if __name__ == "__main__":
    main()
