"""Research 0008: the evidence behind the relative-strength toolkit, under ADR 0029.

uv run python scripts/research/r8_relative_strength.py build PANEL_DIR   # one fetch per ticker
uv run python scripts/research/r8_relative_strength.py report PANEL_DIR  # the note's tables

``build`` writes one parquet per watchlist stock to PANEL_DIR (keep it out of data/): per Friday
the toolkit's own outputs (``rs``, market / industry / peers scores, market z per horizon, the
``leadership`` and ``rs_spike`` flags, from ``compare`` + ``technical.scores``, all trailing so
causal), the vol + ADV inputs of the null, ``burst_3`` (``burst_10w`` at 3 ATR, entry Monday's
open) and ``ret_8w`` (Monday's open to the close 8 weeks on). Rows stop at the last in-sample
signal Friday, so the holdout (Fridays 2025-10-24 to 2026-10-16) is never built, let alone read.
Research only: nothing in the pipeline imports it; nothing goes to the DB.
"""

from __future__ import annotations

import math
import sys
import traceback
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

from solaraalpha.analysis.backtest import entry_and_close
from solaraalpha.analysis.liquidity import traded_value
from solaraalpha.analysis.relative_strength import (
    SPIKE_Z,
    UPTREND,
    Z_CAP,
    compare,
    comparisons,
)
from solaraalpha.analysis.technical import atr, scores
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent
from solaraalpha.data.prices import fetch_daily
from solaraalpha.pipeline import watchlist

START = "2021-10-20"
SESSIONS = 50
LAST_BURST = pd.Timestamp("2025-08-08", tz="UTC")  # last in-sample signal Friday, 50 sessions
LAST_8W = pd.Timestamp("2025-08-22", tz="UTC")  # last in-sample signal Friday, 8 weeks
MIN_ADV = 25_000.0  # the screen's sanity floor (min_adv_gbp)
BLOCK, REPS, SEED = 16, 2000, 8
SIGNALS = ("rs", "market_z4", "market_z13", "market_z26")  # continuous, pre-registered
FLAGS = ("leadership", "rs_spike")  # binary, pre-registered
N_TESTS = 2 * (len(SIGNALS) + len(FLAGS))  # x two outcomes: the multiplicity adjustment
_cache: dict[str, pd.DataFrame] = {}


def fetch(t: str) -> pd.DataFrame:
    if t not in _cache:
        _cache[t] = fetch_daily(t, start=START)
    return _cache[t]


def burst(daily: pd.DataFrame, friday: pd.Timestamp, a: float, k: float = 3) -> float:
    """1 if high >= 1.15 x entry before low <= entry - k x ATR within 50 sessions from Monday's
    open after ``friday`` (same day both = stop first), else 0 (ADR 0029 ``burst_10w``)."""
    fut = daily[daily.index > friday + pd.Timedelta(days=1)].iloc[:SESSIONS]
    if fut.empty or not math.isfinite(a) or a <= 0:
        return math.nan
    entry = float(fut["open"].iloc[0])
    if not math.isfinite(entry) or entry <= 0:
        return math.nan
    tgt, stp = entry * 1.15, entry - k * a
    for lo, hi in zip(fut["low"].to_numpy(), fut["high"].to_numpy(), strict=True):
        if lo <= stp:
            return 0.0
        if hi >= tgt:
            return 1.0
    return 0.0 if len(fut) == SESSIONS else math.nan


def one(t: str, out: Path) -> str:
    try:
        raw = fetch(t)
        daily = clean_prices(raw)
        weekly = to_weekly(trim_to_consistent(daily, check_prices(raw)))
        c = compare(weekly, comparisons(t), fetch)
        tech = scores(weekly)
        df = pd.DataFrame(index=weekly.index)
        df["trend"] = tech["trend"]
        for k, f in c.frames.items():
            df[k] = f["score"]
            if k == "market":
                for h in (4, 13, 26):
                    df[f"market_z{h}"] = f[f"z_{h}"]
                df["rs_high"] = f["rs_high"] & f["te"].notna()
        df["rs"] = df[[k for k in ("market", "industry", "peers") if k in df]].mean(axis=1)
        up = df["trend"] >= UPTREND
        df["leadership"] = ((df["rs_high"] & up) | (df["market_z26"] >= Z_CAP)).astype(float)
        df["rs_spike"] = ((df["market_z4"] >= SPIKE_Z) & (df["trend"] < UPTREND)).astype(float)
        df.loc[df["rs"].isna() | df["trend"].isna(), list(FLAGS)] = np.nan
        entry, close = entry_and_close(weekly)
        df["ret_8w"] = close.shift(-8) / entry - 1
        df = df[df.index <= LAST_8W].dropna(subset=["rs", "market_z26"])
        datr = atr(daily)
        adv, atr_pct, b3 = [], [], []
        for wk in df.index:
            d = daily[daily.index <= wk + pd.Timedelta(days=1)]
            a = float(datr.loc[d.index[-1]])
            adv.append(traded_value(d, 20))
            atr_pct.append(a / float(d["close"].iloc[-1]))
            b3.append(burst(daily, wk, a) if wk <= LAST_BURST else math.nan)
        df["adv"], df["atr_pct"], df["burst_3"] = adv, atr_pct, b3
        df = df.drop(columns="rs_high").rename_axis("week").reset_index()
        df.insert(0, "ticker", t)
        df.to_parquet(out / f"{t}.parquet")
        return f"{t}: {len(df)} weeks ({c.used})"
    except Exception:
        return f"{t}: FAILED {traceback.format_exc(limit=2)}"


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ticks = [t for t in watchlist() if not (out / f"{t}.parquet").exists()]
    with Pool(6) as p:
        for msg in p.starmap(one, [(t, out) for t in ticks]):
            print(msg, flush=True)


# --- report -------------------------------------------------------------------------------


def load(panel: Path) -> pd.DataFrame:
    df = pd.concat([pd.read_parquet(p) for p in sorted(panel.glob("*.parquet"))])
    df = df[(df["week"] <= LAST_8W) & (df["adv"] >= MIN_ADV)].copy()
    df.loc[df["week"] > LAST_BURST, "burst_3"] = np.nan
    df["xs_8w"] = df["ret_8w"] - df.groupby("week")["ret_8w"].transform("mean")
    # vol + ADV null for burst (ADR 0029): logistic on the weekly ranks of ATR % and log ADV
    df["r_atr"] = df.groupby("week")["atr_pct"].rank(pct=True)
    df["r_adv"] = df.groupby("week")["adv"].transform(lambda s: np.log(s).rank(pct=True))
    ok = df["burst_3"].notna()
    X = np.column_stack([np.ones(len(df)), df["r_atr"], df["r_adv"]])
    b = np.zeros(3)
    for _ in range(50):  # IRLS
        p = 1 / (1 + np.exp(-X[ok] @ b))
        w = p * (1 - p)
        b += np.linalg.solve(X[ok].T @ (X[ok] * w[:, None]), X[ok].T @ (df.loc[ok, "burst_3"] - p))
    df["p_null"] = 1 / (1 + np.exp(-X @ b))
    df["adj_burst"] = df["burst_3"] - df["p_null"]
    return df.reset_index(drop=True)


def mbb(v: np.ndarray, alpha: float = 0.10) -> tuple[float, float, float]:
    """Mean of a weekly series and its (1 - alpha) moving-block bootstrap interval (16-week
    blocks: all stocks in a week move together, overlapping windows are covered)."""
    v = v[np.isfinite(v)]
    n = len(v)
    rng = np.random.default_rng(SEED)
    starts = np.arange(n - BLOCK + 1)
    means = np.empty(REPS)
    for i in range(REPS):
        idx = (rng.choice(starts, n // BLOCK + 1)[:, None] + np.arange(BLOCK)).ravel()[:n]
        means[i] = v[idx].mean()
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(v.mean()), float(lo), float(hi)


def weekly_stat(df: pd.DataFrame, fn) -> pd.Series:
    return df.groupby("week").apply(fn, include_groups=False).sort_index()


def ic(sig: str, out: str):
    """Weekly IC: the signal's rank against ``out``, ranked too for a return; a burst residual
    stays unranked (ranking it would sort the hits by their null, i.e. by volatility)."""

    def f(g: pd.DataFrame) -> float:
        g = g[[sig, out]].dropna()
        y = g[out] if out == "adj_burst" else g[out].rank()
        return g[sig].rank().corr(y) if len(g) >= 10 else np.nan

    return f


def gap(flag: str, out: str):
    """Flagged minus unflagged mean of ``out`` in one week (NaN without both groups)."""

    def f(g: pd.DataFrame) -> float:
        g = g[[flag, out]].dropna()
        a, b = g.loc[g[flag] == 1, out], g.loc[g[flag] == 0, out]
        return a.mean() - b.mean() if len(a) and len(b) else np.nan

    return f


def verdict(m: float, lo: float, hi: float, lo_adj: float, hi_adj: float, halves: str) -> str:
    if lo_adj > 0 or hi_adj < 0:
        return "real (survives multiple tests)"
    if lo > 0 or hi < 0:
        return "suggestive (90% only)"
    return "no evidence"


def row(name: str, s: pd.Series, scale: float) -> list:
    s = s.dropna()
    m, lo, hi = mbb(s.to_numpy())
    _, lo_a, hi_a = mbb(s.to_numpy(), alpha=0.10 / N_TESTS)
    half = s.groupby(s.index.year * 2 + (s.index.month > 6)).mean()
    agree = f"{int((np.sign(half) == np.sign(m)).sum())}/{len(half)}"
    return [
        name,
        f"{m * scale:+.3f}" if scale == 1 else f"{m * scale:+.1f}",
        f"[{lo * scale:+.2f}, {hi * scale:+.2f}]",
        agree,
        verdict(m, lo, hi, lo_a, hi_a, agree),
    ]


def table(head: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return "\n".join([*lines, *("| " + " | ".join(map(str, r)) + " |" for r in rows)])


def without_best(df: pd.DataFrame, flag: str) -> float:
    """Pooled flagged minus unflagged mean 8w excess after dropping the 3 stocks whose flagged
    weeks add the most excess (is the gap a few names?)."""
    best = df[df[flag] == 1].groupby("ticker")["xs_8w"].sum().nlargest(3).index
    d = df[~df["ticker"].isin(best)]
    return float(d.loc[d[flag] == 1, "xs_8w"].mean() - d.loc[d[flag] == 0, "xs_8w"].mean())


def report(panel: Path) -> None:
    df = load(panel)
    wk = df["week"]
    print(
        f"panel: {len(df):,} stock-weeks, {df['ticker'].nunique()} stocks, "
        f"{wk.nunique()} weeks ({wk.min():%Y-%m-%d} to {wk.max():%Y-%m-%d}); "
        f"~{wk.nunique() / 8:.0f} independent 8-week periods; burst base rate "
        f"{df['burst_3'].mean():.1%}; tests {N_TESTS}\n"
    )
    head = ["signal", "mean", "90% interval", "half-years same sign", "verdict"]
    for out, label in (("adj_burst", "burst_10w (vs vol + ADV null)"), ("xs_8w", "8w excess")):
        rows = [row(s, weekly_stat(df, ic(s, out)), 1) for s in SIGNALS]
        print(f"### Rank IC vs {label}\n\n{table(head, rows)}\n")
    for out, label, scale in (("adj_burst", "burst_10w, pp", 100), ("xs_8w", "8w excess, %", 100)):
        rows = [row(f, weekly_stat(df, gap(f, out)), scale) for f in FLAGS]
        print(f"### Flagged minus unflagged ({label})\n\n{table(head, rows)}\n")
    rows = []
    for f in FLAGS:
        g = df[df[f] == 1]
        rows.append(
            [
                f,
                f"{len(g):,} ({len(g) / df[f].notna().sum():.0%})",
                g["ticker"].nunique(),
                f"{g['burst_3'].mean():.1%}",
                f"{g['p_null'][g['burst_3'].notna()].mean():.1%}",
                f"{g['ret_8w'].mean():+.1%}",
                f"{g['xs_8w'].median():+.1%}",
                f"{without_best(df, f):+.1%}",
            ]
        )
    rows.append(
        [
            "all",
            f"{len(df):,}",
            df["ticker"].nunique(),
            f"{df['burst_3'].mean():.1%}",
            f"{df['p_null'][df['burst_3'].notna()].mean():.1%}",
            f"{df['ret_8w'].mean():+.1%}",
            "0",
            "",
        ]
    )
    print("### The flags in plain numbers\n")
    print(
        table(
            [
                "group",
                "stock-weeks",
                "stocks",
                "hit +15% first",
                "expected from vol",
                "mean 8w return",
                "median 8w excess",
                "excess gap without best 3 stocks",
            ],
            rows,
        )
    )
    # Quintiles of rs: does the burst rate rise with RS?
    df["q"] = (
        df.groupby("week")["rs"].transform(
            lambda s: pd.qcut(s.rank(method="first"), 5, labels=False)
        )
        + 1
    )
    b = df[df["burst_3"].notna()]
    qs = pd.DataFrame(
        {
            "count": df.groupby("q").size(),
            "burst": b.groupby("q")["burst_3"].mean(),
            "null": b.groupby("q")["p_null"].mean(),
            "xs": df.groupby("q")["xs_8w"].mean(),
        }
    )
    print("\n### rs quintile (1 = weakest, 5 = strongest)\n")
    print(
        table(
            ["quintile", "n", "hit +15% first", "expected from vol", "mean 8w excess"],
            [
                [
                    int(q),
                    f"{int(r['count']):,}",
                    f"{r['burst']:.1%}",
                    f"{r['null']:.1%}",
                    f"{r['xs']:+.1%}",
                ]
                for q, r in qs.iterrows()
            ],
        )
    )


if __name__ == "__main__":
    {"build": build, "report": report}[sys.argv[1]](Path(sys.argv[2]))
