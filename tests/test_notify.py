from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scanfix import build_scan_db
from solaraalpha import notify, store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.triggers import Trigger

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import notify as notify_cli


def _args(db: Path | None, *rest: str) -> list[str]:
    return [*rest, *(["--db", str(db)] if db else [])]


def test_daily_nothing_to_send_exits_3(
    db_backend: Path | None, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = book.connect(db_backend)
    with conn:
        run = store.start_run("daily", {}, conn=conn)
        store.save_triggers(
            run,
            [
                Trigger(
                    "W.L",
                    "watch_break",
                    None,
                    None,
                    {"close": 805.0, "trigger": 800.0},
                    "2026-10-20",
                )
            ],
            conn=conn,
        )  # a report trigger, not an owner action
    assert notify.daily_message(conn, run) is None
    conn.close()
    with pytest.raises(SystemExit) as e:
        notify_cli.main(_args(db_backend, "--mode", "daily", "--run", str(run)))
    assert e.value.code == 3
    assert "nothing to send for daily run" in capsys.readouterr().err


def test_daily_renders_each_item_type(
    db_backend: Path | None, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = book.connect(db_backend)
    with conn:
        pid, oid = book.draft_position(
            conn, "JET2.L", 1500.0, 1400.0, 100, at="2026-10-11T18:30:00+00:00"
        )
        book.approve_position(conn, pid)
        book.fill(conn, oid, 100, 1500.0, at="2026-10-12T09:00:00+00:00")
        p2, o2 = book.draft_position(conn, "E.L", 300.0, 270.0, 50, at="2026-10-11T18:30:00+00:00")
        book.approve_position(conn, p2)
        run = store.start_run("daily", {}, conn=conn)
        conn.execute(
            "UPDATE runs SET started_at = ? WHERE run_id = ?", ("2026-10-20T17:47:00+00:00", run)
        )
        store.save_triggers(
            run,
            [
                Trigger(
                    "JET2.L",
                    "stop_close",
                    pid,
                    None,
                    {"close": 1390.0, "stop": 1400.0},
                    "2026-10-20",
                ),
                Trigger(
                    "JET2.L",
                    "stop_touched",
                    pid,
                    None,
                    {"close": 1402.0, "stop": 1400.0, "low": 1395.0},
                    "2026-10-20",
                ),
                Trigger(
                    "JET2.L",
                    "target_hit",
                    pid,
                    None,
                    {"target": 1800.0, "high": 1810.0},
                    "2026-10-20",
                ),
                Trigger(
                    "E.L", "entry_break", p2, o2, {"close": 301.0, "trigger": 300.0}, "2026-10-20"
                ),
                Trigger(
                    "E.L",
                    "order_stale",
                    p2,
                    o2,
                    {"valid_until": "2026-10-18", "status": "approved"},
                    "2026-10-20",
                ),
                Trigger(
                    "JET2.L",
                    "event_due",
                    pid,
                    None,
                    {"event": "2026-10-22", "sessions": 2, "policy": "flag"},
                    "2026-10-20",
                ),
                Trigger(
                    "JET2.L",
                    "dividend",
                    pid,
                    None,
                    {"amount_gbp": 10.7, "per_share": 10.7, "shares": 100, "currency": "GBp"},
                    "2026-10-20",
                ),
                Trigger(
                    "JET2.L", "split", pid, None, {"ratio": "2:1", "factor": 2.0}, "2026-10-21"
                ),
                Trigger(
                    "JET2.L", "gap", pid, None, {"gap_atr": 2.3}, "2026-10-20"
                ),  # agent trigger: not for the owner
            ],
            conn=conn,
        )
        rid = book.propose_revision(
            conn,
            pid,
            "tighten_stop",
            {"stop": 1480.0},
            "guidance cut",
            "agent:monitor",
            at="2026-10-20T19:00:00+00:00",
        )
        book.propose_revision(
            conn, pid, "hold", {}, "old", "agent:monitor", at="2026-10-19T19:00:00+00:00"
        )  # before the run
        store.save_label(
            run, "X.L", "validate", "ERROR", "pack failed", 0.0, "agent:validate", conn=conn
        )
        store._save_card(
            conn,
            run,
            "JET2.L",
            "monitor",
            {
                "date": "2026-10-20",
                "label": "ERROR",
                "reasons": "claude-code-action failed (job 9)",
            },
        )
    text = notify.daily_message(conn, run)
    conn.close()
    assert text is not None
    assert text.startswith(f"## Daily check — run {run} (2026-10-20T17:47 UTC)")
    for needle in (
        "### Owner actions",
        "closed 1,390.0 below the stop 1,400.0 (position 1): `book.py exit 1 --reason stop`",
        "traded through the stop 1,400.0, closed 1,402.0",
        "reached the target 1,800.0 (position 1): `book.py exit 1 --reason target`",
        f"place entry order {o2} — `book.py placed {o2} --ref ...`",
        f"order {o2} was valid until 2026-10-18 with no fill: `book.py fill {o2}",
        "scheduled event 2026-10-22 in 2 session(s) (position 1): policy **flag**",
        "ex-dividend 2026-10-20: £10.70 (position 1): `book.py cash dividend JET2.L 10.70 --at 2026-10-20`",
        "split 2:1 on 2026-10-21 (position 1): `book.py split 1 --ratio 2:1 --at 2026-10-21`",
        "### Revision proposals",
        f'agent:monitor proposes **tighten_stop** {{"stop": 1480.0}} — guidance cut: `book.py accept {rid}`',
        "### Errors",
        "- X.L `validate`: pack failed",
        "- JET2.L `monitor`: claude-code-action failed (job 9)",
    ):
        assert needle in text, needle
    assert (
        "gap" not in text.split("### Revision")[0] and "**hold**" not in text
    )  # the older proposal
    # --only narrows the message; the script prints to stdout
    notify_cli.main(
        _args(db_backend, "--mode", "daily", "--run", str(run), "--only", "revisions,errors")
    )
    out = capsys.readouterr().out
    assert (
        "### Owner actions" not in out and "### Revision proposals" in out and "### Errors" in out
    )
    with pytest.raises(ValueError, match="unknown channel"):
        notify.send("x", "pigeon")


def test_weekly_message_keeps_the_headline_sections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = build_scan_db(tmp_path)
    conn = book.connect(db)
    text = notify.weekly_message(conn, 1, "https://github.com/o/r/actions/runs/5")
    conn.close()
    assert text is not None and text.startswith("# Weekly pack — run 1")
    for section in (
        "## New plans",
        "## Open positions",
        "## Portfolio",
        "## Errors",
        "## Stale orders",
    ):
        assert section in text, section
    for dropped in ("## Rejected by fit", "## Watch list", "## ACT by rank"):
        assert dropped not in text, dropped
    assert text.rstrip().endswith(
        "Full pack and pipeline report: https://github.com/o/r/actions/runs/5"
    )
    notify_cli.main(["--mode", "weekly", "--run", "1", "--db", str(db)])
    assert "## New plans" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        notify_cli.main(["--mode", "weekly", "--run", "99", "--db", str(db)])
    assert e.value.code == 3
