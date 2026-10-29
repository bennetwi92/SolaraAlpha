"""Build the #223 stock-week panel: one fetch per ticker, cards point-in-time per Friday.

Per ticker and Friday: technical trend, levels compression, fundamentals growth (statutory lags
and NSM publication dates), adv_gbp_20, daily ATR(14)/close, ret_8w, burst_10w at 2/3/4 ATR.
uv run python scripts/research/build_panel.py OUT_DIR [watchlist|research]  (0002 / 0003, #223)
Writes one parquet per ticker plus its filings and fundamentals frames to OUT_DIR (keep it out
of data/). ``research`` builds the AIM research universe (#290) instead of the live watchlist;
every row carries ``floor`` (the £10k research floor that Friday), ``mcap`` and ``size``
(``analysis.universe``). Nothing goes to the DB. Research only: nothing in the pipeline imports
it.
"""

from __future__ import annotations

import math
import sys
import traceback
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

from solaraalpha.analysis.backtest import MIN_WEEKS, entry_and_close
from solaraalpha.analysis.fundamentals import (
    fundamentals_card,
    fx_ticker,
    reporting_currency,
    with_publication,
)
from solaraalpha.analysis.levels import levels_card
from solaraalpha.analysis.liquidity import traded_value
from solaraalpha.analysis.technical import atr, scorecard
from solaraalpha.analysis.universe import panel_tickers, with_universe
from solaraalpha.data.announcements import fetch_announcements, resolve_issuer
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent
from solaraalpha.data.fundamentals import fetch_fundamentals
from solaraalpha.data.http import patient
from solaraalpha.data.prices import fetch_daily

OUT = Path(sys.argv[1])
START = "2021-10-20"
SESSIONS = 50


def burst(daily: pd.DataFrame, friday: pd.Timestamp, a: float, k: float) -> float:
    """1 if high >= 1.15 x entry before low <= entry - k x ATR within 50 sessions from the
    first session after ``friday`` (entry = its open); same-day both = stop first (0)."""
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


def one(t: str) -> str:
    try:
        raw = fetch_daily(t, start=START)
        daily = clean_prices(raw)
        weekly = to_weekly(trim_to_consistent(daily, check_prices(raw)))
        try:
            fund = fetch_fundamentals(t)
        except Exception as e:
            fund = None
            print(f"{t}: no fundamentals ({e})", flush=True)
        ann = None
        try:
            ann = patient(fetch_announcements, resolve_issuer(t).lei)
        except Exception as e:
            print(f"{t}: no filings ({e})", flush=True)
        fund_pub = with_publication(fund, ann) if fund is not None else None
        fx = None
        if fund is not None:
            pair = fx_ticker(reporting_currency(fund), weekly.attrs.get("currency"))
            if pair:
                try:
                    fx = fetch_daily(pair, start=START)["close"]
                except Exception:
                    fx = None
        datr = atr(daily)
        entry, close = entry_and_close(weekly)  # Monday's open after the signal (ADR 0029)
        ret8 = close.shift(-8) / entry - 1
        rows = []
        for i, wk in enumerate(weekly.index):
            if i < MIN_WEEKS:
                continue
            w = weekly.iloc[: i + 1]
            w.attrs = dict(weekly.attrs)
            r: dict = {"ticker": t, "week": wk}
            try:
                r["trend"] = scorecard(w).trend
                lv = levels_card(w)
                r["compression"] = lv.compression
            except (ValueError, IndexError, KeyError):
                continue
            for tag, f in (("stat", fund), ("pub", fund_pub)):
                r[f"growth_{tag}"] = math.nan
                r[f"growth_n_{tag}"] = 0
                if f is None or f.empty:
                    continue
                try:
                    c = fundamentals_card(f, w, as_of=wk, fx=fx)
                    r[f"growth_{tag}"] = c.growth
                    r[f"growth_n_{tag}"] = getattr(c, "growth_n", np.nan)
                except Exception:
                    pass
            d = daily[daily.index <= wk + pd.Timedelta(days=1)]
            r["adv"] = traded_value(d, 20)
            a = float(datr.loc[d.index[-1]]) if len(d) else math.nan
            close = float(d["close"].iloc[-1]) if len(d) else math.nan
            r["atr_pct"] = a / close if close else math.nan
            r["ret_8w"] = float(ret8.loc[wk])
            for k in (2, 3, 4):
                r[f"burst_{k}"] = burst(daily, wk, a, k)
            rows.append(r)
        with_universe(pd.DataFrame(rows), daily, fund_pub, t).to_parquet(OUT / f"{t}.parquet")
        if ann is not None:
            ann.to_parquet(OUT / f"{t}.ann.parquet")
        if fund_pub is not None:
            fund_pub.to_parquet(OUT / f"{t}.fund.parquet")
        return f"{t}: {len(rows)} weeks"
    except Exception:
        return f"{t}: FAILED {traceback.format_exc(limit=2)}"


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    universe = sys.argv[2] if len(sys.argv) > 2 else "watchlist"
    ticks = [t for t in panel_tickers(universe) if not (OUT / f"{t}.parquet").exists()]
    with Pool(4) as p:
        for msg in p.imap_unordered(one, ticks):
            print(msg, flush=True)
