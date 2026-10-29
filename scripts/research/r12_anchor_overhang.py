"""Research 0014: the 52-week-high anchor and the capital-gains overhang (#286, edges B1, B2).

uv run python scripts/research/r12_anchor_overhang.py build PANEL_DIR   # features, no outcomes
uv run python scripts/research/r12_anchor_overhang.py count PANEL_DIR   # power, no outcomes
uv run python scripts/research/r12_anchor_overhang.py run PANEL_DIR     # the pre-registered test
uv run python scripts/research/r12_anchor_overhang.py leavers PANEL_DIR # the survivorship share
uv run python scripts/research/r12_anchor_overhang.py holdout PANEL_DIR # V3 only, owner's sign-off

PANEL_DIR is a research-universe panel (``build_panel.py PANEL_DIR research``; its daily bars in
PANEL_DIR/raw/<ticker>.daily.parquet, filings ``<ticker>.ann.parquet``, fundamentals
``<ticker>.fund.parquet``). ``build`` writes PANEL_DIR/r12_features.parquet: per stock and Friday
``nearness``, ``cgo``, ``new_high`` and ``good_news``, from prices, volumes and filing dates up to
that Friday only. Signal Fridays after 2025-08-08 are dropped on load and never read (ADR 0029
holdout), except by ``holdout``: V3 only, after the owner's sign-off (amendment 2026-10-29). Everything follows research 0014 as pre-registered. Research only.
"""

from __future__ import annotations

import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from solaraalpha.analysis.events import reactions
from solaraalpha.analysis.universe import SIZES, research_tickers, shares_at
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent

FIRST, LAST = pd.Timestamp("2022-12-16", tz="UTC"), pd.Timestamp("2025-08-08", tz="UTC")
YEAR = 52  # weeks: the 52-week high and the overhang's turnover window (fixed in advance)
NEAR = 0.95  # variant 3: nearness at least this
GOOD_Z = 2.0  # variant 3: a good-news day (research 0007)
# business-news filing types (research 0007, r7_reaction_speed.NEWS)
NEWS = frozenset(
    {"TST", "UPD", "IR", "FR", "QRT", "ACQ", "DIS", "CNT", "AGR", "REA", "DRL", "MSCL", "STR"}
    | {"RTT", "AGM", "IOE", "MER", "OFF", "SUS", "PDI", "FUR", "TAB", "SOA", "OUP", "PLC", "CAR"}
)
BLOCK, REPS, SEED = 16, 2000, 286
TESTS = 3  # Bonferroni
HALVES = [
    ("H1 2023", "2022-12-01", "2023-06-30"),
    ("H2 2023", "2023-07-01", "2023-12-31"),
    ("H1 2024", "2024-01-01", "2024-06-30"),
    ("H2 2024", "2024-07-01", "2024-12-31"),
    ("H1 2025", "2025-01-01", "2025-06-30"),
    ("H2 2025", "2025-07-01", "2025-08-08"),
]


# --- features (no outcomes) ----------------------------------------------------------------


def reference_price(close: np.ndarray, turnover: np.ndarray, n: int = YEAR) -> np.ndarray:
    """Grinblatt & Han's turnover-weighted reference price per week from the previous ``n``
    weeks: weight on week t-k = V[t-k] x prod_{j<k} (1 - V[t-j]), normalised to sum to 1."""
    out = np.full(len(close), np.nan)
    v = np.clip(turnover, 0.0, 1.0)
    for t in range(n, len(close)):
        w = np.empty(n)
        keep = 1.0
        for k in range(1, n + 1):
            w[k - 1] = v[t - k] * keep
            keep *= 1 - v[t - k]
        if w.sum() > 0 and np.isfinite(w).all():
            out[t] = float(np.dot(w, close[t - n : t][::-1]) / w.sum())
    return out


def features(panel: Path, t: str) -> pd.DataFrame:
    raw = pd.read_parquet(panel / "raw" / f"{t}.daily.parquet")
    daily = clean_prices(raw)
    weekly = to_weekly(trim_to_consistent(daily, check_prices(raw)))
    if len(weekly) <= YEAR:
        return pd.DataFrame()
    hi = weekly["high"].rolling(YEAR, min_periods=YEAR).max()
    prior = weekly["high"].shift(1).rolling(YEAR, min_periods=YEAR).max()
    fp = panel / f"{t}.fund.parquet"
    fund = pd.read_parquet(fp) if fp.exists() else None
    shares = shares_at(fund, pd.DatetimeIndex(weekly.index))["shares"].to_numpy()
    turnover = weekly["volume"].to_numpy(float) / shares
    out = pd.DataFrame(index=weekly.index)
    out["nearness"] = weekly["close"] / hi
    out["new_high"] = weekly["close"] > prior
    rp = reference_price(weekly["close"].to_numpy(float), turnover)
    out["cgo"] = (weekly["close"].to_numpy(float) - rp) / weekly["close"].to_numpy(float)
    out["good_news"] = False
    ap = panel / f"{t}.ann.parquet"
    if ap.exists():
        ann = pd.read_parquet(ap)
        ann = ann[ann["type_code"].isin(NEWS)]
        if len(ann):
            r = reactions(ann, daily).dropna(subset=["session", "z"])
            good = r[r["z"] >= GOOD_Z]["session"]
            weeks = pd.DatetimeIndex(good).map(
                lambda d: d + pd.Timedelta(days=(4 - d.weekday()) % 7)
            )
            out["good_news"] = out.index.isin(weeks)
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
    df.to_parquet(panel / "r12_features.parquet")
    print(f"{df['ticker'].nunique()} stocks, {len(df):,} stock-weeks")


def panel_meta(panel: Path) -> pd.DataFrame:
    """Panel rows without any outcome column."""
    cols = ["ticker", "week", "adv", "atr_pct", "floor", "size", "trend"]
    frames = [
        pd.read_parquet(p, columns=cols)
        for p in sorted(panel.glob("*.L.parquet"))
        if "ticker" in pq.read_schema(p).names
    ]
    return pd.concat(frames, ignore_index=True)


def joined(panel: Path, outcomes: bool) -> pd.DataFrame:
    f = pd.read_parquet(panel / "r12_features.parquet")
    if outcomes:
        frames = [
            pd.read_parquet(p)
            for p in sorted(panel.glob("*.L.parquet"))
            if "ticker" in pq.read_schema(p).names
        ]
        p = pd.concat(frames, ignore_index=True)
    else:
        p = panel_meta(panel)
    df = p.merge(f, on=["ticker", "week"], how="inner")
    df = df[(df["week"] >= FIRST) & (df["week"] <= LAST)]
    df = df[df["ticker"].isin(research_tickers()) & df["floor"].astype(bool)]
    return df.reset_index(drop=True)


def count(panel: Path) -> None:
    df = joined(panel, outcomes=False)
    ok = df.dropna(subset=["nearness", "cgo"])
    ev = ok[(ok["nearness"] >= NEAR) & ok["good_news"]]
    print(
        f"| stock-weeks past £10k with both signals | **{len(ok):,}** "
        f"({ok['ticker'].nunique()} stocks, {ok['week'].nunique()} Fridays) |"
    )
    print(f"| stocks per Friday (median) | {ok.groupby('week').size().median():.0f} |")
    print(
        f"| variant 3 events (nearness >= {NEAR} and good news that week) | **{len(ev)}** "
        f"in {ev['ticker'].nunique()} stocks |"
    )
    print(f"| good-news weeks in all | {int(ok['good_news'].sum())} |")
    r = ok[["nearness", "cgo", "trend"]].rank()
    print(
        f"| corr(nearness, cgo) / (nearness, trend) / (cgo, trend), Spearman | "
        f"{r['nearness'].corr(r['cgo']):.2f} / {r['nearness'].corr(r['trend']):.2f} / "
        f"{r['cgo'].corr(r['trend']):.2f} |"
    )


# --- the test -----------------------------------------------------------------------------


def null1(df: pd.DataFrame, target: str) -> np.ndarray:
    """P(target) from a logistic on the weekly ranks of ATR % and log ADV (Newton)."""
    x = np.column_stack([np.ones(len(df)), df["r_atr"], df["r_adv"]])
    y = df[target].to_numpy(float)
    b = np.zeros(3)
    for _ in range(25):
        p = 1 / (1 + np.exp(-x @ b))
        h = x.T @ (x * (p * (1 - p))[:, None])
        b += np.linalg.solve(h, x.T @ (y - p))
    return 1 / (1 + np.exp(-x @ b))


def load(panel: Path) -> pd.DataFrame:
    df = joined(panel, outcomes=True).dropna(subset=["burst_3", "nearness", "cgo", "trend"])
    df["r_atr"] = df.groupby("week")["atr_pct"].rank(pct=True)
    df["r_adv"] = df.groupby("week")["adv"].transform(lambda s: np.log(s).rank(pct=True))
    df = df.dropna(subset=["r_atr", "r_adv"]).reset_index(drop=True)
    df["xs_8w"] = df["ret_8w"] - df.groupby("week")["ret_8w"].transform("mean")
    return df


def residual_rank(df: pd.DataFrame, sig: str, on: list[str]) -> pd.Series:
    """Each Friday: the signal's rank less its OLS fit on the ranks of ``on``."""
    out = pd.Series(np.nan, index=df.index)
    for _, g in df.groupby("week"):
        y = g[sig].rank(pct=True).to_numpy()
        x = np.column_stack([np.ones(len(g))] + [g[c].astype(float).rank(pct=True) for c in on])
        b, *_ = np.linalg.lstsq(x, y, rcond=None)
        out.loc[g.index] = y - x @ b
    return out


def weekly_ic(df: pd.DataFrame, sig: str, y: str = "res") -> pd.Series:
    """Per Friday: the Pearson correlation of the signal's rank with ``y``."""

    def ic(g: pd.DataFrame) -> float:
        r = g[sig].rank(pct=True)
        return float(r.corr(g[y])) if len(g) > 10 else math.nan

    return df.groupby("week").apply(ic, include_groups=False).dropna()


def boot_mean(x: pd.Series) -> tuple[float, float]:
    """96.7% moving-block bootstrap interval of a weekly series' mean (16-week blocks)."""
    v = x.to_numpy(float)
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


def ic_test(df: pd.DataFrame, sig: str, label: str, full: bool = True) -> dict:
    ic = weekly_ic(df, sig)
    est = float(ic.mean())
    lo, hi = boot_mean(ic)
    row = {"test": label, "weeks": len(ic), "IC": est, "lo": lo, "hi": hi}
    if not full:
        return row
    hs = halves(ic)
    same = sum(1 for h in hs if np.isfinite(h) and np.sign(h) == np.sign(est))
    contrib = (df[sig].groupby(df["week"]).rank(pct=True) - 0.5) * df["res"]
    by = contrib.groupby(df["ticker"]).sum()
    best = (by.nlargest(3) if est > 0 else by.nsmallest(3)).index
    rest = df[~df["ticker"].isin(best)]
    ic3 = weekly_ic(rest, sig)
    lo3, hi3 = boot_mean(ic3)
    excl = lo > 0 or hi < 0
    row |= {
        "same sign": f"{same} of 6",
        "halves": " / ".join(f"{h:+.3f}" for h in hs),
        "without best 3": f"{ic3.mean():+.3f} [{lo3:+.3f}, {hi3:+.3f}]",
        "pass": bool(
            excl and same >= 5 and np.sign(ic3.mean()) == np.sign(est) and (lo3 > 0 or hi3 < 0)
        ),
    }
    return row


def ev_weekly(df: pd.DataFrame, ev: pd.Series) -> pd.DataFrame:
    g = df.assign(ev=ev, r_ev=df["res"].where(ev, 0.0), r_ot=df["res"].where(~ev, 0.0))
    return g.groupby("week").agg(
        s=("r_ev", "sum"), n=("ev", "sum"), so=("r_ot", "sum"), no=("ev", lambda x: (~x).sum())
    )


def ev_stat(w: pd.DataFrame) -> float:
    return (
        float(w["s"].sum() / w["n"].sum() - w["so"].sum() / w["no"].sum())
        if w["n"].sum()
        else math.nan
    )


def ev_test(df: pd.DataFrame, ev: pd.Series, label: str, full: bool = True) -> dict:
    w = ev_weekly(df, ev)
    est = ev_stat(w)
    a = w.to_numpy(float)
    rng = np.random.default_rng(SEED)
    k = math.ceil(len(a) / BLOCK)
    draws = []
    for _ in range(REPS):
        idx = np.concatenate(
            [np.arange(s, s + BLOCK) for s in rng.integers(0, len(a) - BLOCK + 1, k)]
        )[: len(a)]
        t = a[idx].sum(axis=0)
        if t[1] and t[3]:
            draws.append(t[0] / t[1] - t[2] / t[3])
    q = 100 * 0.1 / TESTS / 2
    lo, hi = (float(v) for v in np.percentile(draws, [q, 100 - q]))
    row = {"test": label, "events": int(ev.sum()), "estimate": est, "lo": lo, "hi": hi}
    if not full:
        return row
    hs = []
    for _, h0, h1 in HALVES:
        m = (w.index >= pd.Timestamp(h0, tz="UTC")) & (w.index <= pd.Timestamp(h1, tz="UTC"))
        hs.append(ev_stat(w[m]) if w[m]["n"].sum() else math.nan)
    same = sum(1 for h in hs if np.isfinite(h) and np.sign(h) == np.sign(est))
    by = df[ev].groupby("ticker")["res"].sum()
    best = (by.nlargest(3) if est > 0 else by.nsmallest(3)).index
    keep = ~df["ticker"].isin(best)
    r3 = ev_test(df[keep], ev[keep], label, full=False)
    excl = lo > 0 or hi < 0
    row |= {
        "same sign": f"{same} of 6",
        "halves": " / ".join(f"{h:+.3f}" if np.isfinite(h) else "n/a" for h in hs),
        "without best 3": f"{r3['estimate']:+.3f} [{r3['lo']:+.3f}, {r3['hi']:+.3f}]",
        "pass": bool(
            excl
            and same >= 5
            and np.sign(r3["estimate"]) == np.sign(est)
            and (r3["lo"] > 0 or r3["hi"] < 0)
        ),
    }
    return row


def run(panel: Path) -> None:
    df = load(panel)
    print(
        f"panel: {len(df):,} stock-weeks past £10k, {df['ticker'].nunique()} stocks, "
        f"{df['week'].nunique()} Fridays"
    )
    ev = (df["nearness"] >= NEAR) & df["good_news"]
    df["near_resid"] = residual_rank(df, "nearness", ["trend", "new_high"])
    df["cgo_resid"] = residual_rank(df, "cgo", ["trend"])
    fmt = {"float_format": lambda x: f"{x:+.3f}", "index": False}
    for target in ("burst_3", "burst_2", "burst_4"):
        df["res"] = df[target] - null1(df, target)
        main = target == "burst_3"
        rows = [ic_test(df, "nearness", "V1 nearness", main), ic_test(df, "cgo", "V2 cgo", main)]
        print(f"\n{target}, V1 / V2 (rank IC net of null 1, 96.7% interval):")
        print(pd.DataFrame(rows).to_string(**fmt))
        print(f"{target}, V3 (mean residual, events - others):")
        print(pd.DataFrame([ev_test(df, ev, "V3 near high + good news", main)]).to_string(**fmt))
        if main:
            print("\noverlap (burst_3): partial IC after trend (+ new_high for nearness)")
            print(
                pd.DataFrame(
                    [
                        ic_test(df, "near_resid", "V1 partial", False),
                        ic_test(df, "cgo_resid", "V2 partial", False),
                        ic_test(df, "trend", "trend itself", False),
                    ]
                ).to_string(**fmt)
            )
            nh = df[df["new_high"]]
            print("V3 within new_high weeks:")
            print(
                pd.DataFrame([ev_test(nh, ev[nh.index], "V3 | new_high", False)]).to_string(**fmt)
            )
            print("\nby size (burst_3):")
            rows = []
            for s in SIZES:
                d = df[df["size"] == s]
                rows += [
                    ic_test(d, "nearness", f"V1 {s}", False),
                    ic_test(d, "cgo", f"V2 {s}", False),
                ]
            print(pd.DataFrame(rows).to_string(**fmt))
            print(
                pd.DataFrame(
                    [
                        ev_test(df[df["size"] == s], ev[df["size"] == s], f"V3 {s}", False)
                        for s in SIZES
                    ]
                ).to_string(**fmt)
            )
    print("\nsecondary xs_8w:")
    print(
        pd.DataFrame(
            [
                ic_test(
                    df.assign(res=df["xs_8w"]).dropna(subset=["res"]), "nearness", "V1 xs_8w", False
                ),
                ic_test(
                    df.assign(res=df["xs_8w"]).dropna(subset=["res"]), "cgo", "V2 xs_8w", False
                ),
            ]
        ).to_string(**fmt)
    )
    x = df.dropna(subset=["xs_8w"])
    print(
        f"V3 xs_8w: events {x.loc[ev[x.index], 'xs_8w'].mean():+.3f} (median "
        f"{x.loc[ev[x.index], 'xs_8w'].median():+.3f}), others {x.loc[~ev[x.index], 'xs_8w'].mean():+.3f}"
    )
    print(
        f"burst_3 rate: V3 events {df.loc[ev, 'burst_3'].mean():.3f} vs others "
        f"{df.loc[~ev, 'burst_3'].mean():.3f}"
    )


def leavers_share(panel: Path) -> None:
    """Research 0012 case 3: the leavers' share of stock-weeks in the window."""
    lv = pd.read_csv("config/aim_leavers.csv")
    lv = lv[(lv["cancelled"] >= str(FIRST.date())) & (lv["how"] != "main_market")]
    fridays = pd.date_range(FIRST, LAST, freq="W-FRI", tz="UTC")
    weeks = sum(int((fridays < pd.Timestamp(c, tz="UTC")).sum()) for c in lv["cancelled"])
    tested = len(joined(panel, outcomes=False))
    print(
        f"leavers in the window: {len(lv)}; their stock-weeks on AIM in it: {weeks:,}, "
        f"against {tested:,} tested ({weeks / (weeks + tested):.0%} of the total)"
    )


def holdout(panel: Path) -> None:
    """The V3 holdout gate (amendment 2026-10-29): holdout Fridays to 2026-08-14 only."""
    f = pd.read_parquet(panel / "r12_features.parquet")
    frames = [
        pd.read_parquet(p)
        for p in sorted(panel.glob("*.L.parquet"))
        if "ticker" in pq.read_schema(p).names
    ]
    df = pd.concat(frames, ignore_index=True).merge(f, on=["ticker", "week"], how="inner")
    lo_, hi_ = pd.Timestamp("2025-10-24", tz="UTC"), pd.Timestamp("2026-08-14", tz="UTC")
    df = df[(df["week"] >= lo_) & (df["week"] <= hi_)]
    df = df[df["ticker"].isin(research_tickers()) & df["floor"].astype(bool)]
    df = df.dropna(subset=["burst_3", "nearness", "cgo"]).copy()
    df["r_atr"] = df.groupby("week")["atr_pct"].rank(pct=True)
    df["r_adv"] = df.groupby("week")["adv"].transform(lambda s: np.log(s).rank(pct=True))
    df = df.dropna(subset=["r_atr", "r_adv"]).reset_index(drop=True)
    df["res"] = df["burst_3"] - null1(df, "burst_3")
    ev = (df["nearness"] >= NEAR) & df["good_news"]
    w = ev_weekly(df, ev)
    a = w.to_numpy(float)
    rng = np.random.default_rng(SEED)
    k = math.ceil(len(a) / BLOCK)
    draws = []
    for _ in range(REPS):
        idx = np.concatenate(
            [np.arange(s, s + BLOCK) for s in rng.integers(0, len(a) - BLOCK + 1, k)]
        )[: len(a)]
        t = a[idx].sum(axis=0)
        if t[1] and t[3]:
            draws.append(t[0] / t[1] - t[2] / t[3])
    q = 100 * 0.1 / TESTS / 2
    lo, hi = np.percentile(draws, [q, 100 - q])
    est = ev_stat(w)
    print(
        f"holdout: {len(df):,} stock-weeks, {df['week'].nunique()} Fridays, {int(ev.sum())} events"
    )
    print(
        f"V3 estimate {est:+.3f} (96.7% interval {lo:+.3f} to {hi:+.3f}); gate >= +0.0445: "
        f"{'PASS' if est >= 0.0445 else 'FAIL'}"
    )
    print(
        f"burst_3 raw: events {df.loc[ev, 'burst_3'].mean():.3f}, others "
        f"{df.loc[~ev, 'burst_3'].mean():.3f}"
    )
    for s in SIZES:
        m = df["size"] == s
        print(f"  {s}: events {int(ev[m].sum())}, estimate {ev_stat(ev_weekly(df[m], ev[m])):+.3f}")


if __name__ == "__main__":
    step, panel = sys.argv[1], Path(sys.argv[2])
    {"build": build, "count": count, "run": run, "leavers": leavers_share, "holdout": holdout}[
        step
    ](panel)
