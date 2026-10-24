from __future__ import annotations

import inspect
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha import store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.rules import Rules, load_rules


@dataclass(frozen=True)
class Card:
    date: pd.Timestamp
    trend: float
    label: str
    flag: bool


def test_no_updates_or_deletes_in_book() -> None:
    src = inspect.getsource(book)
    assert not re.search(r"\b(UPDATE|DELETE)\b", src)


def test_book_tables_added_to_existing_store_db(tmp_path: Path) -> None:
    path = tmp_path / "solara.db"
    run_id = store.save_run("scan", {}, "JET2.L", path=path)
    conn = book.connect(path)
    names = set(store.tables(conn))
    assert {"runs", "results", "positions", "orders", "fills", "cash"} <= names
    assert conn.execute("SELECT run_id FROM runs").fetchall() == [(run_id,)]
    conn.close()


def _draft(conn: store.Conn, ticker: str = "JET2.L", qty: int = 100) -> tuple[int, int]:
    pid = book.new_position(conn, ticker, horizon_weeks=16, thesis="coil breakout")
    book.position_event(conn, pid, "drafted", {"entry": 1500, "stop": 1400, "qty": qty})
    oid = book.add_order(conn, pid, 1, "limit", qty, 1500.0, "entry", valid_until="2026-10-23")
    return pid, oid


def test_full_lifecycle(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    pid, oid = _draft(conn)
    assert book.position_status(conn, pid) == "draft"
    assert book.order_status(conn, oid) == "draft"

    book.position_event(conn, pid, "approved", reason="looks good")
    book.order_event(conn, oid, "approved")
    book.order_event(conn, oid, "placed", broker_ref="B123")
    book.add_fill(conn, oid, 40, 1498.0, at="2026-10-19T09:00:00+00:00", commission=5.0)
    assert book.order_status(conn, oid) == "part_filled"
    book.add_fill(conn, oid, 60, 1502.0, at="2026-10-19T10:00:00+00:00", commission=5.0)
    assert book.order_status(conn, oid) == "filled"
    book.position_event(conn, pid, "opened", at="2026-10-19T10:00:00+00:00")
    sid = book.add_order(conn, pid, -1, "stop", 100, 1400.0, "stop", status="approved")
    assert book.order_status(conn, sid) == "approved"

    st = book.position_state(conn, pid)
    assert st.status == "open" and st.shares == 100
    assert st.avg_cost == pytest.approx(1500.4)
    assert st.costs == 10.0 and st.realised_pnl == -10.0
    assert st.stop == 1400.0 and st.initial_stop == 1400.0
    assert st.risk_at_stop == pytest.approx((1500.4 - 1400) * 100 / 100)  # pence -> GBP
    assert st.r_unit == pytest.approx((1500.4 - 1400) * 100 / 100)
    assert st.opened_at == "2026-10-19T10:00:00+00:00"

    xid = book.add_order(conn, pid, -1, "limit", 100, 1700.0, "exit", status="approved")
    book.order_event(conn, xid, "placed")
    book.add_fill(conn, xid, 100, 1700.0, at="2026-11-20T15:00:00+00:00", commission=5.0)
    book.order_event(conn, sid, "cancelled", reason="position closed")
    book.position_event(conn, pid, "closed", {"exit_reason": "target"})

    st = book.position_state(conn, pid)
    assert st.status == "closed" and st.shares == 0
    assert st.realised_pnl == pytest.approx((1700 - 1500.4) * 100 / 100 - 15.0)
    assert st.stop is None  # no live stop order left
    assert st.avg_cost == pytest.approx(1500.4)  # entry average kept for the record

    pos = book.positions(conn)
    assert list(pos["status"]) == ["closed"]
    assert book.positions(conn, status="open").empty
    od = book.orders(conn, pid)
    assert list(od["status"]) == ["filled", "cancelled", "filled"]
    assert list(od["filled"]) == [100, 0, 100]
    assert od.loc[0, "broker_ref"] == "B123"
    assert len(book.fills(conn, position_id=pid)) == 3
    assert len(book.fills(conn, order_id=oid)) == 2
    conn.close()


def test_illegal_transitions_raise(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    pid, oid = _draft(conn)
    with pytest.raises(ValueError, match="is draft; cannot record 'opened'"):
        book.position_event(conn, pid, "opened")
    with pytest.raises(ValueError, match="cannot fill"):
        book.add_fill(conn, oid, 10, 1500.0)  # draft order
    book.order_event(conn, oid, "cancelled")
    with pytest.raises(ValueError, match="is cancelled; cannot fill"):
        book.add_fill(conn, oid, 10, 1500.0)
    book.position_event(conn, pid, "rejected", reason="no")
    with pytest.raises(ValueError, match="is rejected; cannot record 'approved'"):
        book.position_event(conn, pid, "approved")
    with pytest.raises(ValueError, match="unknown position event kind"):
        book.position_event(conn, pid, "teleported")
    with pytest.raises(ValueError, match="no position 99"):
        book.position_event(conn, 99, "drafted")

    pid2, oid2 = _draft(conn)
    book.position_event(conn, pid2, "approved")
    book.order_event(conn, oid2, "approved")
    book.add_fill(conn, oid2, 60, 1500.0)
    with pytest.raises(ValueError, match="over-fill: 101 of 100"):
        book.add_fill(conn, oid2, 41, 1500.0)
    with pytest.raises(ValueError, match="cannot start as 'placed'"):
        book.add_order(conn, pid2, -1, "stop", 60, 1400.0, "stop", status="placed")
    with pytest.raises(ValueError, match="market orders have no price"):
        book.add_order(conn, pid2, -1, "market", 60, 1400.0, "exit")
    with pytest.raises(ValueError, match="purpose"):
        book.add_order(conn, pid2, -1, "limit", 60, 1400.0, "hedge")
    conn.close()


def test_pence_prices_convert_to_gbp(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    pid, oid = _draft(conn, qty=200)
    book.position_event(conn, pid, "approved")
    book.order_event(conn, oid, "approved")
    book.add_fill(conn, oid, 200, 1000.0, commission=10.0, fees=2.5)  # 1,000p = £10
    book.position_event(conn, pid, "opened")
    xid = book.add_order(conn, pid, -1, "market", 200, None, "exit", status="approved")
    book.add_fill(conn, xid, 200, 1100.0, commission=10.0)
    st = book.position_state(conn, pid)
    assert st.realised_pnl == pytest.approx(200 * 1.0 - 22.5)  # £1 per share gross
    assert st.costs == 22.5
    assert book.to_account(1500, "GBp") == 15.0
    assert book.to_account(15, "GBP") == 15.0
    with pytest.raises(NotImplementedError, match="FX not supported"):
        book.to_account(1, "EUR")
    conn.close()


def test_cash_conditions_and_short_rejected(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    book.cash_event(conn, "deposit", 10_000, note="initial")
    book.cash_event(conn, "withdrawal", 500)
    book.cash_event(conn, "dividend", 1250, currency="GBp")  # 1,250p
    book.cash_event(conn, "fee", 2.0)
    assert book.cash_balance(conn) == pytest.approx(10_000 - 500 + 12.5 - 2)
    with pytest.raises(ValueError, match="cash kind"):
        book.cash_event(conn, "loan", 1)

    pid, _ = _draft(conn)
    card = Card(pd.Timestamp("2026-10-09", tz="UTC"), 0.5, "hold", True)
    n = book.add_entry_conditions(conn, pid, 7, {"technical": card, "research": {"label": "ACT"}})
    assert n == 5
    ec = book.entry_conditions(conn, pid).set_index(["model", "field"])
    assert ec.loc[("technical", "trend"), "value"] == 0.5
    assert ec.loc[("technical", "flag"), "value"] == 1.0
    assert ec.loc[("technical", "label"), "text"] == "hold"
    assert ec.loc[("technical", "date"), "text"] == "2026-10-09"
    assert ec.loc[("research", "label"), "text"] == "ACT"
    assert set(ec["run_id"]) == {7}

    book.add_thesis_condition(conn, pid, "trend < 0", "machine")
    book.add_thesis_condition(conn, pid, "FY27 guidance cut", "agent", evidence_id=3)
    tc = book.thesis_conditions(conn, pid)
    assert list(tc["kind"]) == ["machine", "agent"]
    assert tc.loc[1, "evidence_id"] == 3
    with pytest.raises(ValueError, match="condition kind"):
        book.add_thesis_condition(conn, pid, "x", "vibes")

    with pytest.raises(ValueError, match="long_only"):
        book.new_position(conn, "JET2.L", side=-1)
    r = load_rules()
    shorts_ok = Rules(**{**r.__dict__, "long_only": False})
    assert book.new_position(conn, "JET2.L", side=-1, rules=shorts_ok) == pid + 1
    with pytest.raises(NotImplementedError, match="quote currency unknown"):
        book.new_position(conn, "AAPL")
    assert book.position_state(conn, pid).shares == 0
    assert math.isnan(book.position_state(conn, pid).entry_avg)
    conn.close()


def test_renew_extends_an_entry_and_undo_restores_it(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    with conn:
        pid, oid = book.draft_position(
            conn, "JET2.L", 1500.0, 1400.0, 10, at="2026-10-16T18:00:00+00:00", valid_days=10
        )
        book.approve_position(conn, pid)
        with pytest.raises(ValueError, match="already valid until 2026-10-26"):
            book.accept_revision(
                conn,
                book.propose_revision(
                    conn, pid, "renew", {"valid_until": "2026-10-20"}, "x", book.OWNER
                ),
            )
        rid = book.propose_revision(
            conn, pid, "renew", {"valid_until": "2026-11-01"}, "still on", "agent:pm"
        )
    assert book.revisions(conn, pid).set_index("revision_id").loc[rid, "status"] == "accepted"
    assert book._order(conn, oid)["valid_until"] == "2026-11-01"
    with conn:  # a later amend keeps the renewed validity
        new = book.amend_position(conn, pid, price=1490.0)["new"]["order_id"]
    assert book._order(conn, new)["valid_until"] == "2026-11-01"
    assert book.expire_orders(conn, "2026-10-28") == []
    with conn:
        book.undo_revision(conn, rid, "changed my mind")
    assert book._order(conn, new)["valid_until"] == "2026-10-26"
    conn.close()


def test_expire_ends_an_add_past_its_validity_and_the_stop_stays(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    with conn:
        book.cash_event(conn, "deposit", 50000.0)
        pid, oid = book.draft_position(conn, "JET2.L", 1500.0, 1400.0, 10)
        book.approve_position(conn, pid)
        book.fill(conn, oid, 10, 1500.0)
        add = book.add_order(
            conn, pid, 1, "limit", 5, 1520.0, "add", "2026-01-02", "approved", "agent:monitor"
        )
        assert book.expire_orders(conn, "2026-01-03") == [add]
    od = book.orders(conn, pid)
    assert book.order_status(conn, add) == "expired"
    assert ((od["purpose"] == "stop") & od["status"].isin(list(book.LIVE_ORDER))).any()
    conn.close()
