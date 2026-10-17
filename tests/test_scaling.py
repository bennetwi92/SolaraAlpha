from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha.portfolio import book
from solaraalpha.portfolio.book import PositionState
from solaraalpha.portfolio.plan import add_plan
from solaraalpha.portfolio.review import adds_so_far, propose_add
from solaraalpha.portfolio.rules import Rules, load_rules

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import book as cli

DATE = pd.Timestamp("2026-10-09", tz="UTC")


def rules(**over: object) -> Rules:
    return Rules(**{**load_rules().__dict__, **over})


def state(stop: float | None = 1450.0, shares: int = 100) -> PositionState:
    # bought 100 at 1500p with a 1400p stop: r_unit = £100; now the stop sits at `stop`
    return PositionState(
        1,
        "JET2.L",
        1,
        "GBp",
        "open",
        shares,
        1500.0,
        -5.0,
        5.0,
        stop,
        1400.0,
        max(0.0, (1500.0 - (stop or 0)) * shares / 100),
        100.0,
        100,
        1500.0,
        "2026-09-14T09:00:00+00:00",
    )


def review(r: float = 1.5) -> dict:
    return {"date": DATE, "r_multiple": r, "action": "hold"}


def levels(
    close: float = 1660.0, breakout: int = 1, support: float = 1550.0, atr: float = 40.0
) -> dict:
    return {
        "date": DATE,
        "close": close,
        "atr": atr,
        "support": support,
        "resistance": 1800.0,
        "breakout": breakout,
        "target": 1800.0,
        "target_2": 1900.0,
    }


def tech(trend: float = 0.6) -> dict:
    return {"date": DATE, "trend": trend}


def test_no_add_below_min_r_or_without_a_trigger() -> None:
    assert add_plan(state(), review(0.9), levels(), tech(), rules()) is None  # < 1R
    assert add_plan(state(), review(float("nan")), levels(), tech(), rules()) is None
    assert (
        add_plan(state(), review(1.5), levels(breakout=0, close=1700.0), tech(), rules()) is None
    )  # no trigger
    assert (
        add_plan(state(), review(1.5), levels(breakout=0, close=1580.0), tech(0.1), rules()) is None
    )  # weak trend
    assert (
        add_plan(state(), review(1.5), levels(breakout=0, close=1540.0), tech(), rules()) is None
    )  # below support
    assert (
        add_plan(state(), review(1.5), levels(breakout=0, close=1591.0), tech(), rules()) is None
    )  # > 1 ATR above


def test_breakout_add_raises_the_stop_to_breakeven_and_caps_risk() -> None:
    p = add_plan(state(stop=1450.0), review(1.5), levels(), tech(), rules())
    assert p is not None
    assert p.entry == pytest.approx(1660.0 * 1.01) and p.entry_type == "limit"
    assert (
        p.stop == 1500.0 and p.stop_source == "breakeven"
    )  # raised from 1450 to the entry average
    assert p.flags == ("add: breakout", "add 1 of 2")
    # size: 0.5 x 100 = 50 shares, but the risk cap binds: held risk at 1500 is 0, budget £100,
    # risk per share 176.6p -> 56 shares > 50, so add_fraction binds
    assert p.shares == 50 and p.capped_by == "add_fraction"
    assert p.risk_amount == pytest.approx(50 * (1676.6 - 1500.0) / 100)
    assert p.reward_risk == pytest.approx((1800.0 - 1676.6) / 176.6)
    assert p.valid_until == DATE + pd.Timedelta(days=7)
    # a stop already above breakeven stays (never lowered to breakeven)
    p2 = add_plan(state(stop=1560.0), review(1.5), levels(), tech(), rules())
    assert p2 is not None and p2.stop == 1560.0 and p2.stop_source == "position"
    # risk cap: tiny original risk -> fewer shares
    tiny = PositionState(
        1,
        "JET2.L",
        1,
        "GBp",
        "open",
        100,
        1500.0,
        -5.0,
        5.0,
        1450.0,
        1490.0,
        50.0,
        10.0,
        100,
        1500.0,
        "2026-09-14T09:00:00+00:00",
    )  # r_unit £10
    p3 = add_plan(tiny, review(1.5), levels(), tech(), rules())
    assert p3 is not None and p3.capped_by == "risk" and p3.shares == math.floor(10.0 / 1.766)
    assert p3.risk_amount <= 10.0
    # equity cap
    p4 = add_plan(state(), review(1.5), levels(), tech(), rules(), equity_gbp=8_000.0)
    assert p4 is not None and p4.capped_by == "max_position_pct"
    assert p4.shares == math.floor((0.2 * 8_000 - 1500.0) / 16.766)
    assert (
        add_plan(state(), review(1.5), levels(), tech(), rules(), equity_gbp=7_000.0) is None
    )  # no room


def test_pullback_add_and_max_adds() -> None:
    p = add_plan(state(), review(1.2), levels(close=1580.0, breakout=0), tech(0.25), rules())
    assert p is not None and p.flags[0] == "add: pullback" and p.shares == 50
    assert add_plan(state(), review(1.2), levels(), tech(), rules(), adds=2) is None  # max reached
    p2 = add_plan(state(), review(1.2), levels(), tech(), rules(), adds=1)
    assert p2 is not None and p2.flags[1] == "add 2 of 2"
    assert add_plan(state(), review(1.2), levels(), tech(), rules(max_adds=0)) is None
    # a stop above the entry (position deep in profit, close fell back): nothing sensible to add
    assert add_plan(state(stop=1700.0), review(1.2), levels(), tech(), rules()) is None


def test_accept_drafts_the_add_and_raises_the_stop(
    db_backend: Path | None, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = book.connect(db_backend)
    with conn:
        pid, oid = book.draft_position(
            conn, "JET2.L", 1500.0, 1400.0, 100, at="2026-09-13T18:00:00+00:00"
        )
        book.approve_position(conn, pid)
        book.fill(conn, oid, 100, 1500.0, at="2026-09-14T09:00:00+00:00", commission=5.0)
        st = book.position_state(conn, pid)
        assert adds_so_far(conn, pid) == 0
        plan = add_plan(st, review(1.5), levels(), tech(), rules())
        assert plan is not None
        rid = propose_add(conn, st, plan, run_id=7)
    rv = book.revisions(conn, pid)
    assert len(rv) == 1 and rv["action"].iloc[0] == "add" and rv["status"].iloc[0] == "proposed"
    assert rv["new"].iloc[0]["qty"] == 50 and rv["new"].iloc[0]["stop"] == 1500.0
    assert rv["trigger"].iloc[0] == "review run 7"
    with conn:
        done = book.accept_revision(conn, rid, at="2026-10-11T18:00:00+00:00")
    assert set(done) == {"revision_id", "action", "stop_order_id", "add_order_id"}
    od = book.orders(conn, pid).set_index("order_id")
    add = od.loc[done["add_order_id"]]
    assert (
        add["purpose"] == "add"
        and add["side"] == 1
        and add["qty"] == 50
        and add["status"] == "approved"
    )
    assert add["price"] == pytest.approx(1676.6) and add["valid_until"] == "2026-10-16"
    stop = od.loc[done["stop_order_id"]]
    assert stop["purpose"] == "stop" and stop["price"] == 1500.0 and stop["qty"] == 100
    st = book.position_state(conn, pid)
    assert st.stop == 1500.0 and st.shares == 100
    ev = book.position_events(conn, pid)
    assert "stop_raised" in set(ev["kind"]) and ev["kind"].iloc[-1] == "stop_raised"
    assert adds_so_far(conn, pid) == 1  # live add counts towards max_adds
    # the fill of the add increases the shares and re-sizes the protective stop to 150 at 1500
    with conn:
        out = book.fill(conn, int(done["add_order_id"]), 50, 1676.0, at="2026-10-12T09:00:00+00:00")
    st = book.position_state(conn, pid)
    assert st.shares == 150 and out["stop_order_id"] is not None
    live = book.orders(conn, pid, status="approved")
    stops = live[live["purpose"] == "stop"]
    assert len(stops) == 1 and stops["qty"].iloc[0] == 150 and stops["price"].iloc[0] == 1500.0
    assert st.avg_cost == pytest.approx((1500.0 * 100 + 1676.0 * 50) / 150)
    assert adds_so_far(conn, pid) == 1
    # a second accepted add at a lower stop never lowers the live stop
    with conn:
        rid2 = book.propose_revision(
            conn, pid, "add", {"qty": 10, "entry": 1700.0, "stop": 1450.0}, "x", "pipeline"
        )
        done2 = book.accept_revision(conn, rid2)
    assert "stop_order_id" not in done2 and book.position_state(conn, pid).stop == 1500.0
    with pytest.raises(ValueError, match="add needs a positive qty"), conn:
        rid3 = book.propose_revision(conn, pid, "add", {"qty": 0, "entry": 1700.0}, "x", "pipeline")
        book.accept_revision(conn, rid3)
    conn.close()
    if db_backend is not None:
        cli.main(["--db", str(db_backend), "revisions", "--status", "all"])
        out_text = capsys.readouterr().out
        assert "add" in out_text and "accepted" in out_text
