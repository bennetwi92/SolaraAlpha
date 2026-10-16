from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.planner import final_labels, kill_criteria, plan_run

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan as plan_cli

DATE = pd.Timestamp("2026-10-09", tz="UTC")


@dataclass(frozen=True)
class Levels:
    date: pd.Timestamp
    close: float
    trigger: float
    coil_pending: bool
    stop: float
    target: float
    target_2: float
    setup: float


@dataclass(frozen=True)
class Technical:
    date: pd.Timestamp
    close: float
    stop: float
    trend: float


@dataclass(frozen=True)
class RS:
    date: pd.Timestamp
    rs: float


@dataclass(frozen=True)
class Fund:
    date: pd.Timestamp
    growth: float


@dataclass(frozen=True)
class Events:
    date: pd.Timestamp
    next_event: pd.Timestamp | None


@dataclass(frozen=True)
class Research:
    date: pd.Timestamp
    label: str
    reasons: str
    kill_criteria: str


def _daily(close: float) -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-10-09", periods=120, tz="UTC", name="date")
    c = np.full(len(idx), close)
    df = pd.DataFrame(
        {
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "adj_close": c,
            "volume": 20_000.0,
        },
        index=idx,
    )
    df.attrs = {"ticker": "X", "currency": "GBp"}
    return df


def cards(close: float, coil: bool = False, research: bool = False) -> dict:
    out = {
        "levels": Levels(
            DATE,
            close,
            close * 1.04 if coil else math.nan,
            coil,
            close * 0.92,
            close * 1.3,
            close * 1.5,
            0.6,
        ),
        "technical": Technical(DATE, close, close * 0.9, 0.5),
        "relative_strength": RS(DATE, 0.2),
        "fundamentals": Fund(DATE, 0.3),
        "events": Events(DATE, DATE + pd.Timedelta(days=9)),
    }
    if research:
        kc = [
            {"condition": "trend < 0", "kind": "machine", "evidence_id": 4},
            {"condition": "FY27 guidance cut", "kind": "agent", "evidence_id": None},
            {
                "condition": "fuel goes bananas",
                "kind": "machine",
                "evidence_id": None,
            },  # unparseable
        ]
        out["research"] = Research(
            DATE, "ACT", "Strong winter bookings; fuel hedged.", json.dumps(kc)
        )
    return out


@pytest.fixture
def scan_db(tmp_path: Path) -> Path:
    """A saved scan run with ACT (A.L, coiled, researched), WATCH (W.L) and PASS (P.L), plus
    price snapshots and £10k of cash."""
    db = tmp_path / "solara.db"
    run_id = store.start_run("scan", {}, path=db)
    for ticker, close, label, coil, research in (
        ("A.L", 1500.0, "ACT", True, True),
        ("W.L", 800.0, "WATCH", False, False),
        ("P.L", 300.0, "PASS", False, False),
    ):
        daily = _daily(close)
        daily.attrs["ticker"] = ticker
        store.save_ticker(run_id, ticker, cards(close, coil, research), daily, path=db)
        store.save_label(run_id, ticker, "screen", label, f"{label.lower()} reason", path=db)
    conn = book.connect(db)
    with conn:
        book.cash_event(conn, "deposit", 10_000.0, at="2026-10-01T09:00:00+00:00")
        conn.execute(
            "UPDATE runs SET started_at = ? WHERE run_id = ?", ("2026-10-11T18:17:00+00:00", run_id)
        )
    conn.close()
    return db


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

    plan_cli.main(["--run", "1", "--db", str(scan_db)])
    out = capsys.readouterr().out
    assert "Drafted: A.L -> position 1" in out
    assert "event within 9 days" in out
    conn = book.connect(scan_db)
    pos = book.positions(conn)
    assert list(pos["ticker"]) == ["A.L"] and pos.loc[0, "status"] == "draft"
    assert pos.loc[0, "opened_from_run_id"] == 1 and pos.loc[0, "horizon_weeks"] == 16
    assert pos.loc[0, "thesis"] == "Strong winter bookings; fuel hedged."
    od = book.orders(conn, 1)
    assert len(od) == 1 and od.loc[0, "type"] == "stop" and od.loc[0, "price"] == 1560.0
    assert od.loc[0, "purpose"] == "entry" and od.loc[0, "status"] == "draft"
    assert od.loc[0, "valid_until"] == "2026-10-16"
    # risk 0.75% of £10k = £75; risk/share = 1560 - 1380 = 180p -> 41 shares
    assert od.loc[0, "qty"] == 41
    ev = book.position_events(conn, 1)
    payload = ev["payload"].iloc[0]
    assert payload["entry_type"] == "stop" and payload["stop"] == 1380.0 and payload["shares"] == 41
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
        "A.L: rejected_by_fit: no free slot: 0 open + 8 pending + 0 accepted >= max_positions 8"
        in out
    )
    assert "Drafted: none" in out
    conn = book.connect(scan_db)
    assert "A.L" not in set(book.positions(conn)["ticker"])
    conn.close()
