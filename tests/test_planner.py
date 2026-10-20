from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scanfix import build_scan_db
from solaraalpha import store
from solaraalpha.portfolio import book, shadow
from solaraalpha.portfolio.planner import final_labels, kill_criteria, plan_run

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan as plan_cli


@pytest.fixture
def scan_db(tmp_path: Path) -> Path:
    return build_scan_db(tmp_path)


def test_plan_run_drafts_one_position_and_is_idempotent(
    scan_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_cli.main(["--run", "1", "--dry-run", "--db", str(scan_db)])
    out = capsys.readouterr().out
    assert "A.L        ACT    decided by screen" in out and "W.L        WATCH" in out
    assert "stop 1,560.0" in out and "Dry run: nothing written." in out
    conn = book.connect(scan_db)
    assert book.positions(conn).empty
    conn.close()

    conn = book.connect(scan_db)
    assert shadow.load(conn).empty  # a dry run records no shadow rows either
    conn.close()

    plan_cli.main(["--run", "1", "--db", str(scan_db)])
    out = capsys.readouterr().out
    assert "Drafted: A.L -> position 1" in out
    assert "shadow book: random2_shortlist 1" in out  # ADR 0028: the only shortlisted name
    assert "event within 9 days" in out
    conn = book.connect(scan_db)
    pos = book.positions(conn)
    assert list(pos["ticker"]) == ["A.L"] and pos.loc[0, "status"] == "approved"  # ADR 0026
    assert pos.loc[0, "opened_from_run_id"] == 1 and pos.loc[0, "horizon_weeks"] == 16
    assert pos.loc[0, "thesis"] == "Strong winter bookings; fuel hedged."
    od = book.orders(conn, 1)
    assert len(od) == 1 and od.loc[0, "type"] == "stop" and od.loc[0, "price"] == 1560.0
    assert od.loc[0, "purpose"] == "entry" and od.loc[0, "status"] == "approved"
    assert od.loc[0, "valid_until"] == "2026-10-16"
    # risk 2% of £10k = £200; risk/share = 1560 - 1380 = 180p -> 111 shares
    assert od.loc[0, "qty"] == 111
    ev = book.position_events(conn, 1)
    payload = ev["payload"].iloc[0]
    assert (
        payload["entry_type"] == "stop" and payload["stop"] == 1380.0 and payload["shares"] == 111
    )
    assert (
        payload["capped_by"] == "risk"
        and payload["fit_flags"] == ["event within 9 days"]
        and "event within 9 days" in payload["flags"]
    )
    ec = book.entry_conditions(conn, 1)
    assert set(ec["model"]) == {
        "technical",
        "levels",
        "relative_strength",
        "fundamentals",
        "research",
    }
    assert set(ec["run_id"]) == {1}
    assert ec.set_index(["model", "field"]).loc[("levels", "coil_pending"), "value"] == 1.0
    tc = book.thesis_conditions(conn, 1)
    assert list(zip(tc["condition"], tc["kind"], strict=True)) == [
        ("trend < 0", "machine"),
        ("FY27 guidance cut", "agent"),
        ("fuel goes bananas", "agent"),
    ]
    assert tc.loc[0, "evidence_id"] == 4
    conn.close()

    plan_cli.main(["--run", "1", "--db", str(scan_db)])
    out = capsys.readouterr().out
    assert "Already drafted from this run: A.L" in out and "Drafted: none" in out

    conn = book.connect(scan_db)
    assert len(book.positions(conn)) == 1
    conn.close()

    conn = book.connect(scan_db)
    with conn:
        book.reject_position(conn, 1, "re-plan after a rules change")
    conn.close()
    plan_cli.main(["--run", "1", "--db", str(scan_db)])
    assert "Drafted: none" in capsys.readouterr().out  # a rejected draft still counts as done
    plan_cli.main(["--run", "1", "--replan", "--db", str(scan_db)])
    assert "Drafted: A.L -> position 2" in capsys.readouterr().out


def test_final_label_precedence(scan_db: Path) -> None:
    store.save_label(1, "W.L", "validate", "PASS", "artefact", 0.8, "agent:validate", path=scan_db)
    store.save_label(1, "A.L", "validate", "ACT", "warranted", 0.9, "agent:validate", path=scan_db)
    store.save_label(
        1, "A.L", "research", "WATCH", "results in 4 days", 0.6, "agent:research", path=scan_db
    )
    conn = book.connect(scan_db)
    got = {c.ticker: (c.label, c.stage) for c in final_labels(conn, 1)}
    assert got == {
        "A.L": ("WATCH", "research"),
        "W.L": ("PASS", "validate"),
        "P.L": ("PASS", "screen"),
    }
    cands = final_labels(conn, 1)
    assert cands[0].reasons == {
        "research": "results in 4 days",
        "validate": "warranted",
        "screen": "act reason",
    }
    out = plan_run(conn, 1, dry_run=True)
    assert out.plans == [] and out.report.accepted == []  # nothing is ACT any more
    conn.close()


def test_act_without_agent_levels_is_reported_not_planned(scan_db: Path) -> None:
    # W.L reaches ACT at the validator but has no research verdict: no trade levels (ADR 0023)
    store.save_label(1, "W.L", "validate", "ACT", "warranted", 0.8, "agent:validate", path=scan_db)
    store.save_label(1, "A.L", "validate", "ACT", "warranted", 0.9, "agent:validate", path=scan_db)
    conn = book.connect(scan_db)
    out = plan_run(conn, 1, dry_run=True)
    conn.close()
    assert ("W.L", "no agent levels (no research verdict with a trade)") in out.plan_errors
    (plan,) = out.plans
    assert plan.ticker == "A.L" and plan.stop_source == "agent"
    assert plan.stop_reason == "below the base: the breakout has failed"


def test_kill_criteria_parsing() -> None:
    assert kill_criteria({}) == []
    assert kill_criteria({"kill_criteria": None}) == []
    got = kill_criteria(
        {
            "kill_criteria": json.dumps(
                ["close < 1400", {"condition": "bid lapses", "kind": "agent"}]
            )
        }
    )
    assert got == [
        {"condition": "close < 1400", "kind": "machine", "evidence_id": None},
        {"condition": "bid lapses", "kind": "agent", "evidence_id": None},
    ]
    assert kill_criteria({"kill_criteria": "not json"}) == [
        {"condition": "not json", "kind": "agent", "evidence_id": None}
    ]


def test_fit_rejection_is_reported_not_written(
    scan_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = book.connect(scan_db)
    with conn:  # a pending Travel & Leisure position already uses the only free slot
        for i in range(8):
            book.draft_position(conn, f"H{i}.L", 100.0, 90.0, 10, at="2026-10-05T18:00:00+00:00")
    conn.close()
    plan_cli.main(["--run", "1", "--db", str(scan_db)])
    out = capsys.readouterr().out
    assert (
        "A.L: rejected_by_fit: no free slot: 0 open + 8 pending + 0 accepted >= max_positions 5"
        in out
    )
    assert "Drafted: none" in out
    conn = book.connect(scan_db)
    assert "A.L" not in set(book.positions(conn)["ticker"])
    conn.close()
