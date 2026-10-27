from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import monitoring, store, toolkits
from solaraalpha.analysis.relative_strength import Comparisons
from solaraalpha.portfolio import book, broker
from solaraalpha.portfolio.triggers import Trigger

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import book as book_cli
import monitor_pack
import record

DATE = pd.Timestamp(
    "2026-09-11", tz="UTC"
)  # the world sits a month back so weekly bars exist after entry


def _daily(n: int = 800, end: str = "2026-10-09") -> pd.DataFrame:
    rng = np.random.default_rng(3)
    close = 1000 * np.exp(np.cumsum(0.001 + 0.012 * rng.standard_normal(n)))
    idx = pd.bdate_range(end=end, periods=n, tz="UTC", name="date")
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "adj_close": close,
            "volume": 1e5,
        },
        index=idx,
    )
    df.attrs = {"ticker": "JET2.L", "currency": "GBp"}
    return df


@dataclass(frozen=True)
class Tech:
    date: pd.Timestamp
    trend: float
    momentum: float
    entry: float
    close: float


@dataclass(frozen=True)
class Research:
    date: pd.Timestamp
    label: str
    reasons: str
    kill_criteria: str
    evidence_ids: str


@pytest.fixture
def world(
    db_backend: Path | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path | None, int, int]:
    from solaraalpha.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")  # packs never land in data/
    """An open JET2.L position drafted from a scan run with entry conditions and thesis
    conditions, and a daily run with a filing trigger on it."""
    monkeypatch.setattr(toolkits, "fetch_daily", lambda ticker: _daily())
    monkeypatch.setattr(toolkits, "comparisons", lambda t: Comparisons(None))
    monkeypatch.setattr(toolkits, "_OTHERS", {})
    monkeypatch.setattr(
        monitoring, "fetch_text", lambda url: f"FULL TEXT OF {url}: FY27 guidance cut to GBP 400m"
    )
    scan = store.start_run("scan", {}, path=db_backend)
    tech = Tech(DATE, 0.9, 0.5, 0.2, 1500.0)
    store.save_ticker(
        scan,
        "JET2.L",
        {
            "technical": tech,
            "research": Research(DATE, "ACT", "Winter bookings strong", "[]", "[7, 8]"),
        },
        path=db_backend,
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
            at="2026-10-11T18:30:00+00:00",
            actor="pipeline",
        )
        book.add_entry_conditions(conn, pid, scan, {"technical": tech})
        book.add_thesis_condition(conn, pid, "trend < 0", "machine")
        book.add_thesis_condition(conn, pid, "close < 100", "machine")  # cannot be hit at ~1000p+
        book.add_thesis_condition(conn, pid, "FY27 guidance cut", "agent", evidence_id=7)
        book.add_thesis_condition(conn, pid, "unknown_field > 1", "machine")
        book.approve_position(conn, pid)
        book.fill(conn, oid, 100, 1500.0, at="2026-09-14T09:00:00+00:00")
        daily_run = store.start_run("daily", {}, conn=conn)
        store.save_triggers(
            daily_run,
            [
                Trigger(
                    "JET2.L",
                    "filing",
                    pid,
                    None,
                    {
                        "id": "NI-9",
                        "headline": "Trading Update",
                        "type_code": "TST",
                        "type": "Trading Statement",
                        "category": "Financial Results",
                        "url": "https://data.fca.org.uk/9",
                    },
                    "2026-10-20T06:00:00+00:00",
                ),
                Trigger(
                    "JET2.L",
                    "news",
                    None,
                    None,
                    {"headline": "x", "source": "y", "url": "z"},
                    "2026-10-20T07:00:00+00:00",
                ),
            ],
            conn=conn,
        )
    conn.close()
    return db_backend, pid, daily_run


def test_pack_then_now_diff_and_thesis(
    world: tuple[Path | None, int, int], capsys: pytest.CaptureFixture[str]
) -> None:
    db, pid, run = world
    monitor_pack.main(["--run", str(run), *(["--db", str(db)] if db else [])])
    out = capsys.readouterr().out
    assert f"position {pid} JET2.L (open): 2 trigger(s)" in out and "written to" in out
    from solaraalpha.config import settings

    pack = json.loads(
        (settings.processed_dir / "monitor" / str(pid) / str(run) / "pack.json").read_text()
    )
    assert pack["state"]["shares"] == 100 and pack["state"]["stop"] == 1400.0
    assert pack["plan"]["target"] == 1800.0 and pack["plan"]["qty"] == 100
    assert pack["entry_conditions"]["technical"]["trend"] == 0.9
    assert set(pack["now"]) >= {"technical", "position", "levels", "relative_strength"}
    assert pack["now"]["position"]["entry_price"] == 1500.0
    latest = pack["latest"]
    assert len(latest["bars"]) == 10 and latest["date"] == latest["bars"][-1]["date"]
    assert latest["weekly_date"] <= latest["date"] and latest["close"] > 0
    assert latest["change_vs_weekly"] == pytest.approx(latest["close"] / latest["weekly_close"] - 1)
    assert set(pack["now_labels"]) == {"technical", "levels", "relative_strength", "liquidity"}
    d = pack["diff"]["technical.trend"]
    assert (
        d["then"] == 0.9
        and isinstance(d["now"], float)
        and d["change"] == pytest.approx(d["now"] - 0.9)
    )
    assert "fundamentals.growth" not in pack["diff"]  # neither side has it
    thesis = {t["condition"]: t for t in pack["thesis"]}
    assert thesis["trend < 0"]["hit"] in (True, False) and thesis["trend < 0"]["value"] is not None
    assert thesis["close < 100"]["hit"] is False
    assert (
        thesis["FY27 guidance cut"]["hit"] is None
        and thesis["FY27 guidance cut"]["evidence_id"] == 7
    )
    assert (
        thesis["unknown_field > 1"]["hit"] is None
        and "not evaluable" in thesis["unknown_field > 1"]["note"]
    )
    kinds = [t["kind"] for t in pack["triggers"]]
    assert kinds == ["filing", "news"]
    assert pack["triggers"][0]["text"].startswith("FULL TEXT OF https://data.fca.org.uk/9")
    assert pack["research"]["reasons"] == "Winter bookings strong" and pack[
        "research_evidence_ids"
    ] == [7, 8]

    conn = book.connect(db)
    assert monitoring.positions_to_review(conn, run) == [pid]
    assert monitoring.positions_to_review(conn, run + 100) == []
    conn.close()
    monitor_pack.main(["--run", str(run + 100), *(["--db", str(db)] if db else [])])
    assert "no position needs the monitor" in capsys.readouterr().out


def _verdict(pid: int, run: int, checks: dict[str, str], label: str = "WATCH") -> dict:
    return {
        "stage": "monitor",
        "ticker": "JET2.L",
        "run_id": run,
        "label": label,
        "confidence": 0.7,
        "reasons": ["guidance: FY27 cut", "stop too far below"],
        "scores": {"thesis_intact": -0.5},
        "kill_criteria": [],
        "note_md": "note",
        "evidence": [
            {
                "url": "https://data.fca.org.uk/9",
                "title": "Trading Update",
                "publisher": "FCA NSM",
                "published": "2026-10-20T06:00:00Z",
                "retrieved": "2026-10-20T18:00:00Z",
                "kind": "filing",
                "section": "trigger",
                "summary": "guidance cut",
                "impact_score": -0.6,
                "impact_confidence": 0.8,
                "driver": "guidance",
                "horizon_weeks": 8,
                "nsm_id": "NI-9",
            }
        ],
        "checks": checks,
    }


def _record(
    tmp_path: Path, db: Path | None, pid: int, run: int, checks: dict[str, str], name: str
) -> None:
    f = tmp_path / f"{name}.json"
    f.write_text(json.dumps(_verdict(pid, run, checks)))
    record.main(["revision", str(f), "--position", str(pid), *(["--db", str(db)] if db else [])])


def test_revision_round_trip_each_action(
    world: tuple[Path | None, int, int], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, pid, run = world
    dbargs = ["--db", str(db)] if db else []
    # hold: card + evidence, no revision
    _record(
        tmp_path,
        db,
        pid,
        run,
        {"action": "hold", "structural": "none", "trigger": "filing NI-9"},
        "hold",
    )
    out = capsys.readouterr().out
    assert f"position {pid}: hold" in out and "revision" not in out.split("evidence ids")[1]
    assert store.load_cards(run, "JET2.L", path=db)["monitor"]["label"] == "WATCH"
    assert len(store.load_evidence("JET2.L", run, "monitor", path=db)) == 1
    book_cli.main([*dbargs, "revisions"])
    assert "(none)" in capsys.readouterr().out

    # tighten_stop -> applied as recorded (ADR 0024): stop replaced, stop_raised
    run2 = store.start_run("daily", {}, path=db)
    _record(
        tmp_path,
        db,
        pid,
        run2,
        {
            "action": "tighten_stop",
            "stop": "1480",
            "structural": "guidance",
            "trigger": "filing NI-9",
        },
        "tighten",
    )
    capsys.readouterr()
    book_cli.main([*dbargs, "revisions"])
    assert "(none)" in capsys.readouterr().out  # nothing waits for the owner
    conn = book.connect(db)
    rev = book.revisions(conn)
    conn.close()
    rid = int(str(rev.loc[0, "revision_id"]))
    assert rev.loc[0, "old"] == {"stop": 1400.0, "target": 1800.0, "qty": 100} and rev.loc[
        0, "new"
    ] == {"stop": 1480.0}
    assert rev.loc[0, "status"] == "accepted" and rev.loc[0, "decided_by"] == "agent:monitor"
    done0 = rev["done"].iloc[0]
    assert done0["old_stop"] == 1400.0 and done0["new_stop"] == 1480.0
    conn = book.connect(db)
    st = book.position_state(conn, pid)
    assert st.stop == 1480.0
    od = book.orders(conn, pid)
    stops = od[od["purpose"] == "stop"]
    assert list(stops["status"]) == ["cancelled", "approved"] and list(stops["price"]) == [
        1400.0,
        1480.0,
    ]
    ev = book.position_events(conn, pid)
    assert "stop_raised" in set(ev["kind"]) and "revision_accepted" in set(ev["kind"])
    conn.close()
    with pytest.raises(SystemExit):
        book_cli.main([*dbargs, "accept", str(rid)])  # already decided
    assert "already accepted" in capsys.readouterr().out

    # the owner's undo: the stop order back at 1400, then raised again by the agent
    book_cli.main([*dbargs, "undo", str(rid), "--reason", "too tight"])
    assert "undone" in capsys.readouterr().out
    conn = book.connect(db)
    assert book.position_state(conn, pid).stop == 1400.0
    assert book.revisions(conn).loc[0, "status"] == "undone"
    with conn:
        book.propose_revision(conn, pid, "tighten_stop", {"stop": 1480.0}, "again", "agent:monitor")
    assert book.position_state(conn, pid).stop == 1480.0
    conn.close()

    # a lower stop is refused as it is recorded: declined by the pipeline, nothing changes
    run3 = store.start_run("daily", {}, path=db)
    _record(tmp_path, db, pid, run3, {"action": "tighten_stop", "stop": "1300"}, "lower")
    conn = book.connect(db)
    rv = book.revisions(conn).iloc[-1]
    assert rv["status"] == "declined" and "never lower a stop" in str(rv["decision_reason"])
    assert book.position_state(conn, pid).stop == 1480.0
    conn.close()

    # take_partial: exit order for 40, stop re-sized to 60 on fill
    run4 = store.start_run("daily", {}, path=db)
    _record(
        tmp_path,
        db,
        pid,
        run4,
        {"action": "take_partial", "qty": "40", "structural": "kill_criterion"},
        "partial",
    )
    conn = book.connect(db)
    done = book.revisions(conn).iloc[-1]["done"]
    with conn:
        book.fill(conn, done["exit_order_id"], 40, 1600.0, at="2026-10-21T10:00:00+00:00")
    st = book.position_state(conn, pid)
    assert st.shares == 60 and st.stop == 1480.0
    od = book.orders(conn, pid)
    live_stop = od[(od["purpose"] == "stop") & (od["status"] == "approved")]
    assert list(live_stop["qty"]) == [60]
    exit_ev = book.order_events(conn, done["exit_order_id"])
    assert exit_ev.loc[0, "reason"] == "discretionary"
    conn.close()

    # revise_target: a revised event, no orders
    run5 = store.start_run("daily", {}, path=db)
    _record(tmp_path, db, pid, run5, {"action": "revise_target", "target": "1900"}, "target")
    conn = book.connect(db)
    assert monitoring.latest_plan(conn, pid)["target"] == 1900.0
    n_orders = len(book.orders(conn, pid))
    conn.close()

    # exit: an approved market exit with reason thesis_broken
    run6 = store.start_run("daily", {}, path=db)
    _record(
        tmp_path,
        db,
        pid,
        run6,
        {"action": "exit", "type": "market", "structural": "capital_event"},
        "exit",
    )
    conn = book.connect(db)
    done = book.revisions(conn).iloc[-1]["done"]
    od = book.orders(conn, pid)
    assert len(od) == n_orders + 1
    assert (
        od.iloc[-1]["purpose"] == "exit"
        and od.iloc[-1]["type"] == "market"
        and od.iloc[-1]["qty"] == 60
    )
    assert book.exit_reason_for(conn, int(done["exit_order_id"])) == "thesis_broken"
    conn.close()
    capsys.readouterr()
    book_cli.main([*dbargs, "revisions", "--status", "all"])
    out = capsys.readouterr().out
    assert out.count("accepted") == 4 and out.count("declined") == 1 and out.count("undone") == 1


def test_agent_add_is_applied_within_the_limits(
    world: tuple[Path | None, int, int], tmp_path: Path
) -> None:
    db, pid, run = world
    checks = {
        "action": "add",
        "qty": "40",
        "entry": "1690",
        "type": "limit",
        "stop": "1520",
        "valid_until": "2026-10-30",
        "structural": "breakout",
    }
    # no cash in the book: the add is refused as recorded, nothing changes
    _record(tmp_path, db, pid, run, checks, "add_refused")
    conn = book.connect(db)
    rv = book.revisions(conn).iloc[-1]
    assert rv["status"] == "declined" and "portfolio limits" in str(rv["decision_reason"])
    assert book.position_state(conn, pid).stop == 1400.0
    with conn:
        book.cash_event(conn, "deposit", 20_000.0, "GBP", at="2026-09-01T00:00:00+00:00")
    conn.close()
    run2 = store.start_run("daily", {}, path=db)
    _record(tmp_path, db, pid, run2, checks, "add")
    conn = book.connect(db)
    rv = book.revisions(conn).iloc[-1]
    assert rv["status"] == "accepted" and rv["new"]["entry"] == 1690.0
    od = book.orders(conn, pid).set_index("order_id")
    add = od.loc[rv["done"]["add_order_id"]]
    assert add["purpose"] == "add" and add["qty"] == 40 and add["price"] == 1690.0
    assert add["valid_until"] == "2026-10-30" and add["status"] == "approved"
    assert book.position_state(conn, pid).stop == 1520.0
    pack = monitoring.build_pack(conn, pid, run2, text=False)
    assert pack["limits"]["max_heat"] == pytest.approx(0.10)
    assert "liquidity" in pack["now"]
    conn.close()


def test_revision_rejects_other_stages(
    world: tuple[Path | None, int, int], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, pid, run = world
    v = _verdict(pid, run, {"action": "exit"})
    v["stage"] = "research"
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(v))
    with pytest.raises(SystemExit) as e:
        record.main(
            ["revision", str(f), "--position", str(pid), *(["--db", str(db)] if db else [])]
        )
    assert e.value.code == 2 and "needs stage 'monitor'" in capsys.readouterr().out


def test_pending_entry_review_amend_cancel_and_undo(
    world: tuple[Path | None, int, int], capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR 0024: a pending entry gets a daily review and a pack; the agent's amend / cancel is
    applied as recorded, re-sized to the plan's risk budget, and the owner can undo it."""
    from solaraalpha import notify

    db, open_pid, _ = world
    conn = book.connect(db)
    with conn:
        pid, oid = book.draft_position(
            conn,
            "JET2.L",
            1560.0,
            1380.0,
            100,
            "stop",
            payload={"risk_amount": 180.0, "capped_by": "risk", "wait_for_break": True},
            at="2026-10-18T18:30:00+00:00",
            actor="pipeline",
        )
        book.approve_position(conn, pid)
        book.place_order(conn, oid, "T212-1")
        run = store.start_run("daily", {}, conn=conn)
        store.save_triggers(
            run,
            [
                Trigger("JET2.L", "daily_review", pid, oid, {"status": "pending", "fired": []}),
                Trigger("JET2.L", "daily_review", open_pid, None, {"status": "open", "fired": []}),
                Trigger("JET2.L", "gap", open_pid, None, {"gap_atr": 2.4}, "2026-10-20"),
            ],
            conn=conn,
        )
    # event triggers first, then open positions, then pending entries
    assert monitoring.positions_to_review(conn, run) == [open_pid, pid]
    pack = monitoring.build_pack(conn, pid, run, text=False)
    assert pack["status"] == "pending" and pack["pending_entry"]["order_id"] == oid
    assert pack["pending_entry"]["status"] == "placed"
    assert set(pack["systematic_stops"]) == {"levels", "technical", "chandelier"}
    assert pack["research_levels"] is None  # the fixture's research has no levels
    open_pack = monitoring.build_pack(conn, open_pid, run, text=False)
    assert open_pack["systematic_stops"]["chandelier"] is not None

    # an open-position action on a pending entry is refused, nothing changes
    with conn:
        bad = book.propose_revision(
            conn, pid, "tighten_stop", {"stop": 1400.0}, "x", "agent:monitor"
        )
    rv = book.revisions(conn).set_index("revision_id")
    assert rv.loc[bad, "status"] == "declined"
    assert "pending entry" in str(rv.loc[bad, "decision_reason"])

    # amend_entry: stop raised to 1470 -> qty re-sized to the 180 budget: 180 / 0.90 = 200
    with conn:
        rid = book.propose_revision(
            conn, pid, "amend_entry", {"stop": 1470.0}, "tighter invalidation", "agent:monitor"
        )
    rv = book.revisions(conn).set_index("revision_id")
    assert rv.loc[rid, "status"] == "accepted" and rv["done"].loc[rid]["was_placed"] is True
    entry = book.pending_entry(conn, pid)
    assert entry is not None and entry["qty"] == 200 and entry["price"] == 1560.0
    assert entry["status"] == "approved"  # the replacement has to be placed again
    assert book.position_state(conn, pid).initial_stop == 1470.0
    task = next(t for t in broker.tasks(conn) if t.position_id == pid)
    assert task.title == "Change the entry"
    assert task.steps[0] == (
        f"Cancel your buy order #{oid} (**Buy 100 · Stop order · stop price 1,560p**)"
    )
    new_oid = entry["order_id"]
    assert task.steps[1].startswith(
        f"Place **Buy 200 · Stop order · stop price 1,560p** (#{new_oid})"
    )
    assert "**Sell 200 · Stop order · stop price 1,470p**" in task.steps[2]
    assert task.why == "tighter invalidation (the monitor agent)"
    assert task.undo == f"book.py undo {rid} --reason ..."
    with conn:
        book.undo_revision(conn, rid, "keep the wider stop")
    entry = book.pending_entry(conn, pid)
    assert entry is not None and entry["qty"] == 100
    assert book.position_state(conn, pid).initial_stop == 1380.0

    # cancel: the position cancelled with its open orders; cannot be undone
    with conn:
        cid = book.propose_revision(conn, pid, "cancel", {}, "placing announced", "agent:monitor")
    assert book.position_status(conn, pid) == "cancelled" and book.pending_entry(conn, pid) is None
    with pytest.raises(ValueError, match="re-plan"):
        book.undo_revision(conn, cid, "oops")
    assert all(t.position_id != pid for t in broker.tasks(conn))  # nothing left to place
    (gone,) = broker.cancels_since(conn, "2000-01-01")
    assert gone.ticker == "JET2.L" and gone.steps == [
        f"Cancel your buy order #{oid} (**Buy 100 · Stop order · stop price 1,560p**)"
        " if it is still there"
    ]
    text = notify.daily_message(conn, run, ("revisions",))
    assert text is not None and "### 1. JET2.L — Cancel an order" in text
    conn.close()


def test_event_session_revisions_only_cut_risk(
    world: tuple[Path | None, int, int], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from solaraalpha import event_sessions

    db, pid, _ = world
    run = store.start_run("daily", {"session": 1}, path=db)
    conn = store.connect(db)
    sid = event_sessions.schedule(
        conn, "2026-10-28 13:45", "UK Budget", "AIM", "agent:pm", now="2026-10-27T18:00:00Z"
    )
    event_sessions.start(conn, sid, run)
    conn.close()
    with pytest.raises(SystemExit) as e:
        _record(tmp_path, db, pid, run, {"action": "revise_target", "target": "1900"}, "rt")
    assert e.value.code == 2
    assert "only risk-cutting" in capsys.readouterr().out
    assert "monitor" not in store.load_cards(run, "JET2.L", path=db)  # nothing recorded
    _record(tmp_path, db, pid, run, {"action": "tighten_stop", "stop": "1450"}, "ts")
    assert "applied to the book" in capsys.readouterr().out
