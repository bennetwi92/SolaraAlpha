"""Research 0017: does the crowd-momentum score pick Nasdaq-100 winners? (pre-registered)

uv run python scripts/research/r15_crowd_momentum.py fetch DIR   # 5 years of daily bars -> DIR
uv run python scripts/research/r15_crowd_momentum.py run DIR     # in-sample result, 4 variants
uv run python scripts/research/r15_crowd_momentum.py run DIR --holdout   # owner sign-off only

The design, the pass bar and the holdout are fixed in docs/research/0017 before any outcome was
computed. `run` never touches a holdout signal date without `--holdout`.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from solaraalpha.analysis.momentum import crowd_panel
from solaraalpha.analysis.regime import risk_off_panel
from solaraalpha.data.clean import check_prices, clean_prices, trim_to_consistent
from solaraalpha.data.prices import fetch_daily
from solaraalpha.pipeline import universe

MARKET = "^NDX"
FIRST_SIGNAL = pd.Timestamp("2022-11-23", tz="UTC")
IN_SAMPLE_END = pd.Timestamp("2025-11-21", tz="UTC")  # last forward close in-sample
HOLDOUT_START = pd.Timestamp("2025-11-24", tz="UTC")
H, HORIZONS = 10, (5, 10, 15)
TOP, COST = 10, 0.0035
NW_LAGS = H - 1
T_IC, T_ECON, BLOCKS, BLOCKS_POSITIVE = 2.5, 2.0, 6, 5
VARIANTS = {  # id: (score column, market gate)
    "A": ("crowd", False),
    "B": ("crowd", True),
    "C": ("crowd_beta", False),
    "D": ("crowd_beta", True),
}


def fetch(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for t in [MARKET, *universe()]:
        df = fetch_daily(t)
        df.to_parquet(out / f"{t.replace('^', '_')}.parquet")
        print(f"{t}: {len(df)} sessions {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}")


def _load(out: Path, t: str) -> pd.DataFrame:
    df = pd.read_parquet(out / f"{t.replace('^', '_')}.parquet")
    return trim_to_consistent(clean_prices(df), check_prices(df))


def panel(out: Path) -> tuple[pd.DataFrame, pd.Series]:
    """Long frame (date, ticker): both scores and the forward returns; and the risk-off series."""
    market = _load(out, MARKET)["close"]
    rows = []
    for t in universe():
        d = _load(out, t)
        p = crowd_panel(d, market)[["crowd", "crowd_beta"]]
        entry = d["open"].shift(-1)  # the next session's open: the signal close is never the entry
        for h in HORIZONS:
            p[f"fwd{h}"] = d["close"].shift(-h) / entry - 1
        p["ticker"] = t
        rows.append(p)
    long = pd.concat(rows).reset_index().rename(columns={"index": "date"})
    return long, risk_off_panel(market)


def newey_west_t(x: pd.Series, lags: int = NW_LAGS) -> float:
    """Mean / Newey-West standard error (Bartlett weights), for overlapping windows."""
    x = x.dropna().to_numpy()
    n = len(x)
    if n < lags + 2:
        return math.nan
    e = x - x.mean()
    var = e @ e / n
    for k in range(1, lags + 1):
        var += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / math.sqrt(var / n)) if var > 0 else math.nan


def daily_ic(df: pd.DataFrame, score: str, h: int = H) -> pd.Series:
    def ic(g: pd.DataFrame) -> float:
        g = g[[score, f"fwd{h}"]].dropna()
        return g[score].rank().corr(g[f"fwd{h}"].rank()) if len(g) >= 20 else math.nan

    return df.groupby("date")[[score, f"fwd{h}"]].apply(ic).dropna()


def book(df: pd.DataFrame, score: str, gate: pd.Series | None, h: int = H) -> pd.DataFrame:
    """Per signal date: the top-10's mean forward return net of cost (0 in cash on a gated day)
    and the universe's equal-weight mean."""
    rows = []
    for day, g in df.groupby("date"):
        g = g.dropna(subset=[score, f"fwd{h}"])
        if len(g) < 2 * TOP:
            continue
        uni = float(g[f"fwd{h}"].mean())
        if gate is not None and bool(gate.get(day, False)):
            strat, picks = 0.0, []
        else:
            top = g.nlargest(TOP, score)
            strat, picks = float(top[f"fwd{h}"].mean()) - COST, list(top["ticker"])
        rows.append({"date": day, "strat": strat, "uni": uni, "picks": picks})
    out = pd.DataFrame(rows).set_index("date")
    out["excess"] = out["strat"] - out["uni"]
    return out


def best_contributors(df: pd.DataFrame, b: pd.DataFrame, h: int = H, k: int = 3) -> list[str]:
    fwd = df.set_index(["date", "ticker"])[f"fwd{h}"]
    contrib: dict[str, float] = {}
    for day, picks in b["picks"].items():
        for t in picks:
            contrib[t] = contrib.get(t, 0.0) + float(fwd.get((day, t), 0.0)) / TOP
    return sorted(contrib, key=lambda t: -contrib[t])[:k]


def blocks_positive(ic: pd.Series) -> int:
    return int(sum(part.mean() > 0 for part in np.array_split(ic, BLOCKS)))


def run(out: Path, holdout: bool) -> None:
    long, risk_off = panel(out)
    last_signal = long.loc[long["date"] <= IN_SAMPLE_END, "date"].drop_duplicates().sort_values()
    in_end = last_signal.iloc[-max(HORIZONS) - 1]  # whole 15-session window ends in-sample
    if holdout:
        print("HOLDOUT: owner sign-off required (research 0017). Holdout signal dates only.\n")
        df = long[long["date"] >= HOLDOUT_START]
    else:
        df = long[(long["date"] >= FIRST_SIGNAL) & (long["date"] <= in_end)]
    dates = df["date"].drop_duplicates()
    print(
        f"Signal dates {dates.min():%Y-%m-%d} .. {dates.max():%Y-%m-%d}: {len(dates)} sessions,"
        f" {df['ticker'].nunique()} stocks; risk-off on {int(risk_off.reindex(dates).sum())}\n"
    )
    ics = {s: daily_ic(df, s) for s in ("crowd", "crowd_beta")}
    print("variant  IC10   t(NW)  blocks+  | top10 net  universe  excess  t(NW)  drop-3  pass")
    for v, (score, gated) in VARIANTS.items():
        ic = ics[score]
        t_ic, pos = newey_west_t(ic), blocks_positive(ic)
        b = book(df, score, risk_off if gated else None)
        t_ex = newey_west_t(b["excess"])
        drop = best_contributors(df, b)
        b3 = book(df[~df["ticker"].isin(drop)], score, risk_off if gated else None)
        detection = ic.mean() > 0 and t_ic >= T_IC and pos >= BLOCKS_POSITIVE
        economics = (
            b["excess"].mean() > 0
            and t_ex >= T_ECON
            and b["strat"].mean() > 0
            and b3["excess"].mean() > 0
        )
        print(
            f"   {v}    {ic.mean():+.3f}  {t_ic:+5.2f}   {pos}/{BLOCKS}   |"
            f"  {b['strat'].mean():+.2%}   {b['uni'].mean():+.2%}  {b['excess'].mean():+.2%}"
            f"  {t_ex:+5.2f}  {b3['excess'].mean():+.2%}  "
            f"{'PASS' if detection and economics else 'fail'}"
            f"  (drop {', '.join(drop)})"
        )
    print("\nReported, not tested: mean rank IC at other horizons")
    for h in HORIZONS:
        line = "  ".join(f"{s} {daily_ic(df, s, h).mean():+.3f}" for s in ics)
        print(f"  {h:>2} sessions: {line}")
    print("\nIC by year (crowd / crowd_beta):")
    for y in sorted({d.year for d in dates}):
        parts = [f"{ics[s][ics[s].index.year == y].mean():+.3f}" for s in ics]
        print(f"  {y}: {' / '.join(parts)}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("command", choices=("fetch", "run"))
    p.add_argument("dir", type=Path)
    p.add_argument("--holdout", action="store_true", help="the locked holdout (owner sign-off)")
    args = p.parse_args(argv)
    if args.command == "fetch":
        fetch(args.dir)
    else:
        run(args.dir, args.holdout)


if __name__ == "__main__":
    sys.exit(main())
