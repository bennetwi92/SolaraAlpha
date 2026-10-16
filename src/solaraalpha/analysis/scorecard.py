"""Label scorecard: are ACT labels paying? Forward returns per label, stage and toolkit band.

Every week's labels are stored point-in-time (ADR 0007, 0014), so each (run, ticker) label can
be scored against what the stock did next. :func:`forward_returns` takes the return from the
close of the card's week to 2 / 4 / 8 / 16 weeks later, plus the excess over the stock's market
benchmark, using **only weekly bars after the card date**; rows without enough future data stay
NaN. Only ``labels`` rows (from #111 on) count: older pass / fail ``steps`` are excluded.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from solaraalpha.data.clean import to_weekly

HORIZONS = (2, 4, 8, 16)
LABELS = ("ACT", "WATCH", "PASS")


def weekly_adj(daily: pd.DataFrame, as_of: pd.Timestamp | None = None) -> pd.Series:
    """Friday-labelled weekly ``adj_close`` of a daily frame (complete weeks only)."""
    w = to_weekly(daily, as_of=as_of)
    return w["adj_close"]


def _ret(weekly: pd.Series | None, date: pd.Timestamp, weeks: int) -> float:
    if weekly is None or weekly.empty or date not in weekly.index:
        return np.nan
    later = date + pd.Timedelta(weeks=weeks)
    if later not in weekly.index:
        return np.nan
    start, end = float(weekly[date]), float(weekly[later])
    return end / start - 1 if start > 0 else np.nan


def forward_returns(
    labels: pd.DataFrame,
    prices: Mapping[str, pd.DataFrame],
    horizons: tuple[int, ...] = HORIZONS,
    bench: Mapping[str, pd.DataFrame] | None = None,
    market_of: Mapping[str, str] | None = None,
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """One row per ``labels`` row (needs ``run_id, ticker, stage, label, date``) with
    ``ret_{N}w`` and ``exc_{N}w`` columns. ``prices`` are daily frames per ticker; ``bench`` daily
    frames per benchmark ticker and ``market_of`` ticker -> benchmark ticker for the excess.
    No look-ahead: a horizon whose week is not yet a complete bar stays NaN."""
    weekly = {t: weekly_adj(p, as_of) for t, p in prices.items() if not p.empty}
    bweekly = {t: weekly_adj(p, as_of) for t, p in (bench or {}).items() if not p.empty}
    rows = []
    for r in labels.to_dict("records"):
        date = pd.Timestamp(r["date"])
        date = date.tz_localize("UTC") if date.tz is None else date.tz_convert("UTC")
        row = {k: r[k] for k in ("run_id", "ticker", "stage", "label")}
        row["date"] = date
        own = weekly.get(str(r["ticker"]))
        mkt = bweekly.get((market_of or {}).get(str(r["ticker"]), ""))
        for n in horizons:
            ret = _ret(own, date, n)
            row[f"ret_{n}w"] = ret
            b = _ret(mkt, date, n)
            row[f"exc_{n}w"] = ret - b if not np.isnan(ret) and not np.isnan(b) else np.nan
        rows.append(row)
    cols = ["run_id", "ticker", "stage", "label", "date"]
    cols += [f"{k}_{n}w" for n in horizons for k in ("ret", "exc")]
    return pd.DataFrame(rows, columns=cols)


def _summary(g: pd.DataFrame, horizons: tuple[int, ...]) -> dict[str, float]:
    out: dict[str, float] = {"count": float(len(g))}
    for n in horizons:
        r = g[f"ret_{n}w"].dropna()
        out[f"n_{n}w"] = float(len(r))
        out[f"mean_{n}w"] = float(r.mean()) if len(r) else np.nan
        out[f"median_{n}w"] = float(r.median()) if len(r) else np.nan
        out[f"hit_{n}w"] = float((r > 0).mean()) if len(r) else np.nan
        e = g[f"exc_{n}w"].dropna()
        out[f"excess_{n}w"] = float(e.mean()) if len(e) else np.nan
    return out


def label_scorecard(fwd: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    """Per stage x label: count, and per horizon the mean and median return, hit rate (> 0)
    and mean excess. Stages in screen / validate / research order, labels ACT / WATCH / PASS."""
    if fwd.empty:
        return pd.DataFrame()
    rows = []
    for (stage, label), g in fwd[fwd["label"].isin(LABELS)].groupby(["stage", "label"]):
        rows.append({"stage": stage, "label": label, **_summary(g, horizons)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    order = {"screen": 0, "validate": 1, "research": 2}
    df["_s"] = df["stage"].map(lambda s: order.get(str(s), 9))
    df["_l"] = df["label"].map(LABELS.index)
    return df.sort_values(["_s", "_l"]).drop(columns=["_s", "_l"]).reset_index(drop=True)


def toolkit_scorecard(
    steps: pd.DataFrame, fwd: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS
) -> pd.DataFrame:
    """Per toolkit x band label: the same statistics, joining ``steps`` (``run_id, ticker,
    toolkit, status`` with status act / watch / pass) to the screen rows of ``fwd``."""
    if steps.empty or fwd.empty:
        return pd.DataFrame()
    screen = fwd[fwd["stage"] == "screen"].drop(columns=["stage", "label"])
    st = steps[steps["status"].isin(["act", "watch", "pass"])]
    joined = st.merge(screen, on=["run_id", "ticker"], how="inner")
    rows = []
    for (toolkit, status), g in joined.groupby(["toolkit", "status"]):
        rows.append({"toolkit": toolkit, "label": str(status).upper(), **_summary(g, horizons)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["_l"] = df["label"].map(LABELS.index)
    return df.sort_values(["toolkit", "_l"]).drop(columns="_l").reset_index(drop=True)
