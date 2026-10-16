from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from fakes import FakeTurso
from solaraalpha import store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import db


@dataclass(frozen=True)
class Card:
    date: pd.Timestamp
    score: float
    target: float


CARD = Card(pd.Timestamp("2026-10-09", tz="UTC"), 0.42, math.inf)


def _local(tmp_path: Path, runs: int = 2) -> Path:
    path = tmp_path / "local.db"
    for i in range(runs):
        steps = [("screen", "technical", "pass", f"run {i}")]
        store.save_run("scan", {"i": i}, "JET2.L", {"technical": CARD}, path=path, steps=steps)
    return path


def test_status_names_backend_and_counts(
    fake_turso: FakeTurso, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store.save_run("scan", {}, "JET2.L", {"technical": CARD})
    db.main(["status"])
    out = capsys.readouterr().out
    assert "Turso libsql://fake.turso.io" in out
    assert "runs" in out and "results" in out


def test_push_copies_everything_then_refuses(
    fake_turso: FakeTurso, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    local = _local(tmp_path)
    db.main(["push", "--from", str(local)])
    conn_local, conn_turso = store.connect(local), store.connect()
    assert store.counts(conn_turso) == store.counts(conn_local)
    assert conn_turso.execute("SELECT run_id FROM runs").fetchall() == [(1,), (2,)]
    conn_local.close()
    conn_turso.close()
    res = store.load_results("JET2.L", "technical")  # Turso
    assert list(res[res["field"] == "target"]["value"]) == [math.inf, math.inf]

    with pytest.raises(SystemExit, match="refused: destination already holds 2 runs"):
        db.main(["push", "--from", str(local)])


def test_push_append_new_runs_remaps_ids(fake_turso: FakeTurso, tmp_path: Path) -> None:
    local = _local(tmp_path, runs=2)
    db.main(["push", "--from", str(local)])
    store.save_run("technical", {}, "EZJ.L", {"technical": CARD})  # Turso-only run 3
    store.save_run(
        "scan", {"i": 9}, "JET2.L", {"technical": CARD}, path=local, steps=[("s", "t", "pass", "")]
    )  # local-only run 3

    db.main(["push", "--from", str(local), "--append-new-runs"])
    turso = store.connect()
    runs = turso.execute("SELECT run_id, script, args FROM runs ORDER BY run_id").fetchall()
    assert runs == [
        (1, "scan", '{"i": 0}'),
        (2, "scan", '{"i": 1}'),
        (3, "technical", "{}"),
        (4, "scan", '{"i": 9}'),
    ]
    assert turso.execute("SELECT run_id, reason FROM steps WHERE run_id = 4").fetchall() == [
        (4, "")
    ]
    assert turso.execute("SELECT COUNT(*) FROM results WHERE run_id = 4").fetchone()[0] == 2
    turso.close()

    db.main(["push", "--from", str(local), "--append-new-runs"])  # idempotent
    turso = store.connect()
    assert turso.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 4
    turso.close()


def test_pull_into_new_file_only(fake_turso: FakeTurso, tmp_path: Path) -> None:
    store.save_run("scan", {}, "JET2.L", {"technical": CARD}, steps=[("s", "t", "pass", "")])
    target = tmp_path / "pulled.db"
    db.main(["pull", "--to", str(target)])
    conn = store.connect(target)
    assert store.counts(conn) == {
        "runs": 1,
        "results": 2,
        "prices": 0,
        "fundamentals": 0,
        "evidence": 0,
        "labels": 0,
        "steps": 1,
    }
    conn.close()
    assert store.load_results("JET2.L", path=target).loc[0, "run_id"] == 1

    with pytest.raises(SystemExit, match="exists"):
        db.main(["pull", "--to", str(target)])


def test_push_and_pull_need_turso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from solaraalpha.config import settings

    monkeypatch.setattr(settings, "turso_url", "")
    with pytest.raises(SystemExit, match="Turso not configured"):
        db.main(["pull", "--to", str(tmp_path / "x.db")])
