from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from solaraalpha import agents, store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import record

FIXTURE = Path(__file__).parent / "fixtures" / "verdict_macro.json"


def test_macro_stage_and_pseudo_ticker(
    db_backend: Path | None, capsys: pytest.CaptureFixture[str]
) -> None:
    v = agents.load_verdict(FIXTURE.read_text())
    assert v.stage == "macro" and v.ticker.startswith(agents.SECTOR_PREFIX)
    assert v.scores["macro_risk"] == 0.4  # a _risk score may sit in [0, 1]
    run = store.start_run("scan", {}, path=db_backend)
    for t, lab in (("JET2.L", "ACT"), ("B.L", "ACT"), ("C.L", "WATCH"), ("D.L", "ACT")):
        store.save_label(run, t, "screen", lab, "", path=db_backend)
    dbargs = ["--db", str(db_backend)] if db_backend else []
    conn = store.connect(db_backend)
    # groups: JET2.L has sector Travel & Leisure in config; the others are unknown
    assert agents.queue(conn, run, "macro", 10) == ["_SECTOR:unknown", "_SECTOR:Travel & Leisure"]
    assert agents.queue(conn, run, "macro", 1) == ["_SECTOR:unknown"]
    assert agents.sector_members(conn, run, "_SECTOR:Travel & Leisure") == ["JET2.L"]
    assert agents.sector_members(conn, run, "_SECTOR:unknown") == ["B.L", "D.L"]  # C.L is WATCH
    conn.close()
    record.main(["verdict", str(FIXTURE), "--run", str(run), *dbargs])
    out = capsys.readouterr().out
    assert f"recorded macro WATCH (0.60) for _SECTOR:Travel & Leisure in run {run}" in out
    assert "labels row written" not in out  # card-only stage, no downgrade rule
    cards = store.load_cards(run, "_SECTOR:Travel & Leisure", path=db_backend)
    assert cards["macro"]["label"] == "WATCH" and cards["macro"]["macro"] == 0.3
    assert json.loads(cards["macro"]["checks"])["members"] == "JET2.L"
    conn = store.connect(db_backend)
    assert agents.queue(conn, run, "macro", 10) == ["_SECTOR:unknown"]  # done groups drop out
    assert agents.outcome(conn, run, "macro") == {"total": 1, "errors": 0}
    p = agents.prior(conn, "_SECTOR:Travel & Leisure", "macro")
    assert len(p["verdicts"]) == 1 and p["verdicts"][0]["evidence"][0]["driver"] == "fuel"
    conn.close()
    record.main(["members", "--run", str(run), "--sector", "_SECTOR:unknown", *dbargs])
    assert capsys.readouterr().out.split() == ["B.L", "D.L"]
    record.main(
        [
            "error",
            "_SECTOR:unknown",
            "--run",
            str(run),
            "--stage",
            "macro",
            "--reason",
            "turn limit",
            *dbargs,
        ]
    )
    conn = store.connect(db_backend)
    assert agents.outcome(conn, run, "macro") == {"total": 2, "errors": 1}
    assert agents.queue(conn, run, "macro", 10) == []
    conn.close()
    with pytest.raises(SystemExit) as e:  # one note per group per run
        record.main(["verdict", str(FIXTURE), "--run", str(run), *dbargs])
    assert e.value.code == 2


def test_skills_reference_the_macro_note() -> None:
    root = Path(__file__).resolve().parents[1] / ".claude" / "skills"
    macro = (root / "macro" / "SKILL.md").read_text()
    assert "record.py schema" in macro and "record.py verdict" in macro and "_SECTOR:" in macro
    assert 'stage: "macro"' in macro and "queue --run <N> --stage macro" in macro
    research = (root / "research" / "SKILL.md").read_text()
    assert "--stage macro" in research and "sections 5 and 6 reference it" in research
