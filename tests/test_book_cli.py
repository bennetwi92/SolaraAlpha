from __future__ import annotations

import sys
from pathlib import Path

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
    assert "placeholder portfolio rules" in out

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
