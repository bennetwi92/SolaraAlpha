from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from scanfix import build_scan_db
from solaraalpha import agents, notify, pm, store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.planner import plan_run
from solaraalpha.portfolio.rules import load_rules
from solaraalpha.portfolio.triggers import Trigger
from solaraalpha.portfolio.weekly_pack import build_pack, render_pack

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pm_pack
import record


@pytest.fixture
def scan_db(tmp_path: Path) -> Path:
    db = build_scan_db(tmp_path)
    store.save_label(1, "A.L", "research", "ACT", "catalyst", 0.8, "agent:research", path=db)
    return db


def _pm(**over: Any) -> agents.Verdict:
    v: dict[str, Any] = {
        "stage": "pm",
        "ticker": agents.BOOK,
        "run_id": 1,
        "label": "ACT",
        "confidence": 0.7,
        "reasons": ["fund A.L"],
        "allocation": [{"ticker": "A.L", "risk_pct": 0.01, "reason": "priced-in doubt"}],
        "note_md": "Stance: one idea, small.",
    }
    v.update(over)
    return agents.Verdict.model_validate(v)


def _open(conn: Any, ticker: str, at: str = "2026-09-01T09:00:00+00:00") -> int:
    pid, oid = book.draft_position(conn, ticker, 100.0, 90.0, 10, at=at)
    book.approve_position(conn, pid)
    book.fill(conn, oid, 10, 100.0, at=at)
    return pid


def test_pm_verdict_shape() -> None:
    assert _pm().allocation is not None
    with pytest.raises(ValidationError, match="allocation"):
        _pm(allocation=None)
    with pytest.raises(ValidationError, match="_BOOK"):
        _pm(ticker="A.L")
    with pytest.raises(ValidationError, match="once"):
        _pm(allocation=[{"ticker": "A.L", "risk_pct": 0.01, "reason": "x"}] * 2)
    with pytest.raises(ValidationError, match="only the pm stage"):
        agents.Verdict.model_validate(
            {**_pm().model_dump(), "stage": "macro", "ticker": "_SECTOR:x"}
        )
    assert _pm(label="ERROR", allocation=None).allocation is None  # an ERROR needs none


def test_record_book_refuses_what_the_run_or_book_cannot_honour(scan_db: Path) -> None:
    conn = book.connect(scan_db)
    for bad, msg in (
        ([{"ticker": "W.L", "risk_pct": 0.01, "reason": "x"}], "W.L is WATCH"),
        ([{"ticker": "A.L", "risk_pct": 0.04, "reason": "x"}], "outside risk_per_trade_range"),
    ):
        with pytest.raises(ValueError, match=msg):
            agents.record_book(conn, _pm(allocation=bad))
    with pytest.raises(ValueError, match="no position 99"):
        agents.record_book(conn, _pm(rotations=[{"position_id": 99, "reason": "x"}]))
    assert agents.pm_decision(conn, 1) is None  # nothing was recorded
    agents.record_book(conn, _pm())
    got = agents.pm_decision(conn, 1)
    assert got is not None and got["allocation"][0]["risk_pct"] == 0.01
    with pytest.raises(ValueError, match="already recorded"):
        agents.record_book(conn, _pm())  # once per run
    conn.close()


def test_planner_follows_the_pm_and_the_pm_approves(scan_db: Path) -> None:
    conn = book.connect(scan_db)
    agents.record_book(conn, _pm())
    with conn:
        out = plan_run(conn, 1)
    # 1% of £10k = £100 at 180p a share -> 55 shares (2% would be 111)
    (plan,) = out.plans
    assert plan.shares == 55 and out.pm is not None
    pid = out.written["A.L"]
    assert book.position_status(conn, pid) == "approved"
    ev = book.position_events(conn, pid)
    assert ev.loc[ev["kind"] == "approved", "actor"].iloc[0] == "agent:pm"
    assert set(book.orders(conn, pid)["status"]) == {"approved"}
    conn.close()


def test_unfunded_act_is_reported_and_error_falls_back_to_the_pipeline(scan_db: Path) -> None:
    conn = book.connect(scan_db)
    agents.record_book(conn, _pm(allocation=[], label="PASS"))
    out = plan_run(conn, 1, dry_run=True)
    assert out.plans == [] and ("A.L", "not funded by the PM") in out.plan_errors
    conn.close()

    (scan_db.parent / "two").mkdir()
    db2 = build_scan_db(scan_db.parent / "two")
    store.save_label(1, "A.L", "research", "ACT", "catalyst", 0.8, "agent:research", path=db2)
    conn = book.connect(db2)
    agents.record_book(conn, _pm(label="ERROR", allocation=None, reasons=["pack failed"]))
    with conn:
        out = plan_run(conn, 1)
    pid = out.written["A.L"]
    assert out.pm is None and book.position_status(conn, pid) == "approved"
    ev = book.position_events(conn, pid)
    assert ev.loc[ev["kind"] == "approved", "actor"].iloc[0] == book.PIPELINE
    assert out.plans[0].shares == 111  # the rules' 2%
    conn.close()


def test_pm_approves_a_waiting_draft_from_the_run(scan_db: Path) -> None:
    conn = book.connect(scan_db)
    with conn:  # a draft from the run still waiting (planned before ADR 0026)
        pid, _ = book.draft_position(
            conn, "A.L", 1560.0, 1380.0, 111, "stop", opened_from_run_id=1, at="2026-10-11"
        )
    agents.record_book(conn, _pm())
    with conn:
        out = plan_run(conn, 1)
    assert out.approved == [pid] and out.written == {}
    assert book.position_status(conn, pid) == "approved"
    conn.close()


def test_rotation_exits_a_holding_and_frees_its_slot(scan_db: Path) -> None:
    conn = book.connect(scan_db)
    with conn:
        held = [_open(conn, f"H{i}.L") for i in range(5)]  # every slot taken
    assert "no free slot" in plan_run(conn, 1, dry_run=True).report.rejected[0][1]
    out = agents.record_book(
        conn, _pm(rotations=[{"position_id": held[0], "reason": "dead money", "funds": "A.L"}])
    )
    (rot,) = out["rotations"]
    assert rot["action"] == "exit" and rot["status"] == "accepted"
    od = book.orders(conn, held[0])
    assert ((od["purpose"] == "exit") & (od["status"] == "approved")).any()
    with conn:
        run = plan_run(conn, 1)
    assert run.rotating == [held[0]] and "A.L" in run.written
    conn.close()


def test_rotation_cancels_a_pending_entry(scan_db: Path) -> None:
    conn = book.connect(scan_db)
    with conn:
        pid, _ = book.draft_position(conn, "OLD.L", 100.0, 90.0, 10, at="2026-10-01T09:00:00+00:00")
    out = agents.record_book(conn, _pm(rotations=[{"position_id": pid, "reason": "stale"}]))
    assert out["rotations"][0]["action"] == "cancel"
    assert book.position_status(conn, pid) == "cancelled"
    conn.close()


def test_pm_pack_and_memo(
    scan_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = book.connect(scan_db)
    pack = pm.build_pack(conn, 1)
    (cand,) = pack["candidates"]
    assert cand["ticker"] == "A.L" and cand["research"]["trade"]["stop"] == 1380.0
    assert cand["plan_at_default_risk"]["shares"] == 111
    assert set(cand["sizing"]["at"]) == {"1.00%", "2.00%", "3.00%"}
    assert cand["financing"] is None  # no financing card saved in this run
    assert cand["event_gap"]["n"] == 0 and cand["event_gap"]["in_first_half"]
    assert pack["rank_note"].startswith("The screen rank is unvalidated")  # #210
    assert pack["money"]["slots_free"] == 5 and pack["limits"]["risk_per_trade_range"] == (
        0.01,
        0.03,
    )
    conn.close()

    pm_pack.main(["--run", "1", "--db", str(scan_db), "--out", str(tmp_path / "pk")])
    assert "1 research-ACT candidates" in capsys.readouterr().out
    assert json.loads((tmp_path / "pk" / "pack.json").read_text())["run_id"] == 1

    path = tmp_path / "pm.json"
    path.write_text(_pm().model_dump_json())
    record.main(["verdict", str(path), "--actor", "agent:pm", "--db", str(scan_db)])
    assert "allocated A.L at 1.0% risk" in capsys.readouterr().out
    conn = book.connect(scan_db)
    with conn:
        plan_run(conn, 1)
    text = render_pack(build_pack(conn, 1))
    conn.close()
    assert "## PM memo" in text and "A.L at 1.0% risk" in text and "Stance: one idea" in text
    assert "approved by the PM (ADR 0027)" in text
    conn = book.connect(scan_db)
    msg = notify.weekly_message(conn, 1)
    conn.close()
    assert msg is not None and "Your PM: fund A.L" in msg and "### From your PM" in msg


def test_financing_section() -> None:
    card = {"date": "2026-10-09", "runway_months": float("nan"), "raises_3y": 2.0}
    assert pm.financing(card) == {"runway_months": None, "raises_3y": 2.0}
    assert pm.financing(None) is None


def test_event_gap_costs_the_past_moves() -> None:
    from solaraalpha.portfolio.rules import load_rules

    rules = load_rules()
    events = {
        "next_event": "2026-10-23",
        "event_moves": 3,
        "event_worst_move": -0.4,
        "event_p90_move": 0.25,
    }
    sized = {"at": {"2.00%": {"value_gbp": 1250.0}}}
    got = pm.event_gap(events, sized, "2026-10-11", 5000.0, rules)
    assert got is not None
    assert got["in_first_half"] and got["weeks_to_event"] == 1.7 and got["small_sample"]
    assert got["cost"]["2.00%"] == {
        "worst_gbp": 500.0,
        "p90_gbp": 312.5,
        "worst_pct_equity": 0.1,
        "p90_pct_equity": 0.0625,
    }
    later = pm.event_gap({**events, "next_event": "2027-01-29"}, sized, "2026-10-11", 5e3, rules)
    assert later is not None and not later["in_first_half"]
    none = pm.event_gap({"event_moves": 0}, None, "2026-10-11", 5000.0, rules)
    assert none is not None and none["worst"] is None and none["cost"] == {}
    assert pm.event_gap(None, sized, "2026-10-11", 5000.0, rules) is None


def test_risk_band_rule(tmp_path: Path) -> None:
    from solaraalpha.portfolio.rules import RULES

    assert load_rules().risk_per_trade_range == (0.01, 0.03)
    text = RULES.read_text().replace(
        "risk_per_trade_range = [0.01, 0.03]", "risk_per_trade_range = [0.025, 0.03]"
    )
    (tmp_path / "p.toml").write_text(text)
    with pytest.raises(ValueError, match="risk_per_trade_range"):
        load_rules(tmp_path / "p.toml")


def test_rotation_buy_waits_for_the_sale_then_fits_the_cash(scan_db: Path) -> None:
    from solaraalpha.portfolio import broker
    from solaraalpha.portfolio.daily import free_cash, release_rotations

    conn = book.connect(scan_db)
    rules = load_rules()
    with conn:
        held = [_open(conn, f"H{i}.L") for i in range(5)]  # every slot taken
    agents.record_book(
        conn, _pm(rotations=[{"position_id": held[0], "reason": "dead money", "funds": "A.L"}])
    )
    with conn:
        pid = plan_run(conn, 1).written["A.L"]
    assert book.waiting_on(conn, pid) == [held[0]]
    (task,) = [t for t in broker.tasks(conn) if t.position_id == pid]
    assert "Don't place it yet: selling H0.L (position" in task.steps[0]
    qty = book.pending_entry(conn, pid)["qty"]  # type: ignore[index]
    assert release_rotations(conn, rules) == []  # still waiting: nothing changes

    exit_oid = int(book.orders(conn, held[0]).query("purpose == 'exit'")["order_id"].iloc[0])
    with conn:
        book.fill(conn, exit_oid, 10, 50.0)
        book.cash_event(conn, "withdrawal", 9800.0)  # less cash than it was sized on
    assert book.waiting_on(conn, pid) == []
    cash = free_cash(conn, rules, but=pid)
    want = min(qty, int(cash // 15.6))
    lines = release_rotations(conn, rules)
    new = book.pending_entry(conn, pid)
    assert new is not None and new["qty"] == want
    assert want < qty and "buy cut from" in lines[0]  # the sale freed less than sized on
    (task,) = [t for t in broker.tasks(conn) if t.position_id == pid]
    assert task.steps[0].startswith("Place ")
    assert release_rotations(conn, rules) == []  # once it fits, nothing more
    conn.close()


def test_pm_pack_must_cards(scan_db: Path) -> None:
    conn = book.connect(scan_db)
    with conn:
        store.save_label(
            1, "A.L", "validate", "ACT", "levels hold", 0.7, "agent:validate", conn=conn
        )
        store._save_card(
            conn, 1, "A.L", "validate", {"date": "2026-10-09", "label": "ACT", "reasons": "ok"}
        )
        store._save_card(
            conn, 1, "A.L", "research", {"date": "2026-10-09", "note_md": "## 1. Scene"}
        )
        conn.execute(
            "INSERT INTO steps VALUES (1, 'X.L', 'screen', 'technical', 'error', 'no bars')"
        )
        store.save_label(
            1, "B.L", "research", "ERROR", "turn limit", 0.0, "agent:research", conn=conn
        )
        held = _open(conn, "W.L")
        kc = book.add_thesis_condition(conn, held, "trend < 0", "machine")
        book.add_thesis_condition(conn, held, "guidance cut", "agent")
        pid, _ = book.draft_position(conn, "P.L", 290.0, 270.0, 10, valid_days=10)
        book.approve_position(conn, pid)
        daily = store.start_run("daily", {}, conn=conn)
        store.save_triggers(
            daily,
            [
                Trigger("W.L", "thesis_hit", held, None, {"condition_id": kc}, "2026-10-12"),
                Trigger(
                    "W.L",
                    "broker",
                    held,
                    None,
                    {"what": "fill", "text": "sold 10 at 95p", "command": "fill 9 --qty 10"},
                    "2026-10-12",
                ),
            ],
            conn=conn,
        )
    pack = pm.build_pack(conn, 1)
    conn.close()
    (cand,) = pack["candidates"]
    assert cand["validator"]["label"] == "ACT" and cand["validator"]["reasons"] == "ok"
    assert cand["research"]["note_md"] == "## 1. Scene"
    assert cand["last_close"]["date"] == "2026-10-09" and cand["last_close"]["close"] == 1500.0
    assert "vs_entry" in cand["last_close"]
    (pos,) = pack["open_positions"]
    assert pos["kill_criteria_now"] == [
        {"condition": "trend < 0", "kind": "machine", "hit": True},
        {"condition": "guidance cut", "kind": "agent", "hit": None},
    ]
    (pend,) = pack["pending_entries"]
    assert pend["placed"] is False and pend["waits_for"] == []
    assert pend["last_close"]["vs_entry"] == round(300.0 / 290.0 - 1, 4)
    assert {"ticker": "B.L", "stage": "research", "reason": "turn limit"}.items() <= next(
        f for f in pack["failures"] if f["ticker"] == "B.L"
    ).items()
    assert pack["coverage"]["errors"] == [
        {"ticker": "X.L", "toolkit": "technical", "reason": "no bars"}
    ]
    assert pack["coverage"]["stale_prices"] == {}
    assert pack["broker"]["differences"] == [
        {"ticker": "W.L", "text": "sold 10 at 95p", "command": "fill 9 --qty 10"}
    ]
    assert {"date": "2026-10-18", "ticker": "A.L", "role": "candidate"} in pack["results_calendar"]
    assert pack["changes"] == []  # no previous scan


def test_parse_log_tags_rule_changes() -> None:
    text = (
        "@abc1234 Raise heat cap (#250)\nconfig/portfolio.toml\nCLAUDE.md\n\n"
        "@def5678 Fix typo (#251)\nREADME.md\n\n"
        "@0a1b2c3 New ADR and skill (#252)\ndocs/decisions/0033-x.md\n.claude/skills/pm/SKILL.md\n"
    )
    assert pm.parse_log(text) == [
        "abc1234 Raise heat cap (#250) [rules]",
        "def5678 Fix typo (#251)",
        "0a1b2c3 New ADR and skill (#252) [ADR, skill]",
    ]
