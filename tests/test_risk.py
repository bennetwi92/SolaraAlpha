from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha.portfolio import book, risk
from solaraalpha.portfolio.rules import Rules, load_rules

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import book as book_cli
import portfolio as portfolio_cli

NOW = "2026-11-02T18:00:00+00:00"


def _book(path: Path | None) -> book.Conn:
    """£10k deposited; JET2.L open (100 @ 1500p, stop 1400p, £5 commission); EZJ.L closed at
    +£90 net (100 @ 500p -> 600p, £10 costs)."""
    conn = book.connect(path)
    with conn:
        book.cash_event(conn, "deposit", 10_000, at="2026-10-01T09:00:00+00:00")
        pid, oid = book.draft_position(
            conn, "JET2.L", 1500.0, 1400.0, 100, at="2026-10-18T18:00:00+00:00"
        )
        book.approve_position(conn, pid)
        book.fill(conn, oid, 100, 1500.0, at="2026-10-19T09:00:00+00:00", commission=5.0)
        pid2, oid2 = book.draft_position(
            conn, "EZJ.L", 500.0, 450.0, 100, at="2026-10-04T18:00:00+00:00"
        )
        book.approve_position(conn, pid2)
        book.fill(conn, oid2, 100, 500.0, at="2026-10-05T09:00:00+00:00", commission=5.0)
        xid = book.exit_position(conn, pid2, "target", "limit", 600.0)
        book.fill(conn, xid, 100, 600.0, at="2026-10-26T09:00:00+00:00", commission=5.0)
    return conn


def test_holdings_equity_heat_exposure(db_backend: Path | None) -> None:
    conn = _book(db_backend)
    held, eq, states = risk.snapshot(conn, {"JET2.L": 1600.0}, as_of=NOW)
    conn.close()
    assert list(held["ticker"]) == ["JET2.L"]
    row = held.iloc[0]
    assert row["shares"] == 100 and row["avg_cost"] == 1500.0 and row["last"] == 1600.0
    assert row["value"] == pytest.approx(1600.0)  # 160,000p = £1,600
    assert row["unrealised"] == pytest.approx(100.0) and row["unrealised_pct"] == pytest.approx(
        100 / 1500
    )
    assert (
        row["r"] == pytest.approx(1.0)
        and row["stop"] == 1400.0
        and row["risk"] == pytest.approx(100.0)
    )
    assert row["weeks_held"] == pytest.approx(14.375 / 7)
    assert row["sector"] == "Travel & Leisure" and row["next_event"] == ""

    assert eq.deposits == 10_000 and eq.withdrawals == 0
    assert eq.realised_pnl == pytest.approx(
        90.0 - 5.0
    )  # closed trade net + open trade's commission
    assert eq.open_cost == pytest.approx(1500.0)
    assert eq.cash == pytest.approx(10_000 + 85.0 - 1500.0)
    assert eq.market_value == pytest.approx(1600.0)
    assert eq.equity == pytest.approx(10_000 + 85.0 + 100.0)
    assert eq.unrealised_pnl == pytest.approx(100.0) and eq.positions == 1
    assert risk.heat(held, eq) == pytest.approx(100.0 / eq.equity)
    ex = risk.exposure(held, eq)
    assert ex.index.tolist() == ["Travel & Leisure"] and ex.iloc[0] == pytest.approx(
        1600 / eq.equity
    )
    assert risk.breaches(held, eq, load_rules()) == []
    assert len(states) == 2


def test_missing_price_gives_nan_not_crash(db_backend: Path | None) -> None:
    conn = _book(db_backend)
    held, eq, _ = risk.snapshot(conn, {}, as_of=NOW)
    conn.close()
    assert held["last"].isna().all() and eq.market_value == 0.0
    assert eq.cash == pytest.approx(10_000 + 85.0 - 1500.0)


def _rules(**over: object) -> Rules:
    return Rules(**{**load_rules().__dict__, **over})


def test_each_breach_rule_fires() -> None:
    def state(pid: int, ticker: str, shares: int, cost: float, stop: float) -> book.PositionState:
        return book.PositionState(
            pid,
            ticker,
            1,
            "GBp",
            "open",
            shares,
            cost,
            -5.0,
            5.0,
            stop,
            stop,
            (cost - stop) * shares / 100,
            (cost - stop) * shares / 100,
            shares,
            cost,
            NOW,
        )

    states = [state(1, "JET2.L", 100, 1500.0, 1400.0), state(2, "EZJ.L", 380, 500.0, 450.0)]
    prices = {"JET2.L": 1500.0, "EZJ.L": 500.0}
    held = risk.holdings(states, prices, as_of=NOW, sectors={"EZJ.L": "Travel & Leisure"})
    cash = pd.DataFrame({"amount": [10_000.0], "currency": ["GBP"], "kind": ["deposit"]})
    eq = risk.equity(cash, states, held)
    assert eq.equity == pytest.approx(10_000 - 10)  # commissions only
    assert risk.breaches(held, eq, _rules()) == []
    assert risk.breaches(held, eq, _rules(max_positions=1)) == [
        "2 open positions > max_positions 1"
    ]
    assert risk.breaches(held, eq, _rules(max_position_pct=0.16)) == [
        "EZJ.L is 19% of equity > max_position_pct 16%"
    ]
    assert risk.breaches(held, eq, _rules(max_sector_pct=0.3)) == [
        "sector Travel & Leisure is 34% > max_sector_pct 30%"
    ]
    assert risk.breaches(held, eq, _rules(max_heat=0.02)) == ["heat 2.9% > max_heat 2.0%"]
    assert risk.breaches(held, eq, _rules(cash_buffer=0.7)) == [
        "cash £6,590 < cash_buffer 70% of equity (£6,993)"
    ]


def test_sector_of_defaults_to_unknown() -> None:
    assert risk.sector_of("JET2.L") == "Travel & Leisure"
    assert risk.sector_of("ABDP.L") == "unknown"


def test_portfolio_script_offline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "book.db"
    _book(db).close()
    book_cli.main(["--db", str(db), "cash", "withdraw", "250", "--note", "fees"])
    assert "withdrawal £250.00 recorded; cash balance £9,750.00" in capsys.readouterr().out
    portfolio_cli.main(["--db", str(db), "--price", "JET2.L=1600", "--save"])
    out = capsys.readouterr().out
    assert "placeholder portfolio rules" in out
    assert "JET2.L" in out and "£1,600" in out and "Travel & Leisure" in out
    assert "Equity £9,935" in out and "Heat 1.0%" in out and "Breaches: none" in out
    assert "saved run 1" in out
    from solaraalpha import store

    cards = store.latest("portfolio", path=db)
    assert set(cards.index) == {"JET2.L", "_PORTFOLIO"}
    assert cards.loc["_PORTFOLIO", "equity"] == pytest.approx(9_935.0)
    assert cards.loc["JET2.L", "r"] == pytest.approx(1.0)
