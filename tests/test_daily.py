from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import store
from solaraalpha.data.announcements import Issuer
from solaraalpha.portfolio import book
from solaraalpha.portfolio.daily import Fetchers, render_daily, run_daily
from solaraalpha.portfolio.triggers import (
    entry_break,
    filing_triggers,
    news_triggers,
    price_triggers,
    watch_break,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily as daily_cli

DATE = pd.Timestamp("2026-10-09", tz="UTC")


def _daily(
    n: int = 60, close: float = 1500.0, last: dict | None = None, end: str = "2026-10-20"
) -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=n, tz="UTC", name="date")
    c = close + (np.arange(n) % 2).astype(float)  # alternate by 1p: no stale run, true range 20
    c[-1] = close
    df = pd.DataFrame(
        {"open": c, "high": c + 10, "low": c - 10, "close": c, "adj_close": c, "volume": 5e4},
        index=idx,
    )
    for k, v in (last or {}).items():
        df.loc[idx[-1], k] = v
    df.attrs = {"ticker": "JET2.L", "currency": "GBp"}
    return df


# ATR of the fixture: true range is 20 every day (high - low), so ATR(14) = 20.


def test_stop_triggers_boundaries() -> None:
    assert [t.kind for t in price_triggers("X", _daily(), 1, 1400.0)] == []
    stop_close = price_triggers("X", _daily(last={"close": 1399.0, "low": 1390.0}), 1, 1400.0)
    assert [t.kind for t in stop_close] == ["stop_close"]
    assert stop_close[0].detail["stop"] == 1400.0 and stop_close[0].at == "2026-10-20"
    touched = price_triggers(
        "X", _daily(last={"low": 1400.0}), 1, 1400.0
    )  # low == stop, closed above
    assert [t.kind for t in touched] == ["stop_touched"]
    assert price_triggers("X", _daily(last={"low": 1400.01}), 1, 1400.0) == []
    at_stop = price_triggers("X", _daily(last={"close": 1400.0, "low": 1395.0}), 1, 1400.0)
    assert [t.kind for t in at_stop] == ["stop_touched"]  # close == stop is not "below"
    assert price_triggers("X", _daily(last={"close": 1399.0}), 1, None) == []  # no stop known
    # the fixture survives the price checks (no stale run) and keeps its ATR of 20
    from solaraalpha.data.clean import check_prices

    assert check_prices(_daily()).empty


def test_target_and_gap_triggers() -> None:
    assert [
        t.kind for t in price_triggers("X", _daily(last={"high": 1800.0}), 1, 1400.0, 1800.0)
    ] == ["target_hit"]
    assert price_triggers("X", _daily(last={"high": 1799.9}), 1, 1400.0, 1800.0) == []
    gap = price_triggers(
        "X", _daily(last={"open": 1541.0, "high": 1551.0}), 1, 1400.0
    )  # 41 > 2 x ATR 20
    assert [t.kind for t in gap] == ["gap"]
    assert (
        gap[0].detail["gap_atr"] == pytest.approx(41 / 20) and gap[0].detail["threshold_atr"] == 2.0
    )
    assert (
        price_triggers("X", _daily(last={"open": 1540.0, "high": 1551.0}), 1, 1400.0) == []
    )  # 40 = 2 x ATR: not beyond
    down = price_triggers("X", _daily(last={"open": 1455.0, "low": 1445.0}), 1, 1400.0)
    assert [t.kind for t in down] == ["gap"] and down[0].detail["gap"] == -45.0
    assert price_triggers("X", _daily(n=5, last={"open": 1600.0}), 1, 1400.0) == []  # no ATR yet


def test_watch_and_entry_breaks() -> None:
    assert watch_break("W", _daily(), 1500.0) is None  # close == trigger is not a break
    wb = watch_break("W", _daily(last={"close": 1501.0}), 1500.0)
    assert wb is not None and wb.kind == "watch_break" and wb.position_id is None
    assert (
        watch_break("W", _daily(), None) is None
        and watch_break("W", _daily(), float("nan")) is None
    )
    eb = entry_break("E", _daily(last={"close": 1520.0}), 7, 9, 1510.0)
    assert (
        eb is not None
        and (eb.position_id, eb.order_id) == (7, 9)
        and eb.detail["trigger"] == 1510.0
    )
    assert entry_break("E", _daily(), 7, 9, 1510.0) is None


def _ann() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "published": pd.to_datetime(
                ["2026-10-17T06:00", "2026-10-19T06:00", "2026-10-20T06:00"]
            ).tz_localize("UTC"),
            "type_code": ["TST", "POS", "RDN"],
            "type": ["Trading Statement", "Transaction in own shares", "Director dealing"],
            "category": ["Financial Results", "Other", "Other"],
            "headline": ["Old update", "Buyback", "Director buys"],
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


def test_filing_and_news_triggers() -> None:
    since = "2026-10-18T17:47:00+00:00"
    got = filing_triggers("JET2.L", 1, _ann(), since)
    assert [t.detail["id"] for t in got] == ["NI-3"]  # NI-1 before since, NI-2 routine
    assert got[0].at == "2026-10-20T06:00:00+00:00" and got[0].kind == "filing"
    news = pd.DataFrame(
        {
            "published": pd.to_datetime(["2026-10-18T10:00", "2026-10-19T10:00"]).tz_localize(
                "UTC"
            ),
            "source": ["Paper", "Wire"],
            "headline": ["old", "Jet2 adds Bristol routes"],
            "url": ["u1", "u2"],
        }
    )
    assert [t.detail["headline"] for t in news_triggers("JET2.L", news, since)] == [
        "Jet2 adds Bristol routes"
    ]
    assert filing_triggers("JET2.L", 1, pd.DataFrame(), since) == []


@pytest.fixture
def world(db_backend: Path | None) -> tuple[Path | None, dict[str, pd.DataFrame]]:
    """An open JET2.L position (stop 1400p, target 1800p), a WATCH coil on W.L (trigger 800p),
    a pending wait-for-break entry on E.L (trigger 300p), and a previous daily run."""
    conn = book.connect(db_backend)
    with conn:
        book.cash_event(conn, "deposit", 10_000.0)
        pid, oid = book.draft_position(
            conn, "JET2.L", 1500.0, 1400.0, 100, target=1800.0, at="2026-10-11T18:30:00+00:00"
        )
        book.approve_position(conn, pid)
        book.fill(conn, oid, 100, 1500.0, at="2026-10-12T09:00:00+00:00")
        pid2, _ = book.draft_position(
            conn,
            "E.L",
            300.0,
            270.0,
            50,
            payload={"wait_for_break": True},
            at="2026-10-11T18:30:00+00:00",
        )
        book.approve_position(conn, pid2)
    conn.close()

    @dataclass(frozen=True)
    class Levels:
        date: pd.Timestamp
        close: float
        trigger: float
        coil_pending: bool

    scan = store.start_run("scan", {}, path=db_backend)
    store.save_ticker(scan, "W.L", {"levels": Levels(DATE, 780.0, 800.0, True)}, path=db_backend)
    store.save_label(scan, "W.L", "screen", "WATCH", "levels WATCH: coil", path=db_backend)
    store.save_label(scan, "JET2.L", "screen", "ACT", "all ACT", path=db_backend)
    prev = store.start_run("daily", {}, path=db_backend)
    conn = book.connect(db_backend)
    with conn:
        conn.execute(
            "UPDATE runs SET started_at = ? WHERE run_id = ?", ("2026-10-18T17:47:00+00:00", prev)
        )
    conn.close()
    frames = {
        "JET2.L": _daily(
            last={"open": 1545.0, "high": 1810.0, "low": 1399.0, "close": 1402.0}
        ),  # gap, target, touched
        "W.L": _daily(close=780.0, last={"close": 805.0}),  # break
        "E.L": _daily(close=290.0, last={"close": 301.0}),  # entry break
    }
    return db_backend, frames


def _fetchers(frames: dict[str, pd.DataFrame], fail: str | None = None) -> Fetchers:
    def daily(ticker: str) -> pd.DataFrame:
        if ticker == fail:
            raise ConnectionError("Yahoo down")
        return frames[ticker]

    news = pd.DataFrame(
        {
            "published": pd.to_datetime(["2026-10-19T10:00"]).tz_localize("UTC"),
            "source": ["Wire"],
            "headline": ["Jet2 adds Bristol routes"],
            "url": ["u2"],
        }
    )
    return Fetchers(
        daily=daily,
        announcements=lambda lei, start=None, end=None: _ann(),
        issuer=lambda t: Issuer(t, "GB00", "LEI-1", "Jet2 plc", "AIM"),
        news=lambda query, days=7: news,
    )


def test_run_daily_saves_triggers_and_reports(
    world: tuple[Path | None, dict[str, pd.DataFrame]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, frames = world
    monkeypatch.setattr(
        daily_cli,
        "run_daily",
        lambda conn, save, args: run_daily(conn, _fetchers(frames), save, args),
    )
    daily_cli.main(["--save", *(["--db", str(db)] if db else [])])
    out = capsys.readouterr().out
    assert "Checked: JET2.L (open), W.L (watch), E.L (pending)" in out
    assert "### Owner actions" in out and "traded through the stop 1,400.0" in out
    assert "reached the target 1,800.0" in out and "book.py exit 1 --reason target" in out
    assert "closed 301.0 above the trigger 300.0: place entry order" in out
    assert "### Agent review needed" in out and "gapped +2.2 ATR" in out
    assert "new filing 2026-10-20T06:00 `RDN` Director buys (position 1)" in out
    assert "/monitor --run 3" in out
    assert "### Watch breaks" in out and "W.L** closed 805.0 above its coil trigger 800.0" in out
    assert "### Headlines" in out and "Jet2 adds Bristol routes" in out
    assert "### Errors\n\nnone" in out and "saved run 3" in out

    trig = store.load_triggers(run_id=3, path=db)
    assert sorted(trig["kind"]) == sorted(
        ["stop_touched", "target_hit", "gap", "filing", "news", "watch_break", "entry_break"]
    )
    assert set(trig.loc[trig["kind"] == "filing", "position_id"]) == {1}
    assert (
        trig.loc[trig["kind"] == "entry_break", "order_id"].iloc[0] == 3
    )  # 1 entry, 2 stop, 3 E.L
    assert trig.loc[trig["kind"] == "gap", "detail"].iloc[0]["gap_atr"] == pytest.approx(45 / 20)
    assert (
        store.load_triggers(kind="news", path=db)["detail"].iloc[0]["headline"]
        == "Jet2 adds Bristol routes"
    )
    prices = store.load_prices("W.L", path=db)
    assert len(prices) == 60  # snapshot saved per ticker
    # the next run only looks since this one
    conn = book.connect(db)
    r = run_daily(conn, _fetchers(frames))
    conn.close()
    assert r.since.startswith("2026-10-16") or r.since > "2026-10-18"


def test_one_failure_does_not_stop_the_rest(
    world: tuple[Path | None, dict[str, pd.DataFrame]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, frames = world
    monkeypatch.setattr(
        daily_cli,
        "run_daily",
        lambda conn, save, args: run_daily(conn, _fetchers(frames, fail="JET2.L"), save, args),
    )
    with pytest.raises(SystemExit) as e:
        daily_cli.main(["--db", str(db)] if db else [])
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "- JET2.L: prices: ConnectionError: Yahoo down" in out
    assert "W.L** closed 805.0" in out and "place entry order" in out  # the others still ran
    assert "saved run" not in out


def test_render_empty() -> None:
    from solaraalpha.portfolio.daily import DailyResult

    text = render_daily(DailyResult(since="2026-10-18T17:47:00+00:00", tickers={}))
    for section in (
        "### Owner actions\n\nnone",
        "### Agent review needed\n\nnone",
        "### Watch breaks\n\nnone",
        "### Headlines\n\nnone",
        "### Errors\n\nnone",
    ):
        assert section in text
    assert "Checked: none" in text
