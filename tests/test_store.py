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
def db(db_backend: Path | None) -> Path | None:
    """Runs every test on both backends (a tmp_path file and the fake Turso)."""
    return db_backend


@pytest.fixture
def daily() -> pd.DataFrame:
    return parse_chart(json.loads(FIXTURE.read_text()))


def _set_run_time(db: Path | None, run_id: int, ts: str) -> None:
    conn = store.connect(db)
    with conn:
        conn.execute("UPDATE runs SET started_at = ? WHERE run_id = ?", (ts, run_id))
    conn.close()


def _count(db: Path | None, table: str) -> int:
    conn = store.connect(db)
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return int(n)


def test_save_run_records_run(db: Path | None) -> None:
    run_id = store.save_run("technical", {"ticker": "JET2.L", "short": False}, "JET2.L", path=db)
    conn = store.connect(db)
    q = "SELECT script, args, git_sha, started_at FROM runs WHERE run_id = ?"
    script, args, sha, started = conn.execute(q, (run_id,)).fetchone()
    conn.close()
    assert script == "technical"
    assert json.loads(args) == {"ticker": "JET2.L", "short": False}
    assert sha
    assert pd.Timestamp(started).tz is not None


def test_card_round_trip(db: Path | None) -> None:
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


def test_latest_one_row_per_ticker(db: Path | None) -> None:
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


def test_price_snapshot_stores_only_changes(db: Path | None, daily: pd.DataFrame) -> None:
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


def test_price_snapshot_keeps_last_of_duplicate_dates(db: Path | None, daily: pd.DataFrame) -> None:
    last = daily.iloc[[-1]].copy()
    last["close"] += 1.0
    doubled = pd.concat([daily, last])
    doubled.attrs = daily.attrs
    store.save_run("stock_prices", {}, "JET2.L", daily=doubled, path=db)
    assert _count(db, "prices") == len(daily)
    assert store.load_prices("JET2.L", path=db)["close"].iloc[-1] == last["close"].iloc[0]


def test_price_snapshot_once_per_run(db: Path | None, daily: pd.DataFrame) -> None:
    # a watchlist stock that is also another's peer, refetched minutes later with a moved bar
    run_id = store.start_run("scan", {}, path=db)
    store.save_ticker(run_id, "JET2.L", daily=daily, path=db)
    moved = daily.copy()
    moved.loc[moved.index[-1], "close"] += 5.0
    moved.attrs = daily.attrs
    store.save_ticker(run_id, "OTHER.L", others={"JET2.L": moved}, path=db)
    assert _count(db, "prices") == len(daily)
    assert store.load_prices("JET2.L", path=db)["close"].iloc[-1] == daily["close"].iloc[-1]


def test_fundamentals_snapshot(db: Path | None) -> None:
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


def test_float_jitter_is_not_a_revision(db: Path | None, daily: pd.DataFrame) -> None:
    store.save_run("stock_prices", {}, "JET2.L", daily=daily, path=db)
    jittered = daily.copy()
    jittered["adj_close"] = jittered["adj_close"] + 1.2e-4  # one float32 ulp at ~1300p
    store.save_run("stock_prices", {}, "JET2.L", daily=jittered, path=db)
    assert _count(db, "prices") == len(daily)


def test_steps_round_trip(db: Path | None) -> None:
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


def test_one_run_covers_many_tickers(db: Path | None, daily: pd.DataFrame) -> None:
    run_id = store.start_run("scan", {"save": True}, path=db)
    for ticker in ("JET2.L", "EZJ.L"):
        steps = [("screen", "technical", "pass", "")]
        store.save_ticker(run_id, ticker, {"technical": CARD}, daily, path=db, steps=steps)
    assert _count(db, "runs") == 1
    assert set(store.load_steps(path=db)["ticker"]) == {"JET2.L", "EZJ.L"}
    assert set(store.latest("technical", path=db)["run_id"]) == {run_id}
    assert _count(db, "prices") == 2 * len(daily)


def test_old_steps_layout_is_migrated(tmp_path: Path) -> None:
    db = tmp_path / "solara.db"
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


def test_labels_round_trip(db: Path | None) -> None:
    run_id = store.start_run("scan", {}, path=db)
    store.save_label(run_id, "JET2.L", "screen", "ACT", "technical ACT; levels ACT", path=db)
    store.save_label(run_id, "EZJ.L", "screen", "WATCH", "levels WATCH: failed ...", path=db)
    store.save_label(
        run_id, "JET2.L", "research", "WATCH", "event in 3 days", 0.7, "agent:research", path=db
    )
    got = store.load_labels(run_id=run_id, path=db)
    assert list(zip(got["ticker"], got["stage"], got["label"], strict=True)) == [
        ("JET2.L", "screen", "ACT"),
        ("EZJ.L", "screen", "WATCH"),
        ("JET2.L", "research", "WATCH"),
    ]
    assert got["confidence"].tolist()[2] == 0.7 and got["actor"].tolist()[2] == "agent:research"
    assert "started_at" in got
    assert store.shortlist(run_id, path=db) == ["JET2.L"]
    assert store.shortlist(run_id, "research", ("WATCH", "ACT"), path=db) == ["JET2.L"]
    assert store.load_labels(ticker="EZJ.L", path=db)["label"].tolist() == ["WATCH"]
    assert got["watch_on"].tolist() == [None, None, None] and got["rank"].isna().all()
    with pytest.raises(ValueError, match="label must be"):
        store.save_label(run_id, "X.L", "screen", "MAYBE", path=db)
    with pytest.raises(Exception, match=r"(?i)unique|constraint"):
        store.save_label(run_id, "JET2.L", "screen", "PASS", path=db)  # one per run/ticker/stage


def test_labels_watch_on_and_rank(db: Path | None) -> None:
    run_id = store.start_run("scan", {}, path=db)
    store.save_label(run_id, "C.L", "screen", "ACT", "", path=db, rank=3)
    store.save_label(run_id, "A.L", "screen", "ACT", "", path=db, rank=1)
    store.save_label(run_id, "U.L", "screen", "ACT", "", path=db)  # unranked: last
    store.save_label(run_id, "B.L", "screen", "ACT", "", path=db, rank=2)
    store.save_label(
        run_id,
        "W.L",
        "screen",
        "WATCH",
        "technical WATCH: extended",
        path=db,
        watch_on=[{"toolkit": "technical", "kind": "pullback", "level": 1480.5, "entry": -0.4}],
    )
    assert store.shortlist(run_id, path=db) == ["A.L", "B.L", "C.L", "U.L"]
    got = store.load_labels(run_id=run_id, path=db).set_index("ticker")
    assert got.loc["W.L", "watch_on"] == [
        {"toolkit": "technical", "kind": "pullback", "level": 1480.5, "entry": -0.4}
    ]
    assert got.loc["A.L", "rank"] == 1 and got.loc["W.L", "watch_on"] is not None
    assert got.loc["U.L", "watch_on"] is None
    # the migration is idempotent: connecting again adds nothing and loses nothing
    conn = store.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(labels)")]
    assert cols.count("watch_on") == 1 and cols.count("rank") == 1
    assert cols.count("shortlist") == 1
    conn.close()
    assert store.shortlist(run_id, path=db) == ["A.L", "B.L", "C.L", "U.L"]


def test_labels_shortlist_reason(db: Path | None) -> None:
    run_id = store.start_run("scan", {}, path=db)
    store.save_label(run_id, "A.L", "screen", "ACT", "", path=db, rank=1, shortlist="rank 1")
    store.save_label(
        run_id, "K.L", "screen", "ACT", "", path=db, rank=14, shortlist="catalyst: contract win"
    )
    store.save_label(run_id, "W.L", "screen", "WATCH", "rank 2, not shortlisted", path=db, rank=2)
    got = store.load_labels(run_id=run_id, path=db).set_index("ticker")
    assert got.loc["A.L", "shortlist"] == "rank 1"
    assert got.loc["K.L", "shortlist"] == "catalyst: contract win"
    assert pd.isna(got.loc["W.L", "shortlist"]) and got.loc["W.L", "rank"] == 2
    # the shortlist is the screen ACT rows by rank; a ranked WATCH row is not on it
    assert store.shortlist(run_id, path=db) == ["A.L", "K.L"]


def test_labels_migration_from_the_old_layout(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        "CREATE TABLE runs (run_id INTEGER PRIMARY KEY, started_at TEXT, script TEXT, git_sha TEXT, args TEXT);"
        "CREATE TABLE labels (run_id INTEGER NOT NULL, ticker TEXT NOT NULL, stage TEXT NOT NULL,"
        " label TEXT NOT NULL, confidence REAL, reason TEXT NOT NULL, actor TEXT NOT NULL,"
        " PRIMARY KEY (run_id, ticker, stage));"
        "INSERT INTO runs VALUES (1, '2026-10-01T00:00:00+00:00', 'scan', 'x', '{}');"
        "INSERT INTO labels VALUES (1, 'OLD.L', 'screen', 'ACT', NULL, 'r', 'pipeline');"
    )
    raw.commit()
    raw.close()
    conn = store.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(labels)")]
    assert cols[-3:] == ["watch_on", "rank", "shortlist"]
    conn.close()
    got = store.load_labels(run_id=1, path=db)
    assert got["ticker"].tolist() == ["OLD.L"] and got["watch_on"].tolist() == [None]
    store.save_label(1, "NEW.L", "screen", "ACT", "", path=db, rank=1)
    assert store.shortlist(1, path=db) == ["NEW.L", "OLD.L"]


def test_a_snapshot_compares_with_the_latest_version_only(
    db: Path | None, daily: pd.DataFrame
) -> None:
    store.save_run("stock_prices", {}, "JET2.L", daily=daily, path=db)
    revised = daily.copy()
    revised["adj_close"] = daily["adj_close"] + 1.0
    revised.attrs = daily.attrs
    store.save_run("stock_prices", {}, "JET2.L", daily=revised, path=db)
    store.save_run("stock_prices", {}, "JET2.L", daily=daily, path=db)  # back again: new rows
    assert _count(db, "prices") == 3 * len(daily)
    conn = store.connect(db)
    latest = store._read_latest(conn, "prices", "JET2.L", ["date"])
    conn.close()
    assert len(latest) == len(daily) and set(latest["run_id"]) == {3}
