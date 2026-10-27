from __future__ import annotations

import dataclasses
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import notify, store
from solaraalpha.data.announcements import Issuer
from solaraalpha.portfolio import book
from solaraalpha.portfolio.daily import Fetchers, render_daily, run_daily
from solaraalpha.portfolio.triggers import (
    Trigger,
    entry_break,
    event_due,
    filing_triggers,
    news_triggers,
    price_triggers,
    watch_break,
    watch_pullback,
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


def test_event_due_boundaries() -> None:
    today = pd.Timestamp("2026-10-14", tz="UTC")  # a Wednesday
    assert event_due("J", 1, "2026-10-16", today, "flag") is not None  # Friday: 2 sessions
    assert event_due("J", 1, "2026-10-19", today, "flag") is None  # Monday: 3 sessions
    t = event_due("J", 1, "2026-10-15T07:00:00+00:00", today, "trim")
    assert t is not None and t.detail == {"event": "2026-10-15", "sessions": 1, "policy": "trim"}
    assert t.kind == "event_due" and t.position_id == 1 and t.at == "2026-10-14"
    assert event_due("J", 1, "2026-10-14", today, "exit") is not None  # today: 0 sessions
    assert event_due("J", 1, "2026-10-13", today, "exit") is None  # past
    assert event_due("J", 1, "2026-10-16", today, "hold") is None  # hold: nothing to do
    assert (
        event_due("J", 1, None, today, "flag") is None
        and event_due("J", 1, "", today, "flag") is None
    )
    # a weekend counts no sessions: seen on Saturday, Monday's event is the next session (0 away)
    sat = pd.Timestamp("2026-10-17", tz="UTC")
    mon = event_due("J", 1, "2026-10-19", sat, "flag")
    tue = event_due("J", 1, "2026-10-20", sat, "flag")
    assert mon is not None and mon.detail["sessions"] == 0
    assert tue is not None and tue.detail["sessions"] == 1


def test_watch_pullback_boundaries() -> None:
    # ATR(14) of the fixture is 20; the 10w MA level is 1480, the 40w MA 1400
    assert watch_pullback("W", _daily(last={"close": 1500.0}), 1480.0, 1400.0) is not None  # 1 ATR
    assert watch_pullback("W", _daily(last={"close": 1500.1}), 1480.0, 1400.0) is None  # beyond
    assert watch_pullback("W", _daily(last={"close": 1460.0}), 1480.0, 1400.0) is not None  # below
    assert watch_pullback("W", _daily(last={"close": 1399.0}), 1410.0, 1400.0) is None  # trend gone
    assert watch_pullback("W", _daily(last={"close": 1400.0}), 1410.0, 1400.0) is None  # at the MA
    assert watch_pullback("W", _daily(last={"close": 1405.0}), 1410.0, None) is not None  # no MA
    t = watch_pullback("W", _daily(last={"close": 1490.0}), 1480.0, 1400.0)
    assert t is not None and t.kind == "watch_pullback" and t.position_id is None
    assert t.detail["distance_atr"] == pytest.approx(0.5) and t.detail["level"] == 1480.0
    assert watch_pullback("W", _daily(), None, 1400.0) is None
    assert watch_pullback("W", _daily(), float("nan"), 1400.0) is None
    assert (
        watch_pullback("W", _daily(n=5, last={"close": 1480.0}), 1480.0, 1400.0) is None
    )  # no ATR


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
            valid_until="2099-12-31",  # run_daily checks validity against the real clock
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

    @dataclass(frozen=True)
    class Tech:
        date: pd.Timestamp
        entry: float
        ma10: float
        ma40: float

    @dataclass(frozen=True)
    class Ev:
        date: pd.Timestamp
        next_event: pd.Timestamp

    scan = store.start_run("scan", {}, path=db_backend)
    store.save_ticker(
        scan, "JET2.L", {"events": Ev(DATE, pd.Timestamp("2030-01-15", tz="UTC"))}, path=db_backend
    )
    store.save_ticker(scan, "W.L", {"levels": Levels(DATE, 780.0, 800.0, True)}, path=db_backend)
    store.save_label(scan, "W.L", "screen", "WATCH", "levels WATCH: coil", path=db_backend)
    # an extended WATCH stock with a structured pullback reason; its close is back at the 10w MA
    store.save_ticker(scan, "X.L", {"technical": Tech(DATE, -0.4, 1510.0, 1400.0)}, path=db_backend)
    store.save_label(
        scan,
        "X.L",
        "screen",
        "WATCH",
        "technical WATCH: failed entry >= 0 (entry=-0.4)",
        path=db_backend,
        watch_on=[{"toolkit": "technical", "kind": "pullback", "level": 1510.0, "entry": -0.4}],
    )
    store.save_label(scan, "JET2.L", "screen", "ACT", "all ACT", path=db_backend)
    prev = store.start_run("daily", {}, path=db_backend)
    # it finished: it saved its triggers (a crashed run did not, and its window is looked at again)
    store.save_triggers(
        prev, [Trigger("W.L", "error", None, None, {"reason": "news: x"})], path=db_backend
    )
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
        "X.L": _daily(close=1500.0),  # 0.5 ATR below its 10w MA, above the 40w: pullback
        "E.L": _daily(close=290.0, last={"close": 301.0}),  # entry break
    }
    return db_backend, frames


def _index(n: int = 300, shock: float = 0.0) -> pd.DataFrame:
    """A calm benchmark index; ``shock`` is the last day's log return (a -0.05 is risk_off)."""
    rets = 0.004 * np.random.default_rng(9).standard_normal(n)
    rets[-1] = shock if shock else rets[-1]
    idx = pd.bdate_range(end="2026-10-20", periods=n, tz="UTC", name="date")
    close = 4000 * np.exp(np.cumsum(rets))
    df = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 1e6,
        },
        index=idx,
    )
    df.attrs = {"ticker": "^FTAI", "currency": "GBP"}
    return df


def _fetchers(
    frames: dict[str, pd.DataFrame], fail: str | None = None, index: pd.DataFrame | None = None
) -> Fetchers:
    def daily(ticker: str) -> pd.DataFrame:
        if ticker == fail:
            raise ConnectionError("Yahoo down")
        if ticker == "^FTAI" and ticker not in frames:
            return index if index is not None else _index()
        return frames[ticker]

    news = pd.DataFrame(
        {
            "published": pd.to_datetime(["2026-10-19T10:00"]).tz_localize("UTC"),
            "source": ["Wire"],
            "headline": ["Jet2 adds Bristol routes"],
            "url": ["u2"],
        }
    )
    actions = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-10-20", "2026-10-21"]).tz_localize("UTC"),
            "kind": ["dividend", "split"],
            "value": [10.7, 2.0],
            "ratio": ["", "2:1"],
        }
    )
    return Fetchers(
        daily=daily,
        actions=lambda t: actions if t == "JET2.L" else actions.iloc[0:0],
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
    assert "Checked: JET2.L (open), W.L (watch), X.L (watch), E.L (pending)" in out
    assert "Market (^FTAI): **calm**" in out
    assert "Book drawdown" in out and "circuit-breaker limit 15%; breaker on" in out
    assert "### Risk (" in out and "#### Where the day's P&L came from" in out  # ADR 0036
    assert "### Owner actions" in out and "traded through the stop 1,400.0" in out
    assert "reached the target 1,800.0" in out and "the stop and the daily review manage" in out
    assert "closed 301.0 above the trigger 300.0: place entry order" in out
    assert (
        "ex-dividend 2026-10-20: 10.70p x 100 shares = £10.70 (position 1): confirm against the broker and record — `book.py cash dividend JET2.L 10.70 --at 2026-10-20`"
        in out
    )
    assert (
        "split 2:1 on 2026-10-21 (position 1): record it — `book.py split 1 --ratio 2:1 --at 2026-10-21`"
        in out
    )
    assert "### Agent review needed" in out and "gapped +2.2 ATR" in out
    assert "new filing 2026-10-20T06:00 `RDN` Director buys (position 1)" in out
    assert "/monitor --run 3" in out
    assert "### Watch breaks and pullbacks" in out
    assert "W.L** closed 805.0 above its coil trigger 800.0" in out
    assert (
        "X.L** closed 1,500.0, -0.5 ATR from its 10w MA 1,510.0 with the trend intact: pullback entry candidate — `uv run python scripts/scan.py X.L --save`"
        in out
    )
    assert "### Headlines" in out and "Jet2 adds Bristol routes" in out
    assert "### Errors\n\nnone" in out and "saved run 3" in out

    trig = store.load_triggers(run_id=3, path=db)
    assert sorted(trig["kind"]) == sorted(
        [
            "stop_touched",
            "target_hit",
            "gap",
            "filing",
            "news",
            "watch_break",
            "entry_break",
            "dividend",
            "split",
            "watch_pullback",
            # the pending E.L entry gets filings and headlines too, and each of the two
            # positions its weekday agent review (ADR 0024)
            "filing",
            "news",
            "daily_review",
            "daily_review",
        ]
    )
    assert trig.loc[trig["kind"] == "watch_pullback", "detail"].iloc[0]["run_id"] == 1
    assert set(trig.loc[trig["kind"] == "filing", "position_id"]) == {1, 2}
    reviews = trig[trig["kind"] == "daily_review"]
    detail = {int(p): d for p, d in zip(reviews["position_id"], reviews["detail"], strict=True)}
    assert detail[1]["status"] == "open"
    assert {"gap 2026-10-20", "stop_touched 2026-10-20"} <= set(detail[1]["fired"])
    assert detail[2]["status"] == "pending" and "entry_break 2026-10-20" in detail[2]["fired"]
    assert list(reviews.loc[reviews["position_id"] == 2, "order_id"]) == [3]
    assert "### Daily agent review (ADR 0024)" in out and "position 2 (pending)" in out
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
    risk_card = store.load_cards(3, "_BOOK", path=db)["book_risk"]  # the risk manager reads it
    assert risk_card["positions"] == 1 and risk_card["var"] > 0
    # the next run only looks since this one; a recorded dividend and split are not proposed again
    conn = book.connect(db)
    with conn:
        book.record_dividend(conn, "JET2.L", 10.70, at="2026-10-20T00:00:00+00:00")
        book.split(conn, 1, "2:1", at="2026-10-21T07:00:00+00:00")
    r = run_daily(conn, _fetchers(frames))
    conn.close()
    assert r.since.startswith(pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d"))  # this run
    assert not r.of(("dividend", "split"))


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
    daily_cli.main(["--save", *(["--db", str(db)] if db else [])])  # no exit: the monitor runs
    out = capsys.readouterr().out
    assert "- JET2.L: prices: ConnectionError: Yahoo down" in out
    assert "W.L** closed 805.0" in out and "place entry order" in out  # the others still ran
    conn = book.connect(db)
    err = store.load_triggers(kind="error", conn=conn)
    err = err[
        err["run_id"] == err["run_id"].max()
    ]  # this run's (the fixture's earlier run has one)
    assert list(err["ticker"]) == ["JET2.L"] and err["position_id"].isna().all()
    md = notify.desk_errors(conn, int(err["run_id"].iloc[0]), "pipeline")
    conn.close()
    assert md and "**JET2.L** `daily` failed: prices: ConnectionError: Yahoo down" in md


def test_the_daily_job_fails_when_no_stock_could_be_checked(
    world: tuple[Path | None, dict[str, pd.DataFrame]], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, frames = world

    def down(ticker: str) -> pd.DataFrame:
        raise ConnectionError("Yahoo down")

    monkeypatch.setattr(
        daily_cli,
        "run_daily",
        lambda conn, save, args: run_daily(
            conn, dataclasses.replace(_fetchers(frames), daily=down), save, args
        ),
    )
    with pytest.raises(SystemExit) as e:
        daily_cli.main(["--db", str(db)] if db else [])
    assert e.value.code == 1


def test_market_regime_heads_the_report(
    world: tuple[Path | None, dict[str, pd.DataFrame]], capsys: pytest.CaptureFixture[str]
) -> None:
    db, frames = world
    conn = book.connect(db)
    r = run_daily(conn, _fetchers(frames, index=_index(shock=-0.06)), save=True)
    conn.close()
    assert r.regime is not None and r.regime.regime == "risk_off" and r.regime.index == "^FTAI"
    mt = r.of(("market",))
    assert len(mt) == 1 and mt[0].detail["regime"] == "risk_off" and mt[0].ticker == "^FTAI"
    text = render_daily(r)
    assert text.index("### Market: **RISK_OFF**") < text.index("### Owner actions")
    assert "Consider tightening stops and pausing new entries" in text
    cards = store.load_cards(int(r.run_id or 0), "^FTAI", path=db)
    assert cards["regime"]["regime"] == "risk_off" and cards["regime"]["drawdown_20d"] < 0
    # the index failing is an error line, not a stop
    conn = book.connect(db)
    r2 = run_daily(conn, _fetchers(frames, fail="^FTAI"))
    conn.close()
    assert r2.regime is None and any(e.startswith("regime:") for _, e in r2.errors) and r2.triggers


def test_render_empty() -> None:
    from solaraalpha.portfolio.daily import DailyResult

    text = render_daily(DailyResult(since="2026-10-18T17:47:00+00:00", tickers={}))
    for section in (
        "### Owner actions\n\nnone",
        "### Agent review needed\n\nnone",
        "### Watch breaks and pullbacks\n\nnone",
        "### Headlines\n\nnone",
        "### Errors\n\nnone",
    ):
        assert section in text
    assert "Checked: none" in text


def test_the_watch_list_is_the_latest_full_weekly_scans(db_backend: Path | None) -> None:
    from solaraalpha.portfolio.daily import latest_watch

    week = store.start_run("scan", {"tickers": [], "toolkit": None}, path=db_backend)
    store.save_label(week, "W.L", "screen", "WATCH", "coil", path=db_backend)
    one = store.start_run("scan", {"tickers": ["X.L"], "toolkit": None}, path=db_backend)
    store.save_label(one, "X.L", "screen", "WATCH", "coil", path=db_backend)  # a one-stock scan
    conn = book.connect(db_backend)
    watch = latest_watch(conn)
    conn.close()
    assert list(watch) == ["W.L"] and watch["W.L"]["run_id"] == week


def test_a_daily_run_that_crashed_does_not_move_the_window(db_backend: Path | None) -> None:
    from solaraalpha.portfolio.daily import last_daily_run

    done = store.start_run("daily", {}, path=db_backend)
    store.save_triggers(done, [Trigger("W.L", "error", None, None, {})], path=db_backend)
    store.start_run("daily", {}, path=db_backend)  # crashed before saving its triggers
    conn = book.connect(db_backend)
    since = last_daily_run(conn)
    started = str(store.run_info(done, conn=conn)["started_at"])
    conn.close()
    assert since == started


def test_a_saved_run_expires_entries_past_their_validity_before_the_checks(
    world: tuple[Path | None, dict[str, pd.DataFrame]],
) -> None:
    db, frames = world
    conn = book.connect(db)
    with conn:  # a pending entry whose last valid day was weeks ago
        pid, oid = book.draft_position(
            conn, "OLD.L", 100.0, 90.0, 10, at="2026-01-05T09:00:00+00:00", valid_days=5
        )
        book.approve_position(conn, pid)
    dry = run_daily(conn, _fetchers(frames))  # no save: the book is left alone
    assert book.order_status(conn, oid) == "approved" and dry.expired == []
    r = run_daily(conn, _fetchers(frames), save=True)
    assert book.order_status(conn, oid) == "expired"
    assert book.position_status(conn, pid) == "cancelled"
    assert r.expired == [f"OLD.L: entry order #{oid} expired (valid until 2026-01-10)"]
    assert "OLD.L" not in r.tickers  # never reviewed as a pending entry
    assert "### Expired" in render_daily(r)
    conn.close()


def test_daily_for_an_event_session(
    world: tuple[Path | None, dict[str, pd.DataFrame]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from solaraalpha import desk_chat, event_sessions

    db, frames = world
    monkeypatch.setattr(
        daily_cli,
        "run_daily",
        lambda conn, save, args: run_daily(conn, _fetchers(frames), save, args),
    )
    dbargs = ["--db", str(db)] if db else []
    with pytest.raises(SystemExit, match="not scheduled"):
        daily_cli.main(["--save", "--session", "1", *dbargs])
    conn = store.connect(db)
    sid = event_sessions.schedule(
        conn, "2099-01-05 13:45", "UK Budget", "AIM", "agent:pm", now="2026-10-27T18:00:00Z"
    )
    conn.close()
    daily_cli.main(["--save", "--session", str(sid), *dbargs])
    conn = desk_chat.connect(db)
    s = event_sessions.sessions(conn)[0]
    assert s.status == "started" and s.run_id is not None
    assert json.loads(store.run_info(s.run_id, conn=conn)["args"])["session"] == sid
    thread = desk_chat.thread(conn=conn)
    assert "Event session for UK Budget" in thread["text"].iloc[-1]
    conn.close()
