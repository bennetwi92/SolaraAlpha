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
