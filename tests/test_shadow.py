from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from solaraalpha import store
from solaraalpha.agents import BOOK
from solaraalpha.portfolio import book, shadow

DATE = pd.Timestamp("2026-10-09", tz="UTC")  # the run's card Friday


@dataclass(frozen=True)
class Tech:
    date: pd.Timestamp
    trend: float


@dataclass(frozen=True)
class Lv:
    date: pd.Timestamp
    stop: float


@dataclass(frozen=True)
class Research:
    date: pd.Timestamp
    label: str
    trade_entry_type: str
    trade_entry: float
    trade_stop: float
    trade_stop_reason: str


@dataclass(frozen=True)
class PM:
    date: pd.Timestamp
    label: str
    allocation: str
    rotations: str


def _daily(spike: bool = True) -> pd.DataFrame:
    """Closes alternating 1000 / 1001 with high / low 10 either side: every true range is 20,
    so daily ATR(14) = 20 and the 4-week high = 1011. ``spike``: a session after the card
    Friday with a high of 2000, which the rules must never read."""
    idx = pd.bdate_range(end="2026-10-09", periods=120, tz="UTC", name="date")
    close = 1000.0 + np.arange(len(idx)) % 2
    df = pd.DataFrame(
        {"open": close, "high": close + 10, "low": close - 10, "close": close},
        index=idx,
    )
    df["adj_close"], df["volume"] = close, 20_000.0
    if spike:
        later = pd.DataFrame(
            {"open": 1001.0, "high": 2000.0, "low": 990.0, "close": 1001.0},
            index=pd.DatetimeIndex([pd.Timestamp("2026-10-12", tz="UTC")], name="date"),
        )
        later["adj_close"], later["volume"] = 1001.0, 20_000.0
        df = pd.concat([df, later])
    df.attrs = {"ticker": "X", "currency": "GBp"}
    return df


TICKERS = [f"T{i}.L" for i in range(1, 8)]  # ranked 1..7; the top 3 are the shortlist


def _scan(db: Path | None, args: dict | None = None) -> int:
    run = store.start_run("scan", args if args is not None else {"tickers": []}, path=db)
    for i, t in enumerate(TICKERS, 1):
        daily = _daily()
        daily.attrs["ticker"] = t
        stop = 1005.0 if t == "T1.L" else 900.0  # T1's levels stop sits above the agent entry
        store.save_ticker(
            run, t, {"technical": Tech(DATE, 0.3), "levels": Lv(DATE, stop)}, daily, path=db
        )
        label, why = ("ACT", f"rank {i}") if i <= 3 else ("WATCH", None)
        store.save_label(run, t, "screen", label, "r", path=db, rank=i, shortlist=why)
    return run


def _research(db: Path | None, run: int, pm: bool) -> None:
    for t, label in (("T1.L", "ACT"), ("T2.L", "ACT"), ("T3.L", "PASS")):
        card = Research(DATE, label, "limit", 1000.0, 950.0, "below the base")
        store.save_ticker(run, t, {"research": card}, path=db)
        store.save_label(run, t, "research", label, "r", path=db, confidence=0.7)
    if pm:
        alloc = json.dumps([{"ticker": "T2.L", "risk_pct": 0.03, "reason": "best catalyst"}])
        store.save_ticker(run, BOOK, {"pm": PM(DATE, "ACT", alloc, "[]")}, path=db)


def test_screen_baselines_are_recorded_once_from_the_run(db_backend: Path | None) -> None:
    run = _scan(db_backend)
    conn = book.connect(db_backend)
    try:
        got = shadow.record(conn, run)
        assert got == {"top5_rank": 5, "random2_shortlist": 2}
        df = shadow.load(conn, run)
        top = df[df["baseline"] == "top5_rank"]
        assert top["ticker"].tolist() == TICKERS[:5] and top["priority"].tolist() == [1, 2, 3, 4, 5]
        row = top.iloc[0]
        # buy-stop 0.1 ATR above the 4-week high, stop 2 ATR under it: the spike after the card
        # Friday (high 2000) is never read
        assert row["entry_type"] == "stop" and row["entry"] == pytest.approx(1011 + 2)
        assert row["stop"] == pytest.approx(1013 - 40)
        assert row["inputs"] == {
            "atr": pytest.approx(20),
            "high_4w": 1011.0,
            "levels_stop": 1005.0,
            "rank": 1,
        }
        assert row["risk_pct"] == 0.02 and row["trail"] == "chandelier_3atr"
        assert row["valid_days"] == 7 and row["card_date"] == "2026-10-09"
        assert row["spec"] == "0028" and row["git_sha"] and row["note"] == ""
        rnd = df[df["baseline"] == "random2_shortlist"]
        want = random.Random(run).sample(sorted(TICKERS[:3]), 2)  # seeded by run_id
        assert rnd.sort_values("priority")["ticker"].tolist() == want
        # a re-run (or a plan.py before research) writes nothing new: the first decision stands
        assert shadow.record(conn, run) == {}
        assert len(shadow.load(conn, run)) == 7
    finally:
        conn.close()


def test_agent_baselines_follow_research_and_the_pm(db_backend: Path | None) -> None:
    run = _scan(db_backend)
    conn = book.connect(db_backend)
    try:
        shadow.record(conn, run)  # scan time: screen baselines only
        _research(db_backend, run, pm=True)
        got = shadow.record(conn, run)  # plan time
        assert got == {
            "agent": 1,
            "flat2_rank": 2,
            "entry_breakout": 2,
            "stop_2atr": 2,
            "stop_levels": 2,
            "trail_chandelier": 2,
        }
        df = shadow.load(conn, run)
        row: dict[tuple[str, str], dict[Any, Any]] = {
            (str(r["baseline"]), str(r["ticker"])): r for r in df.to_dict("records")
        }
        agent = row[("agent", "T2.L")]  # the PM's allocation, at its risk
        assert (agent["entry_type"], agent["entry"], agent["stop"]) == ("limit", 1000.0, 950.0)
        assert agent["risk_pct"] == 0.03 and agent["trail"] == "agent"
        flat = df[df["baseline"] == "flat2_rank"].sort_values("priority")
        assert flat["ticker"].tolist() == ["T1.L", "T2.L"]  # every research ACT, rank order
        assert (flat["risk_pct"] == 0.02).all()
        assert row[("entry_breakout", "T1.L")]["entry"] == pytest.approx(1013)
        assert row[("entry_breakout", "T1.L")]["stop"] == 950.0  # the agent's stop
        assert row[("stop_2atr", "T1.L")]["stop"] == pytest.approx(960)  # entry - 2 ATR
        assert row[("stop_levels", "T2.L")]["stop"] == 900.0
        skipped = row[("stop_levels", "T1.L")]  # levels stop above the entry: kept, unpriced
        assert pd.isna(skipped["entry"]) and pd.isna(skipped["stop"])
        assert "at or above entry" in skipped["note"]
        trail = row[("trail_chandelier", "T2.L")]
        assert trail["trail"] == "chandelier_3atr" and trail["stop"] == 950.0
        assert shadow.record(conn, run) == {}  # re-plans write nothing
    finally:
        conn.close()


def test_without_a_pm_the_agent_arm_is_the_planner_fallback(db_backend: Path | None) -> None:
    run = _scan(db_backend)
    _research(db_backend, run, pm=False)
    conn = book.connect(db_backend)
    try:
        shadow.record(conn, run)
        agent = shadow.load(conn, run).query("baseline == 'agent'").sort_values("priority")
        assert agent["ticker"].tolist() == ["T1.L", "T2.L"] and (agent["risk_pct"] == 0.02).all()
        assert agent["inputs"].iloc[0]["pm"] is False
    finally:
        conn.close()


def test_only_full_weekly_scans_are_recorded(db_backend: Path | None) -> None:
    run = _scan(db_backend, {"tickers": ["T1.L"], "toolkit": None})
    conn = book.connect(db_backend)
    try:
        assert shadow.record(conn, run) == {} and shadow.load(conn).empty
    finally:
        conn.close()


def test_inputs_use_only_data_up_to_the_card_date(db_backend: Path | None) -> None:
    run = _scan(db_backend)
    conn = book.connect(db_backend)
    try:
        started = str(store.run_info(run, conn=conn)["started_at"])
        x = shadow.inputs_for(conn, run, "T3.L", started)
        assert x.card_date == DATE and x.high_4w == 1011.0 and x.atr == pytest.approx(20)
    finally:
        conn.close()
    # the same as on a snapshot that stops at the card Friday (truncated series)
    run2 = store.start_run("scan", {"tickers": []}, path=db_backend)
    daily = _daily(spike=False)
    daily.attrs["ticker"] = "C.L"
    store.save_ticker(run2, "C.L", {"technical": Tech(DATE, 0.3)}, daily, path=db_backend)
    conn = book.connect(db_backend)
    try:
        started = str(store.run_info(run2, conn=conn)["started_at"])
        cut = shadow.inputs_for(conn, run2, "C.L", started)
        assert (cut.atr, cut.high_4w) == (x.atr, x.high_4w) and np.isnan(cut.levels_stop)
    finally:
        conn.close()
