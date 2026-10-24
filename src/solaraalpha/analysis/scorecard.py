"""Label scorecard: are ACT labels paying? Forward returns per label, stage and toolkit band.

Every week's labels are stored point-in-time (ADR 0007, 0014), so each (run, ticker) label can
be scored against what the stock did next. :func:`forward_returns` takes the return from the
open of the week after the card (Monday's open: the card is known only after Friday's close,
which cannot be traded; ADR 0029) to the close 2 / 4 / 8 / 16 weeks after the card, plus the
excess over the stock's market
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


def _weekly(daily: pd.DataFrame, as_of: pd.Timestamp | None) -> pd.DataFrame:
    """Weekly ``open`` (adjusted like the close) and ``adj_close``, complete weeks only."""
    w = to_weekly(daily, as_of=as_of)
    return pd.DataFrame({"open": w["open"] * w["adj_close"] / w["close"], "close": w["adj_close"]})


def _ret(weekly: pd.DataFrame | None, date: pd.Timestamp, weeks: int) -> float:
    """From the first open after the card's week to the close ``weeks`` after it."""
    if weekly is None or weekly.empty or date not in weekly.index:
        return np.nan
    later = date + pd.Timedelta(weeks=weeks)
    after = weekly.index[weekly.index > date]
    if later not in weekly.index or after.empty or after[0] > later:
        return np.nan
    start, end = float(weekly["open"][after[0]]), float(weekly["close"][later])
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
    weekly = {t: _weekly(p, as_of) for t, p in prices.items() if not p.empty}
    bweekly = {t: _weekly(p, as_of) for t, p in (bench or {}).items() if not p.empty}
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


AGENT_STAGES = ("validate", "research")
DOWNGRADE_LABELS = ("WATCH", "PASS")
MIN_GROUP = 20  # fewer scored weeks than this per group: say so, conclude nothing
CONFIDENCE_BUCKETS = ((0.0, 0.5), (0.5, 0.75), (0.75, 1.0001))
BOOTSTRAP_N = 2000


def _bootstrap_diff(
    a: np.ndarray, b: np.ndarray, n: int = BOOTSTRAP_N, seed: int = 0
) -> tuple[float, float]:
    """90% bootstrap interval (5th, 95th percentile) for mean(a) - mean(b); NaN without data."""
    if len(a) == 0 or len(b) == 0:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    diffs = rng.choice(a, (n, len(a))).mean(axis=1) - rng.choice(b, (n, len(b))).mean(axis=1)
    lo, hi = np.percentile(diffs, [5, 95])
    return float(lo), float(hi)


def agent_value(
    labels: pd.DataFrame,
    fwd: pd.DataFrame,
    horizons: tuple[int, ...] = HORIZONS,
    seed: int = 0,
) -> pd.DataFrame:
    """Did the agents' downgrades avoid losers? (issue #144, S11.4)

    For each agent stage (validate, research) and horizon: the stocks the screen called ACT
    split into **kept** (the agent also said ACT) and **downgraded** (the agent said WATCH or
    PASS). Columns: counts, mean and median return and mean excess of each group, hit rates,
    the kept - downgraded difference in mean return and a seeded 90% bootstrap interval on it,
    and ``conclusive`` (both groups have at least :data:`MIN_GROUP` scored weeks). ``labels``
    has ``run_id, ticker, stage, label`` (and ``confidence``); ``fwd`` is :func:`forward_returns`
    for the same labels. Rows with no forward return at the horizon are left out."""
    cols = [
        "stage",
        "horizon",
        "n_kept",
        "n_downgraded",
        "kept_mean",
        "downgraded_mean",
        "kept_median",
        "downgraded_median",
        "kept_excess",
        "downgraded_excess",
        "kept_hit",
        "downgraded_hit",
        "diff_mean",
        "diff_lo90",
        "diff_hi90",
        "conclusive",
    ]
    if labels.empty or fwd.empty:
        return pd.DataFrame(columns=cols)
    screen = labels[labels["stage"] == "screen"][["run_id", "ticker", "label"]].rename(
        columns={"label": "screen_label"}
    )
    screen_act = screen[screen["screen_label"] == "ACT"]
    rows = []
    for stage in AGENT_STAGES:
        agent = labels[labels["stage"] == stage][["run_id", "ticker", "label"]]
        if agent.empty:
            continue
        joined = agent.merge(screen_act, on=["run_id", "ticker"], how="inner")
        ret = fwd[fwd["stage"] == "screen"].drop(
            columns=["stage", "label", "date"], errors="ignore"
        )
        joined = joined.merge(ret, on=["run_id", "ticker"], how="left")
        for n in horizons:
            col, exc = f"ret_{n}w", f"exc_{n}w"
            have = joined.dropna(subset=[col])
            kept = have[have["label"] == "ACT"]
            down = have[have["label"].isin(DOWNGRADE_LABELS)]
            a, b = kept[col].to_numpy(dtype="float64"), down[col].to_numpy(dtype="float64")
            lo, hi = _bootstrap_diff(a, b, seed=seed)
            rows.append(
                {
                    "stage": stage,
                    "horizon": n,
                    "n_kept": len(a),
                    "n_downgraded": len(b),
                    "kept_mean": float(a.mean()) if len(a) else np.nan,
                    "downgraded_mean": float(b.mean()) if len(b) else np.nan,
                    "kept_median": float(np.median(a)) if len(a) else np.nan,
                    "downgraded_median": float(np.median(b)) if len(b) else np.nan,
                    "kept_excess": float(kept[exc].mean())
                    if exc in kept and kept[exc].notna().any()
                    else np.nan,
                    "downgraded_excess": float(down[exc].mean())
                    if exc in down and down[exc].notna().any()
                    else np.nan,
                    "kept_hit": float((a > 0).mean()) if len(a) else np.nan,
                    "downgraded_hit": float((b > 0).mean()) if len(b) else np.nan,
                    "diff_mean": float(a.mean() - b.mean()) if len(a) and len(b) else np.nan,
                    "diff_lo90": lo,
                    "diff_hi90": hi,
                    "conclusive": bool(len(a) >= MIN_GROUP and len(b) >= MIN_GROUP),
                }
            )
    return pd.DataFrame(rows, columns=cols)


def calibration(labels: pd.DataFrame, fwd: pd.DataFrame, horizon: int = 8) -> pd.DataFrame:
    """Did the agents' confidence mean anything? Per agent stage and confidence bucket
    (0-0.5, 0.5-0.75, 0.75-1): the count of ACT verdicts and their forward hit rate and mean
    return at ``horizon`` weeks."""
    cols = ["stage", "bucket", "n", "hit_rate", "mean_return"]
    if labels.empty or fwd.empty or "confidence" not in labels:
        return pd.DataFrame(columns=cols)
    ret = fwd[fwd["stage"] == "screen"][["run_id", "ticker", f"ret_{horizon}w"]]
    rows = []
    for stage in AGENT_STAGES:
        agent = labels[(labels["stage"] == stage) & (labels["label"] == "ACT")]
        joined = agent.merge(ret, on=["run_id", "ticker"], how="inner").dropna(
            subset=[f"ret_{horizon}w", "confidence"]
        )
        for lo, hi in CONFIDENCE_BUCKETS:
            g = joined[(joined["confidence"] >= lo) & (joined["confidence"] < hi)]
            r = g[f"ret_{horizon}w"].to_numpy(dtype="float64")
            rows.append(
                {
                    "stage": stage,
                    "bucket": f"{lo:.2f}-{min(hi, 1.0):.2f}",
                    "n": len(r),
                    "hit_rate": float((r > 0).mean()) if len(r) else np.nan,
                    "mean_return": float(r.mean()) if len(r) else np.nan,
                }
            )
    return pd.DataFrame(rows, columns=cols)
