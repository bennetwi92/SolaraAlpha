from __future__ import annotations

import sys
from pathlib import Path

import pytest

from solaraalpha.portfolio import book
from solaraalpha.portfolio.rules import Rules, load_rules

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import book as cli


def rules(**over: object) -> Rules:
    return Rules(**{**load_rules().__dict__, **over})


def _open(conn: book.Conn, deposit: float = 20_000.0) -> int:
    """100 JET2.L bought at 1500p with a 1400p stop (£1,500 at cost, £100 at risk)."""
    book.cash_event(conn, "deposit", deposit, "GBP", at="2026-09-01T00:00:00+00:00")
    pid, oid = book.draft_position(
        conn, "JET2.L", 1500.0, 1400.0, 100, at="2026-09-13T18:00:00+00:00"
    )
    book.approve_position(conn, pid)
    book.fill(conn, oid, 100, 1500.0, at="2026-09-14T09:00:00+00:00", commission=5.0)
    return pid


def test_add_breaches_names_each_limit(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    with conn:
        pid = _open(conn, deposit=5_000.0)
    st = book.position_state(conn, pid)
    # 50 more at 1700p, stop to 1500p: 150 x 1700p = £2,550 of ~£5,000 equity
    assert book.add_breaches(conn, st, 50, 1700.0, 1500.0, rules()) == [
        "position 51% of equity > max_position_pct 25%",
        "sector Travel & Leisure 51% > max_sector_pct 40%",
    ]
    loose = rules(max_position_pct=1.0, max_sector_pct=1.0)
    assert book.add_breaches(conn, st, 10, 1700.0, 1500.0, loose) == []
    # without a stop raise: 200 at an average 1600p to the 1400p stop = £400 at risk, 8% heat
    assert book.add_breaches(conn, st, 100, 1700.0, None, loose) == []
    assert book.add_breaches(
        conn, st, 100, 1700.0, None, rules(max_position_pct=1.0, max_sector_pct=1.0, max_heat=0.05)
    ) == ["heat 8.0% > max_heat 5.0%"]
    # cash: £3,495 left; 210 more at 1550p is £3,255, leaving less than the 5% buffer (£250)
    assert book.add_breaches(conn, st, 210, 1550.0, 1500.0, loose) == [
        "cash £240 after the add < cash_buffer 5%"
    ]
    conn.close()


def test_agent_add_drafts_the_add_and_raises_the_stop(
    db_backend: Path | None, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = book.connect(db_backend)
    with conn:
        pid = _open(conn)
        rid = book.propose_revision(
            conn,
            pid,
            "add",
            {
                "qty": 50,
                "entry": 1676.6,
                "type": "limit",
                "stop": 1500.0,
                "valid_until": "2026-10-16",
            },
            "breakout on volume",
            "agent:monitor",
            at="2026-10-11T18:00:00+00:00",
        )
    rv = book.revisions(conn, pid)
    assert len(rv) == 1 and rv["status"].iloc[0] == "accepted"
    done = rv["done"].iloc[0]
    assert set(done) == {"revision_id", "action", "stop_order_id", "add_order_id"}
    od = book.orders(conn, pid).set_index("order_id")
    add = od.loc[done["add_order_id"]]
    assert add["purpose"] == "add" and add["side"] == 1 and add["qty"] == 50
    assert add["status"] == "approved" and add["price"] == pytest.approx(1676.6)
    assert add["valid_until"] == "2026-10-16"
    # one order per unit (ADR 0031): the buy works beside the stop, raised to 1500 at once
    stop = od.loc[done["stop_order_id"]]
    assert stop["purpose"] == "stop" and stop["price"] == 1500.0 and stop["status"] == "approved"
    assert stop["qty"] == 100
    st = book.position_state(conn, pid)
    assert st.stop == 1500.0 and st.shares == 100
    # the fill of the add increases the shares and re-sizes the protective stop to 150 at 1500
    with conn:
        out = book.fill(conn, int(done["add_order_id"]), 50, 1676.0, at="2026-10-12T09:00:00+00:00")
    st = book.position_state(conn, pid)
    assert st.shares == 150 and out["stop_order_id"] is not None
    live = book.orders(conn, pid, status="approved")
    stops = live[live["purpose"] == "stop"]
    assert len(stops) == 1 and stops["qty"].iloc[0] == 150 and stops["price"].iloc[0] == 1500.0
    assert st.avg_cost == pytest.approx((1500.0 * 100 + 1676.0 * 50) / 150)
    # a second add at a lower stop never lowers the stop
    with conn:
        rid2 = book.propose_revision(
            conn, pid, "add", {"qty": 10, "entry": 1700.0, "stop": 1450.0}, "x", "agent:monitor"
        )
    done2 = dict(book.revisions(conn, pid).set_index("revision_id")["done"][rid2])
    assert "stop_order_id" not in done2 and book.position_state(conn, pid).stop == 1500.0
    # while that add works, another is refused; a bad one too
    with conn:
        rid3 = book.propose_revision(
            conn, pid, "add", {"qty": 5, "entry": 1700.0}, "x", "agent:monitor"
        )
        rid4 = book.propose_revision(
            conn, pid, "add", {"qty": 0, "entry": 1700.0}, "x", "agent:monitor"
        )
    rv = book.revisions(conn, pid).set_index("revision_id")
    assert rv.loc[rid3, "status"] == "declined" and "working" in str(
        rv.loc[rid3, "decision_reason"]
    )
    assert rv.loc[rid4, "status"] == "declined"
    # the add ends unfilled: the stop stays on every share at the level
    with conn:
        book.end_order(conn, int(done2["add_order_id"]), "not_filled")
    stops = book.orders(conn, pid, status="approved")
    assert stops["purpose"].tolist() == ["stop"] and stops["qty"].iloc[0] == 150
    assert stops["price"].iloc[0] == 1500.0
    # an add over a limit is declined as it is recorded; nothing changes
    n_orders = len(book.orders(conn, pid))
    with conn:
        book.propose_revision(
            conn, pid, "add", {"qty": 2000, "entry": 1700.0, "stop": 1600.0}, "x", "agent:monitor"
        )
    last = book.revisions(conn, pid).iloc[-1]
    assert last["status"] == "declined" and "max_position_pct" in str(last["decision_reason"])
    assert len(book.orders(conn, pid)) == n_orders
    conn.close()
    assert rid > 0
    if db_backend is not None:
        cli.main(["--db", str(db_backend), "revisions", "--status", "all"])
        out_text = capsys.readouterr().out
        assert "add" in out_text and "accepted" in out_text and "declined" in out_text
