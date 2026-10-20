"""Events and catalysts for one stock: what is coming (anticipate) and what just happened (react).

Generic across stocks: everything is derived from the issuer's own filing history and prices,
with no per-stock calendar or taxonomy. Interpretation (did it beat, does it change estimates,
is the thesis intact?) is left to the analyst agent — see ``.claude/skills/events/SKILL.md``
and docs/decisions/0006.

* :func:`material` drops routine administrative filings.
* :func:`reactions` measures the market's response to each announcement in the first session
  it could trade on — no look-ahead: a filing at/after the 16:30 London close counts from the
  next session.
* :func:`expected_events` projects the issuer's recurring scheduled filings (results, trading
  updates, AGM) one cycle forward from their publication anniversaries. The dates are
  *estimates*; the company's own notice of a date overrides them.
* :func:`event_moves` summarises the past results-day moves (the gap risk a stop can't cover).
* :func:`build_pack` fetches and assembles all of it into the event pack the agent reads.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from solaraalpha.config import settings
from solaraalpha.data.announcements import Issuer, fetch_announcements, fetch_text, resolve_issuer
from solaraalpha.data.clean import check_prices, clean_prices, trim_to_consistent
from solaraalpha.data.news import fetch_news
from solaraalpha.data.prices import fetch_daily

LONDON = "Europe/London"
CLOSE = pd.Timedelta(hours=16, minutes=30)  # LSE continuous trading ends; auction follows
# Administrative filings with no information about the business: daily buyback execution
# reports, monthly total-voting-rights, six-monthly block listing returns.
ROUTINE = frozenset({"POS", "TVR", "BLR"})
# NSM category groups whose filings recur on the reporting cycle.
SCHEDULED = frozenset({"Financial Results", "General Meetings"})
VOL_SESSIONS = 60  # return volatility for the z-score
VOLUME_SESSIONS = 20  # baseline volume
PRE_SESSIONS = 5  # run-up before the announcement (leakage / positioning)
NOTICE = frozenset({"NOR"})  # "Notice of Results": a date, not news
EVENT_QUANTILE = 0.9


def material(ann: pd.DataFrame) -> pd.DataFrame:
    return ann[~ann["type_code"].isin(ROUTINE)]


def reaction_session(published: pd.Timestamp, sessions: pd.DatetimeIndex) -> pd.Timestamp | None:
    """First trading session (from ``sessions``, midnight-UTC dates) that can act on a filing."""
    local = published.tz_convert(LONDON)
    day = pd.Timestamp(local.date(), tz="UTC")
    after_close = local - local.normalize() >= CLOSE
    pos = int(sessions.searchsorted(day, side="right" if after_close else "left"))
    return sessions[pos] if pos < len(sessions) else None


def reactions(ann: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """``ann`` plus the price response in the first session that could trade on each filing.

    Added columns: ``session``; ``gap`` (open vs previous close); ``ret`` (close-to-close,
    adjusted); ``z`` (``ret`` over the prior ``VOL_SESSIONS``-session daily volatility);
    ``volume_ratio`` (session volume / prior ``VOLUME_SESSIONS``-session median); ``pre``
    (return over the ``PRE_SESSIONS`` sessions before); ``since`` (return from that session's
    close to the latest close). NaN where the session isn't in ``daily`` yet. Several filings
    in one session share its reaction.
    """
    adj, close, vol = daily["adj_close"], daily["close"], daily["volume"]
    logret = np.log(adj).diff()
    sigma = logret.rolling(VOL_SESSIONS).std().shift(1)
    base_vol = vol.rolling(VOLUME_SESSIONS).median().shift(1)
    out = ann.copy()
    cols: dict[str, list[float | pd.Timestamp | None]] = {
        k: [] for k in ["session", "gap", "ret", "z", "volume_ratio", "pre", "since"]
    }
    for published in ann["published"]:
        s = reaction_session(published, pd.DatetimeIndex(daily.index))
        i = daily.index.get_loc(s) if s is not None else None
        if not isinstance(i, int) or i == 0:
            for k in cols:
                cols[k].append(None if k == "session" else np.nan)
            continue
        ret = adj.iloc[i] / adj.iloc[i - 1] - 1
        cols["session"].append(s)
        cols["gap"].append(daily["open"].iloc[i] / close.iloc[i - 1] - 1)
        cols["ret"].append(ret)
        cols["z"].append(np.log1p(ret) / sigma.iloc[i])
        cols["volume_ratio"].append(vol.iloc[i] / base_vol.iloc[i])
        j = i - 1 - PRE_SESSIONS
        cols["pre"].append(adj.iloc[i - 1] / adj.iloc[j] - 1 if j >= 0 else np.nan)
        cols["since"].append(adj.iloc[-1] / adj.iloc[i] - 1)
    for k, v in cols.items():
        out[k] = pd.Series(
            v, index=out.index, dtype="datetime64[ns, UTC]" if k == "session" else "float64"
        )
    return out


def expected_events(
    ann: pd.DataFrame,
    as_of: str | pd.Timestamp,
    horizon_weeks: int = 16,
    tol_days: int = 21,
    min_years: int = 2,
) -> pd.DataFrame:
    """Recurring scheduled filings expected from ``as_of`` to ``horizon_weeks`` ahead.

    Each past filing in a :data:`SCHEDULED` category is projected forward by whole years.
    Projections within ``tol_days`` of each other form one expected event, kept if it is
    backed by at least ``min_years`` distinct past years, and dropped if this cycle's filing
    has already been published (one inside the cluster's window, widened by ``tol_days``, on
    or before ``as_of``). Uses only filings published before ``as_of``.

    Columns: ``window_start`` / ``window_end`` / ``likely`` (median projection), ``years``
    (supporting years), ``last_seen`` (latest past filing in the cluster), ``types``,
    ``headline`` (latest past headline), ``past`` (past publication dates), and — if ``ann``
    has a ``ret`` column — ``median_abs_move`` (typical size of the session reaction).
    """
    now = pd.Timestamp(as_of)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    past = ann[(ann["published"] < now) & ann["category"].isin(SCHEDULED)]
    lo, hi = now - pd.Timedelta(days=tol_days), now + pd.Timedelta(weeks=horizon_weeks)
    if past.empty:
        return pd.DataFrame()
    years = range(1, hi.year - past["published"].min().year + 1)
    proj = pd.concat([past.assign(proj=past["published"] + pd.DateOffset(years=k)) for k in years])
    proj = proj[proj["proj"].between(lo, hi)].sort_values("proj")
    cluster = (proj["proj"].diff().dt.days > tol_days).cumsum()

    rows = []
    for _, c in proj.groupby(cluster):
        n_years = c["published"].dt.year.nunique()
        start, end = c["proj"].min(), c["proj"].max()
        filed = past["published"].between(start - pd.Timedelta(days=tol_days), now)
        if n_years < min_years or filed.any():
            continue
        c = c.sort_values("published")
        row = {
            "window_start": max(start, now),
            "window_end": end,
            "likely": max(c["proj"].median(), now),
            "years": int(n_years),
            "last_seen": c["published"].iloc[-1],
            "types": ",".join(sorted(set(c["type_code"]))),
            "headline": c["headline"].iloc[-1],
            "past": list(c["published"].dt.strftime("%Y-%m-%d")),
        }
        if "ret" in c:
            row["median_abs_move"] = float(c["ret"].abs().median())
        rows.append(row)
    return pd.DataFrame(rows)


def event_moves(ann: pd.DataFrame) -> dict[str, Any]:
    """Past results-day moves from ``ann`` with :func:`reactions`: ``n`` sessions with a
    material Financial Results filing (notices of results dropped; filings sharing a session
    count once), ``worst`` (most negative session return, NaN if none) and ``p90_abs`` (the
    :data:`EVENT_QUANTILE` of the absolute returns)."""
    empty = {"n": 0, "worst": float("nan"), "p90_abs": float("nan")}
    if "ret" not in ann:
        return empty
    res = material(ann[(ann["category"] == "Financial Results") & ~ann["type_code"].isin(NOTICE)])
    rets = res.dropna(subset=["ret"]).drop_duplicates("session")["ret"]
    if rets.empty:
        return empty
    return {
        "n": len(rets),
        "worst": float(rets.min()),
        "p90_abs": float(rets.abs().quantile(EVENT_QUANTILE)),
    }


def build_pack(
    ticker: str,
    days: int = 7,
    horizon: int = 16,
    lei: str | None = None,
    news_query: str | None = None,
    news: bool = True,
    out: Path | None = None,
    daily: pd.DataFrame | None = None,
) -> tuple[dict[str, Any], Path]:
    """Fetch and write the event pack for ``ticker``: ``pack.json`` plus the full text of each
    recent material announcement, under ``out`` (default
    ``data/processed/events/<ticker>/<date>/``). ``daily`` (as fetched) skips a price fetch.

    Returns the pack and its directory.
    """
    now = pd.Timestamp(datetime.now(UTC))
    since = now - pd.Timedelta(days=days)
    notes: list[str] = []
    issuer = Issuer(ticker, "", lei, "", "") if lei else resolve_issuer(ticker)
    out = out or settings.processed_dir / "events" / ticker / f"{now:%Y-%m-%d}"
    (out / "text").mkdir(parents=True, exist_ok=True)

    ann = fetch_announcements(issuer.lei)
    try:
        daily = fetch_daily(ticker) if daily is None else daily
        issues = check_prices(daily)
        if not issues.empty:
            notes.append(f"{len(issues)} price data-quality issues; see scripts/stock_prices.py")
        daily = trim_to_consistent(clean_prices(daily), issues)
        ann = reactions(ann, daily)
        last = {"session": f"{daily.index[-1]:%Y-%m-%d}", "close": float(daily["close"].iloc[-1])}
    except requests.RequestException as e:
        notes.append(f"prices unavailable ({e}); no market reactions")
        last = {}

    recent = material(ann[ann["published"] >= since]).copy()
    paths = []
    for row in recent.itertuples():
        path = out / "text" / f"{row.id}.txt"
        path.write_text(fetch_text(str(row.url)))
        paths.append(str(path))
    recent["text_path"] = paths
    routine = len(ann[ann["published"] >= since]) - len(recent)
    expected = expected_events(ann, now, horizon_weeks=horizon)
    history = material(ann[ann["category"] == "Financial Results"]).tail(12)

    headlines = pd.DataFrame()
    if news:
        query = news_query or f'"{_short_name(issuer.name or ticker)}"'
        try:
            headlines = fetch_news(query, days=days)
        except requests.RequestException as e:
            notes.append(f"news unavailable ({e})")

    pack = {
        "generated": now.isoformat(),
        "window": {"since": since.isoformat(), "until": now.isoformat()},
        "issuer": vars(issuer),
        "last_price": last,
        "announcements": _records(recent),
        "routine_filings_skipped": routine,
        "expected_events": _records(expected),
        "results_history": _records(history),
        "event_moves": event_moves(ann),
        "news_query": headlines.attrs.get("query"),
        "news": _records(headlines),
        "notes": notes,
    }
    (out / "pack.json").write_text(json.dumps(pack, indent=2))
    return pack, out


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso", date_unit="s"))


def _short_name(name: str) -> str:
    """'JET2 PLC' -> 'JET2'; legal suffixes would make the news search miss most articles."""
    return " ".join(
        w for w in name.split() if w.upper().strip(".") not in {"PLC", "LIMITED", "LTD"}
    )
