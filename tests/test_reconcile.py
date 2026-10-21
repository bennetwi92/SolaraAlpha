from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from solaraalpha.data import trading212 as t212
from solaraalpha.portfolio import book
from solaraalpha.portfolio.daily import DailyResult, broker_trigger, render_daily
from solaraalpha.portfolio.reconcile import Broker, reconcile, render
from solaraalpha.store import Conn

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> Any:
    return json.loads((FIX / f"t212_{name}.json").read_text())


def _broker() -> Broker:
    """The fixtures: AAZ held (50 @ 400p) with its stop working, AVG's stop entry working."""
    lines = t212.lse_lines(t212.parse_instruments(_load("instruments")))
    return Broker(
        positions=t212.parse_positions(_load("positions")),
        pending=t212.parse_pending(_load("orders")),
        history=t212.parse_history(_load("history_1")["items"] + _load("history_2")["items"]),
        dividends=t212.parse_dividends([]),
        transactions=t212.parse_transactions(_load("transactions")["items"]),
        to_yahoo={v: k for k, v in lines.items()},
    )


def _book(db: Path | None, avg_placed: bool = True) -> tuple[Conn, dict[str, int]]:
    """The same account in the book: AAZ entry filled and its stop placed (no broker refs), AVG's
    entry placed with its Trading212 id."""
    conn = book.connect(db)
    book.cash_event(conn, "deposit", 1000.0, at="2026-10-20T17:16:11Z")
    aaz, entry = book.draft_position(
        conn, "AAZ.L", 410.0, 340.0, 50, at="2026-10-20T09:00:00Z", horizon_weeks=16
    )
    book.approve_position(conn, aaz, at="2026-10-20T09:00:00Z")
    book.place_order(conn, entry, at="2026-10-20T17:22:00Z")
    stop = book.fill(conn, entry, 50, 400.0, at="2026-10-21T08:40:43Z")["stop_order_id"]
    book.place_order(conn, stop, at="2026-10-21T09:57:00Z")
    avg, avg_entry = book.draft_position(
        conn, "AVG.L", 800.0, 700.0, 25, type="stop", at="2026-10-20T09:00:00Z", horizon_weeks=16
    )
    book.approve_position(conn, avg, at="2026-10-20T09:00:00Z")
    if avg_placed:
        book.place_order(conn, avg_entry, broker_ref="900000003", at="2026-10-20T17:17:00Z")
    return conn, {"aaz": aaz, "stop": stop, "avg": avg, "avg_entry": avg_entry}


def _kinds(diffs: list[Any]) -> list[str]:
    return [d.kind for d in diffs]


def test_everything_matches(db_backend: Path | None) -> None:
    conn, _ = _book(db_backend)
    diffs, matched = reconcile(conn, _broker())
    assert diffs == []
    assert matched == 5  # entry fill, stop order, AVG order, AAZ holding, net deposits
    assert render(diffs, matched) == "Book and Trading212 agree (5 items matched)."


def test_stop_that_sold_is_a_fill_to_book(db_backend: Path | None) -> None:
    conn, ids = _book(db_backend)
    br = _broker()
    sold = {
        "order": {
            "id": 900000004,
            "type": "STOP",
            "ticker": "AAZl_EQ",
            "quantity": -50.0,
            "filledQuantity": 50.0,
            "stopPrice": 340.0,
            "status": "FILLED",
            "side": "SELL",
            "createdAt": "2026-10-21T09:57:16.481Z",
        },
        "fill": {
            "id": 910000004,
            "quantity": 50.0,
            "price": 335.0,
            "filledAt": "2026-10-22T08:01:00Z",
            "walletImpact": {"taxes": []},
        },
    }
    br.history = pd.concat([br.history, t212.parse_history([sold])], ignore_index=True)
    br.pending = br.pending.iloc[1:]  # the stop is no longer working
    br.positions = br.positions.iloc[0:0]  # and nothing is held
    diffs, _ = reconcile(conn, br)
    assert _kinds(diffs) == ["fill"]  # the missing fill explains the missing holding
    d = diffs[0]
    assert (d.ticker, d.order_id, d.position_id) == ("AAZ.L", ids["stop"], ids["aaz"])
    assert d.command == f"fill {ids['stop']} --qty 50 --price 335 --at 2026-10-22T08:01:00+00:00"


def test_book_only_order(db_backend: Path | None) -> None:
    conn, ids = _book(db_backend)
    br = _broker()
    br.pending = br.pending.iloc[:1]  # AVG's entry is gone at the broker
    diffs, _ = reconcile(conn, br)
    assert _kinds(diffs) == ["order_gone"]
    assert diffs[0].order_id == ids["avg_entry"] and "no such order" in diffs[0].text


def test_quantity_and_price_mismatches(db_backend: Path | None) -> None:
    conn, _ = _book(db_backend)
    br = _broker()
    br.positions = br.positions.assign(qty=40.0)
    assert _kinds(reconcile(conn, br)[0]) == ["shares"]
    br.positions = br.positions.assign(qty=50.0, avg_price=420.0)  # 5% off
    diffs, _ = reconcile(conn, br)
    assert _kinds(diffs) == ["avg_price"] and "400p in the book, 420p" in diffs[0].text
    br.positions = br.positions.assign(avg_price=401.0)  # within 0.5%
    assert reconcile(conn, br)[0] == []


def test_fill_price_typed_wrong(db_backend: Path | None) -> None:
    conn, _ = _book(db_backend)
    br = _broker()
    br.history.loc[0, "fill_price"] = 380.0
    br.positions = br.positions.assign(avg_price=380.0)
    assert _kinds(reconcile(conn, br)[0]) == ["avg_price", "avg_price"]


def test_placed_at_broker_before_the_owner_said(db_backend: Path | None) -> None:
    conn, ids = _book(db_backend, avg_placed=False)
    diffs, _ = reconcile(conn, _broker())
    assert _kinds(diffs) == ["placed"]
    assert diffs[0].command == f"placed {ids['avg_entry']} --ref 900000003"


def test_order_working_at_broker_the_book_ended_or_never_had(db_backend: Path | None) -> None:
    conn, ids = _book(db_backend)
    book.end_order(conn, ids["avg_entry"], "cancelled", reason="test")
    br = _broker()
    stray = dict(_load("orders")[1], id=900000099, ticker="BPMl1_EQ", stopPrice=50)
    br.pending = pd.concat([br.pending, t212.parse_pending([stray])], ignore_index=True)
    diffs, _ = reconcile(conn, br)
    assert _kinds(diffs) == ["ended_order", "unknown_order"]
    assert diffs[1].ticker == "BPM.L" and "cancel it there" in diffs[1].text


def test_cash_and_dividends(db_backend: Path | None) -> None:
    conn, _ = _book(db_backend)
    # a correction in the book nets out: deposit, withdrawal, deposit
    book.cash_event(conn, "withdrawal", 1000.0, at="2026-10-20T18:00:00Z")
    book.cash_event(conn, "deposit", 1000.0, at="2026-10-20T18:01:00Z")
    br = _broker()
    assert reconcile(conn, br)[0] == []
    br.dividends = t212.parse_dividends(_load("dividends")["items"])
    br.transactions = pd.concat(
        [br.transactions, br.transactions.assign(amount=250.0)], ignore_index=True
    )
    diffs, _ = reconcile(conn, br)
    assert _kinds(diffs) == ["dividend", "cash"]
    assert diffs[0].command == "cash dividend AAZ.L 2.50 --at 2026-11-02"
    assert diffs[1].command == "cash deposit 250.00"
    book.cash_event(conn, "dividend", 2.5, position_id=_book_pid(conn), at="2026-10-30")
    assert _kinds(reconcile(conn, br)[0]) == ["cash"]


def _book_pid(conn: Conn) -> int:
    return int(book.positions(conn, status="open")["position_id"].iloc[0])


def test_differences_reach_the_daily_report(db_backend: Path | None) -> None:
    conn, _ = _book(db_backend, avg_placed=False)
    diffs, _ = reconcile(conn, _broker())
    r = DailyResult(
        since="2026-10-21", tickers={}, triggers=[broker_trigger(diffs[0], "2026-10-22")]
    )
    out = render_daily(r)
    assert "**AVG.L** Trading212: order #" in out and "`book.py placed" in out


def test_no_book_ignores_old_broker_history(tmp_path: Path) -> None:
    conn = book.connect(tmp_path / "solara.db")
    br = _broker()
    br.positions = br.positions.iloc[0:0]
    br.pending = br.pending.iloc[0:0]
    br.transactions = br.transactions.iloc[0:0]
    assert reconcile(conn, br) == ([], 1)  # only the (zero) net cash


def test_a_line_not_in_pence_fails_loudly(tmp_path: Path) -> None:
    conn = book.connect(tmp_path / "solara.db")
    br = _broker()
    br.positions = br.positions.assign(currency="EUR")
    with pytest.raises(NotImplementedError, match="only GBX"):
        reconcile(conn, br)
