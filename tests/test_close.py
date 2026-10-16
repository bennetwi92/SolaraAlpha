from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha.portfolio import book

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import book as cli


def _open(conn: book.Conn, qty: int = 100, entry: float = 1500.0, stop: float = 1400.0) -> int:
    pid, oid = book.draft_position(conn, "JET2.L", entry, stop, qty, at="2026-10-18T18:00:00+00:00")
    book.approve_position(conn, pid)
    book.place_order(conn, oid)
    book.fill(conn, oid, qty, entry, at="2026-10-19T09:00:00+00:00", commission=5.0)
    return pid


def _daily() -> pd.DataFrame:
    idx = pd.bdate_range("2026-10-12", "2026-12-04", tz="UTC", name="date")
    close = np.full(len(idx), 1500.0)
    df = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 1e5,
        },
        index=idx,
    )
    df.loc["2026-10-26", ["high"]] = 1750.0  # inside the trade
    df.loc["2026-10-27", ["low"]] = 1440.0
    df.loc["2026-11-25", ["high"]] = 2500.0  # after the exit: must not count
    df.attrs = {"ticker": "JET2.L", "currency": "GBp"}
    return df


def test_win_via_target_exit(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    pid = _open(conn)
    xid = book.exit_position(conn, pid, "target", "limit", 1700.0)
    assert book.order_status(conn, xid) == "approved"
    out = book.fill(conn, xid, 100, 1700.0, at="2026-11-13T15:00:00+00:00", commission=5.0)
    c = out["closed"]
    assert c is not None and c.exit_reason == "target"
    assert c.gross_pnl == pytest.approx(200.0)  # 200p x 100 shares = £200
    assert c.costs == 10.0 and c.net_pnl == pytest.approx(190.0)
    assert c.r_multiple == pytest.approx(190.0 / 100.0)  # initial risk 100p x 100 = £100
    assert c.holding_weeks == pytest.approx(25.25 / 7)
    assert c.entry_avg == 1500.0 and c.exit_avg == 1700.0 and c.shares == 100
    assert math.isnan(c.mfe)  # no prices in the fill path
    assert book.position_status(conn, pid) == "closed"
    od = book.orders(conn, pid)
    assert list(od["status"]) == ["filled", "cancelled", "filled"]  # the stop was cancelled
    ev = book.position_events(conn, pid)
    assert ev["kind"].iloc[-1] == "closed" and ev["payload"].iloc[-1]["net_pnl"] == pytest.approx(
        190.0
    )

    with pytest.raises(ValueError, match="is cancelled; cannot fill"):
        book.add_fill(conn, int(od["order_id"].iloc[1]), 1, 1400.0)  # fills after close
    with pytest.raises(ValueError, match="is closed"):
        book.exit_position(conn, pid, "time")
    conn.close()


def test_loss_via_stop_fill_auto_closes(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    pid = _open(conn)
    tgt = book.add_order(conn, pid, -1, "limit", 100, 1800.0, "target", status="approved")
    stop_id = int(book.orders(conn, pid).query("purpose == 'stop'")["order_id"].iloc[0])
    with pytest.raises(ValueError, match="still holds 100 shares"):
        book.close_position(conn, pid, "stop")
    out = book.fill(conn, stop_id, 100, 1395.0, at="2026-10-23T09:30:00+00:00", commission=5.0)
    c = out["closed"]
    assert c is not None and c.exit_reason == "stop"
    assert c.net_pnl == pytest.approx(-105.0 - 10.0)
    assert c.r_multiple == pytest.approx(-1.15)
    assert book.order_status(conn, tgt) == "cancelled"
    assert book.position_status(conn, pid) == "closed"
    conn.close()


def test_trailing_stop_tag_after_stop_raised(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    pid = _open(conn)
    stop_id = int(book.orders(conn, pid).query("purpose == 'stop'")["order_id"].iloc[0])
    new_stop = book.replace_order(conn, stop_id, price=1550.0, reason="trail")
    book.position_event(conn, pid, "stop_raised", {"old": 1400.0, "new": 1550.0}, actor="pipeline")
    assert book.position_state(conn, pid).stop == 1550.0
    out = book.fill(conn, new_stop, 100, 1549.0, at="2026-11-02T10:00:00+00:00")
    assert out["closed"] is not None and out["closed"].exit_reason == "trailing_stop"
    assert out["closed"].net_pnl == pytest.approx(49.0 - 5.0)
    conn.close()


def test_mfe_mae_from_prices_and_pence(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    pid = _open(conn)
    xid = book.exit_position(conn, pid, "discretionary")
    book.fill(conn, xid, 100, 1600.0, at="2026-11-10T12:00:00+00:00")
    c = book.close_summary(conn, pid, _daily())
    assert c.mfe == pytest.approx(250 / 1500)  # 1750 high, not the 2500 after the exit
    assert c.mae == pytest.approx(-60 / 1500)
    assert c.mfe_r == pytest.approx(250 / 100)  # risk per share 100p
    assert c.mae_r == pytest.approx(-0.6)
    assert c.net_pnl == pytest.approx(100.0 - 5.0)  # pence -> GBP
    assert c.closed_at == "2026-11-10T12:00:00+00:00"
    assert c.exit_reason == "discretionary"
    conn.close()


def test_cli_exit_and_show(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "book.db"
    conn = book.connect(db)
    with conn:
        pid = _open(conn)
    conn.close()
    with pytest.raises(SystemExit):
        cli.main(["--db", str(db), "exit", str(pid), "--reason", "because"])
    cli.main(["--db", str(db), "exit", str(pid), "--reason", "thesis_broken", "--qty", "100"])
    assert "exit order 3 approved (thesis_broken)" in capsys.readouterr().out
    cli.main(
        [
            "--db",
            str(db),
            "fill",
            "3",
            "--qty",
            "100",
            "--price",
            "1450",
            "--at",
            "2026-11-03T10:00:00+00:00",
        ]
    )
    out = capsys.readouterr().out
    assert "position closed (thesis_broken): net £-55.00  -0.55R" in out
    monkeypatch.setattr(cli, "fetch_daily", lambda ticker: _daily())
    cli.main(["--db", str(db), "show", str(pid)])
    out = capsys.readouterr().out
    assert "Close summary:" in out and "exit_reason" in out and "thesis_broken" in out
    assert "mfe" in out
    monkeypatch.setattr(
        cli, "fetch_daily", lambda ticker: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    cli.main(["--db", str(db), "show", str(pid)])
    assert "excursions unavailable: offline" in capsys.readouterr().out
