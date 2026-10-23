from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha import agents, store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import record

FIXTURE = Path(__file__).parent / "fixtures" / "verdict_research.json"
DATE = pd.Timestamp("2026-10-09", tz="UTC")


@dataclass(frozen=True)
class Card:
    date: pd.Timestamp
    trend: float


def _run(db: Path | None, label: str = "ACT") -> int:
    run_id = store.start_run("scan", {}, path=db)
    store.save_ticker(run_id, "JET2.L", {"technical": Card(DATE, 0.5)}, path=db)
    store.save_label(run_id, "JET2.L", "screen", label, "technical ACT", path=db)
    return run_id


def _verdict(**over: object) -> dict:
    v = json.loads(FIXTURE.read_text())
    v.update(over)
    return v


def _write(tmp_path: Path, v: dict, name: str = "verdict.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(v))
    return str(path)


def _args(db: Path | None) -> list[str]:
    return ["--db", str(db)] if db is not None else []


def test_valid_verdict_writes_label_card_and_evidence(
    db_backend: Path | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _run(db_backend)
    record.main(["verdict", _write(tmp_path, _verdict(run_id=run_id)), *_args(db_backend)])
    out = capsys.readouterr().out
    assert "recorded research ACT (0.70) for JET2.L in run 1, dated 2026-10-09" in out
    assert "labels row written" in out and "evidence ids: 1, 2" in out

    labels = store.load_labels(run_id, path=db_backend)
    assert list(labels["stage"]) == ["screen", "research"]
    assert labels.iloc[1]["actor"] == "agent:research" and labels.iloc[1]["confidence"] == 0.7
    assert (
        labels.iloc[1]["reason"] == "Winter bookings ahead of last year; fuel 80% hedged for FY27"
    )

    cards = store.load_cards(run_id, "JET2.L", path=db_backend)
    card = cards["research"]
    assert card["date"] == cards["technical"]["date"]  # joins the screen cards on date
    assert card["label"] == "ACT" and card["catalyst_score"] == 0.4 and card["event_risk"] == 0.3
    assert card["reasons"].startswith("Winter bookings")
    kc = json.loads(card["kill_criteria"])
    assert kc == [
        {"condition": "trend < 0", "kind": "machine", "evidence_id": None},
        {"condition": "Winter capacity guidance cut below +5%", "kind": "agent", "evidence_id": 1},
    ]
    assert json.loads(card["evidence_ids"]) == [1, 2]
    assert card["note_md"].startswith("## Bottom line")
    # the trade levels (ADR 0023) round-trip through the card
    assert card["trade_entry_type"] == "stop" and card["trade_stop"] == 1390.0
    assert agents.trade_levels(card) == agents.TradeLevels(
        entry_type="stop",
        entry=1560.0,
        stop=1390.0,
        stop_reason="below the October base: the breakout has failed",
        valid_days=7,
    )
    assert agents.trade_levels(cards["technical"]) is None and agents.trade_levels(None) is None

    ev = store.load_evidence("JET2.L", path=db_backend)
    assert list(ev["kind"]) == ["filing", "web"] and ev.iloc[0]["nsm_id"] == "NI-000012345678"
    assert ev.iloc[0]["published"] == "2026-10-08T06:00:00+00:00"
    assert ev.iloc[0]["impact_score"] == 0.5 and pd.isna(ev.iloc[1]["nsm_id"])
    assert set(ev["actor"]) == {"agent:research"}

    # a second verdict for the same stage is refused (append-only)
    with pytest.raises(SystemExit) as e:
        record.main(["verdict", _write(tmp_path, _verdict(run_id=run_id)), *_args(db_backend)])
    assert e.value.code == 2 and "already recorded" in capsys.readouterr().out


def test_invalid_json_fails_with_field_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = _verdict(label="MAYBE", confidence=1.4, scores={"catalyst_score": 2.0}, extra_field=1)
    with pytest.raises(SystemExit) as e:
        record.main(["verdict", _write(tmp_path, bad), "--db", str(tmp_path / "x.db")])
    assert e.value.code == 2
    out = capsys.readouterr().out
    assert "not a valid verdict" in out
    assert "label:" in out and "confidence:" in out and "scores:" in out and "extra_field:" in out
    assert "outside [-1, 1]" in out


def test_upgrade_rejected_error_allowed_and_events_label(
    db_backend: Path | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _run(db_backend, label="WATCH")
    with pytest.raises(SystemExit) as e:
        record.main(
            [
                "verdict",
                _write(tmp_path, _verdict(run_id=run_id, stage="validate")),
                *_args(db_backend),
            ]
        )
    assert e.value.code == 2
    assert "validate may not upgrade JET2.L from WATCH to ACT" in capsys.readouterr().out

    record.main(
        [
            "verdict",
            _write(tmp_path, _verdict(run_id=run_id, stage="validate", label="PASS")),
            *_args(db_backend),
        ]
    )
    record.main(
        [
            "verdict",
            _write(
                tmp_path,
                _verdict(
                    run_id=run_id,
                    stage="research",
                    label="ERROR",
                    reasons=["pack failed"],
                    evidence=[],
                    kill_criteria=[],
                ),
            ),
            *_args(db_backend),
        ]
    )
    record.main(
        [
            "verdict",
            _write(
                tmp_path,
                _verdict(run_id=run_id, stage="events", label="ACT", evidence=[], kill_criteria=[]),
                "events.json",
            ),
            *_args(db_backend),
        ]
    )
    capsys.readouterr()
    labels = store.load_labels(run_id, path=db_backend).set_index("stage")["label"]
    assert labels.to_dict() == {
        "screen": "WATCH",
        "validate": "PASS",
        "research": "ERROR",
        "events": "ACT",
    }
    # research downgrades from validate when present
    run2 = _run(db_backend, label="ACT")
    record.main(
        [
            "verdict",
            _write(
                tmp_path,
                _verdict(
                    run_id=run2, stage="validate", label="WATCH", evidence=[], kill_criteria=[]
                ),
            ),
            *_args(db_backend),
        ]
    )
    with pytest.raises(SystemExit):
        record.main(
            [
                "verdict",
                _write(tmp_path, _verdict(run_id=run2, stage="research", label="ACT")),
                *_args(db_backend),
            ]
        )
    assert "research may not upgrade JET2.L from WATCH to ACT" in capsys.readouterr().out
    # monitor writes a card but no labels row, and needs no prior label
    run3 = store.start_run("daily", {}, path=db_backend)
    record.main(
        [
            "verdict",
            _write(
                tmp_path,
                _verdict(
                    run_id=run3, stage="monitor", label="WATCH", evidence=[], kill_criteria=[]
                ),
            ),
            *_args(db_backend),
        ]
    )
    assert "labels row written" not in capsys.readouterr().out
    assert store.load_cards(run3, "JET2.L", path=db_backend)["monitor"]["label"] == "WATCH"


def test_prior_lists_previous_verdicts_and_filings(
    db_backend: Path | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run1 = _run(db_backend)
    record.main(["verdict", _write(tmp_path, _verdict(run_id=run1)), *_args(db_backend)])
    run2 = _run(db_backend)
    newer = _verdict(
        run_id=run2,
        label="WATCH",
        scores={"catalyst_score": -0.2},
        evidence=[],
        kill_criteria=[],
        reasons=["bid talk faded"],
    )
    record.main(["verdict", _write(tmp_path, newer), *_args(db_backend)])
    capsys.readouterr()
    record.main(["prior", "JET2.L", *_args(db_backend)])
    out = capsys.readouterr().out
    assert "JET2.L: 2 prior research verdict(s)" in out
    assert out.index(f"== run {run2}") < out.index(f"== run {run1}")  # latest first
    assert "catalyst_score=-0.20" in out and "bid talk faded" in out
    assert "[filing] Jet2 plc - Trading Update (FCA NSM, 2026-10-08)" in out
    assert "Filings already interpreted (nsm_id): NI-000012345678 (research, run 1)" in out
    record.main(["prior", "JET2.L", "--limit", "1", *_args(db_backend)])
    assert "1 prior research verdict(s)" in capsys.readouterr().out
    record.main(["prior", "EZJ.L", *_args(db_backend)])
    out = capsys.readouterr().out
    assert "0 prior" in out and "(nsm_id): none" in out

    conn = store.connect(db_backend)
    p = agents.prior(conn, "JET2.L", limit=5)
    conn.close()
    assert [v["run_id"] for v in p["verdicts"]] == [run2, run1]
    assert len(p["verdicts"][1]["evidence"]) == 2
    # #84: the latest catalyst_score per ticker is one query away
    latest = store.latest("research", path=db_backend)
    assert latest.loc["JET2.L", "catalyst_score"] == -0.2


def test_schema_prints(capsys: pytest.CaptureFixture[str]) -> None:
    record.main(["schema"])
    schema = json.loads(capsys.readouterr().out)
    assert schema["title"] == "Verdict"
    assert set(schema["required"]) >= {
        "stage",
        "ticker",
        "run_id",
        "label",
        "confidence",
        "reasons",
    }
    assert "Evidence" in schema["$defs"] and "KillCriterion" in schema["$defs"]


EVENTS_FIXTURE = Path(__file__).parent / "fixtures" / "verdict_events.json"


def test_fixtures_validate_against_the_schema() -> None:
    research = agents.load_verdict(FIXTURE.read_text())
    assert research.stage == "research" and len(research.evidence) == 2
    assert {"catalyst_score", "event_risk"} <= set(research.scores)
    events = agents.load_verdict(EVENTS_FIXTURE.read_text())
    assert events.stage == "events" and events.label == "ACT"
    assert [e.nsm_id for e in events.evidence] == ["NI-000012345678", "NI-000012340000"]


def test_events_recorded_twice_prior_lists_first_runs_filings(
    db_backend: Path | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # a standalone `/events` run: --new-run creates the run to attach to
    record.main(
        ["verdict", str(EVENTS_FIXTURE), "--new-run", "--actor", "agent:events", *_args(db_backend)]
    )
    out = capsys.readouterr().out
    assert "recorded events ACT (0.75) for JET2.L in run 1" in out and "labels row written" in out
    second = json.loads(EVENTS_FIXTURE.read_text())
    second["evidence"] = second["evidence"][:1]
    second["evidence"][0]["nsm_id"] = "NI-000099999999"
    second["scores"] = {"catalyst_score": 0.1, "event_risk": 0.33}
    record.main(["verdict", _write(tmp_path, second), "--new-run", *_args(db_backend)])
    capsys.readouterr()
    record.main(["prior", "JET2.L", "--stage", "events", *_args(db_backend)])
    out = capsys.readouterr().out
    assert "JET2.L: 2 prior events verdict(s)" in out
    assert "NI-000012345678 (events, run 1)" in out and "NI-000012340000 (events, run 1)" in out
    assert "NI-000099999999 (events, run 2)" in out
    runs = store.query("SELECT script, args FROM runs ORDER BY run_id", store.connect(db_backend))
    assert list(runs["script"]) == ["events", "events"]
    assert store.latest("events", path=db_backend).loc["JET2.L", "catalyst_score"] == 0.1  # #84
    # --run overrides the file's run id
    run3 = store.start_run("scan", {}, path=db_backend)
    record.main(["verdict", str(EVENTS_FIXTURE), "--run", str(run3), *_args(db_backend)])
    assert f"in run {run3}" in capsys.readouterr().out


def test_skills_reference_the_schema_and_record() -> None:
    root = Path(__file__).resolve().parents[1] / ".claude" / "skills"
    for name in ("events", "research", "validate", "pm"):
        text = (root / name / "SKILL.md").read_text()
        assert "record.py schema" in text and "record.py verdict" in text, name
    research = (root / "research" / "SKILL.md").read_text()
    for section in (
        "Scene setting",
        "Recent catalysts",
        "Upcoming events",
        "Commercial",
        "Policy",
        "Macro",
        "Ownership",
        "Thesis and kill criteria",
        "Verdict",
    ):
        assert section in research, section
    assert "30 days" in research and "catalyst_score" in research and "kill_criteria" in research


def test_error_queue_latest_run_and_outcome(
    db_backend: Path | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    @dataclass(frozen=True)
    class Scored:
        date: pd.Timestamp
        trend: float

    run_id = store.start_run("scan", {}, path=db_backend)
    for ticker, trend, label in (
        ("A.L", 0.1, "ACT"),
        ("B.L", 0.9, "ACT"),
        ("C.L", 0.5, "ACT"),
        ("W.L", 0.7, "WATCH"),
    ):
        store.save_ticker(run_id, ticker, {"technical": Scored(DATE, trend)}, path=db_backend)
        store.save_label(run_id, ticker, "screen", label, "", path=db_backend)
    args = _args(db_backend)
    record.main(["latest-run", *args])
    assert capsys.readouterr().out.strip() == str(run_id)
    with pytest.raises(SystemExit, match="no daily run"):
        record.main(["latest-run", "--script", "daily", *args])

    # validate queue: screen ACT by rank, capped
    record.main(["queue", "--run", str(run_id), "--stage", "validate", *args])
    assert capsys.readouterr().out.split() == ["B.L", "C.L", "A.L"]
    record.main(["queue", "--run", str(run_id), "--stage", "validate", "--cap", "2", *args])
    assert capsys.readouterr().out.split() == ["B.L", "C.L"]

    # the runner records an ERROR for a failed skill; it drops out of the queue
    record.main(
        [
            "error",
            "B.L",
            "--run",
            str(run_id),
            "--stage",
            "validate",
            "--reason",
            "turn limit",
            *args,
        ]
    )
    assert "recorded validate ERROR for B.L" in capsys.readouterr().out
    labels = store.load_labels(run_id, "B.L", "validate", path=db_backend)
    assert labels.iloc[0]["label"] == "ERROR" and labels.iloc[0]["actor"] == "agent:validate"
    assert store.load_cards(run_id, "B.L", path=db_backend)["validate"]["reasons"] == "turn limit"
    record.main(["queue", "--run", str(run_id), "--stage", "validate", *args])
    assert capsys.readouterr().out.split() == ["C.L", "A.L"]

    # research queue: final label ACT (B.L is ERROR after validate, so out), ranked; once the
    # validator has worked the run, names it has not reached (C.L, A.L) are not researched
    record.main(["queue", "--run", str(run_id), "--stage", "research", *args])
    assert capsys.readouterr().out.split() == []

    # outcome: exit 1 only when every item errored
    with pytest.raises(SystemExit) as e:
        record.main(["outcome", "--run", str(run_id), "--stage", "validate", *args])
    assert e.value.code == 1 and "1 recorded, 1 ERROR" in capsys.readouterr().out
    record.main(
        ["error", "C.L", "--run", str(run_id), "--stage", "validate", "--reason", "x", *args]
    )
    v = _verdict(
        run_id=run_id, ticker="A.L", stage="validate", label="ACT", evidence=[], kill_criteria=[]
    )
    record.main(["verdict", _write(tmp_path, v), *args])
    capsys.readouterr()
    record.main(["queue", "--run", str(run_id), "--stage", "research", *args])
    assert capsys.readouterr().out.split() == ["A.L"]  # the one the validator kept
    record.main(
        ["outcome", "--run", str(run_id), "--stage", "validate", *args]
    )  # 3 recorded, 2 errors: ok
    assert "3 recorded, 2 ERROR" in capsys.readouterr().out
    record.main(
        ["outcome", "--run", str(run_id), "--stage", "research", *args]
    )  # nothing recorded: ok
    assert "0 recorded" in capsys.readouterr().out

    # --if-missing (the runner after every agent): no-op when a verdict exists, else ERROR
    def if_missing(ticker: str) -> str:
        record.main(
            [
                *("error", ticker, "--run", str(run_id), "--stage", "events", "--if-missing"),
                *("--reason", "the agent ended without recording a verdict", *args),
            ]
        )
        return capsys.readouterr().out

    assert "recorded events ERROR for C.L" in if_missing("C.L")
    assert "already recorded for C.L" in if_missing("C.L")


def test_max_agent_tickers_rule() -> None:
    from solaraalpha.portfolio.rules import load_rules

    assert load_rules().max_agent_tickers == 10


def test_trade_levels_required_on_a_research_act_and_validated() -> None:
    v = _verdict(run_id=1)
    trade = v.pop("trade")
    with pytest.raises(ValueError, match="research ACT must give `trade`"):
        agents.Verdict.model_validate(v)
    agents.Verdict.model_validate({**v, "label": "WATCH"})  # only an ACT needs levels
    agents.Verdict.model_validate({**v, "stage": "validate"})
    with pytest.raises(ValueError, match="must be below the entry"):
        agents.Verdict.model_validate({**v, "trade": {**trade, "stop": 1600.0}})
    with pytest.raises(ValueError, match="stop_reason"):
        agents.Verdict.model_validate({**v, "trade": {**trade, "stop_reason": ""}})


def test_latest_run_is_the_latest_full_weekly_scan(
    db_backend: Path | None, capsys: pytest.CaptureFixture[str]
) -> None:
    full = store.start_run("scan", {"tickers": [], "toolkit": None}, path=db_backend)
    store.start_run("scan", {"tickers": ["JET2.L"], "toolkit": None}, path=db_backend)  # one stock
    args = _args(db_backend)
    record.main(["latest-run", *args])
    assert capsys.readouterr().out.strip() == str(full)
    started = str(store.run_info(full, path=db_backend)["started_at"])
    record.main(["latest-run", "--since", started, *args])
    assert capsys.readouterr().out.strip() == str(full)
    with pytest.raises(SystemExit) as e:  # the pipeline run that just finished saved no full scan
        record.main(["latest-run", "--since", "2999-01-01T00:00:00Z", *args])
    assert e.value.code == 3


def test_research_is_bound_by_the_screen_when_the_validator_errored(
    db_backend: Path | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _run(db_backend, label="WATCH")
    store.save_label(run_id, "JET2.L", "validate", "ERROR", "pack failed", path=db_backend)
    with pytest.raises(SystemExit) as e:
        record.main(["verdict", _write(tmp_path, _verdict(run_id=run_id)), *_args(db_backend)])
    assert e.value.code == 2
    assert "research may not upgrade JET2.L from WATCH to ACT" in capsys.readouterr().out
