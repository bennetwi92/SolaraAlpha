from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

from solaraalpha.portfolio import book
from solaraalpha.portfolio.journal import closed_trades, journal, setup_type

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import learn


def _trade(
    conn: book.Conn,
    ticker: str,
    exit_price: float,
    reason: str,
    type: str = "limit",
    target: float | None = None,
) -> int:
    rr = (1700.0 - 1500.0) / 100.0 if target else "inf"
    pid, oid = book.draft_position(
        conn,
        ticker,
        1500.0,
        1400.0,
        100,
        type=type,
        target=target,
        payload={"reward_risk": rr},
        at="2026-10-18T18:00:00+00:00",
    )
    book.approve_position(conn, pid)
    book.place_order(conn, oid)
    book.fill(conn, oid, 100, 1500.0, at="2026-10-19T09:00:00+00:00", commission=5.0)
    xid = book.exit_position(conn, pid, reason, "limit", exit_price)
    book.fill(conn, xid, 100, exit_price, at="2026-11-16T15:00:00+00:00", commission=5.0)
    return pid


def test_setup_type() -> None:
    assert setup_type({}) == "blue_sky"
    assert setup_type({"target": None, "entry_type": "stop"}) == "blue_sky"
    assert setup_type({"target": math.nan, "entry_type": "limit"}) == "blue_sky"
    assert setup_type({"target": 1700.0, "reward_risk": "inf"}) == "blue_sky"
    assert setup_type({"target": 1700.0, "reward_risk": 2.0, "entry_type": "stop"}) == "breakout"
    assert setup_type({"target": 1700.0, "reward_risk": 2.0, "entry_type": "limit"}) == "pullback"


def test_journal_on_a_fixture_book(
    db_backend: Path | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = book.connect(db_backend)
    with conn:
        _trade(conn, "A.L", 1700.0, "target", target=1700.0)  # limit entry: pullback
        _trade(conn, "B.L", 1400.0, "stop", type="stop", target=1800.0)  # stop entry: breakout
        _trade(conn, "C.L", 1600.0, "time")  # no plan: blue sky
        book.draft_position(conn, "D.L", 500.0, 450.0, 100)  # never closed
    closed = closed_trades(conn)
    assert list(closed["ticker"]) == ["A.L", "B.L", "C.L"]
    assert list(closed["exit_reason"]) == ["target", "stop", "time"]
    assert list(closed["setup"]) == ["pullback", "breakout", "blue_sky"]
    assert closed["net_pnl"].tolist() == pytest.approx([190.0, -110.0, 90.0])
    assert closed["r_multiple"].tolist() == pytest.approx([1.9, -1.1, 0.9])
    assert closed["holding_weeks"].iloc[0] == pytest.approx(4.0 + 6 / 24 / 7)
    assert closed["mfe_r"].isna().all()  # no prices in the fill path

    j = journal(closed)
    allrow = j[j["by"] == "all"].iloc[0]
    assert allrow["trades"] == 3 and allrow["hit_rate"] == pytest.approx(2 / 3)
    assert allrow["avg_r"] == pytest.approx((1.9 - 1.1 + 0.9) / 3)
    assert allrow["expectancy_r"] == allrow["avg_r"]
    assert allrow["total_pnl"] == pytest.approx(170.0)
    assert math.isnan(allrow["avg_mfe_r"])
    stop = j[(j["by"] == "exit_reason") & (j["group"] == "stop")].iloc[0]
    assert stop["trades"] == 1 and stop["hit_rate"] == 0.0 and stop["avg_r"] == pytest.approx(-1.1)
    assert sorted(j.loc[j["by"] == "setup", "group"]) == ["blue_sky", "breakout", "pullback"]
    assert journal(closed.iloc[0:0]).empty
    conn.close()

    if db_backend is not None:
        learn.main(["journal", "--db", str(db_backend)])
        out = capsys.readouterr().out
        assert "3 closed trades" in out and "breakout" in out and "exit_reason" in out
        learn.main(["journal", "--db", str(db_backend), "--since", "2030-01-01"])
        assert "No closed trades yet" in capsys.readouterr().out
    empty = tmp_path / "empty.db"
    book.connect(empty).close()
    learn.main(["journal", "--db", str(empty)])
    assert "No closed trades yet" in capsys.readouterr().out
