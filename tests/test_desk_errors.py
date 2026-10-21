from __future__ import annotations

import sys
from pathlib import Path

import pytest

from solaraalpha import notify, store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.triggers import Trigger

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import notify as notify_cli


def _args(db: Path | None, *rest: str) -> list[str]:
    return [*rest, *(["--db", str(db)] if db else [])]


def _review(ticker: str, pid: int) -> Trigger:
    return Trigger(ticker, "daily_review", pid, None, {"status": "open"}, "2026-10-20")


def test_scan_errors_split_pipeline_and_agents(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    with conn:
        prev = store.start_run("scan", {}, conn=conn)
        store.save_label(
            prev, "A.L", "research", "ERROR", "turn limit", 0.0, "agent:research", conn=conn
        )
        run = store.start_run("scan", {}, conn=conn)
        store.save_label(run, "B.L", "screen", "ERROR", "technical raised: no bars", conn=conn)
        store.save_label(
            run, "C.L", "research", "ERROR", "no verdict", 0.0, "agent:research", conn=conn
        )
        store._save_card(
            conn, run, "_BOOK", "pm", {"date": "2026-10-18", "label": "ERROR", "reasons": "crashed"}
        )
    pipe = notify.desk_errors(conn, run, "pipeline")
    agents = notify.desk_errors(conn, run, "agents")
    head = notify.serious(conn, run)
    conn.close()
    assert pipe is not None and agents is not None
    assert "**B.L** `screen` failed: technical raised: no bars" in pipe
    assert "`uv run python scripts/scan.py B.L`" in pipe
    assert "C.L" not in pipe and "B.L" not in agents
    assert "**C.L** `research` failed: no verdict. Means: the stock has no research note" in agents
    assert f"`/research C.L --run {run}` in Claude Code" in agents
    assert f"**Repeat:** research also failed in run {prev}." in agents
    assert f"`/pm --run {run}`" in agents and "the PM did not decide" in agents
    assert head == ["the PM did not decide"]


def test_unreviewed_position_once_monitors_ran(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    with conn:
        p1, o1 = book.draft_position(conn, "JET2.L", 1500.0, 1400.0, 100)
        p2, o2 = book.draft_position(conn, "E.L", 300.0, 270.0, 50)
        for pid, oid, px, n in ((p1, o1, 1500.0, 100), (p2, o2, 300.0, 50)):
            book.approve_position(conn, pid)
            book.fill(conn, oid, n, px)
        run = store.start_run("daily", {}, conn=conn)
        store.save_triggers(run, [_review("JET2.L", p1), _review("E.L", p2)], conn=conn)
    assert notify.unreviewed(conn, run) == []  # agents off: nothing to report
    assert notify.desk_errors(conn, run, "agents") is None
    with conn:
        store.save_agent_run(run, f"position-{p1}", "monitor", {}, conn=conn)
        store._save_card(
            conn,
            run,
            "JET2.L",
            "monitor",
            {"date": "2026-10-20", "label": "WATCH", "reasons": "ok"},
        )
    assert notify.unreviewed(conn, run) == [(p2, "E.L")]
    text = notify.desk_errors(conn, run, "agents")
    daily = notify.daily_message(conn, run, ("revisions", "errors"))
    conn.close()
    assert text is not None and f"**E.L** (position {p2}) not reviewed today" in text
    assert f"`/monitor --position {p2}` in Claude Code" in text
    assert daily is not None and "> **Desk error:** E.L not reviewed." in daily


def test_cli_errors_exit_3_and_job_failed(
    db_backend: Path | None, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = book.connect(db_backend)
    with conn:
        run = store.start_run("scan", {}, conn=conn)
    conn.close()
    with pytest.raises(SystemExit) as e:
        notify_cli.main(_args(db_backend, "--mode", "errors", "--run", str(run)))
    assert e.value.code == 3
    notify_cli.main(
        ["--mode", "job-failed", "--workflow", "Weekly pipeline", "--url", "https://x/1"]
    )
    out = capsys.readouterr().out
    assert "the **Weekly pipeline** workflow ended `failure`" in out
    assert "no plan this week" in out and "https://x/1" in out
