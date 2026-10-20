from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import agents, postmortem, store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.review import ReviewCard
from solaraalpha.portfolio.triggers import Trigger

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import postmortem_pack
import record

FIXTURES = Path(__file__).parent / "fixtures"
ENTRY_WEEK = pd.Timestamp("2026-07-10", tz="UTC")  # the scan's Friday; entry fills on Monday 13th


@dataclass(frozen=True)
class Tech:
    date: pd.Timestamp
    trend: float
    momentum: float
    close: float


def _daily(end: str, n: int = 400) -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=n, tz="UTC", name="date")
    close = 1500.0 + np.arange(n, dtype="float64") % 7  # not flat, not stale
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "adj_close": close,
            "volume": 1e5,
        },
        index=idx,
    )
    df.attrs = {"ticker": "JET2.L", "currency": "GBp"}
    return df


def _set_started(conn: book.Conn, run_id: int, at: str) -> None:
    conn.execute("UPDATE runs SET started_at = ? WHERE run_id = ?", (at, run_id))


@pytest.fixture
def world(db_backend: Path | None) -> tuple[Path | None, int, int, int]:
    """A JET2.L trade drafted from a scan (entry conditions, research), reviewed once (review card
    + an accepted revision), with a daily trigger, stopped out on 2026-08-21; stored prices at
    the scan (to 10 Jul) and at a later daily run (to 9 Oct)."""
    conn = store.connect(db_backend)
    with conn:
        scan = store.start_run("scan", {}, conn=conn)
        _set_started(conn, scan, "2026-07-12T18:00:00+00:00")
        tech = Tech(ENTRY_WEEK, 0.9, 0.5, 1500.0)
        store.save_ticker(
            scan, "JET2.L", {"technical": tech}, daily=_daily("2026-07-10"), conn=conn
        )
        store.save_label(scan, "JET2.L", "screen", "ACT", "all ACT", conn=conn)
    conn.close()
    record.main(
        [
            "verdict",
            str(FIXTURES / "verdict_research.json"),
            "--run",
            str(scan),
            "--db",
            str(db_backend),
        ]
        if db_backend
        else ["verdict", str(FIXTURES / "verdict_research.json"), "--run", str(scan)]
    )
    conn = book.connect(db_backend)
    with conn:
        pid, oid = book.draft_position(
            conn,
            "JET2.L",
            1500.0,
            1400.0,
            100,
            target=1800.0,
            opened_from_run_id=scan,
            payload={"entry_type": "limit", "reward_risk": 3.0, "coil_pending": False},
            at="2026-07-12T18:30:00+00:00",
            actor="pipeline",
        )
        book.add_entry_conditions(conn, pid, scan, {"technical": tech})
        book.add_thesis_condition(conn, pid, "trend < 0", "machine")
        book.approve_position(conn, pid)
        book.fill(conn, oid, 100, 1500.0, at="2026-07-13T09:00:00+00:00", commission=5.0)
        # a weekly review two weeks in
        review_run = store.start_run("scan", {}, conn=conn)
        _set_started(conn, review_run, "2026-07-26T18:00:00+00:00")
        card = ReviewCard(
            pd.Timestamp("2026-07-24", tz="UTC"),
            pid,
            2,
            0.3,
            1400.0,
            1420.0,
            True,
            "",
            False,
            "hold",
            "stop ratcheted",
        )
        store.save_ticker(review_run, "JET2.L", {"review": card}, conn=conn)
        rid = book.propose_revision(
            conn,
            pid,
            "tighten_stop",
            {"stop": 1420.0},
            "chandelier",
            "agent:monitor",
            at="2026-07-27T18:00:00+00:00",
            auto_apply=False,  # accepted by the owner an hour later (before ADR 0024)
        )
        book.accept_revision(conn, rid, at="2026-07-27T19:00:00+00:00")
        daily = store.start_run("daily", {}, conn=conn)
        _set_started(conn, daily, "2026-08-20T17:30:00+00:00")
        store.save_triggers(
            daily,
            [
                Trigger(
                    "JET2.L",
                    "stop_touched",
                    pid,
                    None,
                    {"low": 1395.0, "stop": 1420.0, "close": 1425.0},
                    "2026-08-20",
                )
            ],
            conn=conn,
        )
        xid = book.exit_position(conn, pid, "stop", "limit", 1390.0, at="2026-08-21T08:00:00+00:00")
        book.fill(conn, xid, 100, 1390.0, at="2026-08-21T15:00:00+00:00", commission=5.0)
        # a later daily run with the full price history (the hindsight bars)
        later = store.start_run("daily", {}, conn=conn)
        _set_started(conn, later, "2026-10-09T17:30:00+00:00")
        store.save_ticker(later, "JET2.L", daily=_daily("2026-10-09"), conn=conn)
    conn.close()
    return db_backend, pid, scan, later


def test_pack_from_a_fixture_closed_position(
    world: tuple[Path | None, int, int, int], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, pid, scan, _ = world
    conn = book.connect(db)
    pack = postmortem.build_pack(conn, pid)
    conn.close()
    assert pack["ticker"] == "JET2.L" and pack["opened_from_run_id"] == scan
    assert pack["opened_at"] == "2026-07-13T09:00:00+00:00"
    assert pack["closed_at"] == "2026-08-21T15:00:00+00:00"
    s = pack["close_summary"]
    assert s["exit_reason"] == "stop" and s["net_pnl"] == pytest.approx(-120.0)
    assert s["r_multiple"] == pytest.approx(-1.2)
    assert pack["setup"] == {
        "type": "pullback",
        "entry_type": "limit",
        "coil_pending": False,
        "blue_sky": None,
        "wait_for_break": None,
    }
    assert pack["entry_conditions"]["technical"]["trend"] == 0.9
    assert pack["thesis_conditions"][0]["condition"] == "trend < 0"
    assert len(pack["reviews"]) == 1
    rv = pack["reviews"][0]
    assert rv["position_id"] == pid and rv["action"] == "hold" and rv["new_stop"] == 1420.0
    assert len(pack["revisions"]) == 1 and pack["revisions"][0]["action"] == "tighten_stop"
    assert pack["revisions"][0]["status"] == "accepted"
    assert [t["kind"] for t in pack["triggers"]] == ["stop_touched"]
    assert pack["triggers"][0]["detail"]["stop"] == 1420.0
    assert len(pack["research"]) == 1 and pack["research"][0]["label"] == "ACT"
    assert pack["research"][0]["run_id"] == scan
    assert len(pack["evidence"]) == 2 and pack["evidence"][0]["stage"] == "research"
    assert pack["monitor"] == []
    assert [e["kind"] for e in pack["events"]][:3] == ["drafted", "approved", "opened"]
    assert len(pack["fills"]) == 2 and len(pack["orders"]) == 4  # entry, stop, stop (raised), exit
    # bars: entry week (fill 13 Jul) 17 Jul - 8w = 22 May ... exit week 21 Aug + 4w = 18 Sep
    bars = pack["bars"]
    assert bars[0]["date"] == "2026-05-22" and bars[-1]["date"] == "2026-09-18"
    assert len(bars) == 18
    by = {b["date"]: b for b in bars}
    assert by["2026-07-10"]["as_seen"] is True and by["2026-07-10"]["hindsight"] is False
    assert by["2026-07-17"]["as_seen"] is False and by["2026-07-17"]["hindsight"] is False
    assert by["2026-08-14"]["hindsight"] is False
    assert by["2026-08-21"]["hindsight"] is True  # the exit week completed after the exit
    assert sum(b["hindsight"] for b in bars) == 5
    assert all(b["as_seen"] for b in bars if b["date"] <= "2026-07-10")
    assert bars[0]["close"] is not None and bars[0]["volume"] == pytest.approx(5e5)
    json.dumps(pack)  # serialisable as it stands

    out = postmortem_pack.main(
        [str(pid), "--out", str(tmp_path / "pm"), *(["--db", str(db)] if db else [])]
    )
    text = capsys.readouterr().out
    assert (
        f"position {pid} JET2.L: stop -1.20R, set-up pullback; 1 review(s), 1 revision(s), 1 trigger(s), 18 weekly bars (5 hindsight)"
        in text
    )
    assert out is None and (tmp_path / "pm" / "pack.json").exists()
    assert json.loads((tmp_path / "pm" / "pack.json").read_text())["position_id"] == pid

    conn = book.connect(db)
    with conn:
        other, _ = book.draft_position(conn, "OPEN.L", 100.0, 90.0, 10)
    with pytest.raises(ValueError, match="a post-mortem needs a closed one"):
        postmortem.build_pack(conn, other)
    assert postmortem.closed_since(conn) == [pid]
    assert postmortem.closed_since(conn, "2026-08-21T15:00:00+00:00") == []
    assert postmortem.closed_since(conn, "2026-08-01") == [pid]
    conn.close()
    with pytest.raises(SystemExit):
        postmortem_pack.main([str(other), *(["--db", str(db)] if db else [])])
    assert "a post-mortem needs a closed one" in capsys.readouterr().out
    postmortem_pack.main(["--since", "2030-01-01", *(["--db", str(db)] if db else [])])
    assert "no position closed since 2030-01-01" in capsys.readouterr().out


def test_fixture_verdict_records_queue_and_outcome(
    world: tuple[Path | None, int, int, int], capsys: pytest.CaptureFixture[str]
) -> None:
    db, pid, scan, later = world
    v = agents.load_verdict((FIXTURES / "verdict_postmortem.json").read_text())
    assert v.stage == "postmortem" and v.scores == {"setup_quality": 0.5, "execution_quality": -0.2}
    dbargs = ["--db", str(db)] if db else []
    conn = store.connect(db)
    # the runner: closed since the previous daily run, not yet recorded
    assert agents.queue(conn, later, "postmortem", 10) == [str(pid)]
    assert agents.queue(conn, later, "postmortem", 0) == []
    first_daily = int(
        store.query("SELECT MIN(run_id) AS r FROM runs WHERE script = 'daily'", conn)["r"].iloc[0]
    )
    assert agents.queue(conn, first_daily, "postmortem", 10) == [
        str(pid)
    ]  # no earlier daily run: ever
    conn.close()

    record.main(
        [
            "verdict",
            str(FIXTURES / "verdict_postmortem.json"),
            "--run",
            str(scan),
            "--position",
            str(pid),
            *dbargs,
        ]
    )
    out = capsys.readouterr().out
    assert f"recorded postmortem WATCH (0.60) for JET2.L in run {scan}, dated 2026-07-10" in out
    assert "labels row written" not in out  # not a pipeline stage
    cards = store.load_cards(scan, "JET2.L", path=db)
    pm = cards["postmortem"]
    assert pm["position_id"] == pid and pm["setup_quality"] == 0.5 and pm["label"] == "WATCH"
    assert json.loads(pm["checks"])["setup"] == "pullback"
    conn = store.connect(db)
    assert agents.queue(conn, later, "postmortem", 10) == []  # done
    assert agents.outcome(conn, scan, "postmortem") == {"total": 1, "errors": 0}
    conn.close()
    with pytest.raises(SystemExit) as e:
        record.main(
            ["verdict", str(FIXTURES / "verdict_postmortem.json"), "--run", str(scan), *dbargs]
        )
    assert e.value.code == 2  # append-only: the card exists
    assert "postmortem already recorded for JET2.L" in capsys.readouterr().out

    # the runner's error path and a hand-booked position (--new-run)
    record.main(
        [
            "error",
            "JET2.L",
            "--run",
            str(later),
            "--stage",
            "postmortem",
            "--position",
            str(pid),
            "--reason",
            "turn limit",
            *dbargs,
        ]
    )
    conn = store.connect(db)
    assert agents.outcome(conn, later, "postmortem") == {"total": 1, "errors": 1}
    conn.close()
    record.main(
        [
            "verdict",
            str(FIXTURES / "verdict_postmortem.json"),
            "--new-run",
            "--position",
            str(pid),
            *dbargs,
        ]
    )
    assert "recorded postmortem WATCH" in capsys.readouterr().out
