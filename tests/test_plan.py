from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from solaraalpha.portfolio.plan import adv_gbp, make_plan
from solaraalpha.portfolio.rules import Rules, load_rules

DATE = pd.Timestamp("2026-10-09", tz="UTC")


def rules(**over: object) -> Rules:  # the sizing tests' numbers assume 0.75% risk per trade
    return Rules(**{**load_rules().__dict__, "risk_per_trade": 0.0075, **over})


def levels(**over: object) -> dict:
    base = {
        "date": DATE,
        "close": 1500.0,
        "trigger": math.nan,
        "coil_pending": False,
        "stop": 1400.0,
        "target": 1800.0,
        "target_2": 2000.0,
    }
    return {**base, **over}


TECH = {"stop": 1380.0}


def test_limit_entry_near_the_close_with_levels_stop() -> None:
    p = make_plan("JET2.L", levels(), TECH, None, rules(), 10_000.0, 10_000.0)
    assert p.entry_type == "limit" and not p.wait_for_break
    assert p.entry == pytest.approx(1515.0)  # close x 1.01
    assert p.stop == 1400.0 and p.stop_source == "levels"
    assert p.risk_per_share == pytest.approx(115.0)
    assert p.target == pytest.approx(1515 * 1.15) and p.target_2 == pytest.approx(1515 * 1.30)
    assert p.reward_risk == pytest.approx(1515 * 0.15 / 115) and p.next_level == 1800.0
    assert p.valid_until == DATE + pd.Timedelta(days=7)
    # risk 0.75% of £10k = £75; risk per share 115p = £1.15 -> 65 shares
    assert p.shares == 65 and p.capped_by == "risk"
    assert p.risk_amount == pytest.approx(65 * 1.15)
    assert p.position_value == pytest.approx(65 * 15.15)
    assert p.horizon_weeks == (2, 16) and p.trail_atr == 3.0
    assert p.flags == () and p.review_dates == ()


def test_coil_breakout_stop_entry_or_limit_waiting_for_the_break() -> None:
    lv = levels(coil_pending=True, trigger=1560.0)
    p = make_plan("JET2.L", lv, TECH, None, rules(stop_entry_orders=True), 10_000.0, 10_000.0)
    assert (p.entry_type, p.entry, p.wait_for_break) == ("stop", 1560.0, False)
    q = make_plan("JET2.L", lv, TECH, None, rules(stop_entry_orders=False), 10_000.0, 10_000.0)
    assert (q.entry_type, q.entry, q.wait_for_break) == ("limit", 1560.0, True)
    # coiled but the trigger is NaN: not pending, enter at the close
    r = make_plan(
        "JET2.L", levels(coil_pending=False, trigger=math.nan), TECH, None, rules(), 1e4, 1e4
    )
    assert r.entry_type == "limit" and r.entry == pytest.approx(1515.0)


def test_stop_falls_back_to_technical_or_raises() -> None:
    p = make_plan("JET2.L", levels(stop=math.nan), TECH, None, rules(), 10_000.0, 10_000.0)
    assert p.stop == 1380.0 and p.stop_source == "technical"
    p = make_plan("JET2.L", levels(stop=1600.0), TECH, None, rules(), 10_000.0, 10_000.0)
    assert p.stop_source == "technical"  # levels stop above the entry is useless
    with pytest.raises(ValueError, match="no stop below the entry"):
        make_plan("JET2.L", levels(stop=1600.0), {"stop": 1700.0}, None, rules(), 1e4, 1e4)
    with pytest.raises(ValueError, match="no stop below"):
        make_plan("JET2.L", levels(stop=math.nan), {"stop": math.nan}, None, rules(), 1e4, 1e4)


def test_each_cap_binds_in_turn_with_pence() -> None:
    lv = levels()  # entry 1515p = £15.15, risk 115p = £1.15
    eq = 10_000.0
    p = make_plan("JET2.L", lv, TECH, None, rules(), eq, eq)
    assert (p.shares, p.capped_by) == (65, "risk")
    p = make_plan("JET2.L", lv, TECH, None, rules(max_position_pct=0.05), eq, eq)
    assert (p.shares, p.capped_by) == (math.floor(500 / 15.15), "max_position_pct")  # 33
    p = make_plan("JET2.L", lv, TECH, None, rules(), eq, eq, adv_gbp=5_000.0)
    assert (p.shares, p.capped_by) == (math.floor(250 / 15.15), "liquidity")  # 16
    p = make_plan("JET2.L", lv, TECH, None, rules(), eq, cash_gbp=700.0)
    assert (p.shares, p.capped_by) == (math.floor((700 - 500) / 15.15), "cash")  # 13
    p = make_plan("JET2.L", lv, TECH, None, rules(), eq, cash_gbp=400.0)
    assert (p.shares, p.capped_by) == (0, "zero") and "size zero" in p.flags
    assert p.position_value == 0 and p.risk_amount == 0


def test_review_date_and_event_flag() -> None:
    ev = {"next_event": DATE + pd.Timedelta(days=10)}
    p = make_plan("JET2.L", levels(), TECH, ev, rules(), 10_000.0, 10_000.0)
    assert p.review_dates == (DATE + pd.Timedelta(days=8),)
    assert p.flags == ("event within 10 days",)
    far = {"next_event": DATE + pd.Timedelta(weeks=10)}
    p = make_plan("JET2.L", levels(), TECH, far, rules(), 10_000.0, 10_000.0)
    assert len(p.review_dates) == 1 and p.flags == ()
    beyond = {"next_event": DATE + pd.Timedelta(weeks=20)}
    p = make_plan("JET2.L", levels(), TECH, beyond, rules(), 10_000.0, 10_000.0)
    assert p.review_dates == () and p.flags == ()
    none = {"next_event": None}
    assert make_plan("JET2.L", levels(), TECH, none, rules(), 1e4, 1e4).review_dates == ()
    nan = {"next_event": math.nan}
    assert make_plan("JET2.L", levels(), TECH, nan, rules(), 1e4, 1e4).flags == ()


def test_targets_are_the_move_sought_and_wide_stops_are_not_flagged() -> None:
    p = make_plan(
        "JET2.L", levels(target=math.nan, target_2=math.nan), TECH, None, rules(), 1e4, 1e4
    )
    assert math.isnan(p.next_level) and p.target == pytest.approx(1515 * 1.15)  # blue sky
    p = make_plan("JET2.L", levels(stop=1200.0), TECH, None, rules(), 1e4, 1e4)
    assert p.flags == ()  # a 21% stop: volatility is wanted (ADR 0022)
    # a raised entry above the first level: the next level is the one above it
    p = make_plan(
        "JET2.L",
        levels(coil_pending=True, trigger=1850.0, target=1800.0),
        TECH,
        None,
        rules(target_moves=(0.2, 0.4)),
        1e4,
        1e4,
    )
    assert p.next_level == 2000.0
    assert (p.target, p.target_2) == (pytest.approx(1850 * 1.2), pytest.approx(1850 * 1.4))


def test_adv_gbp() -> None:
    idx = pd.bdate_range(end="2026-10-09", periods=30, tz="UTC", name="date")
    df = pd.DataFrame({"close": 1000.0, "volume": 50_000.0}, index=idx)
    df.loc[idx[-1], "volume"] = np.nan  # a session without volume is dropped
    df.attrs = {"currency": "GBp"}
    assert adv_gbp(df) == pytest.approx(1000 * 50_000 / 100)  # £500k
    assert math.isnan(adv_gbp(df.iloc[:0]))


def test_agent_levels_set_the_entry_and_the_stop() -> None:
    from solaraalpha.agents import TradeLevels

    lim = TradeLevels(entry_type="limit", entry=1480.0, stop=1250.0, stop_reason="below the base")
    p = make_plan("JET2.L", levels(), TECH, None, rules(), 1e4, 1e4, agent=lim)
    assert (p.entry_type, p.entry, p.wait_for_break) == ("limit", 1480.0, False)
    assert (p.stop, p.stop_source, p.stop_reason) == (1250.0, "agent", "below the base")
    assert p.target == pytest.approx(1480 * 1.15) and p.valid_until == DATE + pd.Timedelta(days=7)
    brk = TradeLevels(
        entry_type="stop", entry=1600.0, stop=1450.0, stop_reason="failed break", valid_days=3
    )
    p = make_plan("JET2.L", levels(), TECH, None, rules(), 1e4, 1e4, agent=brk)
    assert (p.entry_type, p.entry, p.stop) == ("stop", 1600.0, 1450.0)
    assert p.valid_until == DATE + pd.Timedelta(days=3)
    q = make_plan(
        "JET2.L", levels(), TECH, None, rules(stop_entry_orders=False), 1e4, 1e4, agent=brk
    )
    assert (q.entry_type, q.wait_for_break) == ("limit", True)
