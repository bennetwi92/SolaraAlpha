"""Coverage of the AIM research universe per in-sample year (#290, research 0011).

uv run python scripts/research/coverage.py RAW_DIR

Fetches daily bars (from 2021-06) and fundamentals for every stock in
config/research_universe.toml into RAW_DIR (keep it out of data/; files already there are reused),
then prints, per calendar year of in-sample signal Fridays (ADR 0029: 2022-12-16 to 2025-08-08),
how many companies have Yahoo history and how many pass the £10k research floor point-in-time
(``analysis.universe``), split by size group. Research only: nothing goes to the DB.
"""

from __future__ import annotations

import sys
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

from solaraalpha.analysis.universe import SIZES, research_tickers, weekly_universe
from solaraalpha.data.fundamentals import fetch_fundamentals
from solaraalpha.data.prices import fetch_daily
from solaraalpha.pipeline import watchlist

START = "2021-06-01"
FIRST, LAST = "2022-12-16", "2025-08-08"  # in-sample burst_10w signal Fridays (ADR 0029)
FRIDAYS = pd.date_range(FIRST, LAST, freq="W-FRI", tz="UTC")


def fetch(args: tuple[Path, str]) -> str:
    raw, t = args
    msg = []
    for kind, fn in (("daily", lambda: fetch_daily(t, start=START)), ("fund", fetch_fundamentals)):
        path = raw / f"{t}.{kind}.parquet"
        if path.exists():
            continue
        try:
            (fn() if kind == "daily" else fn(t)).to_parquet(path)  # type: ignore[operator]
        except Exception as e:
            msg.append(f"{kind}: {str(e)[:60]}")
    return f"{t}: {'; '.join(msg)}" if msg else ""


def panel(raw: Path) -> pd.DataFrame:
    control = set(watchlist())
    frames = []
    for t in research_tickers():
        path = raw / f"{t}.daily.parquet"
        if not path.exists():
            continue
        daily = pd.read_parquet(path)
        if daily.empty:
            continue
        fp = raw / f"{t}.fund.parquet"
        fund = pd.read_parquet(fp) if fp.exists() else None
        u = weekly_universe(daily, fund, FRIDAYS, t in control)
        frames.append(u.assign(ticker=t).rename_axis("week").reset_index())
    return pd.concat(frames, ignore_index=True)


def report(u: pd.DataFrame, n: int) -> None:
    u = u.assign(year=u["week"].dt.year)
    print(f"Universe: {n} companies; with Yahoo bars in the window: {u['ticker'].nunique()}")
    print(f"Size from a share count public at the time: {u['size_pit'].mean():.0%} of stock-weeks")
    print()
    print(
        "| year | Fridays | with history | past £10k (any Friday) | per Friday past £10k "
        "(median) | aim100 | rest | micro | unknown |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for y, g in u.groupby("year"):
        past = g[g["floor"]]
        per = past.groupby("week").size()
        by = past.pivot_table(index="week", columns="size", aggfunc="size", fill_value=0).median()
        cells = [f"{by.get(s, 0):.0f}" for s in (*SIZES, "unknown")]
        print(
            f"| {y} | {g['week'].nunique()} | {g['ticker'].nunique()} | "
            f"{past['ticker'].nunique()} | {per.median():.0f} | " + " | ".join(cells) + " |"
        )
    first = u.groupby("ticker")["week"].min()
    print()
    print(
        f"History from the first in-sample Friday: {(first == FRIDAYS[0]).sum()}; "
        f"starting later (listed or first traded after it): {(first > FRIDAYS[0]).sum()}"
    )
    ever = u[u["floor"]].groupby("ticker")["size"].agg(lambda s: s.mode().iloc[0])
    print(
        "Past the floor on at least one in-sample Friday, by most common size group: "
        + ", ".join(f"{k} {v}" for k, v in ever.value_counts().items())
    )


def main() -> None:
    raw = Path(sys.argv[1])
    raw.mkdir(parents=True, exist_ok=True)
    ticks = research_tickers()
    with Pool(6) as p:
        for msg in p.imap_unordered(fetch, [(raw, t) for t in ticks]):
            if msg:
                print(msg, file=sys.stderr, flush=True)
    report(panel(raw), len(ticks))


if __name__ == "__main__":
    main()
