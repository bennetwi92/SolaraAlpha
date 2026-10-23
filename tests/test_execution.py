from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from fakes import FakeBroker
from solaraalpha.data import trading212 as t212
from solaraalpha.portfolio import book, execution
from solaraalpha.portfolio.execution import Refused
from solaraalpha.store import Conn

OPEN_AT = pd.Timestamp("2026-10-27T10:00:00Z")  # a Tuesday, 10:00 London (GMT)
LATE = pd.Timestamp("2026-10-27T15:50:00Z")  # past the 15:45 exit deadline
NIGHT = pd.Timestamp("2026-10-27T18:00:00Z")  # market shut


def bars(close: float = 1500.0) -> Any:
    """Yahoo daily bars whose last completed close is ``close``."""
    idx = pd.date_range("2026-10-19", periods=5, freq="D", tz="UTC")
    return lambda ticker: pd.DataFrame({"close": [close] * 5}, index=idx)


def nap(_: float) -> None:
    pass


def _turn(conn: Conn, fake: FakeBroker, now: Any = OPEN_AT, **kw: Any) -> execution.TurnResult:
    kw.setdefault("quotes", bars())
    return execution.turn(conn, fake, now, sleep=nap, **kw)


def _entry(conn: Conn, qty: int = 50, price: float = 1500.0) -> tuple[int, int]:
    with conn:
        book.cash_event(conn, "deposit", 5000.0)
        pid, oid = book.draft_position(conn, "JET2.L", price, 1400.0, qty, horizon_weeks=8)
        book.approve_position(conn, pid)
    return pid, oid


def _open(conn: Conn, fake: FakeBroker) -> tuple[int, int, str]:
    """An open position of 50 JET2 with its stop working at the broker; returns
    ``(position_id, stop_order_id, stop broker ref)``."""
    pid, oid = _entry(conn)
    _turn(conn, fake)
    fake.fill(
        str(book.orders(conn, pid).set_index("order_id").loc[oid, "broker_ref"]), price=1495.0
    )
    _turn(conn, fake)
    od = book.orders(conn, pid)
    stop = od[(od["purpose"] == "stop") & (od["status"] == "placed")]
    return pid, int(stop["order_id"].iloc[0]), str(stop["broker_ref"].iloc[0])


def _working(fake: FakeBroker) -> list[tuple[int, str, float, float | None]]:
    return sorted((o["side"], o["type"], o["qty"], o["price"]) for o in fake.working.values())


def test_calendar() -> None:
    s = execution.session(OPEN_AT)
    assert s == (pd.Timestamp("2026-10-27T08:00Z"), pd.Timestamp("2026-10-27T16:30Z"))
    summer = execution.session(pd.Timestamp("2026-10-23T12:00Z"))  # BST: 07:00-15:30 UTC
    assert summer == (pd.Timestamp("2026-10-23T07:00Z"), pd.Timestamp("2026-10-23T15:30Z"))
    assert execution.session(pd.Timestamp("2026-10-24T12:00Z")) is None  # Saturday
    assert execution.session(pd.Timestamp("2026-12-25T12:00Z")) is None  # Christmas
    half = execution.session(pd.Timestamp("2026-12-24T10:00Z"))
    assert half is not None and half[1] == pd.Timestamp("2026-12-24T12:30Z")
    assert execution.exit_window(OPEN_AT) and not execution.exit_window(LATE)
    assert execution.past_deadline(LATE) and not execution.past_deadline(NIGHT)
    assert not execution.exit_window(pd.Timestamp("2026-10-27T07:59Z"))
    assert execution.deadline_text(OPEN_AT) == "15:45 London"


def test_last_close_skips_the_forming_bar() -> None:
    idx = pd.to_datetime(["2026-10-26", "2026-10-27"]).tz_localize("UTC")
    df = pd.DataFrame({"close": [100.0, 104.0]}, index=idx)
    assert execution.last_close(df, OPEN_AT) == (100.0, 104.0)  # today's bar is still forming
    assert execution.last_close(df, NIGHT) == (104.0, 104.0)
    assert execution.last_close(pd.DataFrame(), OPEN_AT) == (None, None)


def test_entry_goes_out_and_its_fill_gets_the_stop_in_the_same_turn(
    db_backend: Path | None,
) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, oid = _entry(conn)
    res = _turn(conn, fake, NIGHT)  # limit and stop orders go out of hours too
    assert _working(fake) == [(1, "limit", 50.0, 1500.0)]
    ref = str(book.orders(conn, pid).set_index("order_id").loc[oid, "broker_ref"])
    assert book.order_status(conn, oid) == "placed" and ref in fake.working
    assert res.done == [f"JET2.L: order #{oid} entry buy 50 limit @ 1,500p: placed"]
    fake.fill(ref, price=1495.0)
    res = _turn(conn, fake, NIGHT)
    assert book.position_status(conn, pid) == "open"
    # the protective stop is at the broker on exactly the shares filled, in the same turn
    assert _working(fake) == [(-1, "stop", 50.0, 1400.0)]
    st = book.position_state(conn, pid)
    assert st.shares == 50 and st.stop == 1400.0
    assert any("stop sell 50 stop @ 1,400p: placed" in x for x in res.done)
    assert res.booked and "filled 50" in res.booked[0]
    # nothing to do: nothing sent, nothing for the agent
    calls = len(fake.calls)
    res = _turn(conn, fake, NIGHT, agent=True)
    assert len(fake.calls) == calls and res.done == [] and res.refused == []
    assert res.agent == [f"#{st_id} JET2.L: stop sent by code, to review" for st_id in [oid + 1]]
    conn.close()


def test_part_fill_cancels_the_rest_first_then_the_stop_goes_out(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, oid = _entry(conn)
    _turn(conn, fake, NIGHT)
    ref = str(book.orders(conn, pid).set_index("order_id").loc[oid, "broker_ref"])
    with conn:  # 20 filled (as booked from the history), the rest is still working
        book.fill(conn, oid, 20, 1495.0)
    fake.working[ref]["filled"] = 20.0
    fake.held["JET2l_EQ"], fake.cost["JET2l_EQ"] = 20.0, 20 * 1495.0
    assert book.order_status(conn, oid) == "cancelling"
    _turn(conn, fake, NIGHT)
    kinds = [c[0] for c in fake.calls]
    assert kinds.index("cancel") < len(kinds) - 1 and kinds[-1] == "place"  # cancel, then stop
    assert _working(fake) == [(-1, "stop", 20.0, 1400.0)]
    assert book.order_status(conn, oid) == "cancelled"
    conn.close()


def test_a_send_that_timed_out_is_matched_before_anything_is_sent_again(
    db_backend: Path | None,
) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    _pid, oid = _entry(conn)
    fake.lose_answer = True  # placed at the broker, the answer lost
    _turn(conn, fake, NIGHT)  # the turn's next list shows it: booked as placed, never sent twice
    assert book.order_status(conn, oid) == "placed" and len(fake.working) == 1
    _turn(conn, fake, NIGHT)
    assert [c[0] for c in fake.calls] == ["place"]
    # an answer lost before the broker got it: the next turn finds nothing, then sends once more
    pid2, oid2 = book.draft_position(conn, "AAZ.L", 400.0, 340.0, 50, horizon_weeks=8)
    book.approve_position(conn, pid2)
    fake.fail_next = t212.Unknown("connection reset")
    _turn(conn, fake, NIGHT, quotes=bars(400.0))
    assert book.order_status(conn, oid2) == "sending" and len(fake.working) == 1
    _turn(conn, fake, NIGHT, quotes=bars(400.0))
    assert book.order_status(conn, oid2) == "placed" and len(fake.working) == 2
    assert [c[0] for c in fake.calls] == ["place", "place", "place"]
    ex = execution.executions(conn)
    assert (ex["action"] == "unknown").sum() == 3  # two lost answers, one sent back to approved
    conn.close()


def test_guards_refuse(db_backend: Path | None, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    _pid, oid = _entry(conn)
    near = execution.Prices(bars(), NIGHT)
    assert execution.send(conn, fake, oid, NIGHT, near, sleep=nap) == "placed"
    with pytest.raises(Refused, match="is placed, not approved"):
        execution.check(conn, oid, 1500.0, NIGHT)
    with conn:
        new = book.replace_order(conn, oid, None, 1490.0, "lower")
    with pytest.raises(Refused, match="still cancelling"):
        execution.check(conn, new, 1500.0, NIGHT)
    _, oid = book.draft_position(conn, "TET.L", 1500.0, 1400.0, 50, horizon_weeks=8)
    book.approve_position(conn, _)
    with pytest.raises(Refused, match="outside the 10% band"):
        execution.check(conn, oid, 1300.0, NIGHT)
    with pytest.raises(Refused, match="no last price"):
        execution.check(conn, oid, None, NIGHT)
    execution.check(conn, oid, 1450.0, NIGHT)  # 3% off: fine
    # a buy bigger than the cap / the free cash
    pid2, big = book.draft_position(conn, "AAZ.L", 400.0, 340.0, 400, horizon_weeks=8)
    book.approve_position(conn, pid2)
    with pytest.raises(Refused, match="over the position cap"):
        execution.check(conn, big, 400.0, NIGHT)
    pid3, huge = book.draft_position(conn, "AVG.L", 800.0, 700.0, 1000, horizon_weeks=8)
    book.approve_position(conn, pid3)
    with pytest.raises(Refused, match="is free"):
        execution.check(conn, huge, 800.0, NIGHT)
    # the circuit breaker stops new buys
    with monkeypatch.context() as m:
        m.setattr(execution, "breaker_tripped", lambda *a: -0.2)
        with pytest.raises(Refused, match="circuit breaker"):
            execution.check(conn, oid, 1500.0, NIGHT)
    # Trading212 must offer the line
    fake2 = FakeBroker()
    fake2.ticker = lambda yahoo: None  # type: ignore[method-assign]
    with pytest.raises(Refused, match=r"doesn't offer AAZ\.L"):
        execution.send(conn, fake2, big, NIGHT, near, sleep=nap)
    assert fake2.calls == []
    conn.close()


def test_a_sell_beyond_the_shares_not_under_a_working_sell_is_refused(
    db_backend: Path | None,
) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, _stop, _ = _open(conn, fake)
    with conn:
        extra = book.add_order(conn, pid, -1, "limit", 10, 1700.0, "exit", status="approved")
    with pytest.raises(ValueError, match="only 0 shares are not already under a working sell"):
        book.check_sell(conn, extra)
    with pytest.raises(Refused, match="one order per unit"):
        execution.check(conn, extra, 1600.0, OPEN_AT)
    with pytest.raises(ValueError, match="exceeds the 50 shares held"):
        book.exit_position(conn, pid, "target", qty=60)
    conn.close()


def test_rejection_is_recorded_and_retried_only_once(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    _pid, oid = _entry(conn)
    fake.fail_next = t212.T212Error(400, "/api-errors/instrument-not-tradable", "Not tradable")
    _turn(conn, fake, NIGHT)
    assert book.order_status(conn, oid) == "rejected"
    res = _turn(conn, fake, NIGHT, agent=True)  # the code never sends a rejected order again
    assert len(fake.calls) == 1
    assert res.agent == [f"#{oid} JET2.L: rejected by Trading212"]
    prices = execution.Prices(bars(), NIGHT)
    fake.fail_next = t212.T212Error(400, "/api-errors/instrument-not-tradable", "Not tradable")
    out = execution.send(conn, fake, oid, NIGHT, prices, execution.AGENT, "retry", sleep=nap)
    assert out == "rejected"
    with pytest.raises(Refused, match="rejected 2 times"):
        execution.send(conn, fake, oid, NIGHT, prices, execution.AGENT, "again", sleep=nap)
    # a 429 did nothing at the broker: back to approved, sent next turn
    pid2, oid2 = book.draft_position(conn, "AAZ.L", 400.0, 340.0, 50, horizon_weeks=8)
    book.approve_position(conn, pid2)
    fake.fail_next = t212.T212Error(429, "TooManyRequests")
    _turn(conn, fake, NIGHT, quotes=bars(400.0))
    assert book.order_status(conn, oid2) == "approved"
    _turn(conn, fake, NIGHT, quotes=bars(400.0))
    assert book.order_status(conn, oid2) == "placed"
    conn.close()


def test_an_exit_waits_for_market_hours_then_swaps_the_stop(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, stop, stop_ref = _open(conn, fake)
    with conn:
        book.propose_revision(
            conn, pid, "exit", {"type": "market"}, "thesis broken", "agent:monitor"
        )
    exit_id = int(book.orders(conn, pid, status="approved")["order_id"].iloc[0])
    res = _turn(conn, fake, NIGHT)  # decided after the close: the stop stays on overnight
    assert book.order_status(conn, exit_id) == "approved" and stop_ref in fake.working
    assert res.done == [] and res.refused == []
    _turn(conn, fake, OPEN_AT)  # the open: cancel the stop, confirm, then sell
    assert ("cancel", stop_ref) in fake.calls
    assert _working(fake) == [(-1, "market", 50.0, None)]
    assert book.order_status(conn, stop) == "cancelled"
    assert book.order_status(conn, exit_id) == "placed"
    ref = book.orders(conn, pid).set_index("order_id").loc[exit_id, "broker_ref"]
    fake.fill(str(ref), price=1450.0)
    _turn(conn, fake, OPEN_AT)
    assert book.position_status(conn, pid) == "closed" and fake.working == {}
    conn.close()


def test_a_partial_cuts_the_stop_to_the_rest_then_sells(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, _stop, _stop_ref = _open(conn, fake)
    with conn:
        book.propose_revision(conn, pid, "take_partial", {"qty": 20}, "trim", "pipeline")
    _turn(conn, fake, OPEN_AT)
    assert _working(fake) == [(-1, "market", 20.0, None), (-1, "stop", 30.0, 1400.0)]
    assert sum(o["qty"] for o in fake.working.values()) <= 50  # never more than is held
    od = book.orders(conn, pid)
    assert od.loc[od["status"] == "placed", "purpose"].tolist() == ["exit", "stop"]
    conn.close()


def test_the_stop_filling_during_the_swap_books_it_and_sends_no_exit(
    db_backend: Path | None,
) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, _stop, stop_ref = _open(conn, fake)
    with conn:
        book.propose_revision(conn, pid, "exit", {}, "thesis broken", "agent:monitor")
    fake.fill_on_cancel[stop_ref] = 1398.0
    _turn(conn, fake, OPEN_AT)
    assert book.position_status(conn, pid) == "closed"
    assert [c for c in fake.calls if c[0] == "place" and c[4] < 0 and c[1] == "market"] == []
    assert fake.working == {} and book.position_state(conn, pid).shares == 0
    conn.close()


def test_an_exit_unfilled_by_the_deadline_gives_the_stop_back_before_the_close(
    db_backend: Path | None,
) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, _stop, _ = _open(conn, fake)
    with conn:
        book.propose_revision(
            conn, pid, "exit", {"type": "limit", "price": 1600.0}, "sell into strength", "agent:pm"
        )
    _turn(conn, fake, OPEN_AT, quotes=bars(1500.0))
    assert _working(fake) == [(-1, "limit", 50.0, 1600.0)]
    res = _turn(conn, fake, LATE)
    assert _working(fake) == [(-1, "stop", 50.0, 1400.0)]  # protected across the close
    assert any("unfilled by 15:45 London" in x for x in res.done)
    od = book.orders(conn, pid)
    assert od.loc[od["purpose"] == "exit", "status"].tolist() == ["cancelled"]
    assert book.position_state(conn, pid).stop == 1400.0
    _turn(conn, fake, NIGHT)  # and no exit goes out after the deadline
    assert _working(fake) == [(-1, "stop", 50.0, 1400.0)]
    conn.close()


def test_agent_on_new_orders_wait_for_it_then_the_code_sends(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    _pid, oid = _entry(conn)
    approved = execution._approved_at(conn, oid)
    res = _turn(conn, fake, approved + pd.Timedelta(minutes=5), agent=True)
    assert fake.calls == [] and res.agent == [f"#{oid} JET2.L: how and when to send it"]
    # the agent holds it; past the grace it still waits for the hold
    execution.record(
        conn,
        oid,
        "hold",
        "the spread is wide before 9",
        execution.AGENT,
        {"until": str(approved + pd.Timedelta(hours=2))},
    )
    res = _turn(conn, fake, approved + pd.Timedelta(minutes=40), agent=True)
    assert fake.calls == [] and res.agent == []
    _turn(conn, fake, approved + pd.Timedelta(hours=3), agent=True)
    assert book.order_status(conn, oid) == "placed"
    # with no agent action at all, the code sends a new order after the grace
    pid2, oid2 = book.draft_position(conn, "AAZ.L", 400.0, 340.0, 50, horizon_weeks=8)
    book.approve_position(conn, pid2)
    t0 = execution._approved_at(conn, oid2)
    _turn(conn, fake, t0 + pd.Timedelta(minutes=10), agent=True, quotes=bars(400.0))
    assert book.order_status(conn, oid2) == "approved"
    _turn(conn, fake, t0 + pd.Timedelta(minutes=31), agent=True, quotes=bars(400.0))
    assert book.order_status(conn, oid2) == "placed"
    conn.close()


def test_agent_override_and_cancel(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, oid = _entry(conn)
    prices = execution.Prices(bars(), NIGHT)
    with pytest.raises(Refused, match="may only be at or below"):
        execution.send(conn, fake, oid, NIGHT, prices, execution.AGENT, "x", price=1510.0)
    with pytest.raises(Refused, match="keeps its type"):
        execution.send(conn, fake, oid, NIGHT, prices, execution.AGENT, "x", type="market")
    out = execution.send(conn, fake, oid, NIGHT, prices, execution.AGENT, "inside", price=1490.0)
    assert out == "placed" and _working(fake) == [(1, "limit", 50.0, 1490.0)]
    new = int(book.orders(conn, pid, status="placed")["order_id"].iloc[0])
    assert new != oid and book.order_status(conn, oid) == "cancelled"
    # the agent lets it lapse: cancelled at the broker, confirmed, the position cancelled
    assert execution.cancel(conn, fake, new, "no longer wanted", sleep=nap) == "cancelled"
    assert fake.working == {} and book.position_status(conn, pid) == "cancelled"
    conn.close()


def test_the_agent_cannot_send_what_the_book_waits_for(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    with conn:
        book.cash_event(conn, "deposit", 5000.0)
        pid, oid = book.draft_position(
            conn, "JET2.L", 1600.0, 1400.0, 20, type="stop", payload={"wait_for_break": True}
        )
        book.approve_position(conn, pid)
    prices = execution.Prices(bars(1500.0), OPEN_AT)  # no daily close above 1600 yet
    with pytest.raises(Refused, match="waits for a daily close above"):
        execution.agent_send(conn, fake, oid, OPEN_AT, prices, reason="early")
    assert fake.working == {} and book.order_status(conn, oid) == "approved"
    conn.close()


def test_a_blip_while_confirming_a_cancel_does_not_stop_the_turn(
    db_backend: Path | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    _, stop_id, _ = _open(conn, fake)
    with conn:
        book.cancel_order(conn, stop_id, "by hand", book.OWNER)

    def down(ref: str) -> None:
        raise ConnectionError("Trading212 unreachable")

    monkeypatch.setattr(fake, "order", down)
    execution.settle(conn, fake, sleep=nap)  # recorded, not raised
    log = execution.executions(conn)
    assert log["reason"].str.contains("cancel not confirmed (ConnectionError", regex=False).any()
    conn.close()


def test_a_part_fill_on_a_working_buy_gets_its_stop(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, oid = _entry(conn)
    _turn(conn, fake, NIGHT)
    ref = str(book.orders(conn, pid).set_index("order_id").loc[oid, "broker_ref"])
    fake.part_fill(ref, 20, 1495.0)  # 20 of 50 filled, the rest still working
    _turn(conn, fake, OPEN_AT)
    assert book.position_state(conn, pid).shares == 20
    assert _working(fake) == [(-1, "stop", 20.0, 1400.0)]  # the rest cancelled, the 20 protected
    conn.close()


def test_undo_cancels_at_the_broker_too(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, _stop, _stop_ref = _open(conn, fake)
    with conn:
        rid = book.propose_revision(
            conn, pid, "tighten_stop", {"stop": 1450.0}, "breakout held", "agent:monitor"
        )
    _turn(conn, fake, NIGHT)
    assert _working(fake) == [(-1, "stop", 50.0, 1450.0)]
    with conn:
        book.undo_revision(conn, rid, "too tight")
    _turn(conn, fake, NIGHT)
    assert _working(fake) == [(-1, "stop", 50.0, 1400.0)]  # the 1450 stop cancelled there too
    conn.close()


def test_the_agents_limit_keeps_the_exit_reason_and_its_undo(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, _, _ = _open(conn, fake)
    with conn:
        rid = book.propose_revision(conn, pid, "exit", {}, "thesis broken", "agent:monitor")
    exit_id = int(book.orders(conn, pid, status="approved")["order_id"].iloc[0])
    with conn:  # the agent sends it as a limit: a replacement order
        new = execution._override(conn, exit_id, "limit", 1490.0, execution.AGENT)
    assert new != exit_id and book.exit_reason_for(conn, new) == "thesis_broken"
    with conn:
        out = book.undo_revision(conn, rid, "changed my mind")
    assert out["cancelled_order"] == new
    conn.close()


def test_writes_need_trading_on_for_the_account(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    from solaraalpha.config import settings

    monkeypatch.setattr(settings, "t212_key", SecretStr("k"))
    monkeypatch.setattr(settings, "t212_secret", SecretStr("s"))
    monkeypatch.setattr(settings, "t212_env", "demo")
    monkeypatch.setattr(settings, "t212_trading", "off")
    assert "is off" in str(t212.trading_allowed())
    with pytest.raises(RuntimeError, match="is off"):
        t212.place("limit", "AAZl_EQ", 1, 1, 274.0)
    with pytest.raises(RuntimeError, match="is off"):
        execution.LiveClient()
    monkeypatch.setattr(settings, "t212_trading", "live")
    assert "account (SOLARA_T212_ENV) is demo" in str(t212.trading_allowed())
    monkeypatch.setattr(settings, "t212_trading", "demo")
    assert t212.trading_allowed() is None
    monkeypatch.setattr(settings, "t212_env", "live")
    assert "account (SOLARA_T212_ENV) is live" in str(t212.trading_allowed())


def test_the_inbox_reports_each_turn_and_a_stop_not_placed_is_a_desk_error(
    db_backend: Path | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from solaraalpha import notify
    from solaraalpha.config import settings

    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, oid = _entry(conn)
    since = book.now()
    assert notify.execute_message(conn, since) is None  # nothing happened: silence
    _turn(conn, fake, NIGHT)
    ref = str(book.orders(conn, pid).set_index("order_id").loc[oid, "broker_ref"])
    text = notify.execute_message(conn, since)
    assert text is not None and "JET2.L: order #1 entry buy 50 limit @ 1,500p: sent" in text
    assert notify.execute_errors(conn, since) is None
    fake.fill(ref, price=1495.0)
    fake.fail_next = t212.T212Error(400, "/api-errors/some-check", "Refused", "a made-up refusal")
    since = book.now()
    _turn(conn, fake, NIGHT)
    text = notify.execute_message(conn, since)
    assert text is not None and "order #1 filled 50 at 1,495p" in text
    assert "> **Desk error:** JET2.L: stop #2 not at Trading212" in text
    errs = notify.execute_errors(conn, since)
    assert errs is not None and "the shares have no stop" in errs and errs.count("\n- ") == 1
    # the daily inbox gives order steps only while the owner places them
    with conn:
        book.propose_revision(conn, pid, "exit", {}, "thesis broken", "agent:monitor")
    run_id = execution_run(conn)
    owner = notify.daily_message(conn, run_id)
    assert owner is not None and "Place **Sell 50 · Market order**" in owner
    monkeypatch.setattr(settings, "orders_by", "system")
    assert notify.daily_message(conn, run_id) is None  # nothing for the owner to do
    conn.close()


def execution_run(conn: Conn) -> int:
    from solaraalpha import store

    return store.start_run("daily", {}, conn=conn)


def test_a_swap_whose_exit_was_refused_gets_its_stop_back_out_of_hours(
    db_backend: Path | None,
) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, _, _ = _open(conn, fake)
    with conn:
        book.propose_revision(conn, pid, "take_partial", {"qty": 20}, "trim", "pipeline")
        exit_id = int(book.orders(conn, pid, status="approved")["order_id"].iloc[0])
        book.swap_for_exit(conn, exit_id)  # the stop cut to 30, then the exit never went out
    _turn(conn, fake, NIGHT)
    assert _working(fake) == [(-1, "stop", 50.0, 1400.0)]  # every share protected overnight
    assert book.order_status(conn, exit_id) == "approved"
    _turn(conn, fake, OPEN_AT)  # the next session swaps again and sells
    assert _working(fake) == [(-1, "market", 20.0, None), (-1, "stop", 30.0, 1400.0)]
    conn.close()


def test_a_rejected_exit_puts_the_stop_back_at_once(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, _, _ = _open(conn, fake)
    with conn:
        exit_id = book.exit_position(conn, pid, "thesis_broken")
    fake.fail_next = t212.T212Error(400, "/api-errors/x", "Bad", "instrument closed")
    _turn(conn, fake, OPEN_AT)  # the stop swapped off, then Trading212 refused the exit
    assert book.order_status(conn, exit_id) == "rejected"
    assert _working(fake) == [(-1, "stop", 50.0, 1400.0)]  # every share protected again
    _turn(conn, fake, NIGHT)
    assert _working(fake) == [(-1, "stop", 50.0, 1400.0)]
    conn.close()


def test_an_exit_the_guards_refuse_never_takes_the_stop_off(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, stop_id, _ = _open(conn, fake)
    with conn:
        exit_id = book.exit_position(conn, pid, "thesis_broken")
    prices = execution.prices_for(fake.broker(), lambda t: pd.DataFrame(), OPEN_AT)
    with pytest.raises(Refused, match="band"):  # a limit 13% under the market
        execution.send(conn, fake, exit_id, OPEN_AT, prices, "agent:execute", "", "limit", 1300.0)
    assert book.order_status(conn, stop_id) == "placed"
    assert _working(fake) == [(-1, "stop", 50.0, 1400.0)]
    conn.close()


def test_a_fill_on_an_order_the_book_ended_is_reported_not_a_crash(
    db_backend: Path | None,
) -> None:
    from solaraalpha.portfolio import reconcile

    conn = book.connect(db_backend)
    fake = FakeBroker()
    pid, oid = _entry(conn)
    _turn(conn, fake, NIGHT)
    ref = str(book.orders(conn, pid).set_index("order_id").loc[oid, "broker_ref"])
    with conn:
        book.expire_orders(conn, "2030-01-01")  # expired in the book, still working there
    fake.fill(ref, price=1495.0)  # it filled at the open before the cancel reached it
    _turn(conn, fake, OPEN_AT)
    _turn(conn, fake, OPEN_AT)  # every later turn runs too
    diffs, _ = reconcile.reconcile(conn, fake.broker())
    late = [d for d in diffs if d.kind == "late_fill"]
    assert len(late) == 1 and late[0].order_id == oid and "by hand" in late[0].text
    conn.close()


def test_an_order_placed_by_hand_is_never_cancelled(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    _pid, oid = _entry(conn)
    with conn:  # the book's entry ends without ever being sent
        book.end_order(conn, oid, "cancelled", "changed my mind")
    fake.place("limit", "JET2l_EQ", 50, 1, 1500.0)  # the same terms, placed by hand
    _turn(conn, fake, NIGHT)
    assert len(fake.working) == 1 and [c[0] for c in fake.calls] == ["place"]
    conn.close()


def test_the_agents_pack(db_backend: Path | None) -> None:
    conn = book.connect(db_backend)
    fake = FakeBroker()
    _pid, oid = _entry(conn)
    liq = pd.DataFrame({"adv_gbp_20": [12000.0], "spread_proxy": [0.031]}, index=["JET2.L"])
    p = execution.pack(conn, OPEN_AT, quotes=bars(1490.0), liquidity=liq)
    assert p["session"]["exits_may_go_out"] is True
    assert p["session"]["exit_deadline"] == "2026-10-27T15:45:00+00:00"
    (a,) = p["approved"]
    assert a["order_id"] == oid and a["purpose"] == "entry" and a["plan"]["stop"] == 1400.0
    assert a["last_close"] == 1490.0 and a["liquidity"]["spread_proxy"] == 0.031
    assert a["waiting"] is None or "agent" in a["waiting"]
    _turn(conn, fake, NIGHT)
    p = execution.pack(conn, NIGHT, quotes=bars())
    assert p["session"]["open"] is not None and p["session"]["exits_may_go_out"] is False
    assert [o["order_id"] for o in p["at_broker"]] == [oid] and p["approved"] == []
    assert [x["action"] for x in p["since_your_last_turn"]["executions"]] == ["sent"]
    conn.close()


def test_cli_offline_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "execute_cli", Path(__file__).parents[1] / "scripts" / "execute.py"
    )
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    db = tmp_path / "x.db"
    conn = book.connect(db)
    _, oid = _entry(conn)
    conn.close()
    cli.main(["--db", str(db), "hold", str(oid), "--until", "2026-10-27T08:15", "--reason", "open"])
    cli.main(["--db", str(db), "note", "--reason", "nothing else this turn"])
    cli.main(["--db", str(db), "log"])
    out = capsys.readouterr().out
    assert f"order {oid} held until 2026-10-27T08:15:00+00:00" in out
    assert f"agent:execute #{oid} hold: open" in out and "agent:execute note:" in out
    with pytest.raises(SystemExit):  # sending needs SOLARA_T212_TRADING on for the account
        cli.main(["--db", str(db), "send", str(oid), "--reason", "go"])
    assert "SOLARA_T212_TRADING is off" in capsys.readouterr().out
