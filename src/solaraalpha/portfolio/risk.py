"""Holdings, cash, equity and portfolio heat from the book, against the rules.

Pure functions over the book's position states, a ``{ticker: last_price}`` mapping (quote
currency, as Yahoo gives it) and the rules; :func:`snapshot` reads the book for them. Money is
in the account currency (GBP, :func:`solaraalpha.portfolio.book.to_account`). Equity is what
sizing uses, so it is rebuilt from the record every time, never stored and reused.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from solaraalpha.analysis.relative_strength import BENCHMARKS
from solaraalpha.portfolio import book
from solaraalpha.portfolio.book import PositionState, to_account
from solaraalpha.portfolio.rules import Rules
from solaraalpha.store import Conn, query

HOLDING_COLS = [
    "position_id",
    "ticker",
    "shares",
    "avg_cost",
    "last",
    "value",
    "unrealised",
    "unrealised_pct",
    "r",
    "stop",
    "risk",
    "weeks_held",
    "next_event",
    "sector",
]


def sector_of(ticker: str, path: Path = BENCHMARKS) -> str:
    """The stock's ``sector`` from ``config/benchmarks.toml``, else ``"unknown"``."""
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    return str(cfg.get("stocks", {}).get(ticker, {}).get("sector", "unknown"))


@dataclass(frozen=True)
class Equity:
    """Account money view (GBP)."""

    cash: float  # deposits - withdrawals + realised P&L + dividends + interest - fees - open cost
    market_value: float  # open positions at the last price
    equity: float  # cash + market value
    realised_pnl: float  # all positions, net of costs
    unrealised_pnl: float  # open positions vs average cost
    deposits: float
    withdrawals: float
    dividends: float
    open_cost: float  # average cost x shares of the open positions
    positions: int  # open positions


def holdings(
    states: list[PositionState],
    prices: Mapping[str, float],
    next_event: Mapping[str, str] | None = None,
    as_of: str | pd.Timestamp | None = None,
    sectors: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """One row per open position (:data:`HOLDING_COLS`). A ticker missing from ``prices`` gets
    NaN for the price-based columns."""
    now = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC")
    now = now.tz_localize("UTC") if now.tz is None else now
    rows = []
    for st in states:
        if st.status != "open" or st.shares <= 0:
            continue
        last = prices.get(st.ticker, float("nan"))
        value = to_account(last * st.shares, st.currency)
        unreal = to_account((last - st.avg_cost) * st.shares * st.side, st.currency)
        opened = pd.Timestamp(st.opened_at) if st.opened_at else now
        rows.append(
            {
                "position_id": st.position_id,
                "ticker": st.ticker,
                "shares": st.shares,
                "avg_cost": st.avg_cost,
                "last": last,
                "value": value,
                "unrealised": unreal,
                "unrealised_pct": unreal / to_account(st.avg_cost * st.shares, st.currency),
                "r": unreal / st.r_unit if st.r_unit else float("nan"),
                "stop": st.stop,
                "risk": st.risk_at_stop,
                "weeks_held": (now - opened).total_seconds() / (7 * 86400),
                "next_event": (next_event or {}).get(st.ticker, ""),
                "sector": (sectors or {}).get(st.ticker) or sector_of(st.ticker),
            }
        )
    return pd.DataFrame(rows, columns=HOLDING_COLS)


def equity(cash: pd.DataFrame, states: list[PositionState], held: pd.DataFrame) -> Equity:
    """Account view from the cash events, every position's realised P&L and the open holdings."""
    cash = cash.copy()
    cash["gbp"] = [to_account(a, c) for a, c in zip(cash["amount"], cash["currency"], strict=True)]
    by = cash.groupby("kind")["gbp"].sum()
    deposits, withdrawals = float(by.get("deposit", 0.0)), float(by.get("withdrawal", 0.0))
    dividends, interest, fees = (float(by.get(k, 0.0)) for k in ("dividend", "interest", "fee"))
    realised = float(sum(st.realised_pnl for st in states))
    open_cost = float(
        sum(
            to_account(st.avg_cost * st.shares, st.currency)
            for st in states
            if st.status == "open" and st.shares > 0
        )
    )
    cash_now = deposits - withdrawals + realised + dividends + interest - fees - open_cost
    mv = float(held["value"].fillna(0).sum()) if not held.empty else 0.0
    return Equity(
        cash=cash_now,
        market_value=mv,
        equity=cash_now + mv,
        realised_pnl=realised,
        unrealised_pnl=float(held["unrealised"].fillna(0).sum()) if not held.empty else 0.0,
        deposits=deposits,
        withdrawals=withdrawals,
        dividends=dividends,
        open_cost=open_cost,
        positions=len(held),
    )


def heat(held: pd.DataFrame, eq: Equity) -> float:
    """Sum of open risk to the stops / equity."""
    return float(held["risk"].sum()) / eq.equity if eq.equity > 0 and not held.empty else 0.0


def exposure(held: pd.DataFrame, eq: Equity) -> pd.Series:
    """Market value by sector / equity, largest first."""
    if held.empty or eq.equity <= 0:
        return pd.Series(dtype="float64", name="exposure")
    by = held.groupby("sector")["value"].sum() / eq.equity
    return by.sort_values(ascending=False).rename("exposure")


def breaches(held: pd.DataFrame, eq: Equity, rules: Rules) -> list[str]:
    """Human-readable limit breaches against ``rules``; empty when all is within limits."""
    out = []
    if len(held) > rules.max_positions:
        out.append(f"{len(held)} open positions > max_positions {rules.max_positions}")
    if eq.equity > 0:
        for ticker, value in zip(held["ticker"], held["value"], strict=True):
            share = float(value) / eq.equity if pd.notna(value) else 0.0
            if share > rules.max_position_pct:
                out.append(
                    f"{ticker} is {share:.0%} of equity > max_position_pct {rules.max_position_pct:.0%}"
                )
        for sector, share in exposure(held, eq).items():
            if share > rules.max_sector_pct:
                out.append(
                    f"sector {sector} is {share:.0%} > max_sector_pct {rules.max_sector_pct:.0%}"
                )
        h = heat(held, eq)
        if h > rules.max_heat:
            out.append(f"heat {h:.1%} > max_heat {rules.max_heat:.1%}")
        if eq.cash < rules.cash_buffer * eq.equity:
            out.append(
                f"cash £{eq.cash:,.0f} < cash_buffer {rules.cash_buffer:.0%} of equity"
                f" (£{rules.cash_buffer * eq.equity:,.0f})"
            )
    return out


def next_events(conn: Conn) -> dict[str, str]:
    """``next_event`` of the latest ``events`` card per ticker (blank when none)."""
    df = query(
        "SELECT x.ticker, x.text FROM results x WHERE x.model = 'events' AND x.field = 'next_event'"
        " AND x.run_id = (SELECT MAX(run_id) FROM results y WHERE y.ticker = x.ticker"
        " AND y.model = 'events')",
        conn,
    )
    return {str(t): str(v) for t, v in zip(df["ticker"], df["text"], strict=True) if v}


def snapshot(
    conn: Conn, prices: Mapping[str, float], as_of: str | pd.Timestamp | None = None
) -> tuple[pd.DataFrame, Equity, list[PositionState]]:
    """Read the book: ``(holdings, equity, states)`` at ``prices``."""
    ids = [int(i) for i in book.positions(conn)["position_id"]]
    states = [book.position_state(conn, i) for i in ids]
    held = holdings(states, prices, next_events(conn), as_of)
    return held, equity(book.cash(conn), states, held), states
