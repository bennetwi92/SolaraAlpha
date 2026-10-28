"""Research 0008: what a results day costs a position sized from its stop, and what a gap cap buys.

uv run python scripts/research/r7_reaction_speed.py OUT_DIR collect   # the same data as 0007
uv run python scripts/research/r8_results_gap.py OUT_DIR

Every AIM watchlist stock is treated as held into each of its results days (`FR`, `IR`, `QRT`,
`QRF`) and trading statements (`TST`; both in the NSM's "Financial Results" category),
sized at 2% risk from a stop k x ATR(14) below the previous close, capped at 25% of equity.
The trade runs from the previous close to the close 5 sessions after the event (the stop as a
stop-market order: a gap below it fills at the open). The gap cap of research 0006 sizes the
same trade so that a move of g costs 4% of equity, g = the stock's worst past results-day move
when it has 4 or more, else the universe p90 of past |moves|, both point-in-time.
In-sample only (ADR 0029): sessions up to 2025-10-17. Research only; prints, writes nothing.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from solaraalpha.analysis.events import reaction_session
from solaraalpha.data.clean import check_prices, clean_prices, trim_to_consistent

RESULTS = frozenset({"FR", "IR", "QRT", "QRF"})  # finals, interims, quarterlies
UPDATES = frozenset({"TST"})  # trading statements: pre-close updates, profit warnings
LAST_SESSION = pd.Timestamp("2025-10-10", tz="UTC")  # +5 sessions ends by 2025-10-17 (ADR 0029)
RISK = 0.02
MAX_POSITION = 0.25
TAIL = 2 * RISK  # a loss over twice the planned risk
MIN_MOVES = 4  # pm.MIN_EVENT_MOVES
HOLD = 5
REPS = 2000
SEED = 8


def results_days(ann: pd.DataFrame, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    ann = ann[ann["type_code"].isin(RESULTS | UPDATES)]
    ann = ann.assign(kind=np.where(ann["type_code"].isin(RESULTS), "results", "update"))
    ann = ann.assign(session=[reaction_session(p, sessions) for p in ann["published"]])
    # results win when both land on one session
    ann = ann.dropna(subset=["session"]).sort_values("kind")
    return ann.groupby("session").first().reset_index()[["session", "kind", "type_code"]]


def trade(d: pd.DataFrame, i: int, stop: float) -> tuple[float, bool]:
    """Return from the close before session ``i`` to the exit, and whether the stop fired."""
    entry = d["close"].iloc[i - 1]
    for j in range(i, min(i + HOLD + 1, len(d))):
        o, lo = d["open"].iloc[j], d["low"].iloc[j]
        if o <= stop:
            return o / entry - 1, True
        if lo <= stop:
            return stop / entry - 1, True
    return d["close"].iloc[min(i + HOLD, len(d) - 1)] / entry - 1, False


def events(out: Path, atr_k: float) -> pd.DataFrame:
    with open("config/watchlist.toml", "rb") as f:
        tickers = tomllib.load(f)["tickers"]
    rows = []
    for ticker in tickers:
        paths = [out / f"{ticker}.{k}.parquet" for k in ("daily", "nsm")]
        if not all(p.exists() for p in paths):
            continue
        d = clean_prices(pd.read_parquet(paths[0]))
        d = trim_to_consistent(d, check_prices(d))
        if len(d) < 250:
            continue
        prev = d["close"].shift(1)
        tr = pd.concat(
            [d["high"] - d["low"], (d["high"] - prev).abs(), (d["low"] - prev).abs()], axis=1
        ).max(axis=1)
        atr = tr.rolling(14).mean().shift(1)  # known before the session
        sma50, sma200 = (d["close"].rolling(n).mean().shift(1) for n in (50, 200))
        for e in results_days(pd.read_parquet(paths[1]), pd.DatetimeIndex(d.index)).itertuples():
            i = d.index.get_loc(e.session)
            if not isinstance(i, int) or i < 200 or e.session > LAST_SESSION:
                continue
            ref = d["close"].iloc[i - 1]
            stop_pct = atr_k * atr.iloc[i] / ref
            ret, stopped = trade(d, i, ref * (1 - stop_pct))
            rows.append(
                dict(
                    ticker=ticker,
                    session=e.session,
                    kind=e.kind,
                    day0=d["close"].iloc[i] / ref - 1,
                    stop_pct=stop_pct,
                    ret=ret,
                    stopped=stopped,
                    uptrend=ref > sma50.iloc[i] > sma200.iloc[i],
                )
            )
    df = pd.DataFrame(rows).sort_values("session", ignore_index=True)
    # point-in-time gap estimate: only results days before this one
    res = df  # past results days and trading statements both inform the gap estimate
    g = []
    for e in df.itertuples():
        past = res[res["session"] < e.session]
        own = past.loc[past["ticker"] == e.ticker, "day0"]
        if len(own) >= MIN_MOVES:
            g.append(-min(own.min(), 0.0) or np.nan)
        else:
            g.append(past["day0"].abs().quantile(0.9) if len(past) >= 50 else np.nan)
    df["g"] = g
    df["value"] = np.minimum(RISK / df["stop_pct"], MAX_POSITION)  # fraction of equity
    df["value_cap"] = np.minimum(df["value"], TAIL / df["g"])
    df["pnl"] = df["value"] * df["ret"]
    df["pnl_cap"] = df["value_cap"] * df["ret"]
    return df.dropna(subset=["g"])


def summary(label: str, x: pd.DataFrame) -> None:
    rng = np.random.default_rng(SEED)
    diff = (x["pnl_cap"] - x["pnl"]).groupby(x["session"]).sum()
    boot = [rng.choice(diff.values, len(diff)).sum() / len(x) for _ in range(REPS)]
    lo, hi = np.quantile(boot, [0.05, 0.95])
    binds = (x["value_cap"] < x["value"]).mean()
    print(f"  {label:<26} n={len(x):<4} cap binds {binds:.0%}  stopped {x['stopped'].mean():.0%}")
    for arm, col in (("as today", "pnl"), ("gap cap", "pnl_cap")):
        p = x[col]
        print(
            f"    {arm:<9} loss > 2x risk {(p < -TAIL).mean():5.1%}   p95 loss {-p.quantile(0.05):5.2%}"
            f"   worst {-p.min():6.2%}   mean P&L {p.mean():+.3%} of equity"
        )
    print(
        f"    cap - today: mean {diff.sum() / len(x):+.3%} per trade, 90% CI [{lo:+.3%}, {hi:+.3%}]"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("out", type=Path)
    a = p.parse_args()
    for k in (2.0, 3.0):
        df = events(a.out, k)
        print(
            f"\nStop {k:g} x ATR(14): median stop {df['stop_pct'].median():.1%}, median size "
            f"{df['value'].median():.0%} of equity (25% cap binds {(df['value'] >= MAX_POSITION).mean():.0%})"
        )
        for kind, x in df.groupby("kind"):
            summary(f"{kind}", x)
            summary(f"{kind}, uptrend", x[x["uptrend"]])
        if k == 2.0:
            r = df["day0"]
            print(
                f"  results-day and update moves: n={len(r)}  |move| median {r.abs().median():.1%}"
                f"  p90 {r.abs().quantile(0.9):.1%}  below -20% {(r < -0.2).mean():.1%}"
                f"  above +20% {(r > 0.2).mean():.1%}"
            )


if __name__ == "__main__":
    main()
