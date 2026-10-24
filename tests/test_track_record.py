from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha import agents, desk_chat, store, track_record
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


def _run(conn: store.Conn, day: int) -> int:
    at = (pd.Timestamp("2026-11-01", tz="UTC") + pd.Timedelta(days=day)).isoformat()
    with conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at, script, args, git_sha) VALUES (?, 'scan', '{}', 'x')",
            (at,),
        )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _proposal(**kw: object) -> agents.Proposal:
    base: dict[str, object] = {
        "title": "Widen breakout stops to 2.5 ATR",
        "problem": "Breakout stops are hit and the stock then runs.",
        "evidence": "Hunch: 3 of 4 breakout stops reversed within a week.",
        "n": 4,
        "change": "stop = entry - 2.5 ATR on breakouts",
        "where": "/research skill (fundamental-strategist)",
        "risk": "larger loss per stop; smaller size at the same risk",
        "check": "share of breakout stops that reverse within a week",
        "check_on": "2027-01-31",
    }
    return agents.Proposal.model_validate(base | kw)


def _retro(conn: store.Conn, run: int, area: str, proposals: int = 0) -> None:
    v = agents.Verdict(
        stage="retro",
        ticker=f"{agents.DESK_PREFIX}{area}",
        run_id=run,
        label="ACT" if proposals else "PASS",
        confidence=0.6,
        reasons=["went well: x", "went wrong: y"],
        proposals=[_proposal() for _ in range(proposals)],
    )
    agents.record_verdict(conn, v, "agent:retro")


def test_retro_cadence_tapers(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    desk = [f"_DESK:{a}" for a in track_record.AREAS]
    assert [track_record.interval_days(d) for d in (0, 27, 28, 90, 91, 400)] == [
        7,
        7,
        28,
        28,
        91,
        91,
    ]
    r0 = _run(conn, 0)
    assert agents.queue(conn, r0, "retro", 1) == desk  # uncapped: every area, first time
    _retro(conn, r0, "senior-quant")
    assert "_DESK:senior-quant" not in track_record.retro_due(conn, r0)  # done in this run
    assert track_record.retro_due(conn, _run(conn, 3))[0] == "_DESK:fundamental-strategist"
    r7 = _run(conn, 7)
    assert "_DESK:senior-quant" in track_record.retro_due(conn, r7)  # weekly in month one
    _retro(conn, r7, "senior-quant")
    assert "_DESK:senior-quant" not in track_record.retro_due(conn, _run(conn, 30))  # monthly now
    r35 = _run(conn, 35)
    assert "_DESK:senior-quant" in track_record.retro_due(conn, r35)  # 28 days on
    _retro(conn, r35, "senior-quant")
    assert "_DESK:senior-quant" not in track_record.retro_due(conn, _run(conn, 55))
    assert "_DESK:senior-quant" in track_record.retro_due(conn, _run(conn, 62))
    _retro(conn, _run(conn, 100), "senior-quant")  # quarterly after 13 weeks
    assert "_DESK:senior-quant" not in track_record.retro_due(conn, _run(conn, 170))
    assert "_DESK:senior-quant" in track_record.retro_due(conn, _run(conn, 190))
    conn.close()


def test_retro_verdict_rules() -> None:
    with pytest.raises(ValueError, match="only the retro stage"):
        agents.Verdict(
            stage="pm",
            ticker="_BOOK",
            run_id=1,
            label="ACT",
            confidence=0.5,
            reasons=["x"],
            allocation=[],
            proposals=[_proposal()],
        )
    with pytest.raises(ValueError, match="ACT with proposals"):
        agents.Verdict(
            stage="retro", ticker="_DESK:pm", run_id=1, label="ACT", confidence=0.5, reasons=["x"]
        )
    with pytest.raises(ValueError, match="_DESK:"):
        agents.Verdict(
            stage="retro", ticker="JET2.L", run_id=1, label="PASS", confidence=0.5, reasons=["x"]
        )
    with pytest.raises(ValueError):
        _proposal(check_on="next month")


def test_retro_pack_has_observations_and_earlier_proposals(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    conn.executescript(desk_chat.SCHEMA)
    r0 = _run(conn, 0)
    with conn:
        conn.execute(
            "INSERT INTO chat (at, author, text) VALUES (?, 'agent:research', ?)",
            ("2026-10-30T09:00:00+00:00", "observation: shortlist had a stock T212 doesn't offer"),
        )
        conn.execute(
            "INSERT INTO chat (at, author, text) VALUES (?, 'agent:pm', 'funded A.L')",
            ("2026-10-30T10:00:00+00:00",),
        )
    empty = track_record.Inputs(pd.DataFrame(), pd.DataFrame())
    pack = track_record.retro_pack(conn, "execution-trader", empty)
    assert "# Track record: execution-trader" in pack
    assert "observation: shortlist had a stock T212 doesn't offer" in pack
    assert "funded A.L" not in pack
    assert "- None yet." in pack
    _retro(conn, r0, "execution-trader", proposals=1)
    pack = track_record.retro_pack(conn, "execution-trader", empty)
    assert "- 2026-11-01: Widen breakout stops to 2.5 ATR (n=4; check 2027-01-31" in pack
    assert "since your last retro, 2026-11-01" in pack
    assert "doesn't offer" not in pack  # posted before that retro
    with pytest.raises(ValueError, match="per desk area"):
        track_record.retro_pack(conn, "monitor", empty)
    conn.close()
