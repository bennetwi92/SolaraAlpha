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
from solaraalpha.portfolio import book
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
def world(db_backend: Path | None, monkeypatch: pytest.MonkeyPatch) -> tuple[Path | None, int, int]:
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
    assert f"position {pid} JET2.L: 2 trigger(s)" in out and "written to" in out
    from solaraalpha.config import settings

    pack = json.loads(
        (settings.processed_dir / "monitor" / str(pid) / str(run) / "pack.json").read_text()
    )
    assert pack["state"]["shares"] == 100 and pack["state"]["stop"] == 1400.0
    assert pack["plan"]["target"] == 1800.0 and pack["plan"]["qty"] == 100
    assert pack["entry_conditions"]["technical"]["trend"] == 0.9
    assert set(pack["now"]) >= {"technical", "position", "levels", "relative_strength"}
    assert pack["now"]["position"]["entry_price"] == 1500.0
    assert set(pack["now_labels"]) == {"technical", "levels", "relative_strength"}
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

    # tighten_stop -> accepted: stop replaced, stop_raised
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
    out = capsys.readouterr().out
    assert "revision" in out and "proposed" in out
    book_cli.main([*dbargs, "revisions"])
    out = capsys.readouterr().out
    assert "tighten_stop" in out and "proposed" in out and "agent:monitor" in out
    conn = book.connect(db)
    rev = book.revisions(conn)
    conn.close()
    rid = int(str(rev.loc[0, "revision_id"]))
    assert rev.loc[0, "old"] == {"stop": 1400.0, "target": 1800.0, "qty": 100} and rev.loc[
        0, "new"
    ] == {"stop": 1480.0}
    book_cli.main([*dbargs, "accept", str(rid)])
    assert "accepted (tighten_stop)" in capsys.readouterr().out
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
    assert book.revisions(conn).loc[0, "status"] == "accepted"
    conn.close()
    with pytest.raises(SystemExit):
        book_cli.main([*dbargs, "accept", str(rid)])  # already decided
    assert "already accepted" in capsys.readouterr().out

    # a lower stop is refused on accept
    run3 = store.start_run("daily", {}, path=db)
    _record(tmp_path, db, pid, run3, {"action": "tighten_stop", "stop": "1300"}, "lower")
    conn = book.connect(db)
    rid_low = int(str(book.revisions(conn, status="proposed").loc[0, "revision_id"]))
    conn.close()
    with pytest.raises(SystemExit):
        book_cli.main([*dbargs, "accept", str(rid_low)])
    assert "never lower a stop" in capsys.readouterr().out
    book_cli.main([*dbargs, "decline", str(rid_low), "--reason", "no"])
    conn = book.connect(db)
    assert book.revisions(conn).set_index("revision_id").loc[rid_low, "status"] == "declined"
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
    rid_p = int(str(book.revisions(conn, status="proposed").loc[0, "revision_id"]))
    with conn:
        done = book.accept_revision(conn, rid_p)
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
    rid_t = int(str(book.revisions(conn, status="proposed").loc[0, "revision_id"]))
    with conn:
        book.accept_revision(conn, rid_t)
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
    rid_x = int(str(book.revisions(conn, status="proposed").loc[0, "revision_id"]))
    with conn:
        done = book.accept_revision(conn, rid_x)
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
    assert out.count("accepted") == 4 and out.count("declined") == 1


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
