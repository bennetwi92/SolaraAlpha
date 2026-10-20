from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scanfix import build_scan_db
from solaraalpha import notify, store
from solaraalpha.portfolio import book, broker
from solaraalpha.portfolio.triggers import Trigger

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import notify as notify_cli
import plan as plan_cli


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
            conn, "JET2.L", 1500.0, 1400.0, 100, target=1800.0, at="2026-10-11T18:30:00+00:00"
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
        low = book.propose_revision(
            conn,
            pid,
            "tighten_stop",
            {"stop": 1450.0},
            "noise",
            "agent:monitor",
            at="2026-10-20T19:01:00+00:00",
        )  # refused: never lower a stop
        tid = book.propose_revision(
            conn,
            pid,
            "revise_target",
            {"target": 1900.0},
            "new contract",
            "agent:monitor",
            at="2026-10-20T19:02:00+00:00",
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
    od = book.orders(conn, pid)
    stop = int(od.loc[od["status"] == "approved", "order_id"].iloc[-1])
    text = notify.daily_message(conn, run)
    conn.close()
    assert text is not None
    assert text.startswith("## Solara — Tue 20 Oct 2026\n")
    for needle in (
        "**2 stocks need you in Trading212.** Do each stock's steps in order",
        # the protective stop was never placed, so its replacement is simply put on
        "### 1. JET2.L — Put the stop on",
        f"1. Place **Sell 100 · Stop order · stop price 1,480p** (#{stop}), good till cancelled",
        "Why: guidance cut (the monitor agent)",
        f"Then tell me: `placed {stop}`",
        "### 2. E.L — New trade",
        f"1. Place **Buy 50 · Limit order · limit price 300p** (#{o2}), good till cancelled",
        "2. When it fills, place the stop for the shares you got:"
        " **Sell 50 · Stop order · stop price 270p**",
        "### Check and tell me",
        "**JET2.L** closed at 1,390p, below your stop 1,400p. The stop order should have sold it"
        f" in Trading212: tell me the fill, `fill {stop} --qty N --price P`",
        "traded through your stop 1,400p today (closed 1,402p)",
        f"place your buy order #{o2} now, then tell me `placed {o2}`",
        f"**E.L** order #{o2} (**Buy 50 · Limit order · limit price 300p**) passed its end date"
        f" 2026-10-18 with no fill from you. If it filled, tell me `fill {o2} --qty N --price P`;"
        f" if not, cancel it in Trading212 and tell me `unfilled {o2}`.",
        "`cash dividend JET2.L 10.70 --at 2026-10-20`",
        "`split 1 --ratio 2:1 --at 2026-10-21`",
        "### Price alerts",
        "**JET2.L**: move the target alert 1,800p → 1,900p (the monitor agent: new contract)",
        "### For information (nothing to do)",
        "**JET2.L** reached its target 1,800p. Nothing to do",
        "policy **flag**, nothing happens automatically",
        "the monitor agent asked for **tighten stop** but the book refused it",
        "Tell Claude what you did in plain words",
        "<details><summary>Undo an automatic change</summary>",
        f"- JET2.L tighten stop (the monitor agent): `book.py undo {rid} --reason ...`",
        f"`book.py undo {tid} --reason ...`",
        "### Errors",
        "- X.L `validate`: pack failed",
        "- JET2.L `monitor`: claude-code-action failed (job 9)",
    ):
        assert needle in text, needle
    assert f"undo {low}" not in text and "gap" not in text and "**hold**" not in text
    # --only revisions: just the agents' changes since the run started, no owner triggers
    notify_cli.main(
        _args(db_backend, "--mode", "daily", "--run", str(run), "--only", "revisions,errors")
    )
    out = capsys.readouterr().out
    assert "### Check and tell me" not in out and "E.L — New trade" not in out
    assert "**1 stock needs you in Trading212.**" in out and "JET2.L — Put the stop on" in out
    assert "### Price alerts" in out and "### Errors" in out
    with pytest.raises(ValueError, match="unknown channel"):
        notify.send("x", "pigeon")


def test_one_order_per_stock_steps(db_backend: Path | None) -> None:
    """A placed stop is cancelled before an exit, an add, or its replacement (ADR 0026)."""
    conn = book.connect(db_backend)
    with conn:
        pid, oid = book.draft_position(
            conn, "JET2.L", 1500.0, 1400.0, 100, at="2026-10-11T18:30:00+00:00"
        )
        book.approve_position(conn, pid)
        book.place_order(conn, oid)
        sid = book.fill(conn, oid, 100, 1500.0, at="2026-10-12T09:00:00+00:00")["stop_order_id"]
        book.place_order(conn, sid)
        assert broker.tasks(conn) == []
        rid = book.propose_revision(
            conn, pid, "tighten_stop", {"stop": 1450.0}, "breakout held", "agent:monitor"
        )
    (t,) = broker.tasks(conn)
    assert t.title == "Raise the stop (1,400p → 1,450p)"
    assert (
        t.steps[0]
        == f"Cancel your stop order #{sid} (**Sell 100 · Stop order · stop price 1,400p**)"
    )
    assert t.undo == f"book.py undo {rid} --reason ..."
    new_stop = int(book.orders(conn, pid, status="approved")["order_id"].iloc[0])
    with conn:
        book.place_order(conn, new_stop)
        book.propose_revision(
            conn, pid, "take_partial", {"qty": 40, "type": "market"}, "trim", "pipeline"
        )
    (t,) = broker.tasks(conn)
    assert t.title == "Sell 40 of your 100 shares"
    assert t.steps[0].startswith(f"Cancel your stop order #{new_stop}")
    assert t.steps[1].startswith("Place **Sell 40 · Market order**")
    assert t.steps[2] == (
        "When it has filled, put the stop back on the rest:"
        " **Sell 60 · Stop order · stop price 1,450p**"
    )
    assert t.why == "trim (the weekly review)"
    # the stop is off at the broker but the book keeps its level for the heat and the triggers
    assert book.position_state(conn, pid).stop == 1450.0
    conn.close()


def test_weekly_message_lists_the_trades_as_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = build_scan_db(tmp_path)
    conn = book.connect(db)
    text = notify.weekly_message(conn, 1, "https://github.com/o/r/actions/runs/5")
    conn.close()
    assert text is not None and text.startswith("# Solara — week of Sun 11 Oct 2026")
    assert "**No new trades this week.**" in text and "Nothing to place in Trading212." in text
    plan_cli.main(["--run", "1", "--db", str(db)])
    capsys.readouterr()
    conn = book.connect(db)
    text = notify.weekly_message(conn, 1, "https://github.com/o/r/actions/runs/5")
    conn.close()
    assert text is not None
    for needle in (
        "**1 new trade this week.**",
        "### 1. A.L — New trade",
        "1. Place **Buy 111 · Stop order · stop price 1,560p** (#1), good till cancelled",
        "**Sell 111 · Stop order · stop price 1,380p**",
        "3. Set price alerts at 1,794p (target +15%) and 2,028p (second target +30%)",
        "Why: Strong winter bookings; fuel hedged. You lose about £200 if the stop is hit.",
        "If it hasn't filled by Fri 16 Oct, I'll tell you to cancel it.",
        "Then tell me: `placed 1`; when it fills: `fill 1 --qty N --price P`",
        "### Your positions",
        "### Money",
        "- Equity £10,000 (cash £10,000) · 0 of 5 positions",
    ):
        assert needle in text, needle
    assert text.rstrip().endswith(
        "Full pack and pipeline report: https://github.com/o/r/actions/runs/5"
    )
    notify_cli.main(["--mode", "weekly", "--run", "1", "--db", str(db)])
    assert "A.L — New trade" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        notify_cli.main(["--mode", "weekly", "--run", "99", "--db", str(db)])
    assert e.value.code == 3
