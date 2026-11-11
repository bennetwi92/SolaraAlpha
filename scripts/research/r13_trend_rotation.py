"""Research 0015: pure trend following with weekly rotation (the owner's hypothesis, 2026-11-11).

uv run python scripts/research/r13_trend_rotation.py fetch PANEL_DIR  # daily bars, research universe
uv run python scripts/research/r13_trend_rotation.py build PANEL_DIR  # signals + next-week returns
uv run python scripts/research/r13_trend_rotation.py count PANEL_DIR  # power, turnover; no outcomes
uv run python scripts/research/r13_trend_rotation.py run PANEL_DIR    # the pre-registered test

PANEL_DIR is kept out of data/. ``fetch`` writes PANEL_DIR/raw/<ticker>.daily.parquet (Yahoo, from
2021-10-20) for every stock in config/research_universe.toml plus ^FTAI. ``build`` writes
PANEL_DIR/r13.parquet: per stock and signal Friday (2022-12-16 to 2025-10-03 only; holdout
Fridays are never built) the signals s4 / s13 / s26, adv, spread and the next-week return y
(Monday open to the next Monday open). Everything follows research 0015 as pre-registered.
Research only: nothing in the pipeline imports it.
"""

from __future__ import annotations

import math
import sys
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

from solaraalpha.analysis.liquidity import corwin_schultz
from solaraalpha.analysis.universe import research_tickers, weekly_adv
from solaraalpha.data.clean import check_prices, clean_prices, trim_to_consistent
from solaraalpha.data.prices import fetch_daily

START = "2021-10-20"
FIRST, LAST = pd.Timestamp("2022-12-16", tz="UTC"), pd.Timestamp("2025-10-03", tz="UTC")
FLOOR = 25_000.0  # live min_adv_gbp (config/portfolio.toml)
NS = (4, 13, 26)  # lookbacks, weeks (fixed in advance)
VOL_WEEKS = 26
TOP_K = 8  # the book's positions
SPREAD_SESSIONS = 60
BENCH = "^FTAI"
BLOCK, REPS, SEED = 16, 2000, 15
TESTS = 3  # Bonferroni: 96.7% intervals
HALVES = [
    ("H1 2023", "2022-12-01", "2023-06-30"),
    ("H2 2023", "2023-07-01", "2023-12-31"),
    ("H1 2024", "2024-01-01", "2024-06-30"),
    ("H2 2024", "2024-07-01", "2024-12-31"),
    ("H1 2025", "2025-01-01", "2025-06-30"),
    ("H2 2025", "2025-07-01", "2025-10-03"),
]


# --- data ---------------------------------------------------------------------------------


def _fetch(args: tuple[Path, str]) -> str:
    out, t = args
    p = out / "raw" / f"{t}.daily.parquet"
    if p.exists():
        return f"{t}: have"
    try:
        fetch_daily(t, start=START).to_parquet(p)
        return f"{t}: ok"
    except Exception as e:
        return f"{t}: FAILED {e}"


def fetch(out: Path) -> None:
    (out / "raw").mkdir(parents=True, exist_ok=True)
    with Pool(4) as p:
        for msg in p.imap_unordered(_fetch, [(out, t) for t in [*research_tickers(), BENCH]]):
            print(msg, flush=True)


def daily_bars(panel: Path, t: str) -> pd.DataFrame:
    raw = pd.read_parquet(panel / "raw" / f"{t}.daily.parquet")
    return trim_to_consistent(clean_prices(raw), check_prices(raw))


def weekly_frame(daily: pd.DataFrame) -> pd.DataFrame:
    """Per Friday on a full W-FRI calendar: the Friday adjusted close ``c``, the adjusted open of
    the week's first session ``o``, and the next-week return ``y`` for a signal on that Friday
    (first open after Friday t+1 / first open after Friday t - 1)."""
    adj_open = daily["open"] * daily["adj_close"] / daily["close"]
    w = pd.DataFrame(
        {
            "c": daily["adj_close"].resample("W-FRI").last(),
            "o": adj_open.resample("W-FRI").first(),
            "n": daily["close"].resample("W-FRI").count(),
        }
    )
    w.loc[w["n"] == 0, ["c", "o"]] = np.nan
    w["y"] = w["o"].shift(-2) / w["o"].shift(-1) - 1
    return w


def features(panel: Path, t: str) -> pd.DataFrame:
    daily = daily_bars(panel, t)
    w = weekly_frame(daily)
    lr = np.log(w["c"]).diff()
    vol = lr.rolling(VOL_WEEKS, min_periods=20).std()
    out = pd.DataFrame(index=w.index)
    for n in NS:
        out[f"s{n}"] = np.log(w["c"] / w["c"].shift(n)) / (vol * math.sqrt(n))
    out["up4"] = (lr.rolling(4, min_periods=4).min() > 0).astype(float).where(lr.notna())
    out["adv"] = weekly_adv(daily, pd.DatetimeIndex(w.index))
    cs = corwin_schultz(daily)
    sp = np.maximum(
        cs.rolling(SPREAD_SESSIONS, min_periods=20).median(),
        cs.rolling(SPREAD_SESSIONS, min_periods=20).mean(),
    )
    out["spread"] = sp.reindex(sp.index.union(w.index)).ffill().reindex(w.index)
    out["y"] = w["y"]
    out = out[(out.index >= FIRST) & (out.index <= LAST)]  # holdout Fridays never built
    out = out.rename_axis("week").reset_index()
    out.insert(0, "ticker", t)
    return out


def _one(args: tuple[Path, str]) -> pd.DataFrame:
    try:
        return features(*args)
    except Exception as e:  # a stock whose data breaks the build is reported, not fatal
        print(f"{args[1]}: {e}", file=sys.stderr)
        return pd.DataFrame()


def build(panel: Path) -> None:
    ticks = [t for t in research_tickers() if (panel / "raw" / f"{t}.daily.parquet").exists()]
    with ProcessPoolExecutor(4) as ex:
        frames = list(ex.map(_one, [(panel, t) for t in ticks]))
    df = pd.concat([f for f in frames if len(f)], ignore_index=True)
    df.to_parquet(panel / "r13.parquet")
    b = weekly_frame(daily_bars(panel, BENCH))
    b[(b.index >= FIRST) & (b.index <= LAST)][["y"]].to_parquet(panel / "r13_bench.parquet")
    print(f"{df['ticker'].nunique()} stocks, {len(df):,} stock-weeks")


def load(panel: Path, outcomes: bool) -> pd.DataFrame:
    df = pd.read_parquet(panel / "r13.parquet")
    if not outcomes:
        df = df.drop(columns=["y"])
    df = df[(df["adv"] >= FLOOR)]
    return df.reset_index(drop=True)


# --- the rotation -------------------------------------------------------------------------


def rotation(
    df: pd.DataFrame, sig: str, spreads: pd.DataFrame, cost_mult: float = 2.0
) -> pd.DataFrame:
    """Per Friday: the top-8 book (s > 0 only, 1/8 each, cash 0), its gross and net return, the
    equal-weight benchmark of every eligible stock, names bought, and each name's y.
    ``spreads`` (week x ticker) prices a sale of a name that has left the eligible set."""
    rows, prev = [], set()
    for wk, g in df.groupby("week", sort=True):
        g = g.dropna(subset=[sig])
        top = g[g[sig] > 0].nlargest(TOP_K, sig)
        pick = set(top["ticker"])
        y = top.set_index("ticker")["y"]
        missing = int(y.isna().sum())
        gross = float(y.fillna(0.0).sum()) / TOP_K
        buys, sells = pick - prev, prev - pick
        sp = spreads.loc[wk] if wk in spreads.index else pd.Series(dtype=float)
        cost = sum(float(np.nan_to_num(sp.get(t, 0.0))) for t in buys | sells) / TOP_K
        bench = float(g["y"].mean())
        rows.append(
            {
                "week": wk,
                "gross": gross,
                "net": gross - cost_mult * cost,
                "bench": bench,
                "held": len(pick),
                "buys": len(buys),
                "missing_y": missing,
                "picks": dict(y.fillna(0.0)),
            }
        )
        prev = pick
    out = pd.DataFrame(rows).set_index("week")
    out["excess"] = out["net"] - out["bench"]
    return out


def boot_mean(x: pd.Series) -> tuple[float, float]:
    """96.7% moving-block bootstrap interval of a weekly series' mean (16-week blocks)."""
    v = x.dropna().to_numpy(float)
    rng = np.random.default_rng(SEED)
    k = math.ceil(len(v) / BLOCK)
    out = [
        v[
            np.concatenate(
                [np.arange(s, s + BLOCK) for s in rng.integers(0, len(v) - BLOCK + 1, k)]
            )[: len(v)]
        ].mean()
        for _ in range(REPS)
    ]
    a = 100 * 0.1 / TESTS / 2
    lo, hi = np.percentile(out, [a, 100 - a])
    return float(lo), float(hi)


def halves(x: pd.Series) -> list[float]:
    out = []
    for _, a, b in HALVES:
        h = x[(x.index >= pd.Timestamp(a, tz="UTC")) & (x.index <= pd.Timestamp(b, tz="UTC"))]
        out.append(float(h.mean()) if len(h) else math.nan)
    return out


def weekly_ic(df: pd.DataFrame, sig: str) -> pd.Series:
    d = df.dropna(subset=[sig, "y"])

    def ic(g: pd.DataFrame) -> float:
        return float(g[sig].rank().corr(g["y"].rank())) if len(g) > 10 else math.nan

    return d.groupby("week").apply(ic, include_groups=False).dropna()


def ic_test(df: pd.DataFrame, sig: str) -> dict:
    ic = weekly_ic(df, sig)
    est = float(ic.mean())
    lo, hi = boot_mean(ic)
    hs = halves(ic)
    pos = sum(1 for h in hs if np.isfinite(h) and h > 0)
    d = df.dropna(subset=[sig, "y"])
    contrib = (d.groupby("week")[sig].rank(pct=True) - 0.5) * (
        d["y"] - d.groupby("week")["y"].transform("mean")
    )
    best = contrib.groupby(d["ticker"]).sum().nlargest(3).index
    ic3 = weekly_ic(df[~df["ticker"].isin(best)], sig)
    lo3, hi3 = boot_mean(ic3)
    return {
        "IC": est,
        "ci": (lo, hi),
        "halves": hs,
        "pos": pos,
        "ic3": (float(ic3.mean()), lo3, hi3, list(best)),
        "pass": bool(lo > 0 and pos >= 5 and lo3 > 0),
    }


def stats(r: pd.DataFrame, col: str) -> dict:
    v = r[col]
    eq = (1 + v).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    ann = float(eq.iloc[-1] ** (52 / len(v)) - 1)
    return {"ann": ann, "dd": dd, "mean": float(v.mean())}


def econ_test(df: pd.DataFrame, sig: str, spreads: pd.DataFrame) -> dict:
    r = rotation(df, sig, spreads)
    lo, hi = boot_mean(r["excess"])
    hs = halves(r["excess"])
    pos = sum(1 for h in hs if np.isfinite(h) and h > 0)
    contrib: dict[str, float] = {}
    for _wk, row in r.iterrows():
        for t, y in row["picks"].items():
            contrib[t] = contrib.get(t, 0.0) + (y - row["bench"]) / TOP_K
    best = pd.Series(contrib).nlargest(3).index
    r3 = rotation(df[~df["ticker"].isin(best)], sig, spreads)
    lo3, hi3 = boot_mean(r3["excess"])
    r1 = rotation(df, sig, spreads, cost_mult=1.0)
    held_weeks = [y for p in r["picks"] for y in p.values()]
    return {
        "r": r,
        "excess": float(r["excess"].mean()),
        "ci": (lo, hi),
        "halves": hs,
        "pos": pos,
        "ex3": (float(r3["excess"].mean()), lo3, hi3, list(best)),
        "pass": bool(lo > 0 and pos >= 5 and lo3 > 0),
        "net2": stats(r, "net"),
        "net1": stats(r1, "net"),
        "gross": stats(r, "gross"),
        "bench": stats(r, "bench"),
        "buys": float(r["buys"].mean()),
        "hold": float(r["held"].sum() / max(r["buys"].sum(), 1)),
        "hit": float(np.mean([y > 0 for y in held_weeks])) if held_weeks else math.nan,
        "missing": int(r["missing_y"].sum()),
        "cost": float((r["gross"] - r["net"]).mean()),
    }


# --- commands -----------------------------------------------------------------------------


def count(panel: Path) -> None:
    df = load(panel, outcomes=False)
    n = df.groupby("week").size()
    print(f"stock-weeks past £{FLOOR:,.0f}: {len(df):,} ({df['ticker'].nunique()} stocks)")
    print(f"Fridays: {n.size}; stocks per Friday median {n.median():.0f} (min {n.min()})")
    mde = 2.5 / math.sqrt(n.median()) / math.sqrt(n.size / 2)  # halved weeks: autocorrelation
    print(f"approx. minimum detectable IC: {mde:.3f}")
    df["y"] = 0.0  # turnover and cost need no outcome
    spreads = spread_table(panel)
    for s in (f"s{k}" for k in NS):
        r = rotation(df, s, spreads)
        print(
            f"{s}: names bought a week {r['buys'].mean():.1f}, slots filled {r['held'].mean():.1f}"
            f", cost at 2x {100 * (r['gross'] - r['net']).mean():.2f}% a week"
        )
    print(f"median spread (60-session max of median / mean): {df['spread'].median():.2%}")


def spread_table(panel: Path) -> pd.DataFrame:
    full = pd.read_parquet(panel / "r13.parquet", columns=["ticker", "week", "spread"])
    return full.pivot_table(index="week", columns="ticker", values="spread", aggfunc="last")


def fmt_h(hs: list[float], pct: bool) -> str:
    return " / ".join(f"{100 * h:+.2f}" if pct else f"{h:+.3f}" for h in hs)


def run(panel: Path) -> None:
    df = load(panel, outcomes=True)
    spreads = spread_table(panel)
    bench = pd.read_parquet(panel / "r13_bench.parquet")["y"]
    print("\n## Test 1: weekly IC vs next-week return\n")
    print(
        "| variant | IC | 96.7% interval | positive half-years | halves | without best 3 | pass |"
    )
    print("|---|---|---|---|---|---|---|")
    for k in NS:
        s = f"s{k}"
        t = ic_test(df, s)
        i3 = t["ic3"]
        print(
            f"| {s} | {t['IC']:+.4f} | {t['ci'][0]:+.4f} to {t['ci'][1]:+.4f} | {t['pos']} of 6 | "
            f"{fmt_h(t['halves'], False)} | {i3[0]:+.4f} ({i3[1]:+.4f} to {i3[2]:+.4f}) | "
            f"{'yes' if t['pass'] else 'no'} |"
        )
    print("\n## Test 2: top-8 weekly rotation after 2x costs\n")
    print(
        "| variant | weekly excess | 96.7% interval | positive half-years | halves (%) | "
        "without best 3 | pass |"
    )
    print("|---|---|---|---|---|---|---|")
    res = {}
    for k in NS:
        s = f"s{k}"
        e = res[s] = econ_test(df, s, spreads)
        x3 = e["ex3"]
        print(
            f"| {s} | {100 * e['excess']:+.3f}% | {100 * e['ci'][0]:+.3f} to "
            f"{100 * e['ci'][1]:+.3f} | {e['pos']} of 6 | {fmt_h(e['halves'], True)} | "
            f"{100 * x3[0]:+.3f}% ({100 * x3[1]:+.3f} to {100 * x3[2]:+.3f}) | "
            f"{'yes' if e['pass'] else 'no'} |"
        )
    print("\n## The strategy in money terms (reported, not the bar)\n")
    print(
        "| variant | annual gross | annual, 1x costs | annual, 2x costs | max drawdown (2x) | "
        "cost a week (2x) | names bought a week | weeks held | hit rate |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for s, e in res.items():
        print(
            f"| {s} | {e['gross']['ann']:+.1%} | {e['net1']['ann']:+.1%} | {e['net2']['ann']:+.1%}"
            f" | {e['net2']['dd']:.1%} | {e['cost']:.2%} | {e['buys']:.1f} | {e['hold']:.1f} | "
            f"{e['hit']:.1%} |"
        )
    e = next(iter(res.values()))
    b = bench.reindex(e["r"].index)
    beq = (1 + b.fillna(0)).cumprod()
    print(
        f"\nequal-weight eligible universe: annual {e['bench']['ann']:+.1%}, max drawdown "
        f"{e['bench']['dd']:.1%}; FTSE AIM All-Share: annual "
        f"{float(beq.iloc[-1] ** (52 / len(b)) - 1):+.1%}, max drawdown "
        f"{float((beq / beq.cummax() - 1).min()):.1%}; weeks {len(b)}"
    )
    print(
        "top-8 picks with no next-week price (counted as 0%): "
        + ", ".join(f"{s} {v['missing']}" for s, v in res.items())
    )
    print("\n## The owner's question: how often is next week up?\n")
    d = df.dropna(subset=["y"])
    print("| group | stock-weeks | next week up | mean next week |")
    print("|---|---|---|---|")
    print(f"| everyone | {len(d):,} | {(d['y'] > 0).mean():.1%} | {d['y'].mean():+.2%} |")
    for k in NS:
        s = f"s{k}"
        q = d.dropna(subset=[s])
        top = q[q[s] >= q.groupby("week")[s].transform(lambda v: v.quantile(0.9))]
        print(
            f"| top 10% by {s} | {len(top):,} | {(top['y'] > 0).mean():.1%} | "
            f"{top['y'].mean():+.2%} |"
        )
    u = d[d["up4"] == 1]
    print(
        f"| up in each of the last 4 weeks | {len(u):,} | {(u['y'] > 0).mean():.1%} | "
        f"{u['y'].mean():+.2%} |"
    )
    print(
        f"| (flat weeks count as not up; {(d['y'] == 0).mean():.1%} of all next weeks are flat) "
        "| | | |"
    )


if __name__ == "__main__":
    cmd, panel = sys.argv[1], Path(sys.argv[2])
    {"fetch": fetch, "build": build, "count": count, "run": run}[cmd](panel)
