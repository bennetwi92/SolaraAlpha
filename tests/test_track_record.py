from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha import store, track_record
from solaraalpha.portfolio import book, execution

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import track_record as track_record_cli


def _book(db: Path | None) -> tuple[store.Conn, int]:
    """One scan run with labels, PM and post-mortem cards, two closed trades (one stopped out,
    one exited on target, its entry filled 10p above the limit), a monitor stop the owner
    undid, an execution refusal and an agent failure."""
    conn = book.connect(db)
    run = store.start_run("scan.py", {}, conn=conn)
    with conn:
        for t, screen, research in (
            ("A.L", "ACT", "ACT"),
            ("B.L", "ACT", "ACT"),
            ("C.L", "ACT", "PASS"),
        ):
            conn.execute(
                "INSERT INTO labels (run_id, ticker, stage, label, confidence, reason, actor)"
                " VALUES (?, ?, 'screen', ?, NULL, 'rank', 'pipeline')",
                (run, t, screen),
            )
            conn.execute(
                "INSERT INTO labels (run_id, ticker, stage, label, confidence, reason, actor)"
                " VALUES (?, ?, 'research', ?, 0.7, 'note', 'agent:research')",
                (run, t, research),
            )
            store._save_card(conn, run, t, "technical", {"date": "2026-09-04", "trend": 0.5})
        store._save_card(
            conn,
            run,
            "_BOOK",
            "pm",
            {
                "date": "2026-09-04",
                "label": "ACT",
                "allocation": json.dumps([{"ticker": "A.L"}]),
                "rotations": "[]",
                "renewals": "[]",
            },
        )
        store._save_card(
            conn,
            run,
            "A.L",
            "postmortem",
            {
                "date": "2026-09-04",
                "label": "PASS",
                "setup_quality": -0.5,
                "execution_quality": 0.5,
                "reasons": "bought a fading breakout",
                "position_id": 1,
            },
        )
    pids = []
    for t, fill in (("A.L", 1500.0), ("B.L", 1510.0)):
        with conn:
            pid, oid = book.draft_position(
                conn, t, 1500.0, 1400.0, 100, opened_from_run_id=run, at="2026-09-05T18:00:00+00:00"
            )
            book.approve_position(conn, pid)
            book.place_order(conn, oid)
            book.fill(conn, oid, 100, fill, at="2026-09-07T09:00:00+00:00")
        pids.append(pid)
    with conn:
        rid = book.propose_revision(
            conn, pids[1], "tighten_stop", {"stop": 1480.0}, "coil broke", "agent:monitor"
        )
        book.undo_revision(conn, rid, "too tight for this stock's range")
        for pid, (exit_px, why) in zip(pids, ((1400.0, "stop"), (1700.0, "target")), strict=True):
            xid = book.exit_position(conn, pid, why, "limit", exit_px)
            book.fill(conn, xid, 100, exit_px, at="2026-10-05T15:00:00+00:00")
    execution.record(conn, None, "refused", "outside the 10% price band")
    store.save_agent_run(run, "B.L", "research", {"seconds": 120.0}, status="failure", conn=conn)
    return conn, run


def _inputs(run: int) -> track_record.Inputs:
    labels = pd.DataFrame(
        [
            {
                "run_id": run,
                "ticker": t,
                "stage": s,
                "label": lab,
                "confidence": c,
                "date": "2026-09-04",
            }
            for t, lab in (("A.L", "ACT"), ("B.L", "ACT"), ("C.L", "ACT"))
            for s, c in (("screen", None),)
        ]
        + [
            {
                "run_id": run,
                "ticker": t,
                "stage": "research",
                "label": lab,
                "confidence": 0.7,
                "date": "2026-09-04",
            }
            for t, lab in (("A.L", "ACT"), ("B.L", "ACT"), ("C.L", "PASS"))
        ]
    )
    ret = {"A.L": -0.08, "B.L": 0.2, "C.L": 0.01}
    fwd = labels[["run_id", "ticker", "stage", "label"]].assign(
        date=pd.Timestamp("2026-09-04", tz="UTC"),
        ret_4w=labels["ticker"].map(ret) / 2,
        ret_8w=labels["ticker"].map(ret),
    )
    for n in (2, 16):
        fwd[f"ret_{n}w"] = float("nan")
    for n in (2, 4, 8, 16):
        fwd[f"exc_{n}w"] = fwd[f"ret_{n}w"]
    return track_record.Inputs(labels, fwd)


def test_report_covers_every_area(db_backend: Path | None) -> None:
    conn, run = _book(db_backend)
    text = track_record.report(conn, "all", _inputs(run))
    conn.close()
    # screen: three ACTs scored, flagged as too few
    assert "- ACT, 8 weeks on: n=3, average +4.3%, up in 67%, ended 15%+ up in 33% (too few" in text
    # pm: funded A.L lost, unfunded B.L burst
    assert "- funded, 8 weeks on: n=1, average -8.0%" in text
    assert "- not funded, 8 weeks on: n=1, average +20.0%" in text
    # research trades: one stop, one target, both from the scan run
    assert "- research trades: 2 closed, won 50%" in text
    assert "exit by stop: 1, average -1.00R" in text
    # monitor: the undone stop with the owner's reason
    assert "- tighten_stop: 1 undone" in text
    assert "too tight for this stock's range" in text
    # execute: the refusal and B.L's entry 10p over its limit (+67 bp)
    assert "- refused x1: outside the 10% price band" in text
    assert "- entry limit: n=2, average +33 bp, worst +67 bp" in text
    # postmortem lesson and reliability
    assert "- A.L (avoid): bought a fading breakout" in text
    assert "- 1 invocations, 1 failed" in text


def test_areas_and_stages() -> None:
    assert track_record.stages_for("execution-trader") == ("execute",)
    assert track_record.stages_for("monitor") == ("monitor",)
    assert set(track_record.stages_for("all")) == set(track_record.SECTIONS)
    with pytest.raises(ValueError, match="unknown agent"):
        track_record.stages_for("intern")


def test_empty_db_says_nothing_yet(
    db_backend: Path | None, capsys: pytest.CaptureFixture[str]
) -> None:
    track_record_cli.main(
        ["--agent", "all", "--db", str(db_backend)] if db_backend else ["--agent", "all"]
    )
    out = capsys.readouterr().out
    assert "# Track record: all" in out
    assert out.count("- Nothing recorded yet.") >= 5
