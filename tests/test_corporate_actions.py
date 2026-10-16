from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha.data.prices import ACTION_COLUMNS, parse_events
from solaraalpha.portfolio import book
from solaraalpha.portfolio.risk import equity, holdings
from solaraalpha.portfolio.triggers import action_triggers

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import book as cli

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_events_fixture() -> None:
    df = parse_events(json.loads((FIXTURES / "yahoo_chart_events.json").read_text()))
    assert list(df.columns) == ACTION_COLUMNS and len(df) == 5
    assert df.attrs == {"ticker": "JET2.L", "currency": "GBp"}
    divs = df[df["kind"] == "dividend"]
    assert divs["value"].tolist() == [10.7, 4.4, 12.1]
    assert divs["date"].iloc[0] == pd.Timestamp("2024-09-19", tz="UTC")  # ex-date, London day
    splits = df[df["kind"] == "split"]
    assert splits["value"].tolist() == [2.0, 0.1] and splits["ratio"].tolist() == ["2:1", "1:10"]
    assert df["date"].is_monotonic_increasing
    empty = parse_events(
        {"chart": {"result": [{"meta": {"symbol": "X", "exchangeTimezoneName": "UTC"}}]}}
    )
    assert empty.empty and list(empty.columns) == ACTION_COLUMNS
    with pytest.raises(ValueError, match="Yahoo chart error"):
        parse_events({"chart": {"error": {"code": "Not Found"}, "result": None}})


def _open(conn: book.Conn, ticker: str = "JET2.L", qty: int = 100) -> tuple[int, int]:
    pid, oid = book.draft_position(
        conn, ticker, 1500.0, 1400.0, qty, target=1800.0, at="2026-10-18T18:00:00+00:00"
    )
    book.approve_position(conn, pid)
    book.place_order(conn, oid)
    out = book.fill(conn, oid, qty, 1500.0, at="2026-10-19T09:00:00+00:00", commission=5.0)
    return pid, int(out["stop_order_id"])


def test_dividend_in_equity_and_close_summary(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    with conn:
        book.cash_event(conn, "deposit", 10_000.0, at="2026-10-01T09:00:00+00:00")
        pid, _ = _open(conn)
        # before the position opened: not attributed
        assert book.position_for(conn, "JET2.L", "2026-10-18T12:00:00+00:00") is None
        assert book.position_for(conn, "JET2.L", "2026-10-25T00:00:00+00:00") == pid
        assert book.position_for(conn, "OTHER.L") is None
        got = book.record_dividend(conn, "JET2.L", 10.70, at="2026-10-25T00:00:00+00:00")
        assert got == pid
        assert book.dividends_for(conn, pid) == pytest.approx(10.70)
        assert book.cash_balance(conn) == pytest.approx(10_010.70)
    st = book.position_state(conn, pid)
    held = holdings([st], {"JET2.L": 1500.0})
    eq = equity(book.cash(conn), [st], held)
    assert eq.dividends == pytest.approx(10.70)
    assert eq.cash == pytest.approx(10_000 - 1500 - 5 + 10.70)
    assert eq.equity == pytest.approx(10_000 - 5 + 10.70)

    with conn:
        xid = book.exit_position(conn, pid, "target", "limit", 1700.0)
        out = book.fill(conn, xid, 100, 1700.0, at="2026-11-13T15:00:00+00:00", commission=5.0)
    c = out["closed"]
    assert c is not None
    assert c.gross_pnl == pytest.approx(200.0) and c.costs == 10.0
    assert c.dividends == pytest.approx(10.70) and c.net_pnl == pytest.approx(200.70)
    assert c.r_multiple == pytest.approx(200.70 / 100.0)
    ev = book.position_events(conn, pid)
    assert ev["payload"].iloc[-1]["dividends"] == pytest.approx(10.70)
    # a dividend after the close on the same ex-date still finds the position; the day after not
    with conn:
        assert book.position_for(conn, "JET2.L", "2026-11-13T23:00:00+00:00") == pid
        assert book.position_for(conn, "JET2.L", "2026-11-14T09:00:00+00:00") is None
        assert book.record_dividend(conn, "JET2.L", 1.0, at="2026-11-20T00:00:00+00:00") is None
    conn.close()


def test_split_adjusts_shares_cost_and_stop(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    with conn:
        pid, sid = _open(conn)
        tid = book.add_order(conn, pid, -1, "limit", 100, 1800.0, "target", status="approved")
        out = book.split(conn, pid, "2:1", at="2026-10-26T07:00:00+00:00")
    assert out["factor"] == 2.0 and out["shares"] == 200 and out["avg_cost"] == 750.0
    assert set(out["orders"]) == {sid, tid}
    st = book.position_state(conn, pid)
    assert st.shares == 200 and st.avg_cost == 750.0
    assert st.stop == 700.0 and st.initial_stop == 700.0
    assert st.entry_shares == 200 and st.entry_avg == 750.0
    assert st.r_unit == pytest.approx(100.0)  # unchanged in money: (1500 - 1400)p x 100
    assert st.risk_at_stop == pytest.approx(100.0)
    od = book.orders(conn, pid)
    live = od[od["status"].isin(book.LIVE_ORDER)].set_index("purpose")
    assert live.loc["stop", "qty"] == 200 and live.loc["stop", "price"] == 700.0
    assert live.loc["target", "qty"] == 200 and live.loc["target", "price"] == 900.0
    assert list(od["status"]) == ["filled", "cancelled", "cancelled", "approved", "approved"]
    ev = book.position_events(conn, pid)
    assert ev["kind"].iloc[-1] == "split" and ev["payload"].iloc[-1]["shares_before"] == 100

    # a consolidation drops the fraction; closing after the split gives the same money
    with conn:
        out = book.split(conn, pid, "1:3", at="2026-10-27T07:00:00+00:00")
    assert out["shares"] == 66 and out["avg_cost"] == pytest.approx(750.0 * 200 / 66)
    st = book.position_state(conn, pid)
    assert st.stop == pytest.approx(2100.0) and st.initial_stop == pytest.approx(2100.0)
    with conn:
        xid = book.exit_position(conn, pid, "discretionary", "limit", 2500.0)
        closed = book.fill(conn, xid, 66, 2500.0, at="2026-11-02T15:00:00+00:00")["closed"]
    assert closed is not None
    assert closed.gross_pnl == pytest.approx((2500.0 * 66 - 1500.0 * 100) / 100)
    assert closed.shares == 66

    with pytest.raises(ValueError, match="ratio must be NEW:OLD"):
        book.split(conn, pid, "two-for-one")
    with pytest.raises(ValueError, match="is closed"):
        book.split(conn, pid, "2:1")
    conn.close()


def test_corporate_close(db_backend: Path | None, capsys: pytest.CaptureFixture[str]) -> None:
    conn = book.connect(db_backend)
    with conn:
        pid, _ = _open(conn)
        c = book.corporate_close(conn, pid, 1650.0, "takeover", at="2026-11-05T08:00:00+00:00")
    assert c.exit_reason == "corporate_action" and c.net_pnl == pytest.approx(150.0 - 5.0)
    assert c.exit_avg == 1650.0 and c.shares == 100 and c.r_multiple == pytest.approx(1.45)
    assert book.position_status(conn, pid) == "closed"
    od = book.orders(conn, pid)
    assert list(od["status"]) == ["filled", "cancelled", "filled"]
    assert list(od["type"]) == ["limit", "stop", "market"]
    xev = book.order_events(conn, int(od["order_id"].iloc[-1]))
    assert xev.loc[xev["status"] == "approved", "reason"].iloc[0] == "corporate_action: takeover"

    with conn:
        pid2, _ = _open(conn, "DEAD.L")
        c2 = book.corporate_close(conn, pid2, 0.0, "delisting", at="2026-11-06T08:00:00+00:00")
    assert c2.net_pnl == pytest.approx(-1500.0 - 5.0) and c2.r_multiple == pytest.approx(-15.05)
    with pytest.raises(ValueError, match="kind must be one of"):
        book.corporate_close(conn, pid2, 1.0, "merger")
    with pytest.raises(ValueError, match="is closed"):
        book.corporate_close(conn, pid2, 1.0, "takeover")
    conn.close()

    if db_backend is not None:
        conn = book.connect(db_backend)
        with conn:
            pid3, _ = _open(conn, "C.L")
        conn.close()
        cli.main(
            ["--db", str(db_backend), "cash", "dividend", "C.L", "12.50", "--at", "2026-10-22"]
        )
        cli.main(
            ["--db", str(db_backend), "split", str(pid3), "--ratio", "2:1", "--at", "2026-10-23"]
        )
        cli.main(
            [
                "--db",
                str(db_backend),
                "corporate-close",
                str(pid3),
                "--price",
                "800",
                "--reason",
                "takeover",
                "--at",
                "2026-10-24T08:00:00+00:00",
            ]
        )
        out = capsys.readouterr().out
        assert f"dividend £12.50 from C.L recorded against position {pid3}" in out
        assert f"split 2:1 recorded on position {pid3}: now 200 shares at avg cost 750p" in out
        assert "order" in out and "replaced by" in out
        assert (
            f"position {pid3} closed (takeover): net £107.50" in out
        )  # 800p x 200 - £1,505 + 12.50
        cli.main(["--db", str(db_backend), "cash", "deposit", "100"])
        assert "deposit £100.00 recorded" in capsys.readouterr().out
        with pytest.raises(SystemExit):
            cli.main(["--db", str(db_backend), "cash", "dividend", "5"])


def test_action_triggers_since_and_recorded() -> None:
    actions = parse_events(json.loads((FIXTURES / "yahoo_chart_events.json").read_text()))
    trig = action_triggers("JET2.L", actions, 1, 100, "2025-01-02T17:00:00+00:00", "GBp")
    assert [(t.kind, t.at) for t in trig] == [
        ("split", "2025-06-03"),
        ("split", "2025-07-07"),
        ("dividend", "2025-09-18"),
    ]
    assert trig[2].detail == {
        "per_share": 12.1,
        "currency": "GBp",
        "shares": 100,
        "amount_gbp": 12.1,
    }
    assert trig[0].detail == {"ratio": "2:1", "factor": 2.0}
    trig = action_triggers(
        "JET2.L",
        actions,
        1,
        100,
        "2024-01-01",
        recorded_dividends=frozenset({"2024-09-19", "2025-09-18"}),
        recorded_splits=frozenset({"2025-07-07"}),
    )
    assert [(t.kind, t.at) for t in trig] == [("dividend", "2025-01-02"), ("split", "2025-06-03")]
    assert action_triggers("X", actions, 1, 0, "2024-01-01") == []
    assert action_triggers("X", pd.DataFrame(columns=ACTION_COLUMNS), 1, 10, "2024-01-01") == []
    assert math.isclose(trig[0].detail["amount_gbp"], 4.4)
