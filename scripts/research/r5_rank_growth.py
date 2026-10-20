"""Research 0002 and 0003 (#223): missing = neutral in the rank, and growth IC with real lags.

uv run python scripts/research/r5_rank_growth.py PANEL_DIR churn     # 0002 step 1: churn + MDE
uv run python scripts/research/r5_rank_growth.py PANEL_DIR movers    # 0002 step 2: outcomes
uv run python scripts/research/r5_rank_growth.py PANEL_DIR growth    # 0003

PANEL_DIR holds one <ticker>.parquet per stock (scripts/research/build_panel.py): per Friday
trend, compression, growth_stat / growth_pub, adv, atr_pct, ret_8w, burst_2/3/4. In-sample weeks
only (ADR 0029); the holdout is never read. Research only; prints, writes nothing.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

FIRST = pd.Timestamp("2022-12-16", tz="UTC")
LAST_BURST = pd.Timestamp("2025-08-08", tz="UTC")  # last in-sample signal Friday, 50 sessions
LAST_8W = pd.Timestamp("2025-08-22", tz="UTC")  # last in-sample signal Friday, 8 weeks
MIN_ADV = 25_000.0
TOP = 7
BLOCK = 16
REPS = 2000
SEED = 223


def load(panel: Path) -> pd.DataFrame:
    df = pd.concat(
        [pd.read_parquet(p) for p in sorted(panel.glob("*.L.parquet"))], ignore_index=True
    )
    df = df[(df["week"] >= FIRST) & (df["week"] <= LAST_8W)].copy()
    df.loc[df["week"] > LAST_BURST, ["burst_2", "burst_3", "burst_4"]] = np.nan
    df = df[df["adv"] >= MIN_ADV]
    df["xs_8w"] = df["ret_8w"] - df.groupby("week")["ret_8w"].transform("mean")
    return df.reset_index(drop=True)


def logistic(x: np.ndarray, y: np.ndarray, iters: int = 50) -> np.ndarray:
    """Coefficients of a logistic regression (intercept first) by IRLS."""
    X = np.column_stack([np.ones(len(x)), x])
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ b))
        w = p * (1 - p)
        step = np.linalg.solve(X.T @ (X * w[:, None]) + 1e-9 * np.eye(len(b)), X.T @ (y - p))
        b += step
        if np.abs(step).max() < 1e-8:
            break
    return b


def add_null(df: pd.DataFrame, target: str = "burst_3") -> pd.DataFrame:
    """``p_null_<target>``: a vol + ADV logistic null on the weekly percentile ranks of daily
    ATR % and log ADV, fitted on the pooled in-sample rows; ``adj_<target>`` = target - null."""
    df = df.copy()
    df["r_atr"] = df.groupby("week")["atr_pct"].rank(pct=True)
    df["r_adv"] = df.groupby("week")["adv"].transform(lambda s: np.log(s).rank(pct=True))
    ok = df[target].notna() & df["r_atr"].notna() & df["r_adv"].notna()
    x = df.loc[ok, ["r_atr", "r_adv"]].to_numpy()
    b = logistic(x, df.loc[ok, target].to_numpy())
    X = np.column_stack([np.ones(len(df)), df[["r_atr", "r_adv"]].to_numpy()])
    df[f"p_null_{target}"] = 1 / (1 + np.exp(-X @ b))
    df[f"adj_{target}"] = df[target] - df[f"p_null_{target}"]
    print(f"vol + ADV null for {target}: coef (const, r_atr, r_adv) = {np.round(b, 3)}")
    return df


def mbb(series: pd.Series, reps: int = REPS, block: int = BLOCK, seed: int = SEED) -> tuple:
    """Mean of a weekly series (NaN weeks skipped) and its 90% moving-block bootstrap interval."""
    v = series.to_numpy(dtype=float)
    n = len(v)
    rng = np.random.default_rng(seed)
    starts = np.arange(n - block + 1)
    out = []
    for _ in range(reps):
        idx = np.concatenate([np.arange(s, s + block) for s in rng.choice(starts, n // block + 1)])
        out.append(np.nanmean(v[idx[:n]]))
    return float(np.nanmean(v)), float(np.nanpercentile(out, 5)), float(np.nanpercentile(out, 95))


def mbb_pair(a: pd.Series, b: pd.Series, z: float = 90.0) -> tuple:
    """Mean of a - b with the same resampled weeks (paired), interval at ``z``%."""
    v = (a - b).to_numpy(dtype=float)
    n = len(v)
    rng = np.random.default_rng(SEED)
    starts = np.arange(n - BLOCK + 1)
    out = []
    for _ in range(REPS):
        idx = np.concatenate([np.arange(s, s + BLOCK) for s in rng.choice(starts, n // BLOCK + 1)])
        out.append(np.nanmean(v[idx[:n]]))
    lo = (100 - z) / 2
    return (
        float(np.nanmean(v)),
        float(np.nanpercentile(out, lo)),
        float(np.nanpercentile(out, 100 - lo)),
    )


def fmt(t: tuple, pct: bool = False, nd: int = 3) -> str:
    m = 100 if pct else 1
    u = " pp" if pct else ""
    return f"{t[0] * m:+.{1 if pct else nd}f} [{t[1] * m:+.{1 if pct else nd}f}, {t[2] * m:+.{1 if pct else nd}f}]{u}"


# ------------------------------------------------------------------ 0002: rank
def scores(df: pd.DataFrame, growth: str) -> pd.DataFrame:
    df = df.copy()
    f = df[["trend", "compression", growth]]
    df["current"] = f.mean(axis=1, skipna=True)
    df["neutral"] = f.fillna(0).sum(axis=1) / 3
    df.loc[f.isna().all(axis=1), ["current", "neutral"]] = np.nan
    df["has_growth"] = df[growth].notna()
    return df.dropna(subset=["current"])


def tops(df: pd.DataFrame, col: str) -> dict[pd.Timestamp, set[str]]:
    out = {}
    for w, g in df.groupby("week"):
        g = g.sort_values([col, "ticker"], ascending=[False, True])
        out[w] = set(g["ticker"].iloc[:TOP])
    return out


def churn(df: pd.DataFrame, growth: str) -> pd.DataFrame:
    s = scores(df, growth)
    cur, neu = tops(s, "current"), tops(s, "neutral")
    rows = []
    for w in sorted(cur):
        g = s[s["week"] == w]
        rows.append(
            {
                "week": w,
                "n": len(g),
                "changed": len(neu[w] - cur[w]),
                "rho": g["current"].rank().corr(g["neutral"].rank()),
                "share_no_growth": 1 - g["has_growth"].mean(),
                "top_cur_no_growth": (~g[g["ticker"].isin(cur[w])]["has_growth"]).sum(),
                "top_neu_no_growth": (~g[g["ticker"].isin(neu[w])]["has_growth"]).sum(),
            }
        )
    return pd.DataFrame(rows)


def movers(df: pd.DataFrame, growth: str) -> pd.DataFrame:
    s = scores(df, growth)
    cur, neu = tops(s, "current"), tops(s, "neutral")
    s["is_in"] = [t in neu[w] - cur[w] for w, t in zip(s["week"], s["ticker"], strict=True)]
    s["is_out"] = [t in cur[w] - neu[w] for w, t in zip(s["week"], s["ticker"], strict=True)]
    s["top_cur"] = [t in cur[w] for w, t in zip(s["week"], s["ticker"], strict=True)]
    s["top_neu"] = [t in neu[w] for w, t in zip(s["week"], s["ticker"], strict=True)]
    return s


def run_churn(df: pd.DataFrame) -> None:
    for growth in ("growth_pub", "growth_stat"):
        c = churn(df, growth)
        print(f"\n== churn, growth = {growth}: {len(c)} weeks")
        print(f"names past the floor per week: median {c['n'].median():.0f}")
        print(f"share of ranked names with no growth: mean {c['share_no_growth'].mean():.1%}")
        print(
            f"top-7 names changed per week: mean {c['changed'].mean():.2f}, "
            f"median {c['changed'].median():.0f}, distribution {c['changed'].value_counts().sort_index().to_dict()}"
        )
        print(f"weeks with any change: {(c['changed'] > 0).mean():.1%}")
        print(f"Spearman current vs neutral: mean {c['rho'].mean():.3f}")
        print(
            f"no-growth names in top 7: current {c['top_cur_no_growth'].mean():.2f}, neutral {c['top_neu_no_growth'].mean():.2f}"
        )
        burst_weeks = c[c["week"] <= LAST_BURST]
        k = burst_weeks["changed"].sum()
        print(
            f"mover pairs (in-sample burst weeks): {int(k)} in {int((burst_weeks['changed'] > 0).sum())} weeks"
        )


def run_movers(df: pd.DataFrame) -> None:
    df = add_null(df, "burst_3")
    for growth in ("growth_pub", "growth_stat"):
        s = movers(df, growth)
        print(f"\n== movers, growth = {growth}")
        ins, outs = s[s["is_in"]], s[s["is_out"]]
        for name, g in (("IN", ins), ("OUT", outs)):
            print(
                f"{name}: n {len(g)}, tickers {g['ticker'].nunique()}, no growth {(~g['has_growth']).mean():.0%}, "
                f"median ATR {g['atr_pct'].median():.1%}, burst_3 {g['burst_3'].mean():.1%}, "
                f"ret_8w {g['ret_8w'].mean():+.1%}, xs_8w {g['xs_8w'].mean():+.1%}, "
                f"null p {g['p_null_burst_3'].mean():.1%}"
            )
            print(f"   top contributors: {g['ticker'].value_counts().head(6).to_dict()}")
        weeks = sorted(s["week"].unique())
        for col, pct in (
            ("adj_burst_3", True),
            ("burst_3", True),
            ("burst_2", True),
            ("burst_4", True),
            ("ret_8w", True),
            ("xs_8w", True),
        ):
            a = ins.groupby("week")[col].mean().reindex(weeks)
            b = outs.groupby("week")[col].mean().reindex(weeks)
            print(f"IN - OUT {col}: {fmt(mbb(a - b), pct)}")
        for col in ("adj_burst_3", "burst_3", "xs_8w", "ret_8w"):
            a = s[s["top_neu"]].groupby("week")[col].mean().reindex(weeks)
            b = s[s["top_cur"]].groupby("week")[col].mean().reindex(weeks)
            print(f"top7 neutral - top7 current {col}: {fmt(mbb_pair(a, b), True)}")
        # drop the best 3 IN names (by summed burst / xs contribution)
        best = ins.groupby("ticker")["xs_8w"].sum().sort_values(ascending=False).index[:3]
        a = ins[~ins["ticker"].isin(best)].groupby("week")["xs_8w"].mean().reindex(weeks)
        b = outs.groupby("week")["xs_8w"].mean().reindex(weeks)
        print(f"IN - OUT xs_8w without best 3 IN names {list(best)}: {fmt(mbb(a - b), True)}")
        best = ins.groupby("ticker")["adj_burst_3"].sum().sort_values(ascending=False).index[:3]
        a = ins[~ins["ticker"].isin(best)].groupby("week")["adj_burst_3"].mean().reindex(weeks)
        b = outs.groupby("week")["adj_burst_3"].mean().reindex(weeks)
        print(f"IN - OUT adj_burst_3 without best 3 IN names {list(best)}: {fmt(mbb(a - b), True)}")
        # half-years
        hy = ins.assign(
            h=ins["week"].dt.year.astype(str) + "H" + ((ins["week"].dt.month > 6) + 1).astype(str)
        )
        ho = outs.assign(
            h=outs["week"].dt.year.astype(str) + "H" + ((outs["week"].dt.month > 6) + 1).astype(str)
        )
        t = pd.DataFrame(
            {
                "in_adj_burst": hy.groupby("h")["adj_burst_3"].mean(),
                "out_adj_burst": ho.groupby("h")["adj_burst_3"].mean(),
                "in_xs8": hy.groupby("h")["xs_8w"].mean(),
                "out_xs8": ho.groupby("h")["xs_8w"].mean(),
                "n_in": hy.groupby("h").size(),
            }
        )
        print(t.round(3))


# ------------------------------------------------------------------ 0003: growth IC
def weekly_ic(df: pd.DataFrame, sig: str, tgt: str) -> pd.Series:
    def ic(g: pd.DataFrame) -> float:
        g = g[[sig, tgt]].dropna()
        if len(g) < 10 or g[tgt].nunique() < 2 or g[sig].nunique() < 2:
            return math.nan
        return float(g[sig].rank().corr(g[tgt].rank()))

    return df.groupby("week")[[sig, tgt]].apply(ic)


def half(w: pd.Timestamp) -> str:
    h = f"{w.year}H{1 if w.month <= 6 else 2}"
    return "2023H1" if h == "2022H2" else h


def run_growth(df: pd.DataFrame) -> None:
    df = add_null(df, "burst_3")
    both = df["growth_stat"].notna() | df["growth_pub"].notna()
    d = df[both]
    diff = d["growth_stat"].ne(d["growth_pub"]) & ~(
        d["growth_stat"].isna() & d["growth_pub"].isna()
    )
    print(f"stock-weeks with any growth: {both.sum()}, tickers {d['ticker'].nunique()}")
    print(
        f"  stat non-NaN {df['growth_stat'].notna().sum()}, pub non-NaN {df['growth_pub'].notna().sum()}"
    )
    print(f"  stock-weeks where pub != stat: {diff.mean():.1%}")
    print(
        f"  corr(stat, pub) where both: {d[['growth_stat', 'growth_pub']].dropna().corr(method='spearman').iloc[0, 1]:.3f}"
    )
    print(
        f"  growth_n mean: stat {df.loc[df['growth_stat'].notna(), 'growth_n_stat'].mean():.2f}, pub {df.loc[df['growth_pub'].notna(), 'growth_n_pub'].mean():.2f}"
    )
    weeks = sorted(df["week"].unique())
    res = {}
    for tgt in ("burst_3", "adj_burst_3", "burst_2", "burst_4", "ret_8w", "xs_8w"):
        ics = {v: weekly_ic(df, f"growth_{v}", tgt).reindex(weeks) for v in ("stat", "pub")}
        res[tgt] = ics
        print(f"\n{tgt}:")
        for v in ("stat", "pub"):
            m90 = mbb(ics[v])
            m95 = mbb_pair(ics[v], pd.Series(0.0, index=ics[v].index), 95)
            print(
                f"  {v}: IC {fmt(m90)}  (95% {m95[1]:+.3f}, {m95[2]:+.3f}); weeks {ics[v].notna().sum()}"
            )
        print(f"  pub - stat (paired): {fmt(mbb_pair(ics['pub'], ics['stat']))}")
    print("\nhalf-years, IC vs adj_burst_3 and ret_8w:")
    rows = []
    for v in ("stat", "pub"):
        for tgt in ("adj_burst_3", "ret_8w"):
            s = res[tgt][v]
            rows.append(s.groupby([half(w) for w in s.index]).mean().rename(f"{v}:{tgt}"))
    print(pd.concat(rows, axis=1).round(3))
    # same weeks / names as the #210 run would not be identical: report the IC on rows where both exist
    common = df.dropna(subset=["growth_stat", "growth_pub"])
    for v in ("stat", "pub"):
        print(
            f"common rows {v} vs ret_8w: {fmt(mbb(weekly_ic(common, f'growth_{v}', 'ret_8w').reindex(weeks)))}; "
            f"vs adj_burst_3: {fmt(mbb(weekly_ic(common, f'growth_{v}', 'adj_burst_3').reindex(weeks)))}"
        )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("panel", type=Path)
    p.add_argument("step", choices=["churn", "movers", "growth"])
    a = p.parse_args(argv)
    df = load(a.panel)
    print(
        f"panel: {len(df)} stock-weeks past the floor, {df['ticker'].nunique()} tickers, "
        f"{df['week'].nunique()} weeks ({df['week'].min().date()} to {df['week'].max().date()})"
    )
    {"churn": run_churn, "movers": run_movers, "growth": run_growth}[a.step](df)


if __name__ == "__main__":
    main()
