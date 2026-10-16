from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha import store
from solaraalpha.data.prices import parse_chart

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_chart_jet2.json"


@dataclass(frozen=True)
class Card:
    date: pd.Timestamp
    side: int
    score: float
    missing: float
    target: float
    blue_sky: bool
    currency: str
    stopped_on: pd.Timestamp | None


CARD = Card(
    date=pd.Timestamp("2026-10-09", tz="UTC"),
    side=1,
    score=0.42,
    missing=math.nan,
    target=math.inf,
    blue_sky=True,
    currency="GBP",
    stopped_on=None,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "solara.db"


@pytest.fixture
def daily() -> pd.DataFrame:
    return parse_chart(json.loads(FIXTURE.read_text()))


def _set_run_time(db: Path, run_id: int, ts: str) -> None:
    conn = store.connect(db)
    with conn:
        conn.execute("UPDATE runs SET started_at = ? WHERE run_id = ?", (ts, run_id))
    conn.close()


def _count(db: Path, table: str) -> int:
    conn = store.connect(db)
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return int(n)


def test_save_run_records_run(db: Path) -> None:
    run_id = store.save_run("technical", {"ticker": "JET2.L", "short": False}, "JET2.L", path=db)
    conn = store.connect(db)
    q = "SELECT script, args, git_sha, started_at FROM runs WHERE run_id = ?"
    script, args, sha, started = conn.execute(q, (run_id,)).fetchone()
    conn.close()
    assert script == "technical"
    assert json.loads(args) == {"ticker": "JET2.L", "short": False}
    assert sha
    assert pd.Timestamp(started).tz is not None


def test_card_round_trip(db: Path) -> None:
    store.save_run("technical", {}, "JET2.L", {"technical": CARD}, path=db)
    res = store.load_results("JET2.L", "technical", path=db).set_index("field")
    assert set(res["date"]) == {"2026-10-09"}
    assert res.loc["score", "value"] == 0.42
    assert res.loc["side", "value"] == 1.0
    assert res.loc["blue_sky", "value"] == 1.0
    assert res.loc["target", "value"] == math.inf
    assert pd.isna(res.loc["missing", "value"])
    assert res.loc["currency", "text"] == "GBP"
    assert res.loc["stopped_on"].isna()[["value", "text"]].all()
    assert "date" not in res.index


def test_latest_one_row_per_ticker(db: Path) -> None:
    store.save_run("technical", {}, "JET2.L", {"technical": CARD}, path=db)
    newer = Card(**{**CARD.__dict__, "score": -0.1})
    store.save_run("technical", {}, "JET2.L", {"technical": newer}, path=db)
    store.save_run("technical", {}, "EZJ.L", {"technical": CARD}, path=db)
    wide = store.latest("technical", path=db)
    assert list(wide.index) == ["EZJ.L", "JET2.L"]
    assert wide.loc["JET2.L", "score"] == -0.1
    assert wide.loc["EZJ.L", "currency"] == "GBP"
    assert wide.loc["JET2.L", "date"] == "2026-10-09"
    assert store.latest("levels", path=db).empty


def test_price_snapshot_stores_only_changes(db: Path, daily: pd.DataFrame) -> None:
    first = store.save_run("stock_prices", {}, "JET2.L", daily=daily, path=db)
    assert _count(db, "prices") == len(daily)

    store.save_run("stock_prices", {}, "JET2.L", daily=daily, path=db)
    assert _count(db, "prices") == len(daily)  # identical refetch adds nothing

    revised = daily.copy()
    revised.loc[revised.index[0], "adj_close"] = daily["adj_close"].iloc[0] + 1.0
    extra = revised.iloc[[-1]].copy()
    extra.index = extra.index + pd.Timedelta(days=1)
    revised = pd.concat([revised, extra])
    revised.attrs = daily.attrs
    second = store.save_run("stock_prices", {}, "JET2.L", daily=revised, path=db)
    assert _count(db, "prices") == len(daily) + 2

    _set_run_time(db, first, "2026-10-01T12:00:00+00:00")
    _set_run_time(db, 2, "2026-10-01T13:00:00+00:00")
    _set_run_time(db, second, "2026-10-08T12:00:00+00:00")

    now = store.load_prices("JET2.L", path=db)
    pd.testing.assert_frame_equal(now, revised, check_freq=False, check_index_type=False)
    assert now.attrs == {"ticker": "JET2.L", "currency": "GBp"}

    then = store.load_prices("JET2.L", as_seen="2026-10-01", path=db)
    pd.testing.assert_frame_equal(then, daily, check_freq=False, check_index_type=False)
    assert store.load_prices("JET2.L", as_seen="2026-09-30", path=db).empty


def test_fundamentals_snapshot(db: Path) -> None:
    fund = pd.DataFrame(
        {
            "field": ["annualNetIncome", "trailingPeRatio"],
            "date": pd.to_datetime(["2025-03-31", "2026-06-30"]).tz_localize("UTC"),
            "value": [1.5e8, 9.2],
            "currency": ["GBP", ""],
        }
    )
    store.save_run("fundamentals", {}, "JET2.L", fund=fund, path=db)
    store.save_run("fundamentals", {}, "JET2.L", fund=fund, path=db)
    assert _count(db, "fundamentals") == 2
    out = store.load_fundamentals("JET2.L", path=db)
    pd.testing.assert_frame_equal(out, fund)
    assert out.attrs["ticker"] == "JET2.L"


def test_as_seen_end_of_day() -> None:
    assert store._as_seen("2026-10-01") == "2026-10-01T23:59:59.999999+00:00"
    assert store._as_seen("2026-10-01 09:30") == "2026-10-01T09:30:00+00:00"
    assert store._as_seen(None) is None


def test_float_jitter_is_not_a_revision(db: Path, daily: pd.DataFrame) -> None:
    store.save_run("stock_prices", {}, "JET2.L", daily=daily, path=db)
    jittered = daily.copy()
    jittered["adj_close"] = jittered["adj_close"] + 1.2e-4  # one float32 ulp at ~1300p
    store.save_run("stock_prices", {}, "JET2.L", daily=jittered, path=db)
    assert _count(db, "prices") == len(daily)


def test_steps_round_trip(db: Path) -> None:
    steps = [
        ("screen", "technical", "pass", "trend=0.50"),
        ("screen", "levels", "fail", "failed reward_risk >= 2"),
        ("events", "events", "skip", "out at screen"),
    ]
    run_id = store.save_run("scan", {}, "JET2.L", {"technical": CARD}, path=db, steps=steps)
    got = store.load_steps("JET2.L", path=db)
    assert list(got["run_id"].unique()) == [run_id]
    assert list(zip(got["stage"], got["toolkit"], got["status"], strict=True)) == [
        ("screen", "technical", "pass"),
        ("screen", "levels", "fail"),
        ("events", "events", "skip"),
    ]
    assert store.load_steps("OTHER.L", path=db).empty


def test_one_run_covers_many_tickers(db: Path, daily: pd.DataFrame) -> None:
    run_id = store.start_run("scan", {"save": True}, path=db)
    for ticker in ("JET2.L", "EZJ.L"):
        steps = [("screen", "technical", "pass", "")]
        store.save_ticker(run_id, ticker, {"technical": CARD}, daily, path=db, steps=steps)
    assert _count(db, "runs") == 1
    assert set(store.load_steps(path=db)["ticker"]) == {"JET2.L", "EZJ.L"}
    assert set(store.latest("technical", path=db)["run_id"]) == {run_id}
    assert _count(db, "prices") == 2 * len(daily)


def test_old_steps_layout_is_migrated(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE steps (run_id INTEGER NOT NULL, ticker TEXT NOT NULL, node TEXT NOT NULL,"
        " toolkit TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,"
        " PRIMARY KEY (run_id, ticker, node));"
        "INSERT INTO steps VALUES (99, 'JET2.L', 'levels', 'levels', 'fail', 'rr=1.2');"
    )
    conn.close()
    store.connect(db).close()  # migrates
    run_id = store.start_run("scan", {}, path=db)
    store.save_ticker(run_id, "JET2.L", path=db, steps=[("screen", "levels", "pass", "")])
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT run_id, stage, toolkit, status FROM steps ORDER BY rowid"
    ).fetchall()
    conn.close()
    assert rows == [(99, None, "levels", "fail"), (run_id, "screen", "levels", "pass")]
