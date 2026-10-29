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


if __name__ == "__main__":
    step, panel = sys.argv[1], Path(sys.argv[2])
    {"count": count, "sample": sample}[step](panel)
