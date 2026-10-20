from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha.portfolio import book

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import book as cli


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "book.db"


def run(db: Path, *args: str, capsys: pytest.CaptureFixture[str]) -> str:
    cli.main(["--db", str(db), *args])
    return capsys.readouterr().out


def test_new_approve_tickets_placed_round_trip(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = run(
        db,
        "new",
        "JET2.L",
        "--entry",
        "1520",
        "--stop",
        "1400",
        "--qty",
        "100",
        "--target",
        "1800",
        "--thesis",
        "coil",
        capsys=capsys,
    )
    assert "position 1 drafted for JET2.L: entry order 1 (limit 100)" in out
    assert "placeholder portfolio rules" not in out  # confirmed by the owner

    out = run(db, "list", capsys=capsys)
    assert "JET2.L" in out and "draft" in out and "limit 100 @ 1,520p (draft)" in out
    assert run(db, "list", "--status", "open", capsys=capsys).strip() == "(none)"
    assert "(none)" in run(db, "tickets", capsys=capsys)

    run(db, "approve", "1", "--reason", "ok", capsys=capsys)
    out = run(db, "tickets", capsys=capsys)
    assert "JET2.L" in out and "BUY" in out and "1,520p" in out and "entry" in out
    conn = book.connect(db)
    assert book.position_status(conn, 1) == "approved"
    assert book.order_status(conn, 1) == "approved"
    conn.close()

    out = run(
        db, "placed", "1", "--ref", "B-77", "--at", "2026-10-19T08:05:00+00:00", capsys=capsys
    )
    assert "order 1 placed (B-77)" in out
    assert "(none)" in run(db, "tickets", capsys=capsys)
    out = run(db, "show", "1", capsys=capsys)
    assert "Position 1  JET2.L  approved" in out
    assert "thesis: coil" in out
    assert "B-77" in out and "placed" in out and "drafted" in out and "approved" in out
    assert "shares 0" in out


def test_amend_replaces_the_order(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(db, "new", "JET2.L", "--entry", "1520", "--stop", "1400", "--qty", "100", capsys=capsys)
    run(db, "approve", "1", capsys=capsys)
    out = run(
        db,
        "amend",
        "1",
        "--qty",
        "80",
        "--price",
        "1500",
        "--stop",
        "1410",
        "--reason",
        "smaller",
        capsys=capsys,
    )
    assert "amended" in out
    conn = book.connect(db)
    od = book.orders(conn, 1)
    assert list(od["status"]) == ["cancelled", "approved"]
    assert od.loc[1, "qty"] == 80 and od.loc[1, "price"] == 1500.0
    assert od.loc[1, "purpose"] == "entry" and od.loc[1, "valid_until"] == od.loc[0, "valid_until"]
    ev = book.position_events(conn, 1)
    assert list(ev["kind"]) == ["drafted", "approved", "amended"]
    payload = ev["payload"].iloc[2]
    assert payload["old"]["qty"] == 100 and payload["new"]["stop"] == 1410
    assert book.position_state(conn, 1).initial_stop == 1410.0
    conn.close()
    out = run(db, "tickets", capsys=capsys)
    assert "80" in out and "1,500p" in out

    # stop-only amend keeps the order
    run(db, "amend", "1", "--stop", "1420", capsys=capsys)
    conn = book.connect(db)
    assert list(book.orders(conn, 1)["status"]) == ["cancelled", "approved"]
    assert book.position_state(conn, 1).initial_stop == 1420.0
    conn.close()


def test_reject_needs_reason_and_cancels_orders(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(db, "new", "JET2.L", "--entry", "1520", "--stop", "1400", "--qty", "100", capsys=capsys)
    with pytest.raises(SystemExit):
        cli.main(["--db", str(db), "reject", "1"])
    out = run(db, "reject", "1", "--reason", "too extended", capsys=capsys)
    assert "rejected" in out
    conn = book.connect(db)
    assert book.position_status(conn, 1) == "rejected"
    assert list(book.orders(conn, 1)["status"]) == ["cancelled"]
    conn.close()
    with pytest.raises(SystemExit) as e:
        cli.main(["--db", str(db), "approve", "1"])
    assert e.value.code == 2
    assert "is rejected; cannot record 'approved'" in capsys.readouterr().out


def test_market_entry_and_validation(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="--entry is required"):
        cli.main(["--db", str(db), "new", "JET2.L", "--stop", "1400", "--qty", "10"])
    run(
        db,
        "new",
        "JET2.L",
        "--type",
        "market",
        "--stop",
        "1400",
        "--qty",
        "10",
        "--valid-days",
        "3",
        capsys=capsys,
    )
    out = run(db, "show", "1", capsys=capsys)
    assert "market" in out
    conn = book.connect(db)
    od = book.orders(conn, 1)
    assert (od.loc[0, "type"] == "market" and od.loc[0, "price"] is None) or od[
        "price"
    ].isna().all()
    conn.close()


def _approved_entry(db: Path, capsys: pytest.CaptureFixture[str], qty: int = 100) -> None:
    run(db, "new", "JET2.L", "--entry", "1520", "--stop", "1400", "--qty", str(qty), capsys=capsys)
    run(db, "approve", "1", capsys=capsys)
    run(db, "placed", "1", capsys=capsys)


def test_full_fill_opens_and_drafts_stop(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _approved_entry(db, capsys)
    out = run(
        db,
        "fill",
        "1",
        "--qty",
        "100",
        "--price",
        "1518",
        "--commission",
        "9.95",
        "--at",
        "2026-10-19T09:01:00+00:00",
        capsys=capsys,
    )
    assert "order 1 filled; position holds 100 shares; position opened" in out
    assert "protective stop order 2 approved" in out
    conn = book.connect(db)
    assert book.position_status(conn, 1) == "open"
    od = book.orders(conn, 1)
    assert od.loc[1, "purpose"] == "stop" and od.loc[1, "type"] == "stop"
    assert od.loc[1, "qty"] == 100 and od.loc[1, "price"] == 1400.0 and od.loc[1, "side"] == -1
    assert od.loc[1, "status"] == "approved"
    st = book.position_state(conn, 1)
    assert st.avg_cost == 1518.0 and st.costs == 9.95 and st.stop == 1400.0
    assert st.risk_at_stop == pytest.approx(118 * 100 / 100)
    conn.close()
    out = run(db, "tickets", capsys=capsys)
    assert "SELL" in out and "stop" in out and "1,400p" in out
    out = run(db, "show", "1", "--price", "1636", capsys=capsys)
    assert "shares 100" in out and "+1.00R" in out and "unrealised £118.00" in out


def test_two_part_fills_resize_the_stop(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _approved_entry(db, capsys)
    out = run(db, "fill", "1", "--qty", "40", "--price", "1510", "--commission", "5", capsys=capsys)
    assert "part_filled; position holds 40 shares; position opened" in out
    out = run(db, "fill", "1", "--qty", "60", "--price", "1530", "--commission", "5", capsys=capsys)
    assert "order 1 filled; position holds 100 shares;" in out and "opened" not in out
    conn = book.connect(db)
    od = book.orders(conn, 1)
    stops = od[od["purpose"] == "stop"]
    assert list(stops["status"]) == ["cancelled", "approved"]
    assert list(stops["qty"]) == [40, 100]
    st = book.position_state(conn, 1)
    assert st.avg_cost == pytest.approx((40 * 1510 + 60 * 1530) / 100)
    assert st.costs == 10.0
    assert st.stop == 1400.0 and st.entry_shares == 100
    conn.close()


def test_part_fill_then_cancel_keeps_position_open(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _approved_entry(db, capsys)
    run(db, "fill", "1", "--qty", "30", "--price", "1500", capsys=capsys)
    out = run(db, "cancel", "1", "--reason", "rest unfilled", capsys=capsys)
    assert out.strip() == "order 1 cancelled"
    conn = book.connect(db)
    assert book.position_status(conn, 1) == "open"
    assert book.position_state(conn, 1).shares == 30
    assert book.orders(conn, 1).loc[1, "status"] == "approved"  # the stop stays
    conn.close()


def test_no_fill_then_unfilled_cancels_position(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _approved_entry(db, capsys)
    out = run(db, "unfilled", "1", "--reason", "never traded", capsys=capsys)
    assert out.strip() == "order 1 not filled; position cancelled"
    conn = book.connect(db)
    assert book.position_status(conn, 1) == "cancelled"
    assert book.order_status(conn, 1) == "not_filled"
    conn.close()


def test_expire_respects_valid_until(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(
        db,
        "new",
        "JET2.L",
        "--entry",
        "1520",
        "--stop",
        "1400",
        "--qty",
        "10",
        "--valid-days",
        "7",
        capsys=capsys,
    )
    run(db, "approve", "1", capsys=capsys)
    conn = book.connect(db)
    valid_until = str(book.orders(conn, 1)["valid_until"].iloc[0])
    conn.close()
    assert (
        run(db, "expire", "--as-of", valid_until, capsys=capsys).strip() == "expired orders: none"
    )
    day_after = (pd.Timestamp(valid_until) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    assert run(db, "expire", "--as-of", day_after, capsys=capsys).strip() == "expired orders: 1"
    conn = book.connect(db)
    assert book.order_status(conn, 1) == "expired"
    assert book.position_status(conn, 1) == "cancelled"
    conn.close()


def test_over_fill_raises(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _approved_entry(db, capsys, qty=50)
    with pytest.raises(SystemExit) as e:
        cli.main(["--db", str(db), "fill", "1", "--qty", "51", "--price", "1500"])
    assert e.value.code == 2 and "over-fill: 51 of 50" in capsys.readouterr().out
