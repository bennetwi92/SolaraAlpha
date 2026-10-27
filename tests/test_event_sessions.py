from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from solaraalpha import event_sessions as es
from solaraalpha import store

# Wednesday 28 Oct 2026, GMT (clocks went back on the 25th): London time = UTC
NOW = pd.Timestamp("2026-10-27T18:00:00Z")


def test_schedule_due_start(db_backend: Path | None) -> None:
    conn = store.connect(db_backend)
    sid = es.schedule(
        conn, "2026-10-28 13:45", "UK Budget", "AIM IHT relief", "agent:risk", ["JET2.L"], NOW
    )
    # the same event and time is the same session
    assert es.schedule(conn, "2026-10-28 13:45", "UK Budget", "again", "agent:pm", None, NOW) == sid
    [s] = es.upcoming(conn, NOW)
    assert (s.session_id, s.due, s.tickers, s.status) == (
        sid,
        "2026-10-28T13:45:00+00:00",
        ["JET2.L"],
        "scheduled",
    )
    assert "Wed 28 Oct 13:45 London: UK Budget (touches JET2.L)" in es.describe(s)
    assert es.due(conn, "2026-10-28T13:30:00Z") == []  # not yet
    assert [x.session_id for x in es.due(conn, "2026-10-28T14:00:00Z")] == [sid]
    assert es.due(conn, "2026-10-28T15:50:00Z") == []  # past the exit deadline
    assert es.due(conn, "2026-10-29T10:00:00Z") == []  # a missed day is not run late
    run = store.start_run("daily", {"session": sid}, conn=conn)
    es.start(conn, sid, run)
    assert es.due(conn, "2026-10-28T14:00:00Z") == []
    found = es.for_run(conn, run)
    assert found is not None and found.status == "started"
    assert es.for_run(conn, run + 1) is None
    with pytest.raises(ValueError, match="started"):
        es.start(conn, sid, run)
    assert "must cut risk" in es.pack_note(found)
    conn.close()


@pytest.mark.parametrize(
    ("at", "actor", "match"),
    [
        ("2026-10-27 10:00", "agent:pm", "past"),
        ("2026-10-28 15:00", "agent:pm", "London session"),  # too late to act
        ("2026-10-28 07:30", "agent:pm", "London session"),  # before the open
        ("2026-10-31 10:00", "agent:pm", "London session"),  # Saturday
        ("2026-10-28 10:00", "agent:monitor", "cannot book"),
    ],
)
def test_schedule_refused(db_backend: Path | None, at: str, actor: str, match: str) -> None:
    conn = store.connect(db_backend)
    with pytest.raises(ValueError, match=match):
        es.schedule(conn, at, "UK Budget", "why", actor, None, NOW)
    conn.close()


def test_cancel_and_zone(db_backend: Path | None) -> None:
    conn = store.connect(db_backend)
    # a zone-less time is London time: 09:30 BST on 1 Oct 2026 = 08:30 UTC
    sid = es.schedule(conn, "2026-11-05 09:30", "BoE decision", "rates", "owner", now=NOW)
    assert es.sessions(conn)[0].due == "2026-11-05T09:30:00+00:00"
    assert es.parse_due("2026-10-01 09:30") == pd.Timestamp("2026-10-01T08:30:00Z")
    es.cancel(conn, sid, "moved", "agent:pm")
    assert es.upcoming(conn, NOW) == [] and es.sessions(conn)[0].status == "cancelled"
    with pytest.raises(ValueError, match="cancelled"):
        es.cancel(conn, sid, "again", "agent:pm")
    conn.close()
