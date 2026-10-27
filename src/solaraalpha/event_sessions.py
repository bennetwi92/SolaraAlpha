"""Event sessions: a review of the whole book booked for a known midweek event (ADR 0037).

The PM (weekly run) and the risk manager (daily catch-up) book a session for a dated event that
can move the book: the Budget, a rate decision, a held stock's results. When it is due, the
event-session workflow runs the daily check on the session's prices so far and then the daily
agents (monitor → risk → pm-daily when called), whose changes may only cut risk; the execution
job sends them the same afternoon. The ``event_sessions`` table is append-only: a session is
``scheduled``, then ``started`` (with the daily run) or ``cancelled``.

A session is booked inside a London session, from the open to :data:`LATEST` before the close,
so its changes can still go out before the exit deadline (15:45).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from solaraalpha.store import Conn, query

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_sessions (
    session_id INTEGER NOT NULL,
    kind       TEXT NOT NULL,     -- scheduled / started / cancelled
    at         TEXT NOT NULL,     -- when the row was written, ISO-8601 UTC
    due        TEXT,              -- scheduled: when the review runs, ISO-8601 UTC
    event      TEXT,              -- scheduled: the event, in plain words
    tickers    TEXT,              -- scheduled: JSON list of the held stocks it touches ([] = market-wide)
    reason     TEXT,              -- why (scheduled / cancelled)
    actor      TEXT NOT NULL,
    run_id     INTEGER            -- started: the daily run of the session
);
"""
LATEST = pd.Timedelta(hours=2)  # booked no later than 14:30 London: time to act before 15:45
BOOKERS = frozenset({"agent:pm", "agent:risk", "owner"})  # who books a session (owner, 2026-10-27)
# risk-cutting only (owner, 2026-10-27): what the monitor may record in an event session
ALLOWED_ACTIONS = frozenset({"hold", "tighten_stop", "take_partial", "exit", "cancel"})


@dataclass(frozen=True)
class Session:
    session_id: int
    due: str
    event: str
    tickers: list[str]
    reason: str
    actor: str
    status: str  # scheduled / started / cancelled
    run_id: int | None


def ensure(conn: Conn) -> None:
    conn.executescript(SCHEMA)


def sessions(conn: Conn) -> list[Session]:
    """Every session with its current status, by due time."""
    ensure(conn)
    df = query("SELECT * FROM event_sessions ORDER BY rowid", conn)
    out = []
    for _, rows in df.groupby("session_id", sort=False):
        first = rows[rows["kind"] == "scheduled"].iloc[0]
        last = rows.iloc[-1]
        started = rows[rows["kind"] == "started"]
        out.append(
            Session(
                session_id=int(rows["session_id"].iloc[0]),
                due=str(first["due"]),
                event=str(first["event"]),
                tickers=json.loads(first["tickers"] or "[]"),
                reason=str(first["reason"] or ""),
                actor=str(first["actor"]),
                status=str(last["kind"]),
                run_id=int(started["run_id"].iloc[0]) if len(started) else None,
            )
        )
    return sorted(out, key=lambda s: s.due)


def _get(conn: Conn, session_id: int) -> Session:
    for s in sessions(conn):
        if s.session_id == session_id:
            return s
    raise ValueError(f"no event session {session_id}")


def _write(conn: Conn, session_id: int, kind: str, actor: str, **cols: Any) -> None:
    with conn:
        conn.execute(
            "INSERT INTO event_sessions (session_id, kind, at, due, event, tickers, reason, actor,"
            " run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                kind,
                datetime.now(UTC).isoformat(),
                cols.get("due"),
                cols.get("event"),
                cols.get("tickers"),
                cols.get("reason"),
                actor,
                cols.get("run_id"),
            ),
        )


def parse_due(text: str) -> pd.Timestamp:
    """``text`` as a UTC time; a time without a zone is London time."""
    from solaraalpha.portfolio.execution import LONDON

    ts = pd.Timestamp(text)
    return (ts.tz_localize(LONDON) if ts.tzinfo is None else ts).tz_convert("UTC")


def schedule(
    conn: Conn,
    due: str,
    event: str,
    reason: str,
    actor: str,
    tickers: list[str] | None = None,
    now: Any = None,
) -> int:
    """Book a session; returns its id (an existing one for the same event and time). Refused
    (``ValueError``) in the past, outside a London session, or later than :data:`LATEST`
    before its close."""
    from solaraalpha.portfolio.execution import session as london_session

    at = parse_due(due)
    now = pd.Timestamp(now or datetime.now(UTC))
    if actor not in BOOKERS:
        raise ValueError(f"{actor} cannot book an event session: {', '.join(sorted(BOOKERS))} can")
    if not event.strip() or not reason.strip():
        raise ValueError("an event session needs the event and the reason")
    if at <= now:
        raise ValueError(f"{at.isoformat()} is in the past")
    s = london_session(at)
    if s is None or not s[0] <= at <= s[1] - LATEST:
        raise ValueError(
            f"{at.isoformat()} is not inside a London session up to {LATEST} before the close"
            " (08:00-14:30 London on a full day): the review must leave time to act"
        )
    for x in sessions(conn):
        if x.status == "scheduled" and x.event == event.strip() and x.due == at.isoformat():
            return x.session_id
    row = conn.execute("SELECT MAX(session_id) FROM event_sessions").fetchone()
    sid = int(row[0] or 0) + 1
    _write(
        conn,
        sid,
        "scheduled",
        actor,
        due=at.isoformat(),
        event=event.strip(),
        tickers=json.dumps(sorted(tickers or [])),
        reason=reason.strip(),
    )
    return sid


def cancel(conn: Conn, session_id: int, reason: str, actor: str) -> None:
    s = _get(conn, session_id)
    if s.status != "scheduled":
        raise ValueError(f"event session {session_id} is {s.status}: only a scheduled one cancels")
    _write(conn, session_id, "cancelled", actor, reason=reason)


def due(conn: Conn, now: Any = None) -> list[Session]:
    """Scheduled sessions whose time has come today, while there is still time to act (before
    the exit deadline); earliest first. One missed on an earlier day is not run late."""
    from solaraalpha.portfolio.execution import LONDON, exit_window

    now = pd.Timestamp(now or datetime.now(UTC))
    if not exit_window(now):
        return []
    today = now.tz_convert(LONDON).date()
    return [
        s
        for s in sessions(conn)
        if s.status == "scheduled"
        and pd.Timestamp(s.due) <= now
        and pd.Timestamp(s.due).tz_convert(LONDON).date() == today
    ]


def start(conn: Conn, session_id: int, run_id: int, actor: str = "pipeline") -> None:
    s = _get(conn, session_id)
    if s.status != "scheduled":
        raise ValueError(f"event session {session_id} is {s.status}, not scheduled")
    _write(conn, session_id, "started", actor, run_id=run_id)


def for_run(conn: Conn, run_id: int) -> Session | None:
    """The event session daily run ``run_id`` was started for, if any."""
    return next((s for s in sessions(conn) if s.run_id == run_id), None)


def upcoming(conn: Conn, now: Any = None) -> list[Session]:
    now = pd.Timestamp(now or datetime.now(UTC))
    return [s for s in sessions(conn) if s.status == "scheduled" and pd.Timestamp(s.due) > now]


def describe(s: Session) -> str:
    """One line for packs and the thread, in London time."""
    from solaraalpha.portfolio.execution import LONDON

    local = pd.Timestamp(s.due).tz_convert(LONDON).strftime("%a %d %b %H:%M")
    on = f" (touches {', '.join(s.tickers)})" if s.tickers else " (the whole book)"
    return f"#{s.session_id} {local} London: {s.event}{on}, booked by {s.actor}: {s.reason}"


def pack_note(s: Session) -> str:
    """What the monitor and risk packs say in an event session."""
    return (
        f"EVENT SESSION {describe(s)}. Prices are the session so far (intraday), not a close."
        " Every change must cut risk: hold, tighten_stop, take_partial, exit; a pending entry"
        " hold or cancel (no add, revise_target or amend_entry)."
    )
