"""Research 0005 on the wide universe (#284): director-buy clusters, parse check, then the drift test.

uv run python scripts/research/r10_director_buys.py count PANEL_DIR    # power, metadata only
uv run python scripts/research/r10_director_buys.py sample PANEL_DIR   # the 40 pre-step notices
uv run python scripts/research/r10_director_buys.py parse PANEL_DIR    # every DSH notice -> buys
uv run python scripts/research/r10_director_buys.py drift PANEL_DIR    # only after the pre-step
uv run python scripts/research/r10_director_buys.py bound PANEL_DIR    # leavers' bias bound

PANEL_DIR is a research-universe panel (``build_panel.py PANEL_DIR research``: per stock and
Friday ``burst_2/3/4``, ``ret_8w``, ``adv``, ``atr_pct``, ``floor``, ``size``, and the stock's NSM
filings ``<ticker>.ann.parquet``). Notice texts are cached in PANEL_DIR/dsh/. Signal Fridays after
2025-08-08 are dropped on load and never read (ADR 0029 holdout). Everything follows research
0005 as amended on 2026-10-29. Research only: nothing goes to the DB.
"""

from __future__ import annotations

import math
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from solaraalpha.analysis.universe import SIZES, research_tickers
from solaraalpha.data.announcements import fetch_text
from solaraalpha.data.dealings import notice_buys
from solaraalpha.data.http import patient
from solaraalpha.data.prices import fetch_daily

FIRST, LAST = pd.Timestamp("2022-12-16", tz="UTC"), pd.Timestamp("2025-08-08", tz="UTC")
FROM = pd.Timestamp("2022-11-16", tz="UTC")  # 30 days before the first Friday: clusters complete
RESULTS = ("FR", "PRE", "IR")
WINDOW = pd.Timedelta(days=30)
GAP = pd.Timedelta(days=56)  # one signal per stock per 8 weeks
BIG = 50_000.0  # variant B: one PDMR's buys in one notice over £50k
BLOCK, REPS, SEED = 16, 2000, 284
HALVES = [
    ("H1 2023", "2022-12-01", "2023-06-30"),
    ("H2 2023", "2023-07-01", "2023-12-31"),
    ("H1 2024", "2024-01-01", "2024-06-30"),
    ("H2 2024", "2024-07-01", "2024-12-31"),
    ("H1 2025", "2025-01-01", "2025-06-30"),
    ("H2 2025", "2025-07-01", "2025-08-08"),
]
FX = {"USD": "GBPUSD=X", "EUR": "GBPEUR=X", "AUD": "GBPAUD=X", "CAD": "GBPCAD=X"}


def friday(ts: pd.Timestamp) -> pd.Timestamp:
    """The Friday on or after ``ts``'s date (UTC midnight)."""
    d = ts.tz_convert("UTC").normalize()
    return d + pd.Timedelta(days=(4 - d.weekday()) % 7)


def filings(panel: Path) -> pd.DataFrame:
    frames = [
        pd.read_parquet(p).assign(ticker=p.name.removesuffix(".ann.parquet"))
        for p in sorted(panel.glob("*.ann.parquet"))
        if p.name.removesuffix(".ann.parquet") in set(research_tickers())
    ]
    return pd.concat(frames, ignore_index=True)


def dsh(panel: Path) -> pd.DataFrame:
    f = filings(panel)
    return f[(f["type_code"] == "DSH") & (f["published"] >= FROM) & (f["published"] <= LAST)]


def load(panel: Path) -> pd.DataFrame:
    """Panel stock-weeks: in-sample Fridays, past the £10k floor, with a 3-ATR burst."""
    df = pd.concat(
        [pd.read_parquet(p) for p in sorted(panel.glob("*.L.parquet"))], ignore_index=True
    )
    df = df[(df["week"] >= FIRST) & (df["week"] <= LAST)]
    df = df[df["ticker"].isin(research_tickers()) & df["floor"].astype(bool)]
    df = df[df["burst_3"].notna()].copy()
    df["r_atr"] = df.groupby("week")["atr_pct"].rank(pct=True)
    df["r_adv"] = df.groupby("week")["adv"].transform(lambda s: np.log(s).rank(pct=True))
    return df.dropna(subset=["r_atr", "r_adv"]).reset_index(drop=True)


# --- count (metadata only) ------------------------------------------------------------------


def count(panel: Path) -> None:
    f = filings(panel)
    d = dsh(panel)
    res = f[f["type_code"].isin(RESULTS)]
    years = (LAST - FROM).days / 365.25
    per = d.groupby("ticker").size()
    after = 0
    for t, g in d.groupby("ticker"):
        r = res[res["ticker"] == t]["published"].sort_values().to_numpy()
        for p in g["published"].to_numpy():
            i = np.searchsorted(r, p) - 1
            after += bool(i >= 0 and p - r[i] <= np.timedelta64(30, "D"))
    print(f"| issuers with filings read | {f['ticker'].nunique()} of {len(research_tickers())} |")
    print(f"| window | {FROM.date()} to {LAST.date()} ({years:.2f} years) |")
    print(
        f"| `DSH` notices | **{len(d):,}** ({len(d) / per.size / years:.1f} per issuer with any "
        f"a year; median {per.median():.0f} per issuer, p90 {per.quantile(0.9):.0f}) |"
    )
    weeks = d["published"].map(friday)
    print(
        f"| stock-weeks with a `DSH` notice | {pd.Series(list(zip(d['ticker'], weeks, strict=True))).nunique():,} |"
    )
    print(
        f"| `DSH` notices within 30 days after a results filing | {after:,} ({after / len(d):.0%}) |"
    )


# --- parse ----------------------------------------------------------------------------------


def text(panel: Path, url: str) -> str:
    out = panel / "dsh" / (url.rsplit("/", 1)[-1] + ".txt")
    if not out.exists():
        out.write_text(patient(fetch_text, url))
    return out.read_text()


def closes(panel: Path, t: str) -> pd.Series:
    p = panel / "raw" / f"{t}.daily.parquet"
    if not p.exists():
        p.parent.mkdir(exist_ok=True)
        fetch_daily(t, start="2022-06-01").to_parquet(p)
    c = pd.read_parquet(p)["close"]
    return c


def fx_rates() -> dict[str, pd.Series]:
    """Pounds per unit of each currency by day (1 / GBPXXX)."""
    return {k: 1 / fetch_daily(v, start="2022-06-01")["close"] for k, v in FX.items()}


def rows_for(panel: Path, n: pd.Series, fx: dict[str, pd.Series]) -> list[dict]:
    """One row per PDMR with a net open-market buy in a notice (rules v2), else one empty row."""
    c = closes(panel, n["ticker"])
    day = n["published"].normalize()
    close = c[c.index <= day + pd.Timedelta(days=1)]
    cl = float(close.iloc[-1]) if len(close) else None
    rates = {k: float(s[s.index <= day + pd.Timedelta(days=1)].iloc[-1]) for k, s in fx.items()}
    base = {"ticker": n["ticker"], "id": n["id"], "published": n["published"]}
    buys = notice_buys(text(panel, n["url"]), cl, rates)
    if not buys:
        return [base | {"pdmr": "", "buy": False, "value_gbp": 0.0}]
    return [base | {"pdmr": k, "buy": True, "value_gbp": v} for k, v in buys.items()]


def parse(panel: Path) -> None:
    (panel / "dsh").mkdir(exist_ok=True)
    d = dsh(panel)
    fx = fx_rates()
    with ThreadPoolExecutor(3) as ex:
        rows = [r for rs in ex.map(lambda n: rows_for(panel, n[1], fx), d.iterrows()) for r in rs]
    out = pd.DataFrame(rows)
    out.to_parquet(panel / "dsh_blocks.parquet")
    print(
        f"{len(d):,} notices, {len(out):,} PDMR blocks, buys {out['buy'].sum():,} "
        f"in {out[out['buy']]['id'].nunique():,} notices"
    )


def sample(panel: Path) -> None:
    """The pre-step's 40 notices (seed 223), with the rules' reading, for hand labelling."""
    d = dsh(panel).sort_values(["published", "id"]).reset_index(drop=True)
    random.seed(223)
    pick = d.iloc[sorted(random.sample(range(len(d)), 40))]
    fx = fx_rates()
    (panel / "dsh").mkdir(exist_ok=True)
    rows = [r for _, n in pick.iterrows() for r in rows_for(panel, n, fx)]
    out = pd.DataFrame(rows)
    g = out.groupby("id").agg(
        ticker=("ticker", "first"),
        published=("published", "first"),
        rules_buy=("buy", "any"),
        rules_value=("value_gbp", lambda s: float(s[out.loc[s.index, "buy"]].sum())),
        pdmrs=("pdmr", lambda s: "; ".join(s)),
    )
    g = g.loc[pick["id"]].reset_index()
    g["url"] = pick["url"].to_numpy()
    g.to_csv(panel / "prestep.csv", index=False)
    print(g.to_string())


# --- signals and the drift test -------------------------------------------------------------


def signals(blocks: pd.DataFrame, variant: str) -> pd.DataFrame:
    """Signal events (ticker, notice time, signal Friday) for variant A (2+ PDMRs buying within
    30 days, signal at the completing notice) or B (one PDMR's buys in one notice > £50k), one
    per stock per 8 weeks (the earliest kept)."""
    b = blocks[blocks["buy"]].copy()
    b["who"] = b["pdmr"].str.lower().str.replace(r"[^a-z]", "", regex=True)
    ev = []
    for t, g in b.groupby("ticker"):
        g = g.sort_values("published")
        if variant == "A":
            for p in g["published"].unique():
                w = g[(g["published"] <= p) & (g["published"] >= p - WINDOW)]
                if w["who"].nunique() >= 2:
                    ev.append((t, p))
        else:
            v = g.groupby(["id", "who"]).agg(
                published=("published", "first"), value=("value_gbp", "sum")
            )
            ev += [(t, p) for p in v[v["value"] > BIG]["published"]]
    e = pd.DataFrame(ev, columns=["ticker", "published"]).sort_values(["ticker", "published"])
    keep, last = [], {}
    for r in e.itertuples():
        if r.ticker in last and r.published - last[r.ticker] < GAP:
            continue
        last[r.ticker] = r.published
        keep.append((r.ticker, r.published, friday(r.published)))
    out = pd.DataFrame(keep, columns=["ticker", "published", "week"])
    return out[(out["week"] >= FIRST) & (out["week"] <= LAST)]


def after_results(panel: Path, df: pd.DataFrame) -> pd.Series:
    """True for stock-weeks whose Friday is within 30 days after a results filing."""
    f = filings(panel)
    res = f[f["type_code"].isin(RESULTS)]
    out = pd.Series(False, index=df.index)
    for t, g in df.groupby("ticker"):
        r = np.sort(res[res["ticker"] == t]["published"].dt.normalize().to_numpy())
        if not len(r):
            continue
        w = g["week"].to_numpy()
        i = np.searchsorted(r, w, side="right") - 1
        ok = i >= 0
        gap = np.full(len(w), np.timedelta64(10_000, "D"))
        gap[ok] = w[ok] - r[i[ok]]
        out.loc[g.index] = ok & (gap <= np.timedelta64(30, "D"))
    return out


def null1(df: pd.DataFrame, target: str) -> np.ndarray:
    """P(target) from a logistic on the weekly ranks of ATR % and log ADV (Newton, no penalty)."""
    x = np.column_stack([np.ones(len(df)), df["r_atr"], df["r_adv"]])
    y = df[target].to_numpy(float)
    b = np.zeros(3)
    for _ in range(25):
        p = 1 / (1 + np.exp(-x @ b))
        h = x.T @ (x * (p * (1 - p))[:, None])
        b += np.linalg.solve(h, x.T @ (y - p))
    return 1 / (1 + np.exp(-x @ b))


def stat(df: pd.DataFrame) -> float:
    s, n = df[df["sig"]], df[df["null2"]]
    return float(s["res"].mean() - n["res"].mean()) if len(s) and len(n) else math.nan


def boot(df: pd.DataFrame) -> tuple[float, float]:
    """95% moving-block bootstrap interval (16-week blocks, whole weeks) of :func:`stat`."""
    weeks = np.sort(df["week"].unique())
    g = df.groupby("week")
    agg = (
        pd.DataFrame(
            {
                "ss": g.apply(lambda x: x.loc[x["sig"], "res"].sum(), include_groups=False),
                "sn": g["sig"].sum(),
                "ns": g.apply(lambda x: x.loc[x["null2"], "res"].sum(), include_groups=False),
                "nn": g["null2"].sum(),
            }
        )
        .reindex(weeks)
        .to_numpy(float)
    )
    rng = np.random.default_rng(SEED)
    k = math.ceil(len(weeks) / BLOCK)
    out = []
    for _ in range(REPS):
        starts = rng.integers(0, len(weeks) - BLOCK + 1, k)
        idx = np.concatenate([np.arange(s, s + BLOCK) for s in starts])[: len(weeks)]
        a = agg[idx].sum(axis=0)
        if a[1] and a[3]:
            out.append(a[0] / a[1] - a[2] / a[3])
    lo, hi = np.percentile(out, [2.5, 97.5])
    return float(lo), float(hi)


def test_one(df: pd.DataFrame, label: str) -> dict:
    est = stat(df)
    lo, hi = boot(df) if df["sig"].sum() >= 5 else (math.nan, math.nan)
    halves = []
    for _, a, b in HALVES:
        h = df[
            (df["week"] >= pd.Timestamp(a, tz="UTC")) & (df["week"] <= pd.Timestamp(b, tz="UTC"))
        ]
        halves.append(stat(h))
    same = sum(1 for h in halves if np.isfinite(h) and np.sign(h) == np.sign(est))
    best = df[df["sig"]].groupby("ticker")["res"].sum().nlargest(3).index
    rest = df[~df["ticker"].isin(best)]
    est3 = stat(rest)
    lo3, hi3 = boot(rest) if rest["sig"].sum() >= 5 else (math.nan, math.nan)
    return {
        "label": label,
        "events": int(df["sig"].sum()),
        "null2 weeks": int(df["null2"].sum()),
        "estimate": est,
        "lo": lo,
        "hi": hi,
        "half-years same sign": f"{same} of 6",
        "halves": " / ".join(f"{h:+.3f}" if np.isfinite(h) else "n/a" for h in halves),
        "without best 3": f"{est3:+.3f} [{lo3:+.3f}, {hi3:+.3f}]",
        "pass": bool(
            np.isfinite(lo)
            and (lo > 0 or hi < 0)
            and same >= 5
            and np.sign(est3) == np.sign(est)
            and (lo3 > 0 or hi3 < 0)
        ),
    }


def drift(panel: Path) -> None:
    blocks = pd.read_parquet(panel / "dsh_blocks.parquet")
    df = load(panel)
    df["after"] = after_results(panel, df)
    for target in ("burst_3", "burst_2", "burst_4"):
        df[f"p_{target}"] = null1(df, target)
    print(
        f"panel: {len(df):,} stock-weeks past £10k, {df['ticker'].nunique()} stocks, "
        f"{df['week'].nunique()} Fridays ({df['week'].min().date()} to {df['week'].max().date()})"
    )
    for v in ("A", "B"):
        sig = signals(blocks, v)
        key = set(zip(sig["ticker"], sig["week"], strict=True))
        on = pd.Series([k in key for k in zip(df["ticker"], df["week"], strict=True)], df.index)
        print(f"\n## Variant {v}: {len(sig)} signal events, {int(on.sum())} with a tested week")
        for target in ("burst_3", "burst_2", "burst_4"):
            d = df.assign(sig=on, null2=df["after"] & ~on, res=df[target] - df[f"p_{target}"])
            r = [test_one(d, "all")]
            if target == "burst_3":
                r += [test_one(d[d["size"] == s], s) for s in SIZES]
            print(f"\n{target}:")
            print(pd.DataFrame(r).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
        d = df.assign(sig=on, null2=df["after"] & ~on)
        r8 = d[d["week"] <= LAST]
        print(
            f"ret_8w mean: signal {r8.loc[r8['sig'], 'ret_8w'].mean():+.3f}, null 2 "
            f"{r8.loc[r8['null2'], 'ret_8w'].mean():+.3f}, all {r8['ret_8w'].mean():+.3f}"
        )
        p3 = d.loc[d["sig"], "p_burst_3"].mean()
        print(
            f"burst_3 rate: signal {d.loc[d['sig'], 'burst_3'].mean():.3f} (null 1 "
            f"{p3:.3f}), null 2 {d.loc[d['null2'], 'burst_3'].mean():.3f}"
        )


if __name__ == "__main__":
    step, panel = sys.argv[1], Path(sys.argv[2])
    {"count": count, "sample": sample, "parse": parse, "drift": drift}[step](panel)
