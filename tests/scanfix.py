"""A fake saved scan run shared by the planner, weekly pack and later tests."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from solaraalpha import store
from solaraalpha.portfolio import book

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


def build_scan_db(tmp_path: Path) -> Path:
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
