"""Results database: an append-only SQLite store of runs, model outputs and input snapshots.

One file, ``settings.db_path`` (``data/solara.db``, gitignored). Nothing is ever updated or
deleted, so every number can be traced to the run (time, git commit, arguments) that made it:

* ``runs`` — one row per saved script run.
* ``results`` — model cards as a tidy long table: (run, ticker, model, date, field) ->
  ``value`` (numbers, bools as 1/0) or ``text`` (strings, timestamps). ``date`` is the week the
  card describes, separate from the run time, so past scores can be joined to later returns
  without look-ahead. New card fields need no schema change.
* ``steps`` — pipeline outcomes: (run, ticker, toolkit) -> stage, status and reason — see
  :mod:`solaraalpha.pipeline`. ``status`` is the toolkit's label in lowercase (``act`` / ``watch``
  / ``pass`` / ``error`` / ``skip``). Rows from runs before labels existed (#111) hold ``pass`` /
  ``fail``, where ``pass`` meant "moved on to the next stage".
* ``labels`` — one label per (run, ticker, stage): ``screen`` from the pipeline (the combination
  of the four screening toolkits, with each toolkit's label and failing clauses in ``reason``),
  ``validate`` / ``research`` from the agents (``actor`` ``agent:<name>``, with a confidence).
* ``prices`` / ``fundamentals`` — what the source returned, stored only when a row is new or
  differs from the latest stored version, so :func:`load_prices` / :func:`load_fundamentals`
  can rebuild what was known on any past date (``as_seen``) without duplicating history.

A run can cover many tickers (a weekly scan is one run): :func:`start_run`, then
:func:`save_ticker` per ticker. :func:`save_run` does both for a single-ticker script.

With ``SOLARA_TURSO_URL`` / ``SOLARA_TURSO_TOKEN`` set, the same tables live in the hosted
Turso database instead (:mod:`solaraalpha.turso`); unset, the local file is used. Passing
``path`` always uses that local file. Plain SQLite SQL only (docs/decisions/0007, 0013). Read
frames with :func:`query` (not ``pd.read_sql_query``, which rejects the Turso client);
``scripts/db.py`` copies a DB between the two backends with :func:`copy_db`.
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

from solaraalpha import turso
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
CREATE TABLE IF NOT EXISTS labels (
    run_id     INTEGER NOT NULL REFERENCES runs,
    ticker     TEXT NOT NULL,
    stage      TEXT NOT NULL,      -- screen / validate / research
    label      TEXT NOT NULL,      -- ACT / WATCH / PASS / ERROR
    confidence REAL,               -- agents only, [0, 1]
    reason     TEXT NOT NULL,
    actor      TEXT NOT NULL,      -- pipeline / agent:<name>
    PRIMARY KEY (run_id, ticker, stage)
);
"""
STEPS = """
CREATE TABLE IF NOT EXISTS steps (
    run_id  INTEGER NOT NULL REFERENCES runs,
    ticker  TEXT NOT NULL,
    stage   TEXT,              -- NULL: saved before stages were recorded
    toolkit TEXT NOT NULL,
    status  TEXT NOT NULL,     -- act / watch / pass / error / skip (pass / fail before #111)
    reason  TEXT NOT NULL,
    PRIMARY KEY (run_id, ticker, toolkit)
);
"""
# Yahoo jitters the last float32 bit of adj_close between requests; real revisions are far larger.
RTOL = 1e-6
_PRICE_COLS = ["open", "high", "low", "close", "adj_close", "volume", "currency"]


Conn = sqlite3.Connection | turso.Connection


def connect(path: Path | None = None) -> Conn:
    """Open (creating tables if needed) the results database: the local file at ``path``, else
    Turso when configured, else ``settings.db_path``."""
    conn: Conn
    if path is None and settings.turso_url:
        conn = turso.Connection(settings.turso_url, settings.turso_token.get_secret_value())
    else:
        conn = sqlite3.connect(path or settings.db_path)
    conn.executescript(SCHEMA)
    if "node" in _columns(conn, "steps"):  # 0008 layout: node (= toolkit id), no stage
        conn.executescript(
            "ALTER TABLE steps RENAME TO steps_0008;"
            + STEPS
            + "INSERT INTO steps SELECT run_id, ticker, NULL, toolkit, status, reason"
            " FROM steps_0008 ORDER BY rowid; DROP TABLE steps_0008;"
        )
    conn.executescript(STEPS)
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
    """Record a one-ticker run (:func:`start_run` + :func:`save_ticker`); returns its ``run_id``."""
    run_id = start_run(script, args, path)
    save_ticker(run_id, ticker, cards, daily, fund, path, steps, others)
    return run_id


def start_run(script: str, args: dict[str, Any], path: Path | None = None) -> int:
    """Record the start of a run (time, script, args, commit) and return its ``run_id``."""
    conn = connect(path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at, script, args, git_sha) VALUES (?, ?, ?, ?)",
                (datetime.now(UTC).isoformat(), script, json.dumps(args, default=str), git_sha()),
            )
    finally:
        conn.close()
    assert cur.lastrowid is not None
    return cur.lastrowid


def save_ticker(
    run_id: int,
    ticker: str,
    cards: dict[str, Any] | None = None,
    daily: pd.DataFrame | None = None,
    fund: pd.DataFrame | None = None,
    path: Path | None = None,
    steps: list[tuple[str, str, str, str]] | None = None,
    others: dict[str, pd.DataFrame] | None = None,
) -> None:
    """Record one ticker's model ``cards`` (``{model: dataclass}``) and input snapshots under
    ``run_id``.

    ``steps`` are pipeline outcomes ``(stage, toolkit, status, reason)`` for ``ticker``.

    ``daily`` is the frame from :func:`solaraalpha.data.prices.fetch_daily` as fetched (before
    cleaning), ``fund`` the one from :func:`solaraalpha.data.fundamentals.fetch_fundamentals`;
    ``others`` the daily prices of instruments it was compared with (indices, peers, FX), by
    ticker.
    """
    conn = connect(path)
    try:
        with conn:
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
    log.info("saved %s in run %d", ticker, run_id)


def save_label(
    run_id: int,
    ticker: str,
    stage: str,
    label: str,
    reason: str = "",
    confidence: float | None = None,
    actor: str = "pipeline",
    path: Path | None = None,
    conn: Conn | None = None,
) -> None:
    """Record ``ticker``'s label for ``stage`` in ``run_id`` (one per run, ticker and stage)."""
    if label not in ("ACT", "WATCH", "PASS", "ERROR"):
        raise ValueError(f"label must be ACT / WATCH / PASS / ERROR, got {label!r}")
    own = conn is None
    conn = conn or connect(path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO labels VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, ticker, stage, label, confidence, reason, actor),
            )
    finally:
        if own:
            conn.close()


def load_labels(
    run_id: int | None = None,
    ticker: str | None = None,
    stage: str | None = None,
    path: Path | None = None,
) -> pd.DataFrame:
    """Saved labels, with each run's time, script and commit."""
    sql = "SELECT r.started_at, r.script, r.git_sha, l.* FROM labels l JOIN runs r USING (run_id)"
    where, params = [], []
    for col, val in (("run_id", run_id), ("ticker", ticker), ("stage", stage)):
        if val is not None:
            where.append(f"l.{col} = ?")
            params.append(val)
    if where:
        sql += " WHERE " + " AND ".join(where)
    conn = connect(path)
    try:
        return query(sql + " ORDER BY l.run_id, l.rowid", conn, params=params)
    finally:
        conn.close()


def shortlist(
    run_id: int,
    stage: str = "screen",
    labels: tuple[str, ...] = ("ACT",),
    path: Path | None = None,
) -> list[str]:
    """Tickers whose ``stage`` label in ``run_id`` is one of ``labels``, in saved order."""
    df = load_labels(run_id, stage=stage, path=path)
    return [str(t) for t in df.loc[df["label"].isin(labels), "ticker"]]


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


def _cell(v: Any) -> tuple[float | None, str | None]:
    """(value, text) for one card field."""
    if v is None:
        return None, None
    if isinstance(v, pd.Timestamp | datetime):
        return None, _day(v)
    if isinstance(v, str):
        return None, v
    return float(v), None  # numbers and bools; NaN is stored as NULL


def _save_card(conn: Conn, run_id: int, ticker: str, model: str, card: Any) -> None:
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


def _save_snapshot(conn: Conn, run_id: int, table: str, key: list[str], new: pd.DataFrame) -> int:
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


def _columns(conn: Conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _read(conn: Conn, table: str, ticker: str, as_seen: str | None = None) -> pd.DataFrame:
    sql = f"SELECT t.* FROM {table} t JOIN runs r USING (run_id) WHERE t.ticker = ?"
    params: list[str] = [ticker]
    if as_seen is not None:
        sql += " AND r.started_at <= ?"
        params.append(as_seen)
    return query(sql, conn, params=params)


def query(sql: str, conn: Conn, params: list[Any] | None = None) -> pd.DataFrame:
    """Run ``sql`` on ``conn`` and return the rows as a DataFrame (works on both backends;
    ``pd.read_sql_query`` does not accept the Turso client)."""
    cur = conn.execute(sql, params or [])
    return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description or []])


def tables(conn: Conn) -> dict[str, str]:
    """``{table: CREATE statement}`` for every table in ``conn``, in creation order."""
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY rowid"
    ).fetchall()
    return {str(name): str(sql) for name, sql in rows}


def counts(conn: Conn) -> dict[str, int]:
    """Row count per table."""
    return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables(conn)}


def copy_db(
    src: Conn, dst: Conn, append_new_runs: bool = False, page: int = 5000
) -> dict[str, int]:
    """Copy every table of ``src`` into ``dst`` (tables are created in ``dst`` if missing).

    By default ``dst`` must hold no ``runs`` row (``ValueError`` otherwise) and rows are copied
    verbatim, run ids included. With ``append_new_runs``, only runs whose
    ``(started_at, script, git_sha)`` is absent from ``dst`` are copied, under new run ids, with
    the rows of every run-linked table remapped; tables without a ``run_id`` column are skipped.
    Returns the rows copied per table.
    """
    run_map: dict[int, int] | None = None
    existing = int(dst.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
    if not append_new_runs and existing:
        raise ValueError(f"destination already holds {existing} runs; use append_new_runs")
    copied: dict[str, int] = {}
    with dst:
        if append_new_runs:
            seen = set(dst.execute("SELECT started_at, script, git_sha FROM runs").fetchall())
            run_map = {}
            for run_id, started, script, args, sha in src.execute(
                "SELECT run_id, started_at, script, args, git_sha FROM runs ORDER BY run_id"
            ).fetchall():
                if (started, script, sha) in seen:
                    continue
                cur = dst.execute(
                    "INSERT INTO runs (started_at, script, args, git_sha) VALUES (?, ?, ?, ?)",
                    (started, script, args, sha),
                )
                assert cur.lastrowid is not None
                run_map[int(run_id)] = int(cur.lastrowid)
            copied["runs"] = len(run_map)
        for name, sql in tables(src).items():
            dst.execute(
                sql
                if "IF NOT EXISTS" in sql
                else sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)
            )
            if name == "runs" and run_map is not None:
                continue
            cols = _columns(src, name)
            if run_map is not None and "run_id" not in cols:
                log.warning("%s: no run_id column, not copied in append mode", name)
                continue
            copied[name] = 0
            insert = f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
            for offset in range(0, 10**9, page):
                raw = src.execute(
                    f"SELECT {', '.join(cols)} FROM {name} ORDER BY rowid LIMIT ? OFFSET ?",
                    (page, offset),
                ).fetchall()
                rows = raw
                if run_map is not None:
                    i = cols.index("run_id")
                    rows = [
                        tuple(run_map[int(r[i])] if j == i else v for j, v in enumerate(r))
                        for r in raw
                        if int(r[i]) in run_map
                    ]
                if rows:
                    dst.executemany(insert, rows)
                    copied[name] += len(rows)
                if len(raw) < page:
                    break
    return copied


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
        return query(sql + " ORDER BY x.run_id", conn, params=params)
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
        return query(sql + " ORDER BY s.run_id, s.rowid", conn, params=params)
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
