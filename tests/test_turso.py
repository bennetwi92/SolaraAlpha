from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fakes import FakeTurso, _json_roundtrip
from solaraalpha import store, turso
from solaraalpha.config import Settings, settings
from solaraalpha.data.prices import parse_chart

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_chart_jet2.json"


@dataclass(frozen=True)
class Card:
    date: pd.Timestamp
    score: float
    missing: float
    target: float
    currency: str


CARD = Card(pd.Timestamp("2026-10-09", tz="UTC"), 0.42, math.nan, math.inf, "GBP")


@pytest.fixture
def fake(fake_turso: FakeTurso) -> FakeTurso:
    return fake_turso


def test_encode_decode_roundtrip() -> None:
    for v in [None, 0, -7, np.int64(3), True, 1.5, np.float64(2.25), "GBP", b"\x00\x01"]:
        enc = _json_roundtrip(turso._encode(v))
        assert turso._decode(enc) == (int(v) if isinstance(v, bool) else v)
    assert turso._decode(turso._encode(math.nan)) is None  # NaN stored as NULL, like sqlite3
    for v in (math.inf, -math.inf):
        assert turso._decode(_json_roundtrip(turso._encode(v))) == v


def test_store_uses_turso_when_configured(fake: FakeTurso, tmp_path: Path) -> None:
    daily = parse_chart(json.loads(FIXTURE.read_text()))
    steps = [("screen", "technical", "pass", "")]
    run_id = store.save_run("scan", {}, "JET2.L", {"technical": CARD}, daily=daily, steps=steps)
    assert fake.posts > 0
    assert not (tmp_path / "solara.db").exists()

    res = store.load_results("JET2.L", "technical").set_index("field")
    assert res.loc["score", "value"] == 0.42
    assert res.loc["target", "value"] == math.inf
    assert pd.isna(res.loc["missing", "value"])
    assert res.loc["currency", "text"] == "GBP"
    assert store.latest("technical").loc["JET2.L", "run_id"] == run_id
    assert list(store.load_steps("JET2.L")["status"]) == ["pass"]

    pd.testing.assert_frame_equal(store.load_prices("JET2.L"), store_local_prices(daily, tmp_path))
    store.save_run("stock_prices", {}, "JET2.L", daily=daily)  # unchanged: nothing new stored
    assert fake.db.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == len(daily)


def store_local_prices(daily: pd.DataFrame, tmp_path: Path) -> pd.DataFrame:
    db = tmp_path / "local.db"
    store.save_run("stock_prices", {}, "JET2.L", daily=daily, path=db)
    return store.load_prices("JET2.L", path=db)


def test_failed_transaction_rolls_back(fake: FakeTurso) -> None:
    conn = store.connect()
    with pytest.raises(turso.TursoError), conn:
        conn.execute("INSERT INTO runs VALUES (1, 't', 's', '{}', 'sha')")
        conn.execute("INSERT INTO nope VALUES (1)")
    assert fake.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_settings_strip_pasted_quotes() -> None:
    s = Settings(turso_url=' "libsql://x.turso.io" ', turso_token="'tok'")
    assert s.turso_url == "libsql://x.turso.io"
    assert s.turso_token.get_secret_value() == "tok"


@pytest.mark.network
@pytest.mark.skipif(not os.environ.get("SOLARA_TURSO_URL"), reason="Turso not configured")
def test_live_turso_select() -> None:
    conn = turso.Connection(settings.turso_url, settings.turso_token.get_secret_value())
    assert conn.execute("SELECT ?, ?", (1, math.inf)).fetchall() == [(1, math.inf)]
    conn.close()


def test_nested_with_blocks_share_one_transaction(fake: FakeTurso) -> None:
    conn = store.connect()
    with conn:
        conn.execute("INSERT INTO runs VALUES (1, 't', 's', '{}', 'sha')")
        with conn:  # no second BEGIN
            conn.execute("INSERT INTO runs VALUES (2, 't', 's', '{}', 'sha')")
        assert fake.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2  # still open
    assert fake.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
    with pytest.raises(turso.TursoError), conn:
        conn.execute("INSERT INTO runs VALUES (3, 't', 's', '{}', 'sha')")
        with conn:
            conn.execute("INSERT INTO nope VALUES (1)")
    assert fake.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2  # outer rolled back


def test_commit_and_rollback_outside_a_transaction_are_no_ops(fake: FakeTurso) -> None:
    # as in sqlite3: scripts/execute.py ends every command with conn.commit() after its writes
    # were committed by their own `with conn:` blocks (Execution runs 1-3 exited 2 on Turso)
    conn = store.connect()
    with conn:
        conn.execute("INSERT INTO runs VALUES (1, 't', 's', '{}', 'sha')")
    conn.commit()
    conn.rollback()
    assert fake.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
