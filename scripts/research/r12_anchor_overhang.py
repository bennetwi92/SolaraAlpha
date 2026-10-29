"""Research 0014: the 52-week-high anchor and the capital-gains overhang (#286, edges B1, B2).

uv run python scripts/research/r12_anchor_overhang.py build PANEL_DIR   # features, no outcomes
uv run python scripts/research/r12_anchor_overhang.py count PANEL_DIR   # power, no outcomes
uv run python scripts/research/r12_anchor_overhang.py run PANEL_DIR     # the pre-registered test

PANEL_DIR is a research-universe panel (``build_panel.py PANEL_DIR research``; its daily bars in
PANEL_DIR/raw/<ticker>.daily.parquet, filings ``<ticker>.ann.parquet``, fundamentals
``<ticker>.fund.parquet``). ``build`` writes PANEL_DIR/r12_features.parquet: per stock and Friday
``nearness``, ``cgo``, ``new_high`` and ``good_news``, from prices, volumes and filing dates up to
that Friday only. Signal Fridays after 2025-08-08 are dropped on load and never read (ADR 0029
holdout). Everything follows research 0014 as pre-registered. Research only.
"""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from solaraalpha.analysis.events import reactions
from solaraalpha.analysis.universe import research_tickers, shares_at
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


if __name__ == "__main__":
    step, panel = sys.argv[1], Path(sys.argv[2])
    {"build": build, "count": count}[step](panel)
