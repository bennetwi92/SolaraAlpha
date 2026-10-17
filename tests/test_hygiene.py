from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha import store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.triggers import stale_order_triggers

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import book as cli


def test_stale_orders_boundaries(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    with conn:
        # valid until the 16th: stale from the 17th on
        p1, o1 = book.draft_position(
            conn, "A.L", 100.0, 90.0, 10, valid_days=3, at="2026-10-13T18:00:00+00:00"
        )
        book.approve_position(conn, p1)
        book.draft_position(
            conn, "B.L", 100.0, 90.0, 10, valid_days=3, at="2026-10-13T18:00:00+00:00"
        )  # draft: not stale
        p3, o3 = book.draft_position(
            conn, "C.L", 100.0, 90.0, 10, valid_days=3, at="2026-10-13T18:00:00+00:00"
        )
        book.approve_position(conn, p3)
        book.place_order(conn, o3)
        book.fill(conn, o3, 10, 100.0, at="2026-10-14T09:00:00+00:00")  # filled: not stale
        p4, _ = book.draft_position(
            conn, "D.L", 100.0, 90.0, 10, at="2026-10-13T18:00:00+00:00"
        )  # no validity
        book.approve_position(conn, p4)
    assert book.stale_orders(conn, "2026-10-16T18:00:00+00:00").empty  # on the day: still valid
    stale = book.stale_orders(conn, "2026-10-17T07:00:00+00:00")
    assert stale["order_id"].tolist() == [o1] and stale["ticker"].tolist() == ["A.L"]
    assert stale["status"].iloc[0] == "approved" and stale["valid_until"].iloc[0] == "2026-10-16"
    trig = stale_order_triggers(stale, pd.Timestamp("2026-10-17", tz="UTC"))
    assert len(trig) == 1 and trig[0].kind == "order_stale" and trig[0].order_id == o1
    assert trig[0].detail == {
        "status": "approved",
        "purpose": "entry",
        "valid_until": "2026-10-16",
        "qty": 10,
        "price": 100.0,
    }
    assert trig[0].position_id == p1 and trig[0].at == "2026-10-17"
    with conn:
        book.end_order(conn, o1, "expired", at="2026-10-17T08:00:00+00:00")
    assert book.stale_orders(conn, "2026-10-17T09:00:00+00:00").empty
    assert (
        stale_order_triggers(
            book.stale_orders(conn, "2026-10-17T09:00:00+00:00"), pd.Timestamp("2026-10-17")
        )
        == []
    )
    conn.close()


def test_approval_stats_on_a_fixture_book(
    db_backend: Path | None, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = book.connect(db_backend)
    with conn:
        run = store.start_run("scan", {}, conn=conn)
        for t, stages in (
            ("A.L", ("screen", "validate", "research")),
            ("B.L", ("screen", "validate")),
            ("C.L", ("screen",)),
        ):
            for s in stages:
                store.save_label(run, t, s, "ACT", "", conn=conn)
        # A.L (research): approved as drafted; B.L (validate): amended then approved; C.L (screen): rejected
        pa, _ = book.draft_position(
            conn, "A.L", 100.0, 90.0, 10, opened_from_run_id=run, at="2026-10-11T18:00:00+00:00"
        )
        book.approve_position(conn, pa)
        pb, _ = book.draft_position(
            conn, "B.L", 200.0, 180.0, 50, opened_from_run_id=run, at="2026-10-11T18:00:00+00:00"
        )
        book.amend_position(conn, pb, qty=40, price=210.0, reason="smaller")
        book.approve_position(conn, pb)
        pc, _ = book.draft_position(
            conn, "C.L", 300.0, 270.0, 20, opened_from_run_id=run, at="2026-10-11T18:00:00+00:00"
        )
        book.reject_position(conn, pc, "Liquidity too thin")
        # by hand: drafted, amended twice (qty only), rejected
        ph, _ = book.draft_position(conn, "H.L", 50.0, 45.0, 100, at="2026-10-12T18:00:00+00:00")
        book.amend_position(conn, ph, qty=50, reason="half")
        book.amend_position(conn, ph, qty=75, reason="more")
        book.reject_position(conn, ph, "liquidity: wide spread")
    pos = book.positions(conn)
    ev = book.all_position_events(conn)
    labels = store.query("SELECT run_id, ticker, stage FROM labels", conn)
    df = book.approval_stats(pos, ev, labels)
    assert df["stage"].tolist() == ["research", "validate", "screen", "hand", "all"]
    rows = {str(r["stage"]): r for r in df.to_dict("records")}
    counts = ("drafted", "approved", "amended", "rejected")
    assert [rows["research"][k] for k in counts] == [1, 1, 0, 0]
    assert [rows["validate"][k] for k in counts] == [1, 1, 1, 0]
    assert rows["validate"]["avg_qty_change_pct"] == pytest.approx(0.2)
    assert rows["validate"]["avg_price_change_pct"] == pytest.approx(0.05)
    assert rows["screen"]["rejected"] == 1 and rows["screen"]["rejection_reasons"] == "liquidity:1"
    assert math.isnan(float(rows["screen"]["avg_qty_change_pct"]))
    assert [rows["hand"][k] for k in ("drafted", "amended", "rejected")] == [
        1,
        1,
        1,
    ]  # two amendments
    assert rows["hand"]["avg_qty_change_pct"] == pytest.approx((0.5 + 0.5) / 2)
    assert [rows["all"][k] for k in counts] == [4, 2, 2, 2]
    assert rows["all"]["rejection_reasons"] == "liquidity:2"
    assert book.approval_stats(pos.iloc[0:0], ev, labels).empty
    conn.close()
    if db_backend is not None:
        cli.main(["--db", str(db_backend), "stats"])
        out = capsys.readouterr().out
        assert (
            "4 positions" in out and "research" in out and "liquidity:2" in out and "20.0%" in out
        )
        assert "stale orders (past validity, no fill): 0" in out
        cli.main(["--db", str(db_backend), "stats", "--since", "2026-10-12"])
        out = capsys.readouterr().out
        assert "1 positions since 2026-10-12" in out and "hand" in out and "research" not in out
