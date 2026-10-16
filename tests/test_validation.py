from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import store, validation
from solaraalpha.data.announcements import Issuer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import validate_pack

DATE = pd.Timestamp("2026-10-09", tz="UTC")


@dataclass(frozen=True)
class Tech:
    date: pd.Timestamp
    trend: float
    close: float


@dataclass(frozen=True)
class Events:
    date: pd.Timestamp
    next_event: pd.Timestamp


def _daily(n: int = 300, end: str = "2026-10-09", seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(0.001 + 0.02 * rng.standard_normal(n)))
    idx = pd.bdate_range(end=end, periods=n, tz="UTC", name="date")
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "adj_close": close,
            "volume": 5e4,
        },
        index=idx,
    )
    df.attrs = {"ticker": "JET2.L", "currency": "GBp"}
    return df


def _fund() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "field": ["annualNetIncome", "annualNetIncome", "annualNetIncome", "trailingPeRatio"],
            "date": pd.to_datetime(
                ["2024-03-31", "2025-03-31", "2026-09-30", "2026-09-30"]
            ).tz_localize("UTC"),
            "value": [1.0e8, 1.5e8, 1.8e8, 9.2],
            "currency": ["GBP", "GBP", "GBP", ""],
        }
    )


def _announcements(lei: str, start=None, end=None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "published": pd.to_datetime(
                ["2026-08-01T06:00", "2026-10-01T06:00", "2026-10-13T06:00"]
            ).tz_localize("UTC"),
            "type_code": ["TST", "POS", "TST"],
            "type": ["Trading Statement", "Transaction in own shares", "Trading Statement"],
            "category": ["Financial Results", "Other", "Financial Results"],
            "headline": ["Trading update", "Buyback", "Update after the run"],
            "company": ["Jet2 plc"] * 3,
            "source": ["RNS"] * 3,
            "id": ["NI-1", "NI-2", "NI-3"],
            "url": [
                "https://data.fca.org.uk/1",
                "https://data.fca.org.uk/2",
                "https://data.fca.org.uk/3",
            ],
        }
    )


@pytest.fixture
def db(db_backend: Path | None, monkeypatch: pytest.MonkeyPatch) -> Path | None:
    monkeypatch.setattr(
        validation, "resolve_issuer", lambda t: Issuer(t, "GB00", "LEI-1", "Jet2 plc", "AIM")
    )
    monkeypatch.setattr(validation, "fetch_announcements", _announcements)
    run_id = store.start_run("scan", {}, path=db_backend)
    cards = {
        "technical": Tech(DATE, 0.5, 100.0),
        "events": Events(DATE, DATE + pd.Timedelta(days=20)),
    }
    store.save_ticker(
        run_id,
        "JET2.L",
        cards,
        _daily(),
        _fund(),
        path=db_backend,
        steps=[("screen", "technical", "act", "trend=0.50")],
    )
    store.save_label(run_id, "JET2.L", "screen", "ACT", "technical ACT", path=db_backend)
    store.save_label(run_id, "OTHER.L", "screen", "PASS", "technical PASS", path=db_backend)
    conn = store.connect(db_backend)
    with conn:
        conn.execute(
            "UPDATE runs SET started_at = ? WHERE run_id = ?", ("2026-10-11T18:17:00+00:00", run_id)
        )
        store._save_card(
            conn,
            run_id,
            "JET2.L",
            "research",
            {"date": DATE, "note_md": "SECRET NOTE", "label": "ACT"},
        )
    conn.close()
    # a later run sees more (and revised) prices: the pack for run 1 must not
    later = store.start_run("scan", {}, path=db_backend)
    store.save_ticker(
        later,
        "JET2.L",
        {"technical": Tech(DATE, 0.1, 110.0)},
        _daily(305, "2026-10-16"),
        path=db_backend,
    )
    return db_backend


def test_pack_keys_independence_and_as_seen(
    db: Path | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["JET2.L", "--run", "1", "--out", str(tmp_path / "pack")] + (
        ["--db", str(db)] if db else []
    )
    validate_pack.main(args)
    out = capsys.readouterr().out
    assert "JET2.L: screen ACT; 1 cards" in out and "filings: 1" in out
    pack = json.loads((tmp_path / "pack" / "pack.json").read_text())
    assert set(pack) >= {
        "ticker",
        "run_id",
        "run_started_at",
        "card_date",
        "cards",
        "steps",
        "screen_label",
        "prices",
        "fundamentals",
        "filings",
    }
    assert pack["card_date"] == "2026-10-09"
    # independence: no events / research content anywhere in the pack
    text = json.dumps(pack)
    assert (
        "SECRET NOTE" not in text
        and "events" not in pack["cards"]
        and "research" not in pack["cards"]
    )
    assert pack["cards"]["technical"]["trend"] == 0.5  # run 1's card, not the later run's
    assert pack["steps"] == [
        {"stage": "screen", "toolkit": "technical", "status": "act", "reason": "trend=0.50"}
    ]
    assert pack["screen_label"]["label"] == "ACT"
    # prices limited to what was seen by the run start
    prices = pack["prices"]
    assert prices["span"]["fetched_to"] == "2026-10-09" and prices["currency"] == "GBp"
    assert len(prices["daily"]) == 60 and prices["daily"][-1]["date"].startswith("2026-10-09")
    assert len(prices["weekly"]) <= 104 and {"ma10", "ma40", "adj_close", "volume"} <= set(
        prices["weekly"][-1]
    )
    assert prices["weekly"][-1]["date"].startswith("2026-10-09")
    assert len(prices["largest_weekly_moves"]) == 5 and all(
        "week" in m for m in prices["largest_weekly_moves"]
    )
    assert prices["issues"] == [] and prices["span"]["sessions_dropped"] == 0
    # fundamentals: only what was public at the card date (the 2026-09-30 year end is not yet reported)
    fund = pack["fundamentals"]
    assert fund["as_of"] == "2026-10-09" and fund["rows_not_yet_public"] >= 1
    fields = {(r["field"], r["date"][:10]) for r in fund["latest_periods"]}
    assert ("annualNetIncome", "2026-03-31") not in fields and (
        "annualNetIncome",
        "2025-03-31",
    ) in fields
    # filings: material ones before the run start only
    assert pack["filings"]["issuer"] == "Jet2 plc"
    assert [f["id"] for f in pack["filings"]["items"]] == [
        "NI-1"
    ]  # NI-2 routine, NI-3 after the run


def test_all_and_unavailable_filings(
    db: Path | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(lei: str, start=None, end=None) -> pd.DataFrame:
        raise ConnectionError("NSM down")

    monkeypatch.setattr(validation, "fetch_announcements", boom)
    from solaraalpha.config import settings

    validate_pack.main(["--run", "1", "--all"] + (["--db", str(db)] if db else []))
    out = capsys.readouterr().out
    assert "JET2.L:" in out and "OTHER.L" not in out and "filings: unavailable" in out
    pack = json.loads(
        (settings.processed_dir / "validate" / "JET2.L" / "1" / "pack.json").read_text()
    )
    assert pack["filings"] == "unavailable"
    with pytest.raises(SystemExit, match="give one TICKER, or --all"):
        validate_pack.main(["--run", "1"])
