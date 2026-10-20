from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.performance import (
    correlation,
    drawdown,
    equity_curve,
    flows,
    max_drawdown,
    summarise,
    time_weighted,
    vs_benchmark,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import portfolio as portfolio_cli


def _px(ticker: str, closes: dict[str, float], currency: str = "GBp") -> pd.DataFrame:
    idx = pd.bdate_range("2026-10-01", "2026-10-30", tz="UTC", name="date")
    points = pd.Series({pd.Timestamp(d, tz="UTC"): v for d, v in closes.items()}, dtype="float64")
    s = points.reindex(idx).ffill().bfill()
    df = pd.DataFrame(
        {"open": s, "high": s, "low": s, "close": s, "adj_close": s, "volume": 1e5}, index=idx
    )
    df.attrs = {"ticker": ticker, "currency": currency}
    return df


def _cash(*rows: tuple[str, str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "at": [r[0] for r in rows],
            "kind": [r[1] for r in rows],
            "amount": [r[2] for r in rows],
            "currency": ["GBP"] * len(rows),
            "position_id": [None] * len(rows),
            "note": [""] * len(rows),
        }
    )


def _fills(*rows: tuple[str, str, int, float, float]) -> pd.DataFrame:
    """(at, ticker, signed qty, price pence, commission)."""
    return pd.DataFrame(
        {
            "at": [r[0] for r in rows],
            "ticker": [r[1] for r in rows],
            "side": [1 if r[2] > 0 else -1 for r in rows],
            "qty": [abs(r[2]) for r in rows],
            "price": [r[3] for r in rows],
            "commission": [r[4] for r in rows],
            "fees": [0.0] * len(rows),
            "currency": ["GBp"] * len(rows),
        }
    )


def test_equity_curve_with_deposit_round_trip_and_dividend() -> None:
    cash = _cash(
        ("2026-10-01T09:00:00+00:00", "deposit", 10_000.0),
        ("2026-10-15T09:00:00+00:00", "dividend", 20.0),
        ("2026-10-20T09:00:00+00:00", "deposit", 1_000.0),
    )
    fills = _fills(
        ("2026-10-05T09:00:00+00:00", "A.L", 100, 1500.0, 5.0),
        ("2026-10-12T09:00:00+00:00", "A.L", -100, 1600.0, 5.0),
    )
    prices = {"A.L": _px("A.L", {"2026-10-01": 1500.0, "2026-10-07": 1550.0, "2026-10-12": 1600.0})}
    eq = equity_curve(cash, fills, prices)
    assert eq.index[0] == pd.Timestamp("2026-10-01", tz="UTC") and eq.index[-1] == pd.Timestamp(
        "2026-10-30", tz="UTC"
    )
    assert eq[pd.Timestamp("2026-10-02", tz="UTC")] == 10_000.0
    assert eq[pd.Timestamp("2026-10-05", tz="UTC")] == pytest.approx(
        10_000 - 5.0
    )  # bought at the close
    assert eq[pd.Timestamp("2026-10-07", tz="UTC")] == pytest.approx(
        10_000 - 5.0 + 50.0
    )  # marked at 1550p
    assert eq[pd.Timestamp("2026-10-12", tz="UTC")] == pytest.approx(
        10_000 + 100 - 10.0
    )  # sold at 1600p
    assert eq[pd.Timestamp("2026-10-15", tz="UTC")] == pytest.approx(10_090 + 20.0)  # dividend
    assert eq.iloc[-1] == pytest.approx(10_110 + 1_000.0)
    f = flows(cash)
    assert f.sum() == 11_000.0 and len(f) == 2  # the dividend is not a flow


def test_twr_unaffected_by_deposits_and_vs_benchmark() -> None:
    idx = pd.bdate_range("2026-10-01", periods=5, tz="UTC")
    equity = pd.Series(
        [1000.0, 1100.0, 2100.0, 2100.0, 2310.0], index=idx
    )  # +10%, deposit 1000, flat, +10%
    flow = pd.Series({idx[2]: 1000.0})
    twr = time_weighted(equity, flow)
    assert twr.iloc[-1] == pytest.approx(1.21)
    no_flow = time_weighted(equity, pd.Series(dtype="float64"))
    assert (
        no_flow.iloc[-1] > twr.iloc[-1]
    )  # without stripping the flow it would look like performance
    bench = pd.Series([100.0, 100.0, 110.0, 110.0, 110.0], index=idx)
    vb = vs_benchmark(equity, bench, flow)
    assert vb["benchmark"].iloc[-1] == pytest.approx(1.10)
    assert vb["excess"].iloc[-1] == pytest.approx(0.11)
    s = summarise(equity, flow, bench)
    assert (
        s.twr == pytest.approx(0.21)
        and s.benchmark == pytest.approx(0.10)
        and s.excess == pytest.approx(0.11)
    )
    assert s.net_flows == 1000.0 and s.pnl == pytest.approx(1310.0)


def test_drawdown_on_a_synthetic_series() -> None:
    idx = pd.bdate_range("2026-10-01", periods=6, tz="UTC")
    equity = pd.Series([100.0, 120.0, 90.0, 110.0, 130.0, 117.0], index=idx)
    dd = drawdown(equity)
    assert dd["peak"].tolist() == [100, 120, 120, 120, 130, 130]
    assert dd["drawdown"].iloc[2] == pytest.approx(-0.25) and dd["drawdown"].iloc[
        -1
    ] == pytest.approx(-0.1)
    m = max_drawdown(equity)
    assert m.max_drawdown == pytest.approx(-0.25) and m.peak == idx[1] and m.trough == idx[2]
    assert max_drawdown(pd.Series([1.0, 2.0, 3.0], index=idx[:3])).peak is None
    assert max_drawdown(pd.Series(dtype="float64")).max_drawdown == 0.0


def test_correlation_flags_pairs() -> None:
    idx = pd.date_range("2026-01-02", periods=40, freq="W-FRI", tz="UTC")
    rng = np.random.default_rng(1)
    base = rng.standard_normal(40)
    rets = {
        "A.L": pd.Series(base, index=idx),
        "B.L": pd.Series(base + 0.1 * rng.standard_normal(40), index=idx),
        "C.L": pd.Series(rng.standard_normal(40), index=idx),
    }
    corr, pairs = correlation(rets)
    assert (
        corr.shape == (3, 3)
        and len(pairs) == 1
        and pairs[0][:2] == ("A.L", "B.L")
        and pairs[0][2] > 0.9
    )
    assert correlation({"A.L": rets["A.L"]})[1] == [] and correlation({})[1] == []


def test_history_script_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "book.db"
    conn = book.connect(db)
    with conn:
        book.cash_event(conn, "deposit", 10_000.0, at="2026-10-01T09:00:00+00:00")
        pid, oid = book.draft_position(
            conn, "A.L", 1500.0, 1400.0, 100, at="2026-10-02T18:00:00+00:00"
        )
        book.approve_position(conn, pid)
        book.fill(conn, oid, 100, 1500.0, at="2026-10-05T09:00:00+00:00", commission=5.0)
        pid2, oid2 = book.draft_position(
            conn, "B.L", 500.0, 450.0, 100, at="2026-10-02T18:00:00+00:00"
        )
        book.approve_position(conn, pid2)
        book.fill(conn, oid2, 100, 500.0, at="2026-10-05T09:00:00+00:00", commission=5.0)
    conn.close()
    frames = {
        "A.L": _px("A.L", {"2026-10-01": 1500.0, "2026-10-20": 1650.0}),
        "B.L": _px("B.L", {"2026-10-01": 500.0, "2026-10-20": 550.0}),
        "^FTAI": _px("^FTAI", {"2026-10-01": 4000.0, "2026-10-20": 4200.0}, currency="GBP"),
    }
    monkeypatch.setattr(portfolio_cli, "fetch_daily", lambda t: frames[t])
    portfolio_cli.main(["--history", "--db", str(db)])
    out = capsys.readouterr().out
    assert "Equity curve 2026-10-01 → 2026-10-30: £10,190 on net flows £10,000 (P&L £190)" in out
    assert "Time-weighted return +1.90% vs ^FTAI +5.00% (excess -3.10%)" in out
    assert "Max drawdown -0.10% from 2026-10-01 to 2026-10-05" in out  # the commissions
    assert "Correlated holdings (26w > 0.7):" in out

    # the weekly pack reads the same history from stored price snapshots
    run_id = store.start_run("scan", {}, path=db)
    for t, f in frames.items():
        store.save_ticker(run_id, t, daily=f, path=db)
    store.save_label(run_id, "A.L", "screen", "PASS", "", path=db)
    conn = book.connect(db)
    with conn:
        conn.execute(
            "UPDATE runs SET started_at = ? WHERE run_id = ?", ("2026-10-31T18:17:00+00:00", run_id)
        )
    from solaraalpha.portfolio.weekly_pack import build_pack, render_pack

    text = render_pack(build_pack(conn, run_id))
    conn.close()
    assert (
        "- performance since 2026-10-01: P&L £190 on net flows £10,000, time-weighted +1.9%, ^FTAI +5.0%, max drawdown -0.1%"
        in text
    )

    # a book with no history
    empty = tmp_path / "empty.db"
    book.connect(empty).close()
    portfolio_cli.main(["--history", "--db", str(empty)])
    assert "No history yet" in capsys.readouterr().out
