"""Results database: an append-only SQLite store of runs, model outputs and input snapshots.

One file, ``settings.db_path`` (``data/solara.db``, gitignored). Nothing is ever updated or
deleted, so every number can be traced to the run (time, git commit, arguments) that made it:

* ``runs`` — one row per saved script run.
* ``results`` — model cards as a tidy long table: (run, ticker, model, date, field) ->
  ``value`` (numbers, bools as 1/0) or ``text`` (strings, timestamps). ``date`` is the week the
  card describes, separate from the run time, so past scores can be joined to later returns
  without look-ahead. New card fields need no schema change.
* ``steps`` — pipeline outcomes: (run, ticker, node = toolkit id) -> toolkit, status
  (pass / fail / skip / error) and reason — see :mod:`solaraalpha.pipeline`.
* ``prices`` / ``fundamentals`` — what the source returned, stored only when a row is new or
  differs from the latest stored version, so :func:`load_prices` / :func:`load_fundamentals`
  can rebuild what was known on any past date (``as_seen``) without duplicating history.

Plain SQLite SQL only, so the file can move to a hosted SQLite (see docs/decisions/0007).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from solaraalpha.config import REPO_ROOT, settings
from solaraalpha.log import get_logger

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,  -- ISO-8601 UTC
    script     TEXT NOT NULL,
    args       TEXT NOT NULL,  -- JSON
    git_sha    TEXT NOT NULL   -- '-dirty' suffix: uncommitted changes
);
CREATE TABLE IF NOT EXISTS results (
    run_id INTEGER NOT NULL REFERENCES runs,
    ticker TEXT NOT NULL,
    model  TEXT NOT NULL,
    date   TEXT NOT NULL,      -- YYYY-MM-DD the card is as of
    field  TEXT NOT NULL,
    value  REAL,
    text   TEXT,
    PRIMARY KEY (run_id, ticker, model, date, field)
);
CREATE TABLE IF NOT EXISTS steps (
    run_id  INTEGER NOT NULL REFERENCES runs,
    ticker  TEXT NOT NULL,
    node    TEXT NOT NULL,
    toolkit TEXT NOT NULL,
    status  TEXT NOT NULL,     -- pass / fail / skip / error
    reason  TEXT NOT NULL,
    PRIMARY KEY (run_id, ticker, node)
);
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    date   TEXT NOT NULL,
    run_id INTEGER NOT NULL REFERENCES runs,  -- first run that saw this version of the row
    open REAL, high REAL, low REAL, close REAL, adj_close REAL, volume REAL, currency TEXT,
    PRIMARY KEY (ticker, date, run_id)
);
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker TEXT NOT NULL,
    field  TEXT NOT NULL,
    date   TEXT NOT NULL,
    run_id INTEGER NOT NULL REFERENCES runs,
    value REAL, currency TEXT,
    PRIMARY KEY (ticker, field, date, run_id)
);
"""
# Yahoo jitters the last float32 bit of adj_close between requests; real revisions are far larger.
RTOL = 1e-6
_PRICE_COLS = ["open", "high", "low", "close", "adj_close", "volume", "currency"]


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the results database."""
    conn = sqlite3.connect(path or settings.db_path)
    conn.executescript(SCHEMA)
    return conn


def save_run(
    script: str,
    args: dict[str, Any],
    ticker: str,
    cards: dict[str, Any] | None = None,
    daily: pd.DataFrame | None = None,
    fund: pd.DataFrame | None = None,
    path: Path | None = None,
    steps: list[tuple[str, str, str, str]] | None = None,
    others: dict[str, pd.DataFrame] | None = None,
) -> int:
    """Record one run: its model ``cards`` (``{model: dataclass}``) and input snapshots.

    ``steps`` are pipeline outcomes ``(node, toolkit, status, reason)`` for ``ticker``.

    ``daily`` is the frame from :func:`solaraalpha.data.prices.fetch_daily` as fetched (before
    cleaning), ``fund`` the one from :func:`solaraalpha.data.fundamentals.fetch_fundamentals`;
    ``others`` the daily prices of instruments it was compared with (indices, peers, FX), by
    ticker. Returns the new ``run_id``.
    """
    conn = connect(path)
    try:
        with conn:
            run_id = _start_run(conn, script, args)
            for model, card in (cards or {}).items():
                _save_card(conn, run_id, ticker, model, card)
            conn.executemany(
                "INSERT INTO steps VALUES (?, ?, ?, ?, ?, ?)",
                [(run_id, ticker, *s) for s in steps or []],
            )
            if daily is not None:
                _save_snapshot(conn, run_id, "prices", ["date"], _price_rows(ticker, daily))
            for other, prices in (others or {}).items():
                _save_snapshot(conn, run_id, "prices", ["date"], _price_rows(other, prices))
            if fund is not None:
                rows = fund.assign(ticker=ticker, date=_days(fund["date"]))
                _save_snapshot(conn, run_id, "fundamentals", ["field", "date"], rows)
    finally:
        conn.close()
    log.info("saved run %d (%s %s)", run_id, script, ticker)
    return run_id


def git_sha() -> str:
    """Short commit hash of the repo, ``-dirty`` if there are uncommitted changes."""
    try:
        git = ["git", "-C", str(REPO_ROOT)]
        sha = subprocess.run([*git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        dirty = subprocess.run([*git, "status", "--porcelain"], capture_output=True, text=True)
    except OSError:
        return "unknown"
    if sha.returncode:
        return "unknown"
    return sha.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")


def _start_run(conn: sqlite3.Connection, script: str, args: dict[str, Any]) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, script, args, git_sha) VALUES (?, ?, ?, ?)",
        (datetime.now(UTC).isoformat(), script, json.dumps(args, default=str), git_sha()),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _cell(v: Any) -> tuple[float | None, str | None]:
    """(value, text) for one card field."""
    if v is None:
        return None, None
    if isinstance(v, pd.Timestamp | datetime):
        return None, _day(v)
    if isinstance(v, str):
        return None, v
    return float(v), None  # numbers and bools; NaN is stored as NULL


def _save_card(conn: sqlite3.Connection, run_id: int, ticker: str, model: str, card: Any) -> None:
    fields = asdict(card)
    date = _day(fields.pop("date"))
    conn.executemany(
        "INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(run_id, ticker, model, date, k, *_cell(v)) for k, v in fields.items()],
    )


def _day(ts: Any) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _days(s: pd.Series | pd.Index) -> list[str]:
    return [_day(t) for t in s]


def _price_rows(ticker: str, daily: pd.DataFrame) -> pd.DataFrame:
    rows = daily.reset_index().assign(ticker=ticker, currency=daily.attrs.get("currency"))
    rows["date"] = _days(rows["date"])
    return rows


def _save_snapshot(
    conn: sqlite3.Connection, run_id: int, table: str, key: list[str], new: pd.DataFrame
) -> int:
    """Insert rows of ``new`` that are absent from, or differ (beyond ``RTOL``) from, the latest
    stored version."""
    cols = [c for c in _columns(conn, table) if c not in ("ticker", "run_id", *key)]
    ticker = str(new["ticker"].iloc[0]) if len(new) else ""
    old = _latest(_read(conn, table, ticker), key)
    both = new.merge(old, on=key, how="left", suffixes=("", "_old"), indicator=True)
    changed = both["_merge"] == "left_only"
    for c in cols:
        a, b = both[c], both[f"{c}_old"]
        if pd.api.types.is_numeric_dtype(a):
            same = np.isclose(a, pd.to_numeric(b), rtol=RTOL, atol=0, equal_nan=True)
        else:
            same = (a == b) | (a.isna() & b.isna())
        changed |= ~same
    rows = both.loc[changed, ["ticker", *key, *cols]].assign(run_id=run_id)
    rows = rows.astype(object).where(rows.notna(), None)
    names = list(rows.columns)
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
        rows.itertuples(index=False, name=None),
    )
    log.info("%s: %d new/changed rows of %d for %s", table, len(rows), len(new), ticker)
    return len(rows)


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _read(
    conn: sqlite3.Connection, table: str, ticker: str, as_seen: str | None = None
) -> pd.DataFrame:
    sql = f"SELECT t.* FROM {table} t JOIN runs r USING (run_id) WHERE t.ticker = ?"
    params: list[str] = [ticker]
    if as_seen is not None:
        sql += " AND r.started_at <= ?"
        params.append(as_seen)
    return pd.read_sql_query(sql, conn, params=params)


def _latest(df: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    return df.sort_values("run_id").drop_duplicates(key, keep="last").drop(columns="run_id")


def _as_seen(ts: str | pd.Timestamp | None) -> str | None:
    """End of the given day (UTC) as an ISO string comparable with ``runs.started_at``."""
    if ts is None:
        return None
    t = pd.Timestamp(ts)
    t = t.tz_localize(UTC) if t.tz is None else t.tz_convert(UTC)
    if t == t.normalize():
        t += pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return t.isoformat()


def load_prices(
    ticker: str, as_seen: str | pd.Timestamp | None = None, path: Path | None = None
) -> pd.DataFrame:
    """Saved daily prices for ``ticker`` as last seen by ``as_seen`` (default: latest).

    Same shape as :func:`solaraalpha.data.prices.fetch_daily` (UTC date index, ``attrs``).
    """
    conn = connect(path)
    try:
        df = _latest(_read(conn, "prices", ticker, _as_seen(as_seen)), ["date"])
    finally:
        conn.close()
    currency = df["currency"].iloc[-1] if len(df) else None
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("date")), name="date").tz_localize(UTC)
    df = df.sort_index()[_PRICE_COLS[:-1]].astype("float64")
    df.attrs = {"ticker": ticker, "currency": currency}
    return df


def load_fundamentals(
    ticker: str, as_seen: str | pd.Timestamp | None = None, path: Path | None = None
) -> pd.DataFrame:
    """Saved fundamentals for ``ticker`` as last seen by ``as_seen`` (default: latest).

    Same shape as :func:`solaraalpha.data.fundamentals.fetch_fundamentals`. ``as_seen`` is when
    *we* saw the data; it does not replace :func:`solaraalpha.analysis.fundamentals.known`.
    """
    conn = connect(path)
    try:
        df = _latest(_read(conn, "fundamentals", ticker, _as_seen(as_seen)), ["field", "date"])
    finally:
        conn.close()
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(UTC)
    df = df.sort_values(["field", "date"], ignore_index=True)[
        ["field", "date", "value", "currency"]
    ]
    df.attrs["ticker"] = ticker
    return df


def load_results(
    ticker: str | None = None, model: str | None = None, path: Path | None = None
) -> pd.DataFrame:
    """All saved card fields (long), with each run's time, script and commit."""
    sql = "SELECT r.started_at, r.script, r.git_sha, x.* FROM results x JOIN runs r USING (run_id)"
    where, params = [], []
    for col, val in (("ticker", ticker), ("model", model)):
        if val is not None:
            where.append(f"x.{col} = ?")
            params.append(val)
    if where:
        sql += " WHERE " + " AND ".join(where)
    conn = connect(path)
    try:
        return pd.read_sql_query(sql + " ORDER BY x.run_id", conn, params=params)
    finally:
        conn.close()


def load_steps(ticker: str | None = None, path: Path | None = None) -> pd.DataFrame:
    """All saved pipeline outcomes, with each run's time, script and commit."""
    sql = "SELECT r.started_at, r.script, r.git_sha, s.* FROM steps s JOIN runs r USING (run_id)"
    params = [ticker] if ticker is not None else []
    if ticker is not None:
        sql += " WHERE s.ticker = ?"
    conn = connect(path)
    try:
        return pd.read_sql_query(sql + " ORDER BY s.run_id, s.rowid", conn, params=params)
    finally:
        conn.close()


def latest(model: str, path: Path | None = None) -> pd.DataFrame:
    """Most recent ``model`` card per ticker, one row each — for screening across stocks."""
    df = load_results(model=model, path=path)
    if df.empty:
        return pd.DataFrame()
    last = df.groupby("ticker")["run_id"].transform("max")
    df = df[df["run_id"] == last]
    df = df.assign(cell=df["value"].astype(object).where(df["text"].isna(), df["text"]))
    wide = df.pivot_table(
        index="ticker", columns="field", values="cell", aggfunc="first", dropna=False
    )
    meta = df.groupby("ticker")[["date", "started_at", "run_id"]].first()
    return meta.join(wide[[f for f in df["field"].drop_duplicates()]])
