"""Research 0013: the placings event study (#285, epic #283, edge E1).

uv run python scripts/research/r11_placings.py count PANEL_DIR    # power, metadata only
uv run python scripts/research/r11_placings.py sample PANEL_DIR   # the 40 pre-step events
uv run python scripts/research/r11_placings.py run PANEL_DIR      # only after the pre-step

PANEL_DIR is a research-universe panel (``build_panel.py PANEL_DIR research``: per stock and
Friday ``burst_2/3/4``, ``ret_8w``, ``adv``, ``atr_pct``, ``floor``, ``size``, and the stock's NSM
filings ``<ticker>.ann.parquet``). Notice texts are cached in PANEL_DIR/raise/. Signal Fridays
after 2025-08-08 are dropped on load and never read (ADR 0029 holdout). Everything follows
research 0013 as pre-registered. Research only: nothing goes to the DB.
"""

from __future__ import annotations

import math
import random
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from solaraalpha.analysis.financing import RAISE
from solaraalpha.analysis.universe import SIZES, research_tickers
from solaraalpha.data.announcements import fetch_text
from solaraalpha.data.http import patient
from solaraalpha.data.raises import directors_in, issue_price, use_class

FIRST, LAST = pd.Timestamp("2022-12-16", tz="UTC"), pd.Timestamp("2025-08-08", tz="UTC")
LAG = 4  # weeks: the second entry, "once the overhang clears"
GAP = pd.Timedelta(days=14)  # raise filings closer than this are one raise (financing.py)
# headlines that match RAISE but are not a company raising equity
NOISE = re.compile(
    r"exercise|option|warrant|employee benefit|\bebt\b|treasury|block ?listing|convertible"
    r"|loan note|subscription agreement|director.{0,20}subscri|investment in"
    r"|subscribes? (?:for|to) shares in",
    re.I,
)
BLOCK, REPS, SEED = 16, 2000, 285
HALVES = [
    ("H1 2023", "2022-12-01", "2023-06-30"),
    ("H2 2023", "2023-07-01", "2023-12-31"),
    ("H1 2024", "2024-01-01", "2024-06-30"),
    ("H2 2024", "2024-07-01", "2024-12-31"),
    ("H1 2025", "2025-01-01", "2025-06-30"),
    ("H2 2025", "2025-07-01", "2025-08-08"),
]


def friday(ts: pd.Timestamp) -> pd.Timestamp:
    """The Friday on or after ``ts``'s date (UTC midnight)."""
    d = ts.tz_convert("UTC").normalize()
    return d + pd.Timedelta(days=(4 - d.weekday()) % 7)


def filings(panel: Path) -> pd.DataFrame:
    keep = set(research_tickers())
    frames = [
        pd.read_parquet(p).assign(ticker=p.name.removesuffix(".ann.parquet"))
        for p in sorted(panel.glob("*.ann.parquet"))
        if p.name.removesuffix(".ann.parquet") in keep
    ]
    return pd.concat(frames, ignore_index=True)


def events(panel: Path) -> pd.DataFrame:
    """One row per equity raise (metadata only): the issuer's raise filings (``RAISE`` headline
    or NSM type ``ROI``, less ``NOISE``) within 14 days of each other are one raise; its date is
    the first ``ROI`` (Result of Equity Issue) filing, else the first raise filing. Signal Friday =
    the Friday on or after it; in-sample Fridays only."""
    f = filings(panel)
    f = f[(f["published"] >= FIRST - pd.Timedelta(days=30)) & (f["published"] <= LAST)]
    raise_ = f["headline"].str.contains(RAISE, na=False) | (f["type_code"] == "ROI")
    hit = f[raise_ & ~f["headline"].str.contains(NOISE, na=False)]
    rows = []
    for t, g in hit.sort_values("published").groupby("ticker"):
        new = g["published"].diff() > GAP
        new.iloc[0] = True
        for _, c in g.groupby(new.cumsum()):
            roi = c[c["type_code"] == "ROI"]
            first = roi.iloc[0] if len(roi) else c.iloc[0]
            rows.append(
                {
                    "ticker": t,
                    "published": first["published"],
                    "id": first["id"],
                    "url": first["url"],
                    "headline": first["headline"],
                    "has_roi": len(roi) > 0,
                    "filings": len(c),
                    "urls": list(c["url"]),
                }
            )
    e = pd.DataFrame(rows)
    e["week"] = e["published"].map(friday)
    return e[(e["week"] >= FIRST) & (e["week"] <= LAST)].reset_index(drop=True)


def panel_meta(panel: Path) -> pd.DataFrame:
    """Panel rows without any outcome column (ticker, week, adv, floor, size)."""
    cols = ["ticker", "week", "adv", "floor", "size"]
    frames = [
        pd.read_parquet(p, columns=cols)
        for p in sorted(panel.glob("*.L.parquet"))
        if "ticker" in pq.read_schema(p).names
    ]
    return pd.concat(frames, ignore_index=True)


# --- count (metadata only) ------------------------------------------------------------------


def count(panel: Path) -> None:
    e = events(panel)
    m = e.merge(panel_meta(panel), on=["ticker", "week"], how="left")
    floor = m["floor"].fillna(False).astype(bool)
    print(
        f"| raises (events), in-sample signal Fridays | **{len(e)}** in {e['ticker'].nunique()} "
        f"issuers |"
    )
    print(f"| with a Result of Equity Issue (`ROI`) filing | {e['has_roi'].mean():.0%} |")
    print(f"| on a stock-week past the £10k floor | **{int(floor.sum())}** |")
    by = m[floor]["size"].value_counts()
    print(
        "| of which aim100 / rest / micro | "
        + " / ".join(str(int(by.get(s, 0))) for s in SIZES)
        + " |"
    )
    yr = m[floor]["week"].dt.year.value_counts().sort_index()
    print("| by year | " + ", ".join(f"{y}: {n}" for y, n in yr.items()) + " |")


# --- pre-step ---------------------------------------------------------------------------------


def text(panel: Path, url: str) -> str:
    out = panel / "raise" / (url.rsplit("/", 1)[-1] + ".txt")
    if not out.exists():
        out.parent.mkdir(exist_ok=True)
        out.write_text(patient(fetch_text, url))
    return out.read_text()


def raise_text(panel: Path, ev: pd.Series) -> str:
    """The raise's filings' texts, in publication order, joined."""
    return "\n\n".join(text(panel, u) for u in ev["urls"])


def past_floor(panel: Path) -> pd.DataFrame:
    e = events(panel)
    m = e.merge(panel_meta(panel), on=["ticker", "week"], how="left")
    return m[m["floor"].fillna(False).astype(bool)].reset_index(drop=True)


def pick(panel: Path, seed: int, skip: int | None = None) -> pd.DataFrame:
    """40 events past the floor at random (``seed``); ``skip``: leave out that seed's 40."""
    e = past_floor(panel).sort_values(["published", "id"]).reset_index(drop=True)
    pool = list(range(len(e)))
    if skip is not None:
        random.seed(skip)
        gone = set(random.sample(pool, 40))
        pool = [i for i in pool if i not in gone]
    random.seed(seed)
    return e.iloc[sorted(random.sample(pool, 40))].reset_index(drop=True)


def sample(panel: Path, seed: int = SEED, skip: int | None = None) -> pd.DataFrame:
    """The pre-step events with the rules' reading (written to PANEL_DIR/prestep_<seed>.csv)."""
    e = pick(panel, seed, skip)
    rows = []
    for _, ev in e.iterrows():
        t = raise_text(panel, ev)
        rows.append(
            {
                "ticker": ev["ticker"],
                "published": ev["published"],
                "id": ev["id"],
                "headline": ev["headline"],
                "rules_price": issue_price(t),
                "rules_use": use_class(t),
                "rules_directors": directors_in(t),
            }
        )
    out = pd.DataFrame(rows)
    out.to_csv(panel / f"prestep_{seed}.csv", index=False)
    return out


# --- the test (only after the pre-step) ----------------------------------------------------


def load(panel: Path) -> pd.DataFrame:
    """Panel stock-weeks: in-sample Fridays, past the £10k floor, with a 3-ATR burst."""
    frames = [
        pd.read_parquet(p)
        for p in sorted(panel.glob("*.L.parquet"))
        if "ticker" in pq.read_schema(p).names
    ]
    df = pd.concat(frames, ignore_index=True)
    df = df[(df["week"] >= FIRST) & (df["week"] <= LAST)]
    df = df[df["ticker"].isin(research_tickers()) & df["floor"].astype(bool)]
    df = df[df["burst_3"].notna()].copy()
    df["r_atr"] = df.groupby("week")["atr_pct"].rank(pct=True)
    df["r_adv"] = df.groupby("week")["adv"].transform(lambda s: np.log(s).rank(pct=True))
    return df.dropna(subset=["r_atr", "r_adv"]).reset_index(drop=True)


def one_per_8_weeks(e: pd.DataFrame) -> pd.DataFrame:
    keep, last = [], {}
    for i, r in e.sort_values(["ticker", "published"]).iterrows():
        if r["ticker"] in last and r["published"] - last[r["ticker"]] < pd.Timedelta(days=56):
            continue
        last[r["ticker"]] = r["published"]
        keep.append(i)
    return e.loc[keep]


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


def stat(df: pd.DataFrame, a: str, b: str) -> float:
    """Mean residual in group ``a`` minus group ``b`` (boolean columns)."""
    x, y = df.loc[df[a], "res"], df.loc[df[b], "res"]
    return float(x.mean() - y.mean()) if len(x) and len(y) else math.nan


def boot(df: pd.DataFrame, a: str, b: str) -> tuple[float, float]:
    """98% moving-block bootstrap interval (16-week blocks, whole weeks) of :func:`stat`."""
    weeks = np.sort(df["week"].unique())
    agg = np.column_stack(
        [
            df.assign(v=df["res"].where(df[a], 0.0)).groupby("week")["v"].sum().reindex(weeks),
            df.groupby("week")[a].sum().reindex(weeks),
            df.assign(v=df["res"].where(df[b], 0.0)).groupby("week")["v"].sum().reindex(weeks),
            df.groupby("week")[b].sum().reindex(weeks),
        ]
    ).astype(float)
    rng = np.random.default_rng(SEED)
    k = math.ceil(len(weeks) / BLOCK)
    out = []
    for _ in range(REPS):
        starts = rng.integers(0, len(weeks) - BLOCK + 1, k)
        idx = np.concatenate([np.arange(s, s + BLOCK) for s in starts])[: len(weeks)]
        t = agg[idx].sum(axis=0)
        if t[1] and t[3]:
            out.append(t[0] / t[1] - t[2] / t[3])
    lo, hi = np.percentile(out, [1, 99])
    return float(lo), float(hi)


def test(df: pd.DataFrame, a: str, b: str, label: str, full: bool = True) -> dict:
    est = stat(df, a, b)
    lo, hi = boot(df, a, b) if df[a].sum() >= 5 and df[b].sum() >= 5 else (math.nan, math.nan)
    row = {
        "test": label,
        "n": int(df[a].sum()),
        "vs": int(df[b].sum()),
        "estimate": est,
        "lo (98%)": lo,
        "hi (98%)": hi,
    }
    if not full:
        return row
    halves = []
    for _, h0, h1 in HALVES:
        h = df[
            (df["week"] >= pd.Timestamp(h0, tz="UTC")) & (df["week"] <= pd.Timestamp(h1, tz="UTC"))
        ]
        halves.append(stat(h, a, b))
    same = sum(1 for h in halves if np.isfinite(h) and np.sign(h) == np.sign(est))
    by = df[df[a]].groupby("ticker")["res"].sum()
    best = (by.nlargest(3) if est > 0 else by.nsmallest(3)).index
    rest = df[~df["ticker"].isin(best)]
    est3 = stat(rest, a, b)
    lo3, hi3 = boot(rest, a, b)
    excl = bool(np.isfinite(lo) and (lo > 0 or hi < 0))
    row |= {
        "half-years same sign": f"{same} of 6",
        "halves": " / ".join(f"{h:+.3f}" if np.isfinite(h) else "n/a" for h in halves),
        "without best 3": f"{est3:+.3f} [{lo3:+.3f}, {hi3:+.3f}]",
        "pass": excl and same >= 5 and np.sign(est3) == np.sign(est) and (lo3 > 0 or hi3 < 0),
    }
    return row


def mark(df: pd.DataFrame, e: pd.DataFrame, lag: int) -> pd.Series:
    key = set(zip(e["ticker"], e["week"] + pd.Timedelta(weeks=lag), strict=True))
    return pd.Series([k in key for k in zip(df["ticker"], df["week"], strict=True)], df.index)


def directors(panel: Path, e: pd.DataFrame) -> pd.Series:
    """Directors took part: the raise's text says so, or a DSH filing within 7 days after."""
    f = filings(panel)
    dsh = f[f["type_code"] == "DSH"]
    out = []
    for _, ev in e.iterrows():
        d = dsh[
            (dsh["ticker"] == ev["ticker"])
            & (dsh["published"] >= ev["published"])
            & (dsh["published"] <= ev["published"] + pd.Timedelta(days=7))
        ]
        out.append(directors_in(raise_text(panel, ev)) or len(d) > 0)
    return pd.Series(out, e.index)


def run(panel: Path) -> None:
    df = load(panel)
    e = one_per_8_weeks(events(panel))
    e["dir"] = directors(panel, e)
    print(
        f"panel: {len(df):,} stock-weeks past £10k, {df['ticker'].nunique()} stocks, "
        f"{df['week'].nunique()} Fridays; raises after the 8-week rule: {len(e)}"
    )
    for target in ("burst_3", "burst_2", "burst_4"):
        df["res"] = df[target] - null1(df, target)
        rows = []
        for lag, name in ((0, "E0"), (LAG, "E4")):
            d = df.assign(ev=mark(df, e, lag))
            d["other"] = ~d["ev"]
            rows.append(test(d, "ev", "other", f"{name} all", target == "burst_3"))
            if target == "burst_3":
                rows += [
                    test(d[d["size"] == s], "ev", "other", f"{name} {s}", False) for s in SIZES
                ]
        d = df.assign(ev=mark(df, e[e["dir"]], 0), no=mark(df, e[~e["dir"]], 0))
        rows.append(test(d, "ev", "no", "E0 directors in - not", target == "burst_3"))
        print(f"\n{target}:")
        print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    for lag, name in ((0, "E0"), (LAG, "E4")):
        on = mark(df, e, lag)
        p3 = null1(df, "burst_3")
        print(
            f"{name}: burst_3 rate {df.loc[on, 'burst_3'].mean():.3f} (null 1 "
            f"{p3[on.to_numpy()].mean():.3f}), others {df.loc[~on, 'burst_3'].mean():.3f}; "
            f"ret_8w mean {df.loc[on, 'ret_8w'].mean():+.3f} vs {df.loc[~on, 'ret_8w'].mean():+.3f}"
            f"; median {df.loc[on, 'ret_8w'].median():+.3f} vs {df.loc[~on, 'ret_8w'].median():+.3f}"
        )


if __name__ == "__main__":
    step, panel = sys.argv[1], Path(sys.argv[2])
    {"count": count, "sample": sample, "run": run}[step](panel)
