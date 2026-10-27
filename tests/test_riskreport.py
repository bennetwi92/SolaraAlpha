from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.riskreport import (
    build_risk,
    ewma_filtered,
    exceptions,
    latest_card,
    render_risk,
    scenarios,
    var_es,
)
from solaraalpha.portfolio.rules import Rules, load_rules

END = "2026-10-23"


def _rules(**over: object) -> Rules:
    return Rules(**{**load_rules().__dict__, **over})


def _px(ticker: str, rets: np.ndarray, start: float = 1000.0, ccy: str = "GBp") -> pd.DataFrame:
    idx = pd.bdate_range(end=END, periods=len(rets) + 1, tz="UTC", name="date")
    close = start * np.concatenate([[1.0], np.cumprod(1 + rets)])
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
    df.attrs = {"ticker": ticker, "currency": ccy}
    return df


def test_var_es_on_known_scenarios() -> None:
    scen = pd.DataFrame({"A": np.linspace(-0.10, 0.09, 20), "B": np.zeros(20)})
    t = var_es({"A": 1000.0, "B": 500.0}, scen, 0.95)
    q = -float(np.quantile(np.linspace(-0.10, 0.09, 20) * 1000, 0.05))
    assert t.var == pytest.approx(q)
    assert t.es == pytest.approx(100.0)  # only the worst scenario is beyond the 5% quantile
    assert t.components == {"A": pytest.approx(100.0), "B": pytest.approx(0.0)}
    assert var_es({}, scen, 0.95).var == 0.0


def test_scenarios_compound_and_filter_rescales_to_todays_vol() -> None:
    r = pd.DataFrame({"A": [0.1, 0.1, -0.5, 0.2]})
    five = scenarios(r, 2)
    assert list(five["A"].round(4)) == [0.21, -0.45, -0.4]
    calm_then_wild = pd.DataFrame({"A": [0.001] * 200 + [0.05, -0.05] * 10})
    f = ewma_filtered(calm_then_wild)
    assert abs(f["A"].iloc[50]) > 10 * 0.001  # an old calm day counts at today's vol


def test_exceptions_count_losses_beyond_the_previous_var() -> None:
    h = pd.DataFrame({"var_1d": [10.0, 10.0, 10.0, 10.0], "pnl_day": [0.0, -11.0, -5.0, -20.0]})
    assert exceptions(h) == (2, 3)


@pytest.fixture
def risky_book(db_backend: Path | None) -> tuple[Path | None, dict[str, pd.DataFrame]]:
    """£5,000 in; A.L bought two sessions ago and topped up today; B.L bought a week ago."""
    rng = np.random.default_rng(4)
    a = _px("A.L", 0.03 * rng.standard_normal(400))
    b = _px("B.L", 0.02 * rng.standard_normal(400), start=500.0)
    days = a.index
    conn = book.connect(db_backend)
    with conn:
        book.cash_event(conn, "deposit", 5_000.0, at="2026-01-02T09:00:00+00:00")
        pa, oa = book.draft_position(
            conn, "A.L", 1000.0, 900.0, 100, at="2026-10-19T18:00:00+00:00"
        )
        book.approve_position(conn, pa)
        book.fill(
            conn, oa, 100, float(a["close"].iloc[-3]), at=f"{days[-3]:%Y-%m-%d}T10:00:00+00:00"
        )
        pb, ob = book.draft_position(conn, "B.L", 500.0, 450.0, 200, at="2026-10-12T18:00:00+00:00")
        book.approve_position(conn, pb)
        book.fill(
            conn, ob, 200, float(b["close"].iloc[-6]), at=f"{days[-6]:%Y-%m-%d}T10:00:00+00:00"
        )
    conn.close()
    return db_backend, {"A.L": a, "B.L": b}


def test_build_risk_numbers_add_up(risky_book: tuple[Path | None, dict[str, pd.DataFrame]]) -> None:
    db, px = risky_book
    bench = _px("^FTAI", 0.01 * np.random.default_rng(1).standard_normal(400), ccy="GBP")
    conn = book.connect(db)
    card = build_risk(conn, px, bench, _rules())
    assert card is not None
    d = json.loads(card["detail"])
    a, b = px["A.L"]["close"], px["B.L"]["close"]
    assert card["equity"] == pytest.approx(
        5000
        - 100 * a.iloc[-3] / 100
        - 200 * b.iloc[-6] / 100
        + 100 * a.iloc[-1] / 100
        + 200 * b.iloc[-1] / 100,
        abs=0.01,
    )
    # the day's P&L is the price moves on yesterday's shares, nothing else happened today
    want = 100 * (a.iloc[-1] - a.iloc[-2]) / 100 + 200 * (b.iloc[-1] - b.iloc[-2]) / 100
    assert card["pnl_day"] == pytest.approx(want, abs=0.01)
    assert d["pnl"]["price"] == pytest.approx(want, abs=0.02) and abs(d["pnl"]["other"]) < 0.05
    assert d["pnl"]["market"] + d["pnl"]["specific"] == pytest.approx(d["pnl"]["price"], abs=0.02)
    # the CVaR components add up to the CVaR; the VaR-change steps add up to the change
    assert sum(p["es_component"] for p in d["positions"]) == pytest.approx(card["es"], abs=0.05)
    vc = d["var_change"]
    assert vc["previous"] + vc["trades"] + vc["prices"] + vc["new_data"] == pytest.approx(
        vc["now"], abs=0.05
    )
    assert vc["trades"] == 0.0  # no fills today
    assert 0 < card["var_1d"] < card["var"] <= card["es"]
    assert card["window"] == 400 and card["positions"] == 2
    assert card["var_used"] == max(card["var"], card["var_filtered"])
    assert card["pnl_total"] == pytest.approx(card["equity"] - 5000, abs=0.01)
    conn.close()


def test_a_fill_today_moves_the_var_through_trades_and_the_breach_escalates(
    risky_book: tuple[Path | None, dict[str, pd.DataFrame]],
) -> None:
    db, px = risky_book
    day = px["A.L"].index[-1]
    close = float(px["A.L"]["close"].iloc[-1])
    conn = book.connect(db)
    with conn:
        pid = int(book.positions(conn, status="open")["position_id"].iloc[0])
        assert book.position_state(conn, pid).ticker == "A.L"
        oid = book.add_order(
            conn,
            pid,
            1,
            "limit",
            100,
            close + 10,
            "add",
            status="approved",
            at=f"{day:%Y-%m-%d}T09:00:00+00:00",
        )
    with conn:
        book.fill(conn, oid, 100, close + 10, at=f"{day:%Y-%m-%d}T10:00:00+00:00")
    card = build_risk(conn, px, None, _rules(max_var=0.01))
    assert card is not None
    d = json.loads(card["detail"])
    assert d["var_change"]["trades"] > 0
    a = next(p for p in d["positions"] if p["ticker"] == "A.L")
    assert a["trading"] == pytest.approx(-100 * 10 / 100, abs=0.01)  # bought 10p over the close
    assert card["escalate"] and "over the 1% limit" in card["flags"]
    conn.close()


def test_nothing_in_the_book_gives_no_card(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    assert build_risk(conn, {}, None, _rules()) is None
    conn.close()


def test_card_round_trips_through_the_store_and_renders(
    risky_book: tuple[Path | None, dict[str, pd.DataFrame]],
) -> None:
    db, px = risky_book
    conn = book.connect(db)
    card = build_risk(conn, px, None, _rules())
    assert card is not None
    run = store.start_run("daily", {}, conn=conn)
    store.save_ticker(run, "_BOOK", {"book_risk": card}, conn=conn)
    got = latest_card(conn)
    assert got is not None and got["run_id"] == run and got["var"] == pytest.approx(card["var"])
    out = render_risk(got)
    assert "## Risk (2026-10-23)" in out and "| 5 days, historical |" in out
    assert "Where the day's P&L came from" in out and "| A.L (1) |" in out
    conn.close()


def _verdict(stage: str, run: int, label: str = "ACT", **more: object) -> str:
    return json.dumps(
        {
            "stage": stage,
            "ticker": "_BOOK",
            "run_id": run,
            "label": label,
            "confidence": 0.8,
            "reasons": ["VaR over the limit"],
            **more,
        }
    )


def test_the_pm_is_called_by_a_breach_or_the_risk_manager_and_its_actions_only_cut_risk(
    risky_book: tuple[Path | None, dict[str, pd.DataFrame]],
) -> None:
    from solaraalpha import agents

    db, px = risky_book
    conn = book.connect(db)
    run = store.start_run("daily", {}, conn=conn)
    store.save_ticker(run, "_BOOK", {"book_risk": build_risk(conn, px, None, _rules())}, conn=conn)
    assert not agents.pm_called(conn, run)
    agents.record_verdict(conn, agents.load_verdict(_verdict("risk", run)), "agent:risk")
    assert agents.pm_called(conn, run)  # the risk manager's judgement calls the PM too
    with pytest.raises(ValueError, match="already recorded"):
        agents.record_verdict(conn, agents.load_verdict(_verdict("risk", run)), "agent:risk")

    ids = dict(
        zip(
            *(book.positions(conn, status="open")[c] for c in ("ticker", "position_id")),
            strict=True,
        )
    )
    a, b = int(ids["A.L"]), int(ids["B.L"])
    stop_a = book.position_state(conn, a).stop
    assert stop_a is not None
    actions = [
        {"position_id": a, "action": "tighten_stop", "stop": stop_a + 5, "reason": "cut the heat"},
        {"position_id": b, "action": "tighten_stop", "stop": 1.0, "reason": "lower: refused"},
    ]
    out = agents.record_pm_daily(
        conn, agents.load_verdict(_verdict("pm_daily", run, actions=actions)), "agent:pm"
    )
    got = {r["position_id"]: r["status"] for r in out["actions"]}
    assert got == {a: "accepted", b: "declined"}
    assert book.position_state(conn, a).stop == pytest.approx(stop_a + 5)
    with pytest.raises(ValueError, match="needs `stop`"):
        agents.load_verdict(
            _verdict(
                "pm_daily",
                run,
                actions=[{"position_id": a, "action": "tighten_stop", "reason": "x"}],
            )
        )
    with pytest.raises(ValueError, match="only the pm_daily stage"):
        agents.load_verdict(_verdict("risk", run, actions=actions))
    with pytest.raises(ValueError, match="ticker must be '_BOOK'"):
        agents.load_verdict(_verdict("risk", run).replace('"_BOOK"', '"A.L"'))
    conn.close()


def test_a_breach_on_the_card_calls_the_pm_whatever_the_label(
    risky_book: tuple[Path | None, dict[str, pd.DataFrame]],
) -> None:
    from solaraalpha import agents

    db, px = risky_book
    conn = book.connect(db)
    run = store.start_run("daily", {}, conn=conn)
    card = build_risk(conn, px, None, _rules(max_var=0.001))
    store.save_ticker(run, "_BOOK", {"book_risk": card}, conn=conn)
    agents.record_verdict(
        conn, agents.load_verdict(_verdict("risk", run, label="PASS")), "agent:risk"
    )
    assert agents.pm_called(conn, run)
    conn.close()


def test_the_risk_pack_script(
    risky_book: tuple[Path | None, dict[str, pd.DataFrame]],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import risk_pack

    db, px = risky_book
    conn = book.connect(db)
    run = store.start_run("daily", {}, conn=conn)
    store.save_ticker(run, "_BOOK", {"book_risk": build_risk(conn, px, None, _rules())}, conn=conn)
    empty = store.start_run("daily", {}, conn=conn)
    conn.close()
    dbarg = ["--db", str(db)] if db else []
    risk_pack.main(["--run", str(run), "--out", str(tmp_path / "pack.md"), *dbarg])
    out = capsys.readouterr().out
    assert "## Risk (2026-10-23)" in out and "## Trend" in out and "## Your previous report" in out
    assert "none: this is the first" in out
    assert (tmp_path / "pack.md").read_text().startswith("## Risk (2026-10-23)")
    with pytest.raises(SystemExit) as e:
        risk_pack.main(["--run", str(empty), *dbarg])
    assert e.value.code == 3


def test_a_weekend_deposit_is_not_counted_as_profit(db_backend: Path | None) -> None:
    a = _px("A.L", 0.02 * np.random.default_rng(2).standard_normal(300))
    conn = book.connect(db_backend)
    with conn:
        book.cash_event(conn, "deposit", 5_000.0, at="2026-10-17T09:00:00+00:00")  # a Saturday
        pid, oid = book.draft_position(
            conn, "A.L", 1000.0, 900.0, 10, at="2026-10-19T18:00:00+00:00"
        )
        book.approve_position(conn, pid)
        book.fill(
            conn, oid, 10, float(a["close"].iloc[-3]), at=f"{a.index[-3]:%Y-%m-%d}T10:00:00+00:00"
        )
    card = build_risk(conn, {"A.L": a}, None, _rules())
    assert card is not None
    assert card["pnl_total"] == pytest.approx(card["equity"] - 5000, abs=0.01)
    conn.close()
