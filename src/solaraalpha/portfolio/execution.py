"""Orders to Trading212 through one guarded door: the execution job and agent (ADR 0031).

The book is the intent; this module makes the broker match it. Every order goes out through
:func:`send`, which checks the guards (:func:`check`) and writes ``sending`` to the book
**before** the call, so a call that times out is never sent again blind: the next turn lists
the broker's orders (``reconcile.absorb``) and only an order the broker never showed goes back
to ``approved``. Every cancel goes through :func:`settle`: the orders the book is
``cancelling`` (or has ended) that still work at the broker are cancelled there, confirmed gone
(``GET /{id}`` 404), and their end state (cancelled, or a fill that raced the cancel) is booked
from the history. Cancel, confirm, then place: nothing new goes out for a position while one of
its orders is still cancelling.

- **Stops** go out at once, in the turn that sees the fill (owner, 2026-10-21).
- **Exits** go out only in market hours, before :data:`EXIT_DEADLINE` ahead of the close:
  :func:`send` swaps the stop off the shares first (``book.swap_for_exit``; a partial cuts it
  to the shares left). An exit unfilled at the deadline is cancelled and the stop goes back on
  before the close, so a holding never crosses a close without its stop.
- **Entries and adds** go out as booked; a ``wait_for_break`` entry once a daily close is above
  its price, a PM rotation's buy once its sale has filled. Once the session of its
  ``valid_until`` day has closed, a buy expires in the book and is cancelled at the broker in
  the same turn, an order placed by hand included (ADR 0034).
- With the agent on (``agent=True``), a new non-stop order waits up to :data:`AGENT_GRACE` for
  the execution agent to choose how and when (``send`` with a type / price, ``hold``); after
  that the code sends it as booked, so an agent failure never stops the book trading.

Every action is a row in the ``executions`` table (append-only, with its reason and actor).
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from solaraalpha.data import trading212 as t212
from solaraalpha.log import get_logger
from solaraalpha.portfolio import book, reconcile, risk
from solaraalpha.portfolio.rules import Rules, load_rules
from solaraalpha.store import Conn, query

log = get_logger(__name__)

ACTOR = "execute"  # the code's own actions; the agent acts as agent:execute
AGENT = "agent:execute"
PRICE_BAND = 0.10  # a limit (or a buy stop) more than 10% from the last trade is refused
EXIT_DEADLINE = pd.Timedelta(minutes=45)  # before the close (15:45 London; owner, 2026-10-23)
AGENT_GRACE = pd.Timedelta(minutes=30)  # then the code sends a new order as booked
CONFIRM_SECONDS = 10  # polls of GET /{id} (1 per second) before a cancel counts as unconfirmed
MAX_REJECTIONS = 2  # the agent's one retry; then the owner decides

# London Stock Exchange: weekdays 08:00-16:30, closed on England & Wales bank holidays,
# 12:30 close on Christmas Eve and New Year's Eve. Extend each year (a warning says when).
LONDON = ZoneInfo("Europe/London")
OPEN, CLOSE, HALF_CLOSE = dtime(8, 0), dtime(16, 30), dtime(12, 30)
HOLIDAYS = frozenset(
    {
        "2026-01-01",
        "2026-04-03",
        "2026-04-06",
        "2026-05-04",
        "2026-05-25",
        "2026-08-31",
        "2026-12-25",
        "2026-12-28",
        "2027-01-01",
        "2027-03-26",
        "2027-03-29",
        "2027-05-03",
        "2027-05-31",
        "2027-08-30",
        "2027-12-27",
        "2027-12-28",
    }
)
HALF_DAYS = frozenset({"2026-12-24", "2026-12-31", "2027-12-24", "2027-12-31"})
CALENDAR_YEARS = frozenset({2026, 2027})


class Refused(Exception):
    """A guard refused the order: nothing was sent."""


# ---------------------------------------------------------------------------- calendar


def session(at: Any) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """The London session ``(open, close)`` (UTC) of ``at``'s London day, None when shut."""
    local = pd.Timestamp(at).tz_convert(LONDON)
    day = local.strftime("%Y-%m-%d")
    if local.year not in CALENDAR_YEARS:
        log.warning("no LSE holiday list for %s: weekdays only", local.year)
    if local.weekday() >= 5 or day in HOLIDAYS:
        return None
    close = HALF_CLOSE if day in HALF_DAYS else CLOSE
    d = local.date()
    return (
        pd.Timestamp(datetime.combine(d, OPEN), tz=LONDON).tz_convert("UTC"),
        pd.Timestamp(datetime.combine(d, close), tz=LONDON).tz_convert("UTC"),
    )


def exit_window(at: Any) -> bool:
    """Whether an exit may go out now: market open, before the deadline ahead of the close."""
    s = session(at)
    return s is not None and s[0] <= pd.Timestamp(at) < s[1] - EXIT_DEADLINE


def past_deadline(at: Any) -> bool:
    """In today's session, at or after the exit deadline (working exits are cancelled)."""
    s = session(at)
    return s is not None and s[1] - EXIT_DEADLINE <= pd.Timestamp(at) < s[1]


def expiry_as_of(at: Any) -> str:
    """The date for :func:`book.expire_orders` at ``at``: an order valid until a day is good
    through that day's session, so after today's close it is tomorrow's date (orders valid
    until today expire), else today's (orders valid until yesterday or before)."""
    local = pd.Timestamp(at).tz_convert(LONDON)
    s = session(at)
    if s is not None and pd.Timestamp(at) >= s[1]:
        local += pd.Timedelta(days=1)
    return local.strftime("%Y-%m-%d")


def deadline_text(at: Any) -> str:
    s = session(at)
    end = (s[1] - EXIT_DEADLINE) if s else None
    return end.tz_convert(LONDON).strftime("%H:%M London") if end is not None else "the open"


# ---------------------------------------------------------------------------- the broker


class Client(Protocol):
    """What the executor needs from the broker (the live API, or a fake in tests)."""

    def broker(self) -> reconcile.Broker: ...

    def ticker(self, yahoo: str) -> str | None: ...

    def place(
        self, type: str, t212_ticker: str, qty: int, side: int, price: float | None
    ) -> dict[str, Any]: ...

    def cancel(self, ref: str) -> None: ...

    def order(self, ref: str) -> dict[str, Any] | None: ...


class LiveClient:
    """Trading212 through :mod:`solaraalpha.data.trading212`. ``write=True`` refuses at once
    unless orders may be sent (``SOLARA_T212_TRADING`` names the account, ADR 0031)."""

    def __init__(self, write: bool = True) -> None:
        if write and (why := t212.trading_allowed()):
            raise RuntimeError(why)
        self.lines = t212.lse_lines(t212.fetch_instruments())  # Yahoo -> Trading212

    def broker(self) -> reconcile.Broker:
        return reconcile.fetch_broker({v: k for k, v in self.lines.items()})

    def ticker(self, yahoo: str) -> str | None:
        return self.lines.get(yahoo)

    def place(
        self, type: str, t212_ticker: str, qty: int, side: int, price: float | None
    ) -> dict[str, Any]:
        return t212.place(type, t212_ticker, qty, side, price)

    def cancel(self, ref: str) -> None:
        t212.cancel(ref)

    def order(self, ref: str) -> dict[str, Any] | None:
        return t212.fetch_order(ref)


Quotes = Callable[[str], pd.DataFrame]  # Yahoo daily bars (prices.fetch_daily)


def _quotes() -> Quotes:
    from solaraalpha.data.prices import fetch_daily

    return fetch_daily


def last_close(bars: pd.DataFrame, now: Any) -> tuple[float | None, float | None]:
    """``(last completed daily close, latest price)`` from Yahoo bars: today's bar during the
    session is still forming, so it is the latest price but not a close."""
    if bars is None or bars.empty:
        return None, None
    closes = bars["close"].dropna()
    if closes.empty:
        return None, None
    latest = float(closes.iloc[-1])
    last = pd.Timestamp(closes.index[-1])
    day = (last.tz_convert(LONDON) if last.tz else last).date()
    s = session(now)
    forming = s is not None and pd.Timestamp(now) < s[1]
    forming = forming and day == pd.Timestamp(now).tz_convert(LONDON).date()
    done = closes.iloc[:-1] if forming else closes
    return (float(done.iloc[-1]) if not done.empty else None), latest


@dataclass
class Prices:
    """Last prices per ticker for a turn: Trading212's price of a holding, else Yahoo's."""

    quotes: Quotes
    now: Any
    held: dict[str, float] = field(default_factory=dict)  # Yahoo ticker -> Trading212 price
    _bars: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)

    def _get(self, ticker: str) -> tuple[float | None, float | None]:
        if ticker not in self._bars:
            try:
                self._bars[ticker] = last_close(self.quotes(ticker), self.now)
            except Exception as e:  # a missing price refuses the order; it never sends blind
                log.warning("%s: no prices (%s)", ticker, e)
                self._bars[ticker] = (None, None)
        return self._bars[ticker]

    def last(self, ticker: str) -> float | None:
        return self.held.get(ticker) or self._get(ticker)[1]

    def close(self, ticker: str) -> float | None:
        return self._get(ticker)[0]


def prices_for(br: reconcile.Broker, quotes: Quotes, now: Any) -> Prices:
    pos = br.positions
    held = {br.yahoo(str(t)): float(p) for t, p in zip(pos["t212"], pos["price"], strict=True)}
    return Prices(quotes, now, held)


# ---------------------------------------------------------------------------- the log


def record(
    conn: Conn,
    order_id: int | None,
    action: str,
    reason: str = "",
    actor: str = ACTOR,
    detail: dict[str, Any] | None = None,
) -> int:
    """Append one ``executions`` row; returns its id."""
    with conn:  # durable at once: the log must survive a crash in the next broker call
        cur = conn.execute(
            "INSERT INTO executions (at, order_id, action, detail, actor, reason)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (book.now(), order_id, action, json.dumps(detail or {}, default=str), actor, reason),
        )
    assert cur.lastrowid is not None
    return cur.lastrowid


def executions(conn: Conn, since: str | None = None) -> pd.DataFrame:
    """The ``executions`` rows (oldest first) at or after ``since``, ``detail`` parsed."""
    sql, params = "SELECT * FROM executions", []
    if since:
        sql += " WHERE at >= ?"
        params.append(since)
    df = query(sql + " ORDER BY execution_id", conn, params)
    df["detail"] = df["detail"].map(json.loads)
    return df


def _rows(conn: Conn, order_id: int) -> pd.DataFrame:
    df = query(
        "SELECT * FROM executions WHERE order_id = ? ORDER BY execution_id", conn, [order_id]
    )
    df["detail"] = df["detail"].map(json.loads)
    return df


def _count(conn: Conn, order_id: int, action: str) -> int:
    return int((_rows(conn, order_id)["action"] == action).sum())


def reviewed(conn: Conn, order_id: int, since: str = "") -> bool:
    """Whether the agent has acted on the order (sent, held, cancelled, noted) since ``since``."""
    rows = _rows(conn, order_id)
    mine = rows[rows["actor"].str.startswith(book.AGENT_PREFIX) & (rows["at"] >= since)]
    return not mine.empty


def held_until(conn: Conn, order_id: int, now: Any) -> str | None:
    """The time the agent holds the order until, when that is still ahead (else None)."""
    rows = _rows(conn, order_id)
    rows = rows[rows["actor"].str.startswith(book.AGENT_PREFIX)]
    if rows.empty or rows["action"].iloc[-1] != "hold":
        return None
    until = str(rows["detail"].iloc[-1].get("until") or "")
    return until if until and pd.Timestamp(until) > pd.Timestamp(now) else None


# ---------------------------------------------------------------------------- guards


def breaker_tripped(conn: Conn, now: Any, rules: Rules) -> float | None:
    """The drawdown when the circuit breaker stops new buys (#143), else None."""
    if not rules.circuit_breaker:
        return None
    from solaraalpha.portfolio.weekly_pack import drawdown_from_store

    try:
        dd = drawdown_from_store(conn, pd.Timestamp(now).isoformat(), rules)
    except Exception as e:
        log.warning("drawdown unavailable (%s)", e)
        return None
    if dd is None or math.isnan(dd) or dd >= -rules.max_drawdown_new_entries:
        return None
    return dd


def check(
    conn: Conn,
    order_id: int,
    last: float | None,
    now: Any,
    rules: Rules | None = None,
    sell_room: bool = True,
) -> None:
    """Refuse (:class:`Refused`) an order the guards don't let out (ADR 0031): it must be
    approved (or rejected once, for the agent's retry), long only, the side its purpose says;
    nothing else of the position still cancelling at the broker; a sell within the shares not
    already under a working sell; a limit (or buy stop) within :data:`PRICE_BAND` of ``last``;
    a buy inside the free cash and the position cap, and none while the circuit breaker is on.
    A sell stop is not price-checked: its job is to be below the market. ``sell_room`` False
    skips the per-unit check (an exit before its swap: the stop still holds the shares)."""
    rules = rules or load_rules()
    o = book._order(conn, order_id)
    status = book.order_status(conn, order_id)
    if status not in ("approved", "rejected"):
        raise Refused(f"order {order_id} is {status}, not approved")
    if status == "rejected" and _count(conn, order_id, "rejected") >= MAX_REJECTIONS:
        raise Refused(f"order {order_id} was rejected {MAX_REJECTIONS} times: the owner decides")
    pid = int(o["position_id"])
    st = book.position_state(conn, pid)
    side, purpose, qty = int(o["side"]), str(o["purpose"]), int(o["qty"])
    if st.side != 1:
        raise Refused("long only: the account holds no shorts")
    if (side == 1) != (purpose in book.BUY_PURPOSES):
        raise Refused(f"a {purpose} order cannot {'buy' if side == 1 else 'sell'}")
    od = book.orders(conn, pid)
    busy = od[(od["status"] == "cancelling") & (od["order_id"] != order_id)]
    if not busy.empty:
        raise Refused(
            f"order #{int(busy['order_id'].iloc[0])} of the position is still cancelling at"
            " Trading212: confirm it gone first"
        )
    if side == -1 and sell_room:
        try:
            book.check_sell(conn, order_id)
        except ValueError as e:
            raise Refused(str(e)) from None
    price = None if pd.isna(o["price"]) else float(o["price"])
    if price is not None and (o["type"] == "limit" or side == 1):
        if not last:
            raise Refused(f"no last price for {st.ticker} to check the order against")
        if abs(price / last - 1) > PRICE_BAND:
            raise Refused(
                f"price {book.fmt_price(price, st.currency)} is {price / last - 1:+.0%} from the"
                f" last trade {book.fmt_price(last, st.currency)}: outside the"
                f" {PRICE_BAND:.0%} band"
            )
    if side == 1:
        dd = breaker_tripped(conn, now, rules)
        if dd is not None:
            raise Refused(
                f"circuit breaker: drawdown {-dd:.0%} > {rules.max_drawdown_new_entries:.0%},"
                " no new buys"
            )
        px = price or last
        if not px:
            raise Refused(f"no price for {st.ticker} to size the buy against")
        from solaraalpha.portfolio.daily import free_cash

        cost = book.to_account(px * qty, st.currency)
        free = free_cash(conn, rules, but=pid)
        if cost > free + 0.01:
            raise Refused(f"the buy needs £{cost:,.2f} but £{max(free, 0):,.2f} is free")
        states = [book.position_state(conn, int(p)) for p in book.positions(conn)["position_id"]]
        acct = risk.equity(book.cash(conn), states, pd.DataFrame())
        eq = acct.cash + sum(
            book.to_account(s.avg_cost * s.shares, s.currency)
            for s in states
            if s.status == "open" and s.shares > 0
        )
        value = book.to_account(px * (st.shares + qty), st.currency)
        if eq <= 0 or value > rules.max_position_pct * eq + 0.01:
            raise Refused(
                f"£{value:,.0f} in {st.ticker} would be over the position cap"
                f" ({rules.max_position_pct:.0%} of £{max(eq, 0):,.0f})"
            )


# ---------------------------------------------------------------------------- send / cancel


def _override(conn: Conn, order_id: int, type: str | None, price: float | None, actor: str) -> int:
    """The agent's how (ADR 0031): an exit at market or a limit, a buy limit at or below the
    research's entry. The book records it (replace) so book and broker stay one. Stops are
    sent as booked."""
    o = book._order(conn, order_id)
    purpose, kind = str(o["purpose"]), type or str(o["type"])
    if purpose == "stop":
        raise Refused("a stop goes out as booked (a stop order at its level)")
    if purpose == "exit" and kind not in ("market", "limit"):
        raise Refused("an exit is a market or a limit order")
    if purpose in book.BUY_PURPOSES:
        if kind != str(o["type"]):
            raise Refused(f"a {purpose} keeps its type ({o['type']}); only a lower limit")
        if price is not None and (pd.isna(o["price"]) or price > float(o["price"])):
            raise Refused(f"a buy limit may only be at or below the booked {o['price']}")
    if kind != "market" and price is None and pd.isna(o["price"]):
        raise Refused(f"a {kind} order needs --price")
    if kind == str(o["type"]) and (price is None or price == o["price"]):
        return order_id
    # the approved reason carries the exit's tag (book.exit_reason_for): keep it in front
    ev = book.order_events(conn, order_id)
    was = ev.loc[ev["status"] == "approved", "reason"]
    why = f"how to send it: {kind}"
    why = f"{was.iloc[-1]}: {why}" if not was.empty and was.iloc[-1] else why
    return book.replace_order(conn, order_id, None, price, why, actor, type=kind)


def _stop_back(
    conn: Conn,
    client: Client,
    position_id: int,
    now: Any,
    prices: Prices,
    actor: str,
    rules: Rules,
    sleep: Callable[[float], None],
) -> None:
    """An exit that did not go out after its swap (refused or rejected): the stop back on every
    share at once, so the holding never waits for a later turn without one."""
    if book.position_status(conn, position_id) != "open":
        return
    with conn:
        new = book.protect(conn, position_id, actor=actor)
    if new is None or book.order_status(conn, new) != "approved":
        return
    why = "the stop back: the exit did not go out"
    try:
        send(conn, client, new, now, prices, actor, why, rules=rules, sleep=sleep)
    except Refused as e:  # e.g. the old stop still cancelling: the next turn sends it
        record(conn, new, "refused", str(e), actor)


def send(
    conn: Conn,
    client: Client,
    order_id: int,
    now: Any,
    prices: Prices,
    actor: str = ACTOR,
    reason: str = "",
    type: str | None = None,
    price: float | None = None,
    rules: Rules | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Send one approved order (the one door, ADR 0031); returns ``placed`` / ``rejected`` /
    ``unknown`` (the call may have reached the broker: the next turn matches before anything
    is sent again) / ``dropped`` (the stop filled during an exit's swap). Raises
    :class:`Refused` when a guard says no; nothing is sent then."""
    rules = rules or load_rules()
    if type is not None or price is not None:
        with conn:
            order_id = _override(conn, order_id, type, price, actor)
    o = book._order(conn, order_id)
    pid = int(o["position_id"])
    ticker = str(book._position(conn, pid)["ticker"])
    if o["purpose"] == "exit":
        if not exit_window(now):
            raise Refused(
                "an exit goes out only in market hours, before"
                f" {deadline_text(now)}: the stop stays on until then"
            )
        # every guard the swap can't change first: a refusal after it leaves the shares bare
        if not client.ticker(ticker):
            raise Refused(f"Trading212 doesn't offer {ticker} in GBX")
        check(conn, order_id, prices.last(ticker), now, rules, sell_room=False)
        with conn:
            swapped = book.swap_for_exit(conn, order_id, actor)
        if swapped["cancelled"] is not None:
            record(conn, order_id, "swap", f"stop #{swapped['cancelled']} off for the exit", actor)
            settle(conn, client, actor, sleep=sleep)
            new = swapped["stop_order_id"]
            if new is not None and book.order_status(conn, int(new)) == "approved":
                send(conn, client, int(new), now, prices, actor, "the stop on the shares left")
        if book.order_status(conn, order_id) not in ("approved", "rejected"):
            record(conn, order_id, "dropped", "the position changed during the swap", actor)
            return "dropped"
    t212_ticker = client.ticker(ticker)
    if not t212_ticker:
        raise Refused(f"Trading212 doesn't offer {ticker} in GBX")
    try:
        check(conn, order_id, prices.last(ticker), now, rules)
    except Refused:
        if o["purpose"] == "exit":
            _stop_back(conn, client, pid, now, prices, actor, rules, sleep)
        raise
    side, qty = int(o["side"]), int(o["qty"])
    px = None if pd.isna(o["price"]) else float(o["price"])
    terms = {"type": str(o["type"]), "qty": qty, "side": side, "price": px, "t212": t212_ticker}
    with conn:
        book.order_event(conn, order_id, "sending", actor=actor, reason=reason)
    try:
        res = client.place(str(o["type"]), t212_ticker, qty, side, px)
    except t212.T212Error as e:
        with conn:
            if e.rate_limited:  # a 429 did nothing: back to approved, sent next turn
                book.order_event(conn, order_id, "approved", actor=actor, reason=f"not sent: {e}")
                record(conn, order_id, "refused", f"rate limited: {e}", actor, terms)
                return "refused"
            book.order_event(conn, order_id, "rejected", actor=actor, reason=str(e))
            record(conn, order_id, "rejected", str(e), actor, {**terms, "status": e.status})
        if o["purpose"] == "exit":
            _stop_back(conn, client, pid, now, prices, actor, rules, sleep)
        return "rejected"
    except t212.Unknown as e:
        with conn:
            record(conn, order_id, "unknown", str(e), actor, terms)
        return "unknown"
    with conn:
        book.place_order(conn, order_id, str(res["id"]), actor=actor)
        record(conn, order_id, "sent", reason, actor, {**terms, "ref": str(res["id"])})
    return "placed"


def settle(
    conn: Conn,
    client: Client,
    actor: str = ACTOR,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Cancel at the broker every working order the book is cancelling or has ended, confirm
    each gone (``GET /{id}`` 404), then book what the broker shows (the cancels confirmed, a
    fill that raced one). Returns what was booked, in plain words."""
    br = client.broker()
    od = reconcile._book_orders(conn)
    match, _ = reconcile._matcher(od, br)
    by_id = od.set_index("order_id")
    placed = _placed_ids(conn)
    todo = []
    for o in br.pending.to_dict("records"):
        oid = match(o)
        if oid is None:
            continue  # not the book's: never touched (an order by hand is the owner's to resolve)
        status, ref = by_id.loc[oid, "status"], by_id.loc[oid, "broker_ref"]
        # the book's own order: sent with this ref, or placed by hand (no ref) and matched on
        # its terms; an order the book ended before it was ever placed is not this one
        ours = str(ref) == str(o["id"]) or (pd.isna(ref) and oid in placed)
        ended = status in ("cancelled", "expired", "not_filled") and ours
        if status == "cancelling" or ended:
            todo.append((oid, str(o["id"])))
    for oid, ref in todo:
        try:
            client.cancel(ref)
        except t212.T212Error as e:
            if e.status != 404:  # 404: not working there any more
                record(conn, oid, "error", f"cancel refused: {e}", actor, {"ref": ref})
                continue
        except t212.Unknown as e:
            record(conn, oid, "error", f"cancel unanswered: {e}", actor, {"ref": ref})
            continue
        try:
            for _ in range(CONFIRM_SECONDS):
                if client.order(ref) is None:
                    record(conn, oid, "cancel_sent", "confirmed gone", actor, {"ref": ref})
                    break
                sleep(1)
            else:
                record(conn, oid, "error", "cancel not confirmed: tried again next turn", actor)
        except Exception as e:  # a blip while polling: the next turn confirms; the turn goes on
            why = f"cancel not confirmed ({type(e).__name__}: {e}): tried again next turn"
            record(conn, oid, "error", why, actor, {"ref": ref})
    return _absorb(conn, client.broker() if todo else br)


def _placed_ids(conn: Conn) -> set[int]:
    df = query("SELECT DISTINCT order_id FROM order_events WHERE status = 'placed'", conn)
    return {int(x) for x in df["order_id"]}


def _absorb(conn: Conn, br: reconcile.Broker) -> list[str]:
    """Book what the broker shows (``reconcile.absorb``), each item logged as ``booked``."""
    with conn:
        booked, _, _ = reconcile.absorb(conn, br)
    out = []
    for d in booked:
        text = f"{d.ticker}: {reconcile.booked_text(d)}"
        record(conn, d.order_id, "booked", reconcile.booked_text(d), reconcile.ACTOR, dict(d.data))
        out.append(text)
    return out


# ---------------------------------------------------------------------------- the turn


def _approved_at(conn: Conn, order_id: int) -> pd.Timestamp:
    ev = book.order_events(conn, order_id)
    return pd.Timestamp(str(ev.loc[ev["status"] == "approved", "at"].iloc[-1]))


def waiting(conn: Conn, order_id: int, now: Any, prices: Prices, agent: bool = False) -> str | None:
    """Why an approved order is not sent this turn (None: it goes out now)."""
    o = book._order(conn, order_id)
    pid, purpose = int(o["position_id"]), str(o["purpose"])
    status = book.position_status(conn, pid)
    if status != ("approved" if purpose == "entry" else "open"):
        return f"position is {status}"
    until = held_until(conn, order_id, now)
    if until:
        return f"held by the agent until {until}"
    if purpose == "exit" and not exit_window(now):
        return f"an exit waits for market hours (before {deadline_text(now)})"
    if purpose == "entry":
        sales = book.waiting_on(conn, pid)
        if sales:
            return f"waits for the sale of position {', '.join(map(str, sales))} to fill"
        if book._drafted_plan(conn, pid).get("wait_for_break"):
            close = prices.close(str(book._position(conn, pid)["ticker"]))
            if close is None or pd.isna(o["price"]) or close <= float(o["price"]):
                return f"waits for a daily close above {o['price']}"
    new = agent and purpose != "stop" and not reviewed(conn, order_id)
    if new and pd.Timestamp(now) - _approved_at(conn, order_id) < AGENT_GRACE:
        return "waits for the execution agent"
    return None


def agent_send(
    conn: Conn,
    client: Client,
    order_id: int,
    now: Any,
    prices: Prices,
    actor: str = AGENT,
    reason: str = "",
    type: str | None = None,
    price: float | None = None,
) -> str:
    """The agent's ``execute.py send``: how and when, never past what the book waits for (a
    break not closed yet, the sale that pays for it, market hours); its own hold it may lift.
    Raises :class:`Refused` while the order waits, else :func:`send`."""
    why = waiting(conn, order_id, now, prices)
    if why and not why.startswith("held by the agent"):
        raise Refused(f"order {order_id} {why}")
    return send(conn, client, order_id, now, prices, actor, reason, type, price)


@dataclass
class TurnResult:
    started: str
    booked: list[str] = field(default_factory=list)  # what the broker showed, booked
    done: list[str] = field(default_factory=list)  # sent / cancelled / swapped, in plain words
    refused: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    agent: list[str] = field(default_factory=list)  # why the agent is needed this turn


def _ticker(conn: Conn, order_id: int) -> str:
    return str(book._position(conn, int(book._order(conn, order_id)["position_id"]))["ticker"])


def _refuse_once(conn: Conn, order_id: int, why: str, actor: str) -> bool:
    """Log a refusal unless it repeats the order's last one; returns whether it was new."""
    rows = _rows(conn, order_id)
    last = rows[rows["action"] == "refused"]
    if not last.empty and last["reason"].iloc[-1] == why and rows["action"].iloc[-1] == "refused":
        return False
    record(conn, order_id, "refused", why, actor)
    return True


def turn(
    conn: Conn,
    client: Client,
    now: Any = None,
    quotes: Quotes | None = None,
    agent: bool = False,
    actor: str = ACTOR,
    rules: Rules | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> TurnResult:
    """One execution turn (code only): book what the broker shows; expire the buys past their
    validity; send again what the broker never received; at the deadline cancel the exits still
    working; then settle the cancels and send every approved order that may go out now, stops
    first, until nothing changes."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    rules = rules or load_rules()
    res = TurnResult(started=book.now())
    br = client.broker()
    prices = prices_for(br, quotes or _quotes(), now)
    res.booked += _absorb(conn, br)

    # after the fills are booked, so an order that filled before its expiry keeps its fill
    stale = book.orders(conn).set_index("order_id")["valid_until"]
    with conn:
        expired = book.expire_orders(conn, expiry_as_of(now), actor=actor)
    for oid in expired:
        why = f"valid until {stale[oid]}"
        record(conn, oid, "expire", why, actor)
        o = book._order(conn, oid)
        res.done.append(
            f"{_ticker(conn, oid)}: {o['purpose']} #{oid} expired ({why}): cancelled at"
            " Trading212 if it was working there"
        )

    od = book.orders(conn)
    for r in od[od["status"] == "sending"].to_dict("records"):
        if str(r["updated_at"]) < res.started:  # an earlier turn's send the broker never showed
            oid = int(r["order_id"])
            with conn:
                book.order_event(
                    conn, oid, "approved", actor=actor, reason="not at Trading212: send again"
                )
                record(conn, oid, "unknown", "never reached Trading212: back to approved", actor)

    # an exit refused after its swap (out of the window) or rejected: the stop back on every share
    back = ["rejected"] if exit_window(now) else ["rejected", "approved"]
    work = od[(od["purpose"] == "exit") & od["status"].isin(back)]
    for pid in dict.fromkeys(int(x) for x in work["position_id"]):
        if book.position_status(conn, pid) == "open":
            with conn:
                book.protect(conn, pid, actor=actor)

    if past_deadline(now):
        work = od[(od["purpose"] == "exit") & od["status"].isin(["placed", "part_filled"])]
        for oid in (int(x) for x in work["order_id"]):
            why = (
                f"unfilled by {deadline_text(now)}: cancelled so the stop is back before the close"
            )
            with conn:
                book.cancel_order(conn, oid, why, actor)
                record(conn, oid, "cancel", why, actor)
            res.done.append(f"{_ticker(conn, oid)}: exit #{oid} {why}")

    for _ in range(3):
        settled = settle(conn, client, actor, sleep)
        res.booked += settled
        sent = 0
        od = book.orders(conn)
        todo = od[od["status"] == "approved"].assign(
            _rank=lambda d: d["purpose"].map({"stop": 0, "exit": 1}).fillna(2)
        )
        for oid in (int(x) for x in todo.sort_values(["_rank", "order_id"])["order_id"]):
            if book.order_status(conn, oid) != "approved":
                continue  # an earlier send in this pass changed it (a swap's stop)
            if waiting(conn, oid, now, prices, agent):
                continue
            tk = _ticker(conn, oid)
            try:
                out = send(conn, client, oid, now, prices, actor, "", rules=rules, sleep=sleep)
            except Refused as e:
                if _refuse_once(conn, oid, str(e), actor):
                    res.refused.append(f"{tk}: order #{oid} not sent: {e}")
                continue
            except Exception as e:  # never stop the turn: the next one tries again
                log.exception("order %s", oid)
                record(conn, oid, "error", f"{type(e).__name__}: {e}", actor)
                res.errors.append(f"{tk}: order #{oid}: {type(e).__name__}: {e}")
                continue
            sent += out in ("placed", "unknown")  # a 429 waits; an unknown is listed next
            res.done.append(f"{tk}: {describe(conn, oid, out)}")
        if not sent and not settled:
            break
    res.agent = agent_queue(conn, now, prices, agent)
    return res


def describe(conn: Conn, order_id: int, outcome: str) -> str:
    """``order #12 stop sell 73 @ 337p: placed`` (the order's current row)."""
    o = book._order(conn, order_id)
    ccy = str(book._position(conn, int(o["position_id"]))["currency"])
    px = book.fmt_price(None if pd.isna(o["price"]) else float(o["price"]), ccy)
    side = "buy" if int(o["side"]) == 1 else "sell"
    return f"order #{order_id} {o['purpose']} {side} {int(o['qty'])} {o['type']} @ {px}: {outcome}"


def agent_queue(conn: Conn, now: Any, prices: Prices, agent: bool = True) -> list[str]:
    """What the execution agent has to decide this turn (empty: no agent call): new non-stop
    orders waiting for it, rejected orders, stops the code sent since the agent's last turn."""
    if not agent:
        return []
    od = book.orders(conn)
    out = []
    for oid in (int(x) for x in od.loc[od["status"] == "approved", "order_id"]):
        if waiting(conn, oid, now, prices, agent) == "waits for the execution agent":
            out.append(f"#{oid} {_ticker(conn, oid)}: how and when to send it")
    for oid in (int(x) for x in od.loc[od["status"] == "rejected", "order_id"]):
        rows = _rows(conn, oid)
        at = rows.loc[rows["action"] == "rejected", "at"]
        if not reviewed(conn, oid, str(at.iloc[-1]) if not at.empty else ""):
            out.append(f"#{oid} {_ticker(conn, oid)}: rejected by Trading212")
    ex = executions(conn)
    mine = ex[ex["actor"].str.startswith(book.AGENT_PREFIX)]
    since = str(mine["at"].iloc[-1]) if not mine.empty else ""
    code = ex[(ex["actor"] == ACTOR) & (ex["action"] == "sent") & (ex["at"] > since)]
    for oid in (int(x) for x in code["order_id"].dropna()):
        if book._order(conn, oid)["purpose"] == "stop":
            out.append(f"#{oid} {_ticker(conn, oid)}: stop sent by code, to review")
    return out


def hold(conn: Conn, order_id: int, until: Any, reason: str, actor: str = AGENT) -> str:
    """The agent keeps an approved order back until ``until`` (UTC unless it has an offset);
    returns it as ISO. Refused past the order's validity: it would expire unsent, so let it
    lapse (``cancel``) instead."""
    at = pd.Timestamp(until)
    at = at.tz_localize("UTC") if at.tz is None else at.tz_convert("UTC")
    if book.order_status(conn, order_id) != "approved":
        raise Refused(f"order {order_id} is not approved: nothing to hold")
    valid = book._order(conn, order_id)["valid_until"]
    if valid and not pd.isna(valid) and expiry_as_of(at) > str(valid):
        raise Refused(
            f"order {order_id} is valid until {valid}: held past that it expires unsent;"
            " cancel it instead if it should not go out"
        )
    record(conn, order_id, "hold", reason, actor, {"until": at.isoformat()})
    return at.isoformat()


def cancel(
    conn: Conn,
    client: Client,
    order_id: int,
    reason: str,
    actor: str = AGENT,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """The agent lets an approved order lapse or cancels one working at the broker (never a
    stop: a holding keeps its stop). Returns the order's status after."""
    o = book._order(conn, order_id)
    if o["purpose"] == "stop":
        raise Refused("a stop is never cancelled by the execution agent")
    status = book.order_status(conn, order_id)
    with conn:
        if status in ("approved", "rejected"):
            book.end_order(conn, order_id, "cancelled", reason, actor)
        elif status in book.AT_BROKER - {"cancelling"}:
            book.cancel_order(conn, order_id, reason, actor)
        else:
            raise Refused(f"order {order_id} is {status}: nothing to cancel")
        record(conn, order_id, "cancel", reason, actor)
    settle(conn, client, actor, sleep)
    return str(book.order_status(conn, order_id))


# ---------------------------------------------------------------------------- the agent's pack


def _plain(v: Any) -> Any:
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v.item() if hasattr(v, "item") else v


def _order_row(conn: Conn, r: Any) -> dict[str, Any]:
    pid = int(r["position_id"])
    pos = book._position(conn, pid)
    keys = (
        "order_id",
        "purpose",
        "side",
        "type",
        "qty",
        "price",
        "status",
        "broker_ref",
        "valid_until",
    )
    out = {k: _plain(r[k]) for k in keys}
    ev = book.order_events(conn, int(r["order_id"]))
    return {
        **out,
        "position_id": pid,
        "ticker": str(pos["ticker"]),
        "currency": str(pos["currency"]),
        "filled": int(r["filled"]),
        "since": str(r["updated_at"]),
        "why": str(ev["reason"].iloc[-1] or ev["reason"].iloc[0] or ""),
    }


def pack(
    conn: Conn,
    now: Any = None,
    quotes: Quotes | None = None,
    liquidity: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """The execution agent's pack (DB and Yahoo only): the session, what it has to decide
    (:func:`agent_queue`), every approved order not yet sent with why it waits, its research
    plan, last close / price and liquidity; the orders at the broker; the rejected ones; what
    the code did and what filled since the agent's last turn; the guards it works inside."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    prices = Prices(quotes or _quotes(), now)
    liq = liquidity if liquidity is not None else pd.DataFrame()
    s = session(now)
    od = book.orders(conn)
    ex = executions(conn)
    mine = ex[ex["actor"].str.startswith(book.AGENT_PREFIX)]
    since = str(mine["at"].iloc[-1]) if not mine.empty else ""
    approved = []
    for r in od[od["status"] == "approved"].to_dict("records"):
        row = _order_row(conn, r)
        plan = book._drafted_plan(conn, row["position_id"])
        row |= {
            "waiting": waiting(conn, row["order_id"], now, prices, agent=True),
            "held_until": held_until(conn, row["order_id"], now),
            "plan": {
                k: plan.get(k)
                for k in ("entry", "entry_type", "stop", "stop_reason", "target", "valid_until")
            },
            "last_close": prices.close(row["ticker"]),
            "last_price": prices.last(row["ticker"]),
        }
        if not liq.empty and row["ticker"] in liq.index:
            card = liq.loc[row["ticker"]]
            row["liquidity"] = {
                k: _plain(card.get(k)) for k in ("adv_gbp_20", "spread_proxy", "max_position_gbp")
            }
        approved.append(row)
    fl = book.fills_with_tickers(conn)
    return {
        "now": now.isoformat(),
        "session": {
            "open": s[0].isoformat() if s else None,
            "close": s[1].isoformat() if s else None,
            "exit_deadline": (s[1] - EXIT_DEADLINE).isoformat() if s else None,
            "exits_may_go_out": exit_window(now),
        },
        "decide": agent_queue(conn, now, prices),
        "approved": approved,
        "at_broker": [
            _order_row(conn, r) for r in od[od["status"].isin(book.AT_BROKER)].to_dict("records")
        ],
        "rejected": [
            {**_order_row(conn, r), "rejections": _count(conn, int(r["order_id"]), "rejected")}
            for r in od[od["status"] == "rejected"].to_dict("records")
        ],
        "since_your_last_turn": {
            "since": since or None,
            "executions": [
                {
                    k: _plain(v)
                    for k, v in x.items()
                    if k in ("at", "order_id", "action", "actor", "reason", "detail")
                }
                for x in ex[ex["at"] > since].to_dict("records")
            ],
            "fills": [
                {
                    k: _plain(f[k])
                    for k in ("at", "ticker", "order_id", "purpose", "side", "qty", "price")
                }
                for f in fl[fl["at"] > since].to_dict("records")
            ],
        },
        "guards": {
            "price_band": PRICE_BAND,
            "exit_deadline_before_close_minutes": int(EXIT_DEADLINE.total_seconds() // 60),
            "agent_grace_minutes": int(AGENT_GRACE.total_seconds() // 60),
            "max_rejections": MAX_REJECTIONS,
        },
    }
