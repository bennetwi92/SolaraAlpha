"""Holdings, cash, equity, heat and sector exposure against config/portfolio.toml.

uv run python scripts/portfolio.py [--save] [--price JET2.L=1520 ...] [--db PATH]
uv run python scripts/portfolio.py --history [--db PATH]

Fetches the last daily close of every held ticker (check / clean before use); --price overrides
or supplies a price (tests run offline this way). --save records a `portfolio` card per open
position and one under ticker "_PORTFOLIO" (equity, cash, heat) in a `portfolio` run.
--history rebuilds the daily equity curve from the record (cash, fills, daily closes of every
ticker ever held) and prints return, time-weighted return, max drawdown, the comparison with
`Rules.benchmark` and the correlated pairs among holdings (portfolio.performance). Prints the
placeholder-rules banner while `confirmed` is false.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from solaraalpha.data.clean import check_prices, clean_prices
from solaraalpha.data.prices import fetch_daily
from solaraalpha.portfolio import book, performance, risk
from solaraalpha.portfolio.rules import load_rules
from solaraalpha.store import save_ticker, start_run


@dataclass(frozen=True)
class HoldingCard:
    """One open position at the last price (money in GBP)."""

    date: pd.Timestamp
    position_id: int
    shares: int
    avg_cost: float
    last: float
    value: float
    unrealised: float
    unrealised_pct: float
    r: float
    stop: float
    risk: float
    weeks_held: float
    sector: str


@dataclass(frozen=True)
class PortfolioCard:
    date: pd.Timestamp
    equity: float
    cash: float
    market_value: float
    realised_pnl: float
    unrealised_pnl: float
    heat: float
    positions: int
    breaches: int


def last_prices(tickers: list[str], given: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    prices, notes = dict(given), []
    for t in tickers:
        if t in prices:
            continue
        try:
            daily = clean_prices(fetch_daily(t))
            issues = check_prices(daily)
            if not issues.empty:
                notes.append(
                    f"{t}: {len(issues)} price data-quality issues (scripts/stock_prices.py)"
                )
            prices[t] = float(daily["close"].iloc[-1])
        except Exception as e:
            notes.append(f"{t}: price unavailable ({e})")
    return prices, notes


def history(db: Path | None) -> str:
    """The equity-curve summary and correlated pairs, as text."""
    rules = load_rules()
    fetch = fetch_daily  # looked up at call time so tests can monkeypatch it
    conn = book.connect(db)
    try:
        fl = book.fills_with_tickers(conn)
        tickers = sorted(set(fl["ticker"])) if not fl.empty else []
        prices: dict[str, pd.DataFrame] = {}
        notes = []
        for t in [*tickers, rules.benchmark]:
            try:
                prices[t] = clean_prices(fetch(t))
            except Exception as e:
                notes.append(f"{t}: price history unavailable ({e})")
        bench = prices.pop(rules.benchmark, pd.DataFrame())
        bench_close = bench["close"] if not bench.empty else None
        summary = performance.history_from_book(conn, prices, bench_close)[2]
        open_tickers = sorted(set(book.positions(conn, status="open")["ticker"]))
    finally:
        conn.close()
    lines = [f"NOTE: {n}" for n in notes]
    if summary.start is None:
        return "\n".join([*lines, "No history yet: no cash events or fills in the book."])
    dd = summary.max_drawdown
    lines += [
        f"Equity curve {summary.start:%Y-%m-%d} → {summary.end:%Y-%m-%d}: £{summary.equity:,.0f}"
        f" on net flows £{summary.net_flows:,.0f} (P&L £{summary.pnl:,.0f})",
        f"Time-weighted return {summary.twr:+.2%}"
        + (
            f" vs {rules.benchmark} {summary.benchmark:+.2%} (excess {summary.excess:+.2%})"
            if summary.benchmark is not None
            else f" ({rules.benchmark} unavailable)"
        ),
        f"Max drawdown {dd.max_drawdown:.2%}"
        + (f" from {dd.peak:%Y-%m-%d} to {dd.trough:%Y-%m-%d}" if dd.peak is not None else ""),
    ]
    rets = {t: performance.weekly_returns(prices[t]) for t in open_tickers if t in prices}
    _, pairs = performance.correlation(rets)
    lines.append(
        "Correlated holdings (26w > 0.7): "
        + (", ".join(f"{a}/{b} {c:.2f}" for a, b, c in pairs) or "none")
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--price", action="append", default=[], help="TICKER=PRICE (quote currency)")
    p.add_argument("--save", action="store_true", help="record portfolio cards in the results DB")
    p.add_argument(
        "--history", action="store_true", help="equity curve, drawdown, benchmark, correlation"
    )
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    given = {k: float(v) for k, v in (kv.split("=", 1) for kv in args.price)}
    rules = load_rules()
    if args.history:
        if rules.warning:
            print(rules.warning)
        print(history(args.db))
        return

    conn = book.connect(args.db)
    try:
        open_tickers = sorted(set(book.positions(conn, status="open")["ticker"]))
        prices, notes = last_prices(open_tickers, given)
        held, eq, _ = risk.snapshot(conn, prices)
    finally:
        conn.close()
    h, ex, br = risk.heat(held, eq), risk.exposure(held, eq), risk.breaches(held, eq, rules)

    if rules.warning:
        print(rules.warning)
    for n in notes:
        print(f"NOTE: {n}")
    print("\nHoldings:")
    if held.empty:
        print("  (none)")
    else:
        show = held.copy()
        for c in ("value", "unrealised", "risk"):
            show[c] = show[c].map(lambda v: f"£{v:,.0f}")
        show["unrealised_pct"] = show["unrealised_pct"].map(lambda v: f"{v:+.1%}")
        show["r"] = show["r"].map(lambda v: f"{v:+.2f}")
        show["weeks_held"] = show["weeks_held"].map(lambda v: f"{v:.1f}")
        print("  " + show.to_string(index=False).replace("\n", "\n  "))
    print(
        f"\nEquity £{eq.equity:,.0f} = cash £{eq.cash:,.0f} + market value £{eq.market_value:,.0f}"
        f"   realised £{eq.realised_pnl:,.0f}  unrealised £{eq.unrealised_pnl:,.0f}"
        f"   deposits £{eq.deposits:,.0f}  withdrawals £{eq.withdrawals:,.0f}"
    )
    print(
        f"Heat {h:.1%} of equity (max {rules.max_heat:.0%}); {eq.positions} of {rules.max_positions} positions"
    )
    print("Exposure by sector: " + (", ".join(f"{s} {v:.0%}" for s, v in ex.items()) or "none"))
    print("Breaches: " + ("; ".join(br) if br else "none"))

    if args.save:
        today = pd.Timestamp(datetime.now(UTC).date())
        run_id = start_run("portfolio", vars(args), args.db)
        for row in held.itertuples(index=False):
            card = HoldingCard(
                today,
                int(row.position_id),
                int(row.shares),
                float(row.avg_cost),
                float(row.last),
                float(row.value),
                float(row.unrealised),
                float(row.unrealised_pct),
                float(row.r),
                float(row.stop) if row.stop is not None else float("nan"),
                float(row.risk),
                float(row.weeks_held),
                str(row.sector),
            )
            save_ticker(run_id, str(row.ticker), {"portfolio": card}, path=args.db)
        total = PortfolioCard(
            today,
            eq.equity,
            eq.cash,
            eq.market_value,
            eq.realised_pnl,
            eq.unrealised_pnl,
            h,
            eq.positions,
            len(br),
        )
        save_ticker(run_id, "_PORTFOLIO", {"portfolio": total}, path=args.db)
        print(f"saved run {run_id}")


if __name__ == "__main__":
    main()
