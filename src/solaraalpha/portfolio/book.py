"""The book: positions, orders, fills and cash in the results DB, append-only.

Lives in the same database as the model results (:mod:`solaraalpha.store`; local file or
Turso) under its own tables, ``BOOK_SCHEMA``. Nothing is ever updated or deleted: a change of
state (an order getting filled, a position being approved) is a new **event row**, and the
current state is the latest state-changing event. Every write is one ``INSERT``; illegal
transitions raise ``ValueError`` before anything is written.

Money: order and fill prices are in the instrument's quote currency (Yahoo gives LSE prices
in pence, ``GBp``), kept on the position as ``currency``. Commission and fees are in the account
currency (GBP). :func:`to_account` converts for money totals. See epic #104, story map §5.

Open a connection with :func:`connect` and pass it to every function; callers group writes
with ``with conn:`` where several must land together.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.portfolio.rules import Rules, load_rules
from solaraalpha.store import Conn, query

BOOK_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    position_id        INTEGER PRIMARY KEY,
    ticker             TEXT NOT NULL,
    side               INTEGER NOT NULL,      -- 1 long, -1 short
    currency           TEXT NOT NULL,         -- quote currency of prices (LSE: GBp)
    opened_from_run_id INTEGER,               -- scan run that produced the plan (NULL: by hand)
    horizon_weeks      INTEGER NOT NULL,
    thesis             TEXT NOT NULL,
    created_at         TEXT NOT NULL          -- ISO-8601 UTC
);
CREATE TABLE IF NOT EXISTS position_events (
    event_id    INTEGER PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES positions,
    at          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,                -- JSON
    actor       TEXT NOT NULL,                -- owner / pipeline / agent:<name>
    reason      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    order_id    INTEGER PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES positions,
    side        INTEGER NOT NULL,             -- 1 buy, -1 sell
    type        TEXT NOT NULL,                -- limit / stop / market
    qty         INTEGER NOT NULL,
    price       REAL,                         -- quote currency (NULL: market order)
    valid_until TEXT,
    purpose     TEXT NOT NULL,                -- entry / stop / target / add / exit
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_events (
    event_id   INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders,
    at         TEXT NOT NULL,
    status     TEXT NOT NULL,                 -- draft / approved / sending / placed / rejected /
                                              -- part_filled / filled / not_filled / cancelling /
                                              -- cancelled / expired
    broker_ref TEXT,
    actor      TEXT NOT NULL,
    reason     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fills (
    fill_id    INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders,
    at         TEXT NOT NULL,
    qty        INTEGER NOT NULL,
    price      REAL NOT NULL,                 -- quote currency
    commission REAL NOT NULL,                 -- account currency
    fees       REAL NOT NULL                  -- account currency (stamp duty etc.)
);
CREATE TABLE IF NOT EXISTS cash (
    at          TEXT NOT NULL,
    kind        TEXT NOT NULL,                -- deposit / withdrawal / dividend / fee / interest
    amount      REAL NOT NULL,                -- positive, the kind gives the direction
    currency    TEXT NOT NULL,
    position_id INTEGER,
    note        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS executions (
    execution_id INTEGER PRIMARY KEY,
    at           TEXT NOT NULL,
    order_id     INTEGER,                     -- NULL: a note on the turn, not one order
    action       TEXT NOT NULL,               -- sent / refused / rejected / unknown / cancel_sent /
                                              -- hold / pass / note / error (ADR 0031)
    detail       TEXT NOT NULL,               -- JSON
    actor        TEXT NOT NULL,
    reason       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entry_conditions (
    position_id INTEGER NOT NULL REFERENCES positions,
    run_id      INTEGER NOT NULL,
    model       TEXT NOT NULL,
    field       TEXT NOT NULL,
    value       REAL,
    text        TEXT
);
CREATE TABLE IF NOT EXISTS thesis_conditions (
    condition_id INTEGER PRIMARY KEY,
    position_id  INTEGER NOT NULL REFERENCES positions,
    condition    TEXT NOT NULL,
    kind         TEXT NOT NULL,               -- machine (field op value) / agent (text)
    evidence_id  INTEGER,
    created_at   TEXT NOT NULL
);
"""

OWNER = "owner"
PIPELINE = "pipeline"
ORDER_TYPES = ("limit", "stop", "market")
PURPOSES = ("entry", "stop", "target", "add", "exit")
BUY_PURPOSES = frozenset({"entry", "add"})
CASH_KINDS = ("deposit", "withdrawal", "dividend", "fee", "interest")
CASH_IN = frozenset({"deposit", "dividend", "interest"})
CONDITION_KINDS = ("machine", "agent")

# Position status = the latest event whose kind is in this map.
POSITION_STATUS = {
    "drafted": "draft",
    "approved": "approved",
    "rejected": "rejected",
    "opened": "open",
    "closed": "closed",
    "cancelled": "cancelled",
}
# kind -> statuses it may follow (None: no event yet)
POSITION_TRANSITIONS: dict[str, frozenset[str | None]] = {
    "drafted": frozenset({None}),
    "approved": frozenset({"draft"}),
    "amended": frozenset({"draft", "approved"}),
    "rejected": frozenset({"draft", "approved"}),
    "cancelled": frozenset({"draft", "approved"}),
    "opened": frozenset({"approved"}),
    "revised": frozenset({"open"}),
    "stop_raised": frozenset({"open"}),
    # revisions: open positions, and pending entries (draft / approved) since ADR 0024
    "revision_proposed": frozenset({"open", "draft", "approved"}),
    "revision_accepted": frozenset({"open", "draft", "approved"}),
    "revision_declined": frozenset({"open", "draft", "approved"}),
    "revision_undone": frozenset({"open", "draft", "approved"}),
    "split": frozenset({"open"}),
    "closed": frozenset({"open"}),
}
ORDER_TRANSITIONS: dict[str, frozenset[str | None]] = {
    "draft": frozenset({None}),
    # a protective stop is born approved; a send the broker never saw goes back to approved
    "approved": frozenset({None, "draft", "sending"}),
    "sending": frozenset({"approved", "rejected"}),  # written before the call (ADR 0031)
    "placed": frozenset({"approved", "sending"}),
    "rejected": frozenset({"sending"}),  # the broker refused it; sent again only on purpose
    "part_filled": frozenset({"approved", "sending", "placed", "part_filled"}),
    "filled": frozenset({"approved", "sending", "placed", "part_filled", "cancelling"}),
    "not_filled": frozenset({"approved", "sending", "placed", "part_filled", "cancelling"}),
    # an order at the broker is cancelling until the broker confirms it gone (or filled)
    "cancelling": frozenset({"sending", "placed", "part_filled"}),
    "cancelled": frozenset(
        {"draft", "approved", "sending", "placed", "rejected", "part_filled", "cancelling"}
    ),
    "expired": frozenset({"approved", "placed"}),
}
OPEN_ORDER = frozenset({"draft", "approved", "sending", "placed", "rejected", "part_filled"})
LIVE_ORDER = frozenset({"approved", "sending", "placed", "part_filled"})  # at / going to the broker
AT_BROKER = frozenset({"sending", "placed", "part_filled", "cancelling"})  # may be working there
FILLABLE = frozenset({"approved", "sending", "placed", "part_filled", "cancelling"})
PART_FILL = "part fill: the rest cancelled so the shares held get their stop"
SWAP = "taken off for exit order #{} (Trading212 holds the shares under a stop)"


def connect(path: Path | None = None) -> Conn:
    """The results DB (see :func:`solaraalpha.store.connect`) with the book tables present."""
    conn = store.connect(path)
    conn.executescript(BOOK_SCHEMA)
    return conn


def to_account(amount: float, currency: str | None) -> float:
    """``amount`` in ``currency`` -> account currency (GBP). Pence (``GBp``/``GBX``) / 100."""
    if currency in ("GBp", "GBX"):
        return amount / 100
    if currency == "GBP":
        return amount
    raise NotImplementedError(f"FX not supported yet (currency {currency!r})")


def quote_currency(ticker: str) -> str:
    """Default quote currency of a ticker's prices: pence for LSE listings."""
    if ticker.endswith(".L"):
        return "GBp"
    raise NotImplementedError(f"quote currency unknown for {ticker!r}; pass currency=")


def now() -> str:
    return datetime.now(UTC).isoformat()


def iso(at: str | pd.Timestamp | datetime | None) -> str:
    """ISO-8601 UTC text for ``at`` (default now); naive input is taken as UTC."""
    if at is None:
        return now()
    t = pd.Timestamp(at)
    t = t.tz_localize(UTC) if t.tz is None else t.tz_convert(UTC)
    return t.isoformat()


# ---------------------------------------------------------------------------- writes


def new_position(
    conn: Conn,
    ticker: str,
    side: int = 1,
    horizon_weeks: int | None = None,
    thesis: str = "",
    opened_from_run_id: int | None = None,
    currency: str | None = None,
    at: str | None = None,
    rules: Rules | None = None,
) -> int:
    """Insert a position row (no event yet) and return its id. Shorts are rejected while
    ``Rules.long_only``."""
    rules = rules or load_rules()
    if side not in (1, -1):
        raise ValueError("side must be 1 (long) or -1 (short)")
    if side == -1 and rules.long_only:
        raise ValueError("short positions are not allowed (long_only = true)")
    cur = conn.execute(
        "INSERT INTO positions (ticker, side, currency, opened_from_run_id, horizon_weeks, thesis,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            ticker,
            side,
            currency or quote_currency(ticker),
            opened_from_run_id,
            horizon_weeks if horizon_weeks is not None else rules.horizon_weeks[1],
            thesis,
            iso(at),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def position_status(conn: Conn, position_id: int) -> str | None:
    """Current status (draft / approved / open / closed / rejected / cancelled), None if the
    position has no status-changing event yet."""
    _position(conn, position_id)
    kinds = ", ".join("?" * len(POSITION_STATUS))
    row = conn.execute(
        f"SELECT kind FROM position_events WHERE position_id = ? AND kind IN ({kinds})"
        " ORDER BY event_id DESC LIMIT 1",
        (position_id, *POSITION_STATUS),
    ).fetchone()
    return POSITION_STATUS[row[0]] if row else None


def position_event(
    conn: Conn,
    position_id: int,
    kind: str,
    payload: dict[str, Any] | None = None,
    actor: str = OWNER,
    reason: str = "",
    at: str | None = None,
) -> int:
    """Append a position event after checking the transition; returns its ``event_id``."""
    if kind not in POSITION_TRANSITIONS:
        raise ValueError(f"unknown position event kind {kind!r}")
    status = position_status(conn, position_id)
    if status not in POSITION_TRANSITIONS[kind]:
        raise ValueError(f"position {position_id} is {status}; cannot record {kind!r}")
    cur = conn.execute(
        "INSERT INTO position_events (position_id, at, kind, payload, actor, reason)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (position_id, iso(at), kind, json.dumps(payload or {}, default=str), actor, reason),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def add_order(
    conn: Conn,
    position_id: int,
    side: int,
    type: str,
    qty: int,
    price: float | None,
    purpose: str,
    valid_until: str | None = None,
    status: str = "draft",
    actor: str = OWNER,
    reason: str = "",
    at: str | None = None,
) -> int:
    """Insert an order and its first event (``status`` draft, or approved for a protective stop)."""
    if type not in ORDER_TYPES:
        raise ValueError(f"order type must be one of {ORDER_TYPES}, got {type!r}")
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}, got {purpose!r}")
    if side not in (1, -1):
        raise ValueError("order side must be 1 (buy) or -1 (sell)")
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    if (price is None) != (type == "market"):
        raise ValueError("market orders have no price; limit and stop orders need one")
    if None not in ORDER_TRANSITIONS.get(status, frozenset()):
        raise ValueError(f"an order cannot start as {status!r}")
    _position(conn, position_id)
    t = iso(at)
    cur = conn.execute(
        "INSERT INTO orders (position_id, side, type, qty, price, valid_until, purpose, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (position_id, side, type, int(qty), price, valid_until, purpose, t),
    )
    assert cur.lastrowid is not None
    order_event(conn, cur.lastrowid, status, actor=actor, reason=reason, at=t)
    return cur.lastrowid


def order_status(conn: Conn, order_id: int) -> str | None:
    _order(conn, order_id)
    row = conn.execute(
        "SELECT status FROM order_events WHERE order_id = ? ORDER BY event_id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    return str(row[0]) if row else None


def order_event(
    conn: Conn,
    order_id: int,
    status: str,
    broker_ref: str | None = None,
    actor: str = OWNER,
    reason: str = "",
    at: str | None = None,
) -> int:
    """Append an order status event after checking the transition; returns its ``event_id``."""
    if status not in ORDER_TRANSITIONS:
        raise ValueError(f"unknown order status {status!r}")
    current = order_status(conn, order_id)
    if current not in ORDER_TRANSITIONS[status]:
        raise ValueError(f"order {order_id} is {current}; cannot record {status!r}")
    cur = conn.execute(
        "INSERT INTO order_events (order_id, at, status, broker_ref, actor, reason)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (order_id, iso(at), status, broker_ref, actor, reason),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def filled_qty(conn: Conn, order_id: int) -> int:
    row = conn.execute("SELECT COALESCE(SUM(qty), 0) FROM fills WHERE order_id = ?", (order_id,))
    return int(row.fetchone()[0])


def add_fill(
    conn: Conn,
    order_id: int,
    qty: int,
    price: float,
    at: str | None = None,
    commission: float = 0.0,
    fees: float = 0.0,
    actor: str = OWNER,
) -> int:
    """Insert a fill and the order event it implies (``part_filled`` or ``filled``).

    The order must be fillable (approved, placed or part-filled) and the cumulative quantity
    may not exceed the order's. Position-level effects (opening, stops, closing) are the
    caller's job (see ``fill`` in the booking CLI layer).
    """
    if qty <= 0:
        raise ValueError(f"fill qty must be positive, got {qty}")
    status = order_status(conn, order_id)
    if status not in FILLABLE:
        raise ValueError(f"order {order_id} is {status}; cannot fill")
    total = _order(conn, order_id)["qty"]
    done = filled_qty(conn, order_id) + int(qty)
    if done > total:
        raise ValueError(f"over-fill: {done} of {total} on order {order_id}")
    t = iso(at)
    cur = conn.execute(
        "INSERT INTO fills (order_id, at, qty, price, commission, fees) VALUES (?, ?, ?, ?, ?, ?)",
        (order_id, t, int(qty), float(price), float(commission), float(fees)),
    )
    assert cur.lastrowid is not None
    if done == total:
        order_event(conn, order_id, "filled", actor=actor, at=t)
    elif status != "cancelling":  # a part fill racing a cancel: still cancelling at the broker
        order_event(conn, order_id, "part_filled", actor=actor, at=t)
    return cur.lastrowid


def cash_event(
    conn: Conn,
    kind: str,
    amount: float,
    currency: str = "GBP",
    position_id: int | None = None,
    note: str = "",
    at: str | None = None,
) -> None:
    """Record cash in or out of the account (``amount`` positive; ``kind`` gives the sign)."""
    if kind not in CASH_KINDS:
        raise ValueError(f"cash kind must be one of {CASH_KINDS}, got {kind!r}")
    if amount <= 0:
        raise ValueError(f"amount must be positive, got {amount}")
    conn.execute(
        "INSERT INTO cash (at, kind, amount, currency, position_id, note) VALUES (?, ?, ?, ?, ?, ?)",
        (iso(at), kind, float(amount), currency, position_id, note),
    )


def add_entry_conditions(conn: Conn, position_id: int, run_id: int, cards: dict[str, Any]) -> int:
    """Freeze every field of ``cards`` (``{model: dataclass or dict}``) onto the position, as
    the results table stores them (numbers / bools in ``value``, strings and dates in ``text``).
    Returns the number of rows written."""
    _position(conn, position_id)
    rows = []
    for model, card in cards.items():
        fields = asdict(card) if is_dataclass(card) and not isinstance(card, type) else dict(card)
        for k, v in fields.items():
            value, text = store._cell(v)
            rows.append((position_id, run_id, model, k, value, text))
    conn.executemany(
        "INSERT INTO entry_conditions (position_id, run_id, model, field, value, text)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def add_thesis_condition(
    conn: Conn,
    position_id: int,
    condition: str,
    kind: str,
    evidence_id: int | None = None,
    at: str | None = None,
) -> int:
    if kind not in CONDITION_KINDS:
        raise ValueError(f"condition kind must be one of {CONDITION_KINDS}, got {kind!r}")
    _position(conn, position_id)
    cur = conn.execute(
        "INSERT INTO thesis_conditions (position_id, condition, kind, evidence_id, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (position_id, condition, kind, evidence_id, iso(at)),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


# ---------------------------------------------------------------------------- readers


def _position(conn: Conn, position_id: int) -> dict[str, Any]:
    df = query("SELECT * FROM positions WHERE position_id = ?", conn, [position_id])
    if df.empty:
        raise ValueError(f"no position {position_id}")
    return {str(k): v for k, v in df.iloc[0].items()}


def _order(conn: Conn, order_id: int) -> dict[str, Any]:
    df = query("SELECT * FROM orders WHERE order_id = ?", conn, [order_id])
    if df.empty:
        raise ValueError(f"no order {order_id}")
    return {str(k): v for k, v in df.iloc[0].items()}


def positions(conn: Conn, status: str | None = None) -> pd.DataFrame:
    """Positions with their current ``status`` and ``updated_at`` (time of that event)."""
    pos = query("SELECT * FROM positions ORDER BY position_id", conn)
    kinds = ", ".join(f"'{k}'" for k in POSITION_STATUS)
    ev = query(
        f"SELECT position_id, at, kind FROM position_events WHERE kind IN ({kinds})"
        " ORDER BY event_id",
        conn,
    )
    last = ev.drop_duplicates("position_id", keep="last").set_index("position_id")
    pos["status"] = pos["position_id"].map(last["kind"]).map(POSITION_STATUS)
    pos["updated_at"] = pos["position_id"].map(last["at"])
    if status is not None:
        pos = pos[pos["status"] == status]
    return pos.reset_index(drop=True)


def position_events(conn: Conn, position_id: int) -> pd.DataFrame:
    df = query(
        "SELECT * FROM position_events WHERE position_id = ? ORDER BY event_id", conn, [position_id]
    )
    df["payload"] = df["payload"].map(json.loads)
    return df


def orders(conn: Conn, position_id: int | None = None, status: str | None = None) -> pd.DataFrame:
    """Orders with current ``status``, ``updated_at``, ``broker_ref`` and ``filled`` quantity."""
    sql, params = "SELECT * FROM orders", []
    if position_id is not None:
        sql += " WHERE position_id = ?"
        params.append(position_id)
    od = query(sql + " ORDER BY order_id", conn, params)
    ev = query("SELECT order_id, at, status, broker_ref FROM order_events ORDER BY event_id", conn)
    last = ev.drop_duplicates("order_id", keep="last").set_index("order_id")
    refs = ev.dropna(subset=["broker_ref"]).drop_duplicates("order_id", keep="last")
    fl = query("SELECT order_id, SUM(qty) AS filled FROM fills GROUP BY order_id", conn)
    od["status"] = od["order_id"].map(last["status"])
    od["updated_at"] = od["order_id"].map(last["at"])
    od["broker_ref"] = od["order_id"].map(refs.set_index("order_id")["broker_ref"])
    od["filled"] = od["order_id"].map(fl.set_index("order_id")["filled"]).fillna(0).astype(int)
    if status is not None:
        od = od[od["status"] == status]
    return od.reset_index(drop=True)


def order_events(conn: Conn, order_id: int) -> pd.DataFrame:
    return query(
        "SELECT * FROM order_events WHERE order_id = ? ORDER BY event_id", conn, [order_id]
    )


def fills(conn: Conn, order_id: int | None = None, position_id: int | None = None) -> pd.DataFrame:
    """Fills with their order's ``position_id``, ``side`` and ``purpose``, oldest first."""
    sql = "SELECT f.*, o.position_id, o.side, o.purpose FROM fills f JOIN orders o USING (order_id)"
    where, params = [], []
    if order_id is not None:
        where.append("f.order_id = ?")
        params.append(order_id)
    if position_id is not None:
        where.append("o.position_id = ?")
        params.append(position_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return query(sql + " ORDER BY f.at, f.fill_id", conn, params)


def fills_with_tickers(conn: Conn) -> pd.DataFrame:
    """Every fill with its position's ``ticker`` and ``currency`` (for the equity curve)."""
    return query(
        "SELECT f.*, o.position_id, o.side, o.purpose, p.ticker, p.currency FROM fills f"
        " JOIN orders o USING (order_id) JOIN positions p USING (position_id)"
        " ORDER BY f.at, f.fill_id",
        conn,
    )


def cash(conn: Conn) -> pd.DataFrame:
    return query("SELECT * FROM cash ORDER BY at, rowid", conn)


def cash_balance(conn: Conn) -> float:
    """Signed sum of cash events in the account currency (deposits, dividends and interest in;
    withdrawals and fees out). Excludes the cost of positions: see ``portfolio.risk.equity``."""
    df = cash(conn)
    if df.empty:
        return 0.0
    signed = [
        to_account(a, c) * (1 if k in CASH_IN else -1)
        for a, c, k in zip(df["amount"], df["currency"], df["kind"], strict=True)
    ]
    return float(sum(signed))


def entry_conditions(conn: Conn, position_id: int) -> pd.DataFrame:
    return query(
        "SELECT * FROM entry_conditions WHERE position_id = ? ORDER BY rowid", conn, [position_id]
    )


def thesis_conditions(conn: Conn, position_id: int) -> pd.DataFrame:
    return query(
        "SELECT * FROM thesis_conditions WHERE position_id = ? ORDER BY condition_id",
        conn,
        [position_id],
    )


# ---------------------------------------------------------------------------- state


@dataclass(frozen=True)
class PositionState:
    """Derived from the record: what is held, at what cost and risk."""

    position_id: int
    ticker: str
    side: int
    currency: str  # of prices
    status: str | None
    shares: int  # held now
    avg_cost: float  # running average cost of the shares held, quote currency
    realised_pnl: float  # account currency, net of commission and fees
    costs: float  # commission + fees so far, account currency
    stop: float | None  # the live stop order's price; while an exit sells every share (the
    # stop swapped for it, ADR 0031) the stop level it goes back on at
    initial_stop: float | None  # planned stop at entry (drafted payload), else the first stop order
    risk_at_stop: float  # (avg_cost - stop) x shares, account currency, floored at 0
    r_unit: float  # initial risk per share x initial shares, account currency (0 if unknown)
    entry_shares: int  # shares bought on entry orders
    entry_avg: float  # average entry price, quote currency
    opened_at: str | None


def position_state(conn: Conn, position_id: int) -> PositionState:
    pos = _position(conn, position_id)
    side, ccy = int(pos["side"]), str(pos["currency"])
    fl = fills(conn, position_id=position_id)
    splits = _splits(conn, position_id)

    # fills and splits in time order: a split scales the shares held (cost conserved)
    steps: list[tuple[str, int, Any]] = [(at, 0, k) for at, k in splits]
    steps += [(str(f["at"]), 1, f) for f in fl.to_dict("records")]
    steps.sort(key=lambda x: (x[0], x[1]))
    shares, avg, realised, costs = 0, 0.0, 0.0, 0.0
    entry_qty, entry_cost = 0, 0.0
    for _, is_fill, item in steps:
        if not is_fill:
            k = float(item)
            new_shares = int(shares * k)  # fractional entitlements are paid in cash, not booked
            avg = avg * shares / new_shares if new_shares else avg
            shares = new_shares
            entry_qty = int(entry_qty * k)
            continue
        f = item
        q, px = int(f["qty"]), float(f["price"])
        costs += float(f["commission"]) + float(f["fees"])
        if int(f["side"]) == side:  # adding to the position
            avg = (avg * shares + px * q) / (shares + q) if shares + q else px
            shares += q
            if f["purpose"] == "entry":
                entry_cost += px * q
                entry_qty += q
        else:  # reducing it
            realised += to_account(side * (px - avg) * q, ccy)
            shares -= q
    realised -= costs
    entry_avg = entry_cost / entry_qty if entry_qty else float("nan")
    total_factor = 1.0
    for _, k in splits:
        total_factor *= k

    od = orders(conn, position_id)
    ev = position_events(conn, position_id)
    status = position_status(conn, position_id)
    stops = od[(od["purpose"] == "stop") & od["status"].isin(LIVE_ORDER)]
    stop = float(stops["price"].iloc[-1]) if not stops.empty else None
    if stop is None and status == "open" and shares > 0:
        stop = _stop_level(od, ev)
    initial = _planned_stop(conn, position_id)
    if initial is None:
        first = od[od["purpose"] == "stop"]
        initial = float(first["price"].iloc[0]) if not first.empty else None
    if initial is not None and total_factor != 1.0:
        initial = (
            initial / total_factor
        )  # planned before every split (splits need an open position)

    risk = to_account(max(0.0, side * (avg - stop) * shares), ccy) if stop and shares else 0.0
    r_unit = (
        to_account(abs(entry_avg - initial) * entry_qty, ccy)
        if entry_qty and initial is not None
        else 0.0
    )
    opened = ev.loc[ev["kind"] == "opened", "at"]
    return PositionState(
        position_id=position_id,
        ticker=str(pos["ticker"]),
        side=side,
        currency=ccy,
        status=status,
        shares=shares,
        avg_cost=avg if shares else entry_avg,
        realised_pnl=realised,
        costs=costs,
        stop=stop,
        initial_stop=initial,
        risk_at_stop=risk,
        r_unit=r_unit,
        entry_shares=entry_qty,
        entry_avg=entry_avg,
        opened_at=str(opened.iloc[0]) if not opened.empty else None,
    )


def _stop_level(od: pd.DataFrame, ev: pd.DataFrame) -> float | None:
    """The stop level of an open position with no live stop order (swapped for an exit at the
    broker, ADR 0031): the latest of the stop orders' prices and the ``stop_raised`` levels, a
    raise winning a tie."""
    found: list[tuple[str, int, float]] = []
    for at, px in zip(
        od.loc[od["purpose"] == "stop", "created_at"],
        od.loc[od["purpose"] == "stop", "price"],
        strict=True,
    ):
        found.append((str(at), 0, float(px)))
    for at, p in zip(
        ev.loc[ev["kind"] == "stop_raised", "at"],
        ev.loc[ev["kind"] == "stop_raised", "payload"],
        strict=True,
    ):
        if p.get("new") is not None:
            found.append((str(at), 1, float(p["new"])))
    return max(found)[2] if found else None


def _splits(conn: Conn, position_id: int) -> list[tuple[str, float]]:
    """``(at, factor)`` of every ``split`` event (factor = new shares per old share)."""
    ev = position_events(conn, position_id)
    sp = ev[ev["kind"] == "split"]
    return [(str(a), float(p["factor"])) for a, p in zip(sp["at"], sp["payload"], strict=True)]


def _planned_stop(conn: Conn, position_id: int) -> float | None:
    """The stop in the latest ``drafted`` / ``amended`` payload, if any."""
    ev = position_events(conn, position_id)
    for p in reversed(list(ev.loc[ev["kind"].isin(["drafted", "amended"]), "payload"])):
        if p.get("stop") is not None:
            return float(p["stop"])
    return None


# ---------------------------------------------------------------------------- operations
# Multi-step bookings the CLI (scripts/book.py) and the planner call; each is several INSERTs
# that the caller wraps in one ``with conn:`` transaction.


def draft_position(
    conn: Conn,
    ticker: str,
    entry: float | None,
    stop: float,
    qty: int,
    type: str = "limit",
    target: float | None = None,
    valid_days: int | None = None,
    horizon_weeks: int | None = None,
    thesis: str = "",
    opened_from_run_id: int | None = None,
    payload: dict[str, Any] | None = None,
    actor: str = OWNER,
    at: str | None = None,
    rules: Rules | None = None,
    valid_until: str | None = None,
) -> tuple[int, int]:
    """Create a draft position with its entry order; returns ``(position_id, order_id)``.

    The planned ``stop`` (and ``target``) live in the ``drafted`` payload, merged over
    ``payload`` (e.g. a full trade plan); the protective stop order is drafted when the first
    fill arrives. ``valid_until`` (a date) defaults to ``at`` + ``valid_days``, which defaults
    to ``Rules.entry_validity_days``.
    """
    rules = rules or load_rules()
    t = iso(at)
    days = valid_days if valid_days is not None else rules.entry_validity_days
    if valid_until is None:
        valid_until = (pd.Timestamp(t) + pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    pid = new_position(
        conn,
        ticker,
        1,
        horizon_weeks,
        thesis,
        opened_from_run_id,
        at=t,
        rules=rules,
    )
    plan = {
        **(payload or {}),
        "entry": entry,
        "entry_type": type,
        "stop": stop,
        "target": target,
        "qty": qty,
        "valid_until": valid_until,
    }
    position_event(conn, pid, "drafted", plan, actor=actor, at=t)
    oid = add_order(conn, pid, 1, type, qty, entry, "entry", valid_until, "draft", actor, at=t)
    return pid, oid


def approve_position(
    conn: Conn, position_id: int, reason: str = "", actor: str = OWNER, at: str | None = None
) -> None:
    """Position ``approved``; its draft orders ``approved`` with it. The planner approves its
    plans as it drafts them (ADR 0026): the owner is told what to place, never asked."""
    t = iso(at)
    position_event(conn, position_id, "approved", actor=actor, reason=reason, at=t)
    for oid in orders(conn, position_id, status="draft")["order_id"]:
        order_event(conn, int(oid), "approved", actor=actor, reason=reason, at=t)


def reject_position(conn: Conn, position_id: int, reason: str, actor: str = OWNER) -> None:
    """Position ``rejected``; every open order ``cancelled``."""
    if not reason:
        raise ValueError("a rejection needs a reason")
    t = now()
    position_event(conn, position_id, "rejected", actor=actor, reason=reason, at=t)
    cancel_open_orders(conn, position_id, reason=f"rejected: {reason}", actor=actor, at=t)


def cancel_order(
    conn: Conn, order_id: int, reason: str, actor: str = OWNER, at: str | None = None
) -> str:
    """Cancel an open order: ``cancelled`` when it never reached the broker, ``cancelling`` when
    it may be working there (sending / placed / part filled), until the broker confirms it gone
    (ADR 0031: cancel, confirm, then place). Returns the status recorded."""
    status = "cancelling" if order_status(conn, order_id) in AT_BROKER else "cancelled"
    order_event(conn, order_id, status, actor=actor, reason=reason, at=at)
    return status


def cancel_open_orders(
    conn: Conn, position_id: int, reason: str, actor: str = OWNER, at: str | None = None
) -> list[int]:
    od = orders(conn, position_id)
    ids = [int(o) for o in od.loc[od["status"].isin(OPEN_ORDER), "order_id"]]
    for oid in ids:
        cancel_order(conn, oid, reason, actor, at)
    return ids


def replace_order(
    conn: Conn,
    order_id: int,
    qty: int | None = None,
    price: float | None = None,
    reason: str = "",
    actor: str = OWNER,
    at: str | None = None,
    status: str | None = None,
    type: str | None = None,
) -> int:
    """Cancel ``order_id`` and add a copy with the new ``qty`` / ``price`` / ``type``; returns
    the new id.

    The copy starts ``draft`` if the old order was a draft, else ``approved`` (a placed order's
    replacement has to be placed again, once the old one is confirmed gone). Rows are never
    mutated.
    """
    old = _order(conn, order_id)
    old_status = order_status(conn, order_id)
    if old_status not in OPEN_ORDER:
        raise ValueError(f"order {order_id} is {old_status}; nothing to replace")
    t = iso(at)
    kind = type or str(old["type"])
    cancel_order(conn, order_id, reason or "replaced", actor, t)
    return add_order(
        conn,
        int(old["position_id"]),
        int(old["side"]),
        kind,
        int(qty if qty is not None else old["qty"]),
        None if kind == "market" else (float(price) if price is not None else float(old["price"])),
        str(old["purpose"]),
        old["valid_until"] if pd.notna(old["valid_until"]) else None,
        status or ("draft" if old_status == "draft" else "approved"),
        actor,
        reason,
        at=t,
    )


def amend_position(
    conn: Conn,
    position_id: int,
    qty: int | None = None,
    price: float | None = None,
    stop: float | None = None,
    reason: str = "",
    actor: str = OWNER,
) -> dict[str, Any]:
    """Change the entry order's size / price and/or the planned stop on a draft or approved
    position. Records an ``amended`` event (old → new) and replaces the entry order (cancel +
    new row). Returns the event payload."""
    if qty is None and price is None and stop is None:
        raise ValueError("nothing to amend: pass qty, price or stop")
    status = position_status(conn, position_id)
    if status not in POSITION_TRANSITIONS["amended"]:
        raise ValueError(f"position {position_id} is {status}; cannot record 'amended'")
    t = now()
    od = orders(conn, position_id)
    entry = od[(od["purpose"] == "entry") & od["status"].isin(OPEN_ORDER)]
    planned = _planned_stop(conn, position_id)
    old: dict[str, Any] = {"stop": planned}
    new: dict[str, Any] = {"stop": stop if stop is not None else planned}
    if entry.empty:
        if qty is not None or price is not None:
            raise ValueError(f"position {position_id} has no open entry order to amend")
    else:
        row = entry.iloc[-1]
        old |= {"order_id": int(row["order_id"]), "qty": int(row["qty"]), "price": row["price"]}
        new |= {
            "qty": int(qty) if qty is not None else int(row["qty"]),
            "price": float(price) if price is not None else row["price"],
        }
        if qty is not None or price is not None:
            new["order_id"] = replace_order(
                conn, int(row["order_id"]), qty, price, reason or "amended", actor, t
            )
    payload = {"old": old, "new": new, "stop": new["stop"]}
    position_event(conn, position_id, "amended", payload, actor=actor, reason=reason, at=t)
    return payload


def place_order(
    conn: Conn,
    order_id: int,
    broker_ref: str | None = None,
    at: str | None = None,
    actor: str = OWNER,
) -> None:
    order_event(conn, order_id, "placed", broker_ref=broker_ref, actor=actor, at=at)


def tickets(conn: Conn) -> pd.DataFrame:
    """Approved, not yet placed orders as a broker-ready table (oldest first)."""
    od = orders(conn, status="approved")
    pos = query("SELECT position_id, ticker, currency FROM positions", conn)
    out = od.merge(pos, on="position_id", how="left")
    cols = ["order_id", "position_id", "ticker", "side", "type", "qty", "price", "currency"]
    return out[[*cols, "valid_until", "purpose"]].reset_index(drop=True)


def fmt_price(price: float | None, currency: str | None) -> str:
    """``1520.0, 'GBp'`` -> ``'1,520p'``; ``None`` -> ``'market'``."""
    if price is None or (isinstance(price, float) and math.isnan(price)):
        return "market"
    if currency in ("GBp", "GBX"):
        return f"{price:,.0f}p" if float(price).is_integer() else f"{price:,.2f}p"
    return f"{price:,.2f} {currency or ''}".strip()


# ---------------------------------------------------------------------------- fills


def fill(
    conn: Conn,
    order_id: int,
    qty: int,
    price: float,
    at: str | None = None,
    commission: float = 0.0,
    fees: float = 0.0,
    actor: str = OWNER,
) -> dict[str, Any]:
    """Record a fill and its consequences for the position.

    A fill on a buying order (``entry`` / ``add``) opens the position on its first fill and
    (re)drafts the **protective stop**: an ``approved`` stop order at the planned stop for every
    share held, replacing the previous stop order so it always covers the whole holding. A part
    fill on a buying order cancels the rest of it: the shares held get their stop instead of
    waiting for the remainder. A fill on
    a selling order that brings the shares to zero **closes** the position (:func:`close_position`,
    reason from :func:`exit_reason_for`). Returns ``{"fill_id", "order_status", "opened",
    "stop_order_id", "shares", "closed"}`` (``closed`` is the :class:`CloseSummary` or None).
    """
    t = iso(at)
    fid = add_fill(conn, order_id, qty, price, t, commission, fees, actor)
    order = _order(conn, order_id)
    pid = int(order["position_id"])
    out: dict[str, Any] = {
        "fill_id": fid,
        "order_status": order_status(conn, order_id),
        "opened": False,
        "stop_order_id": None,
    }
    if order["purpose"] in BUY_PURPOSES:
        if position_status(conn, pid) == "approved":
            position_event(
                conn, pid, "opened", {"order_id": order_id, "price": price}, actor=actor, at=t
            )
            out["opened"] = True
        if out["order_status"] == "part_filled":  # protect what is held, not the remainder
            out["order_status"] = cancel_order(conn, order_id, PART_FILL, actor, t)
        out["stop_order_id"] = protect(conn, pid, actor=actor, at=t)
    out["shares"] = position_state(conn, pid).shares
    out["closed"] = None
    if order["purpose"] not in BUY_PURPOSES:
        if out["shares"] == 0:
            reason = exit_reason_for(conn, order_id)
            out["closed"] = close_position(conn, pid, reason, actor=actor, at=t)
        elif order["purpose"] != "stop":  # a partial exit: the stop covers what is left
            out["stop_order_id"] = protect(conn, pid, actor=actor, at=t)
    return out


def protect(conn: Conn, position_id: int, actor: str = OWNER, at: str | None = None) -> int | None:
    """Make the live protective stop order cover every share held that no exit order at the
    broker is selling, at the planned stop price (or the current stop's price once one exists).
    One order per unit of stock (ADR 0031): the sells working on a holding never add up to more
    than it holds, and a buy (entry, add) works beside the stop. Replaces the old stop order
    (cancel + new, status ``approved``); returns the live stop's order id, None when nothing
    is held, no stop is known or an exit at the broker sells every share."""
    st = position_state(conn, position_id)
    price = st.stop if st.stop is not None else st.initial_stop
    od = orders(conn, position_id)
    qty = st.shares - _remaining(od[od["purpose"].isin(["exit", "target"])], AT_BROKER)
    if qty <= 0 or price is None:
        return None
    live = od[(od["purpose"] == "stop") & od["status"].isin(LIVE_ORDER)]
    if not live.empty:
        if int(live["qty"].iloc[-1]) == qty and float(live["price"].iloc[-1]) == price:
            return int(live["order_id"].iloc[-1])
        return replace_order(
            conn, int(live["order_id"].iloc[-1]), qty, price, "stop re-sized", actor, at
        )
    return add_order(
        conn,
        position_id,
        -st.side,
        "stop",
        qty,
        price,
        "stop",
        None,
        "approved",
        actor,
        at=at,
    )


def _remaining(od: pd.DataFrame, statuses: frozenset[str]) -> int:
    """Shares still to trade on the orders of ``od`` whose status is in ``statuses``."""
    live = od[od["status"].isin(statuses)]
    return int((live["qty"] - live["filled"]).clip(lower=0).sum())


def working(od: pd.DataFrame) -> pd.DataFrame:
    """The live exit / add orders among a position's ``orders``: one at a time per position."""
    return od[od["purpose"].isin(["exit", "add"]) & od["status"].isin(LIVE_ORDER)]


def sell_room(conn: Conn, position_id: int) -> int:
    """Shares a new sell order may cover: those held less what the sells that may be working at
    the broker still sell (one order per unit of stock, ADR 0031; Trading212 refuses the rest
    with ``selling-equity-not-owned``)."""
    od = orders(conn, position_id)
    sells = od[od["side"] == -position_state(conn, position_id).side]
    return position_state(conn, position_id).shares - _remaining(sells, AT_BROKER)


def check_sell(conn: Conn, order_id: int) -> None:
    """Refuse (``ValueError``) sending a sell order that would sell more than the shares not
    already under a working sell (the per-unit rule, ADR 0031)."""
    o = _order(conn, order_id)
    pid = int(o["position_id"])
    if int(o["side"]) == position_state(conn, pid).side:
        return
    room = sell_room(conn, pid)
    if int(o["qty"]) > room:
        raise ValueError(
            f"order {order_id} sells {int(o['qty'])} but only {max(room, 0)} shares are not"
            " already under a working sell (one order per unit of stock)"
        )


def take_stop_off(
    conn: Conn, position_id: int, what: str, actor: str = OWNER, at: str | None = None
) -> int | None:
    """Cancel the live stop order (``what`` says why); its level stays the position's stop.
    Returns the cancelled order id, None if there was none."""
    od = orders(conn, position_id)
    live = od[(od["purpose"] == "stop") & od["status"].isin(LIVE_ORDER)]
    if live.empty:
        return None
    oid = int(live["order_id"].iloc[-1])
    cancel_order(conn, oid, what, actor, at)
    return oid


def swap_for_exit(
    conn: Conn, exit_order_id: int, actor: str = OWNER, at: str | None = None
) -> dict[str, Any]:
    """The book side of the stop-for-exit swap (ADR 0031): Trading212 holds the shares under the
    stop, so before an exit sells them the stop comes off (all of it for a full exit; for a
    partial, it is cut to the shares left). Called only while the market is open, so a holding
    never crosses a close without its stop. The old stop is ``cancelling`` until the broker
    confirms it gone; only then do the smaller stop and the exit go out. Returns
    ``{"cancelled": id | None, "stop_order_id": id | None}``."""
    o = _order(conn, exit_order_id)
    if o["purpose"] != "exit" or order_status(conn, exit_order_id) not in ("approved", "rejected"):
        raise ValueError(f"order {exit_order_id} is not an approved exit")
    pid = int(o["position_id"])
    st = position_state(conn, pid)
    od = orders(conn, pid)
    live = od[(od["purpose"] == "stop") & od["status"].isin(LIVE_ORDER)]
    if live.empty:
        return {"cancelled": None, "stop_order_id": None}
    t = iso(at)
    rest = st.shares - int(o["qty"]) - _remaining(od[od["purpose"] == "exit"], AT_BROKER)
    old = int(live["order_id"].iloc[-1])
    if rest > 0 and int(live["qty"].iloc[-1]) <= rest:
        return {"cancelled": None, "stop_order_id": None}  # already cut for this exit
    if rest > 0:
        new = replace_order(conn, old, rest, None, SWAP.format(exit_order_id), actor, t)
        return {"cancelled": old, "stop_order_id": new}
    cancel_order(conn, old, SWAP.format(exit_order_id), actor, t)
    return {"cancelled": old, "stop_order_id": None}


def end_order(
    conn: Conn,
    order_id: int,
    status: str,
    reason: str = "",
    actor: str = OWNER,
    at: str | None = None,
) -> bool:
    """Mark an order ``not_filled`` / ``cancelled`` / ``expired``. An entry order ending with no
    fill on an unopened position cancels the position too; an exit or add order ending on an
    open position puts its stop order back (approved, to place). Returns whether the position
    was cancelled."""
    if status not in ("not_filled", "cancelled", "expired"):
        raise ValueError(
            f"end_order: status must be not_filled / cancelled / expired, got {status!r}"
        )
    t = iso(at)
    order = _order(conn, order_id)
    order_event(conn, order_id, status, actor=actor, reason=reason, at=t)
    pid = int(order["position_id"])
    if order["purpose"] in ("exit", "add") and position_status(conn, pid) == "open":
        protect(conn, pid, actor=actor, at=t)  # an exit that ended: its shares get the stop back
        return False
    if order["purpose"] != "entry" or position_status(conn, pid) not in ("draft", "approved"):
        return False
    if filled_qty(conn, order_id) or not fills(conn, position_id=pid).empty:
        return False
    od = orders(conn, pid)
    if od.loc[(od["purpose"] == "entry") & od["status"].isin(OPEN_ORDER)].empty:
        position_event(conn, pid, "cancelled", actor=actor, reason=f"entry {status}", at=t)
        cancel_open_orders(conn, pid, reason=f"entry {status}", actor=actor, at=t)
        return True
    return False


def expire_orders(conn: Conn, as_of: str | None = None, actor: str = OWNER) -> list[int]:
    """Mark every approved or placed entry order whose ``valid_until`` is before ``as_of``
    (default today, UTC) as ``expired``; returns their ids. A placed one is cancelled at the
    broker by the execution job, which ends it (ADR 0031)."""
    today = pd.Timestamp(iso(as_of)).strftime("%Y-%m-%d")
    od = orders(conn)
    stale = od[
        (od["purpose"] == "entry")
        & od["status"].isin(["approved", "placed"])
        & od["valid_until"].notna()
        & (od["valid_until"] < today)
    ]
    ids = [int(o) for o in stale["order_id"]]
    for oid in ids:
        end_order(
            conn,
            oid,
            "expired",
            f"valid until {od.set_index('order_id').loc[oid, 'valid_until']}",
            actor,
            as_of,
        )
    return ids


def stale_orders(conn: Conn, as_of: str | None = None) -> pd.DataFrame:
    """Approved or placed orders past ``valid_until`` (before ``as_of``'s date, default today)
    with no terminal status: a forgotten fill or a dead order (#130). Columns: the order plus
    ``ticker`` and ``status``."""
    today = pd.Timestamp(iso(as_of)).strftime("%Y-%m-%d")
    od = orders(conn)
    if od.empty:
        return od.assign(ticker=pd.Series(dtype="object"))
    stale = od[
        od["status"].isin(["approved", "placed", "part_filled"])
        & od["valid_until"].notna()
        & (od["valid_until"] < today)
    ].copy()
    pos = positions(conn).set_index("position_id")["ticker"]
    stale["ticker"] = [str(pos.get(int(p), "")) for p in stale["position_id"]]
    return stale.reset_index(drop=True)


# ---------------------------------------------------------------------------- closing

EXIT_REASONS = (
    "stop",
    "trailing_stop",
    "target",
    "thesis_broken",
    "time",
    "discretionary",
    "corporate_action",
)


@dataclass(frozen=True)
class CloseSummary:
    """Outcome of a closed trade, computed from the record (money in the account currency)."""

    position_id: int
    ticker: str
    exit_reason: str
    gross_pnl: float  # trading P&L before costs
    costs: float
    dividends: float  # dividends received while held (cash rows tagged with the position)
    net_pnl: float  # gross - costs + dividends
    r_multiple: float  # net P&L / initial risk (NaN if the initial stop is unknown)
    holding_weeks: float  # first fill to last fill
    entry_avg: float  # quote currency
    exit_avg: float
    shares: int  # shares traded on entry
    opened_at: str | None
    closed_at: str | None
    mfe: float  # max favourable excursion from entry_avg, fraction (NaN without prices)
    mae: float  # max adverse excursion, fraction (<= 0)
    mfe_r: float  # the same in R
    mae_r: float


def exit_position(
    conn: Conn,
    position_id: int,
    reason: str,
    type: str = "market",
    price: float | None = None,
    qty: int | None = None,
    actor: str = OWNER,
    at: str | None = None,
) -> int:
    """Draft an ``approved`` exit order for ``qty`` shares (default all) tagged with ``reason``
    (one of :data:`EXIT_REASONS`, kept in the order's ``approved`` event). Returns the order id.
    The stop stays on until the exit is sent: the execution job swaps it for the exit only
    while the market is open (:func:`swap_for_exit`, ADR 0031); the exit ending unfilled puts
    it back on the shares left.
    """
    if reason not in EXIT_REASONS:
        raise ValueError(f"exit reason must be one of {EXIT_REASONS}, got {reason!r}")
    st = position_state(conn, position_id)
    if st.status != "open" or st.shares <= 0:
        raise ValueError(f"position {position_id} is {st.status} with {st.shares} shares; no exit")
    n = int(qty) if qty is not None else st.shares
    if n > st.shares:
        raise ValueError(f"exit qty {n} exceeds the {st.shares} shares held")
    if (working(orders(conn, position_id))["purpose"] == "exit").any():
        raise ValueError(f"position {position_id} already has an exit order working")
    return add_order(
        conn, position_id, -st.side, type, n, price, "exit", None, "approved", actor, reason, at=at
    )


def exit_reason_for(conn: Conn, order_id: int) -> str:
    """The exit reason a fill on ``order_id`` implies: the tag on an exit order's ``approved``
    event; ``target`` for a target order; ``stop``, or ``trailing_stop`` once the stop was raised."""
    order = _order(conn, order_id)
    purpose = str(order["purpose"])
    if purpose == "exit":
        ev = order_events(conn, order_id)
        tag = ev.loc[ev["status"] == "approved", "reason"]
        head = str(tag.iloc[-1]).partition(": ")[0] if not tag.empty else ""
        return head if head in EXIT_REASONS else "discretionary"
    if purpose == "target":
        return "target"
    if purpose == "stop":
        ev = position_events(conn, int(order["position_id"]))
        return "trailing_stop" if (ev["kind"] == "stop_raised").any() else "stop"
    raise ValueError(f"order {order_id} ({purpose}) does not exit a position")


def close_position(
    conn: Conn, position_id: int, reason: str, actor: str = OWNER, at: str | None = None
) -> CloseSummary:
    """Record the ``closed`` event (payload: the close summary, excursions NaN) and cancel every
    remaining open order. Only when no shares are held."""
    st = position_state(conn, position_id)
    if st.shares != 0:
        raise ValueError(f"position {position_id} still holds {st.shares} shares; cannot close")
    if reason not in EXIT_REASONS:
        raise ValueError(f"exit reason must be one of {EXIT_REASONS}, got {reason!r}")
    t = iso(at)
    cancel_open_orders(conn, position_id, reason="position closed", actor=actor, at=t)
    summary = close_summary(conn, position_id, reason=reason, closed_at=t)
    position_event(conn, position_id, "closed", asdict(summary), actor=actor, reason=reason, at=t)
    return summary


def close_summary(
    conn: Conn,
    position_id: int,
    daily: pd.DataFrame | None = None,
    reason: str | None = None,
    closed_at: str | None = None,
) -> CloseSummary:
    """Outcome numbers for a closed (or closing) position. With ``daily`` prices (checked and
    cleaned by the caller), MFE / MAE use the highs and lows between the first and last fill."""
    st = position_state(conn, position_id)
    fl = fills(conn, position_id=position_id)
    if fl.empty:
        raise ValueError(f"position {position_id} has no fills")
    sells = fl[fl["side"] != st.side]
    exit_avg = (
        float((sells["qty"] * sells["price"]).sum() / sells["qty"].sum())
        if len(sells)
        else float("nan")
    )
    first, last = pd.Timestamp(fl["at"].iloc[0]), pd.Timestamp(fl["at"].iloc[-1])
    if reason is None:
        ev = position_events(conn, position_id)
        closed = ev[ev["kind"] == "closed"]
        reason = str(closed["reason"].iloc[-1]) if not closed.empty else "unknown"
        closed_at = str(closed["at"].iloc[-1]) if not closed.empty else None
    gross = st.realised_pnl + st.costs
    divs = dividends_for(conn, position_id)
    net = st.realised_pnl + divs
    r_mult = net / st.r_unit if st.r_unit else float("nan")
    mfe = mae = mfe_r = mae_r = float("nan")
    if daily is not None and not daily.empty and st.entry_shares:
        span = daily[(daily.index >= first.normalize()) & (daily.index <= last.normalize())]
        if not span.empty:
            hi, lo = float(span["high"].max()), float(span["low"].min())
            best, worst = (hi, lo) if st.side == 1 else (lo, hi)
            mfe = max(0.0, st.side * (best / st.entry_avg - 1))
            mae = min(0.0, st.side * (worst / st.entry_avg - 1))
            if st.r_unit:
                per_r = to_account(st.entry_avg * st.entry_shares, st.currency) / st.r_unit
                mfe_r, mae_r = mfe * per_r, mae * per_r
    return CloseSummary(
        position_id=position_id,
        ticker=st.ticker,
        exit_reason=reason,
        gross_pnl=gross,
        costs=st.costs,
        dividends=divs,
        net_pnl=net,
        r_multiple=r_mult,
        holding_weeks=(last - first).total_seconds() / (7 * 86400),
        entry_avg=st.entry_avg,
        exit_avg=exit_avg,
        shares=st.entry_shares,
        opened_at=st.opened_at,
        closed_at=closed_at,
        mfe=mfe,
        mae=mae,
        mfe_r=mfe_r,
        mae_r=mae_r,
    )


# ---------------------------------------------------------------------------- approval statistics

STAGE_ORDER = ("research", "validate", "screen")


def approval_stats(
    positions_df: pd.DataFrame, events: pd.DataFrame, labels: pd.DataFrame
) -> pd.DataFrame:
    """Where the owner and the system disagree (#130): per deciding stage (the latest stage
    with a label for the position's ``opened_from_run_id`` and ticker; ``hand`` when booked
    without a run) and ``all``: positions ``drafted`` / ``approved`` / ``amended`` /
    ``rejected``, the ``rejection_reasons`` grouped by first word, and the mean amendment size
    (``avg_qty_change_pct``, ``avg_price_change_pct``, NaN when none).

    ``positions_df`` is :func:`positions`, ``events`` every position's events (``position_id,
    kind, payload`` as dicts, ``reason``), ``labels`` has ``run_id, ticker, stage``."""
    cols = [
        "stage",
        "drafted",
        "approved",
        "amended",
        "rejected",
        "avg_qty_change_pct",
        "avg_price_change_pct",
        "rejection_reasons",
    ]
    if positions_df.empty:
        return pd.DataFrame(columns=cols)
    by_run: dict[tuple[int, str], str] = {}
    for r in labels.to_dict("records") if not labels.empty else []:
        key: tuple[int, str] = (int(r["run_id"]), str(r["ticker"]))
        cur = by_run.get(key)
        if cur is None or STAGE_ORDER.index(str(r["stage"])) < STAGE_ORDER.index(cur):
            by_run[key] = str(r["stage"])
    stage_of: dict[int, str] = {}
    for p in positions_df.to_dict("records"):
        run = p.get("opened_from_run_id")
        stage_of[int(p["position_id"])] = (
            by_run.get((int(run), str(p["ticker"])), "unknown")
            if run is not None and not pd.isna(run)
            else "hand"
        )
    groups: dict[str, dict[str, Any]] = {}

    def bucket(stage: str) -> dict[str, Any]:
        return groups.setdefault(
            stage,
            {
                "drafted": set(),
                "approved": set(),
                "amended": set(),
                "rejected": set(),
                "qty": [],
                "price": [],
                "reasons": [],
            },
        )

    for e in events.to_dict("records") if not events.empty else []:
        pid = int(e["position_id"])
        kind = str(e["kind"])
        if kind not in ("drafted", "approved", "amended", "rejected"):
            continue
        payload = (
            e["payload"] if isinstance(e["payload"], dict) else json.loads(e["payload"] or "{}")
        )
        for stage in (stage_of.get(pid, "unknown"), "all"):
            g = bucket(stage)
            g[kind].add(pid)
            if kind == "rejected":
                words = str(e.get("reason") or "").strip().split()
                g["reasons"].append(words[0].lower().strip(",.;:") if words else "(none)")
            if kind == "amended":
                old, new = payload.get("old") or {}, payload.get("new") or {}
                for fld, acc in (("qty", "qty"), ("price", "price")):
                    a, b = old.get(fld), new.get(fld)
                    if a not in (None, 0) and b is not None and not pd.isna(a) and not pd.isna(b):
                        g[acc].append(abs(float(b) - float(a)) / abs(float(a)))
    rows = []
    order = [*STAGE_ORDER, "hand", "unknown", "all"]
    for stage in sorted(groups, key=lambda s: order.index(s) if s in order else 99):
        g = groups[stage]
        counts = (
            pd.Series(g["reasons"]).value_counts() if g["reasons"] else pd.Series(dtype="int64")
        )
        rows.append(
            {
                "stage": stage,
                "drafted": len(g["drafted"]),
                "approved": len(g["approved"]),
                "amended": len(g["amended"]),
                "rejected": len(g["rejected"]),
                "avg_qty_change_pct": float(sum(g["qty"]) / len(g["qty"]))
                if g["qty"]
                else float("nan"),
                "avg_price_change_pct": float(sum(g["price"]) / len(g["price"]))
                if g["price"]
                else float("nan"),
                "rejection_reasons": ", ".join(f"{k!s}:{int(v)}" for k, v in counts.items()),
            }
        )
    return pd.DataFrame(rows, columns=cols)


def all_position_events(conn: Conn, since: str | None = None) -> pd.DataFrame:
    """Every position event (payload parsed), optionally only those at or after ``since``."""
    sql, params = "SELECT * FROM position_events", []
    if since:
        sql += " WHERE at >= ?"
        params.append(since)
    df = query(sql + " ORDER BY event_id", conn, params)
    df["payload"] = df["payload"].map(json.loads)
    return df


# ---------------------------------------------------------------------------- corporate actions

CORPORATE_CLOSES = ("takeover", "delisting")


def dividends_for(conn: Conn, position_id: int) -> float:
    """Dividends (account currency) recorded against ``position_id``."""
    df = query(
        "SELECT amount, currency FROM cash WHERE kind = 'dividend' AND position_id = ?",
        conn,
        [position_id],
    )
    return float(
        sum(to_account(float(a), str(c)) for a, c in zip(df["amount"], df["currency"], strict=True))
    )


def position_for(conn: Conn, ticker: str, at: str | None = None) -> int | None:
    """The position in ``ticker`` that was open at ``at`` (default now): opened on or before it
    and not closed before it. The latest one when several match; None when none."""
    t = iso(at)
    found = None
    for pid in positions(conn)[lambda d: d["ticker"] == ticker]["position_id"]:
        ev = position_events(conn, int(pid))
        opened = ev.loc[ev["kind"] == "opened", "at"]
        closed = ev.loc[ev["kind"] == "closed", "at"]
        if opened.empty or str(opened.iloc[0]) > t:
            continue
        if not closed.empty and str(closed.iloc[-1])[:10] < t[:10]:
            continue
        found = int(pid)
    return found


def record_dividend(
    conn: Conn,
    ticker: str,
    amount: float,
    at: str | None = None,
    position_id: int | None = None,
    note: str = "",
) -> int | None:
    """Book a dividend received (``amount`` in the account currency, GBP) against the position
    in ``ticker`` open at ``at`` (or ``position_id``); returns that position id (None: unmatched,
    the cash still counts in equity)."""
    pid = position_id if position_id is not None else position_for(conn, ticker, at)
    cash_event(conn, "dividend", amount, "GBP", pid, note or f"{ticker} dividend", at)
    return pid


def split(
    conn: Conn,
    position_id: int,
    ratio: str,
    at: str | None = None,
    actor: str = OWNER,
    reason: str = "",
) -> dict[str, Any]:
    """Record a share split or consolidation ``ratio`` (``NEW:OLD``, e.g. ``2:1`` doubles the
    shares, ``1:10`` consolidates ten into one) on an open position.

    Appends a ``split`` event (``factor`` = new / old); :func:`position_state` scales the shares
    and average cost from that time (cost conserved, fractions dropped), and every open order
    of the position is replaced at the scaled quantity and price. Returns ``{"event_id",
    "factor", "shares", "avg_cost", "orders": {old_id: new_id}}``.
    """
    try:
        new, old = (int(x) for x in ratio.split(":"))
    except ValueError:
        raise ValueError(f"ratio must be NEW:OLD integers, got {ratio!r}") from None
    if new <= 0 or old <= 0:
        raise ValueError(f"ratio parts must be positive, got {ratio!r}")
    st = position_state(conn, position_id)
    if st.status != "open" or st.shares <= 0:
        raise ValueError(f"position {position_id} is {st.status} with {st.shares} shares; no split")
    t = iso(at)
    factor = new / old
    eid = position_event(
        conn,
        position_id,
        "split",
        {
            "ratio": f"{new}:{old}",
            "new": new,
            "old": old,
            "factor": factor,
            "shares_before": st.shares,
        },
        actor=actor,
        reason=reason or f"split {new}:{old}",
        at=t,
    )
    replaced: dict[int, int] = {}
    od = orders(conn, position_id)
    for o in od[od["status"].isin(OPEN_ORDER)].to_dict("records"):
        qty = max(1, int(int(o["qty"]) * factor))
        price = float(o["price"]) / factor if pd.notna(o["price"]) else None
        replaced[int(o["order_id"])] = replace_order(
            conn, int(o["order_id"]), qty, price, f"split {new}:{old}", actor, t
        )
    after = position_state(conn, position_id)
    return {
        "event_id": eid,
        "factor": factor,
        "shares": after.shares,
        "avg_cost": after.avg_cost,
        "orders": replaced,
    }


def corporate_close(
    conn: Conn,
    position_id: int,
    price: float,
    kind: str,
    at: str | None = None,
    actor: str = OWNER,
) -> CloseSummary:
    """Close an open position taken over or delisted: a synthetic exit fill of every share at
    ``price`` (quote currency; 0 for a worthless delisting, no commission), exit reason
    ``corporate_action`` (the order's approval reason keeps the ``kind``)."""
    if kind not in CORPORATE_CLOSES:
        raise ValueError(f"kind must be one of {CORPORATE_CLOSES}, got {kind!r}")
    if price < 0:
        raise ValueError(f"price must be >= 0, got {price}")
    st = position_state(conn, position_id)
    if st.status != "open" or st.shares <= 0:
        raise ValueError(f"position {position_id} is {st.status} with {st.shares} shares; no close")
    t = iso(at)
    oid = add_order(
        conn,
        position_id,
        -st.side,
        "market",
        st.shares,
        None,
        "exit",
        None,
        "approved",
        actor,
        f"corporate_action: {kind}",
        at=t,
    )
    out = fill(conn, oid, st.shares, price, t, 0.0, 0.0, actor)
    assert out["closed"] is not None
    return out["closed"]


# ---------------------------------------------------------------------------- revisions

REVISION_ACTIONS = (
    "hold",
    "tighten_stop",
    "take_partial",
    "exit",
    "revise_target",
    "add",
    "amend_entry",
    "cancel",
)
OPEN_ACTIONS = frozenset({"hold", "tighten_stop", "take_partial", "exit", "revise_target", "add"})
PENDING_ACTIONS = frozenset({"hold", "amend_entry", "cancel"})  # a draft / approved entry
AGENT_PREFIX = "agent:"  # an agent's revision is applied as it is recorded (ADR 0024)


def auto_applied(actor: str) -> bool:
    """Agents' (ADR 0024) and the pipeline's (pre-results trims and exits; ADR 0026) revisions are
    applied as they are recorded; the owner is told what to do at the broker and can undo."""
    return actor.startswith(AGENT_PREFIX) or actor == PIPELINE


def pending_entry(conn: Conn, position_id: int) -> dict[str, Any] | None:
    """The latest open entry order of a position (``order_id``, ``qty``, ``price``, ``type``,
    ``status``), None when there is none."""
    od = orders(conn, position_id)
    entry = od[(od["purpose"] == "entry") & od["status"].isin(OPEN_ORDER)]
    if entry.empty:
        return None
    r = entry.iloc[-1]
    price = None if r["price"] is None or pd.isna(r["price"]) else float(r["price"])
    return {
        "order_id": int(r["order_id"]),
        "qty": int(r["qty"]),
        "price": price,
        "type": str(r["type"]),
        "status": str(r["status"]),
    }


def entry_ever_placed(conn: Conn, position_id: int) -> bool:
    """Whether any entry order of the position has reached the broker (a ``placed`` event, now
    or before a replacement): then a change to the entry is also a change at the broker."""
    od = orders(conn, position_id)
    ids = [int(o) for o in od.loc[od["purpose"] == "entry", "order_id"]]
    if not ids:
        return False
    marks = ", ".join("?" * len(ids))
    row = conn.execute(
        f"SELECT COUNT(*) FROM order_events WHERE status = 'placed' AND order_id IN ({marks})",
        ids,
    ).fetchone()
    return bool(row and row[0])


def _drafted_plan(conn: Conn, position_id: int) -> dict[str, Any]:
    ev = position_events(conn, position_id)
    payloads = list(ev.loc[ev["kind"] == "drafted", "payload"])
    return dict(payloads[-1]) if payloads else {}


def waiting_on(conn: Conn, position_id: int) -> list[int]:
    """The positions whose sale pays for this one's entry (a PM rotation, ``waits_for`` in its
    plan) that are not closed yet: its buy is placed only once they are."""
    ids = _drafted_plan(conn, position_id).get("waits_for") or []
    return [int(i) for i in ids if position_status(conn, int(i)) == "open"]


def _old_values(conn: Conn, position_id: int, st: PositionState) -> dict[str, Any]:
    if st.status in ("draft", "approved"):
        pe = pending_entry(conn, position_id) or {}
        return {
            "entry": pe.get("price"),
            "stop": _planned_stop(conn, position_id),
            "qty": pe.get("qty"),
        }
    ev = position_events(conn, position_id)
    payloads = [x for x in ev.loc[ev["kind"].isin(["drafted", "amended", "revised"]), "payload"]]
    target = next(
        (p.get("target") for p in reversed(payloads) if p.get("target") is not None), None
    )
    return {"stop": st.stop, "target": target, "qty": st.shares}


def propose_revision(
    conn: Conn,
    position_id: int,
    action: str,
    new: dict[str, Any],
    reason: str,
    actor: str,
    evidence_ids: list[int] | None = None,
    trigger: str = "",
    at: str | None = None,
    auto_apply: bool | None = None,
) -> int:
    """Record a ``revision_proposed`` event (payload: action, old → new, reason, evidence,
    trigger) and return its event id, the revision id.

    Open positions take :data:`OPEN_ACTIONS`; pending entries (draft / approved positions)
    take :data:`PENDING_ACTIONS` (``amend_entry``: a new entry ``price`` and / or ``stop``,
    optionally ``qty``; ``cancel``). ``auto_apply`` (default: the actor is an agent,
    ``agent:...``, ADR 0024, or the pipeline, ADR 0026) accepts it at once under the same actor;
    one the book refuses
    (e.g. a stop not above the current one) is recorded as declined by the pipeline with the
    reason, so the owner sees it, and nothing else changes."""
    if action not in REVISION_ACTIONS:
        raise ValueError(f"action must be one of {REVISION_ACTIONS}, got {action!r}")
    st = position_state(conn, position_id)
    payload = {
        "action": action,
        "old": _old_values(conn, position_id, st),
        "new": {k: v for k, v in new.items() if v is not None},
        "reason": reason,
        "evidence_ids": list(evidence_ids or []),
        "trigger": trigger,
    }
    rid = position_event(conn, position_id, "revision_proposed", payload, actor, reason, at)
    if auto_apply is None:
        auto_apply = auto_applied(actor)
    if auto_apply and action != "hold":
        try:
            accept_revision(conn, rid, actor=actor, at=at)
        except ValueError as e:
            position_event(
                conn,
                position_id,
                "revision_declined",
                {"revision_id": rid},
                PIPELINE,
                f"auto-apply refused: {e}",
                at,
            )
    return rid


def revisions(
    conn: Conn, position_id: int | None = None, status: str | None = None
) -> pd.DataFrame:
    """Proposed revisions with their decision (``proposed`` / ``accepted`` / ``declined`` /
    ``undone``): ``decided_by`` is the decision's actor (an agent for an auto-applied one),
    ``decision_reason`` its reason, ``done`` what accepting it did (order ids, old → new)."""
    sql = "SELECT * FROM position_events WHERE kind IN ('revision_proposed', 'revision_accepted', 'revision_declined', 'revision_undone')"
    params: list[Any] = []
    if position_id is not None:
        sql += " AND position_id = ?"
        params.append(position_id)
    ev = query(sql + " ORDER BY event_id", conn, params)
    ev["payload"] = ev["payload"].map(json.loads)
    decided: dict[int, tuple[str, Any, Any, Any, dict[str, Any]]] = {}
    for kind, p, at, by, why in zip(
        ev["kind"], ev["payload"], ev["at"], ev["actor"], ev["reason"], strict=True
    ):
        if kind == "revision_proposed" or "revision_id" not in p:
            continue
        done = p.get("done") or decided.get(int(p["revision_id"]), ("", 0, 0, 0, {}))[4]
        decided[int(p["revision_id"])] = (kind.removeprefix("revision_"), at, by, why, done)
    rows = []
    for r in ev[ev["kind"] == "revision_proposed"].to_dict("records"):
        p = r["payload"]
        state, when, by, why, done = decided.get(
            int(r["event_id"]), ("proposed", None, None, None, {})
        )
        rows.append(
            {
                "revision_id": int(r["event_id"]),
                "position_id": int(r["position_id"]),
                "at": r["at"],
                "actor": r["actor"],
                "action": p.get("action"),
                "old": p.get("old"),
                "new": p.get("new"),
                "reason": p.get("reason", r["reason"]),
                "trigger": p.get("trigger"),
                "status": state,
                "decided_at": when,
                "decided_by": by,
                "decision_reason": why,
                "done": done,
            }
        )
    df = pd.DataFrame(
        rows,
        columns=[
            "revision_id",
            "position_id",
            "at",
            "actor",
            "action",
            "old",
            "new",
            "reason",
            "trigger",
            "status",
            "decided_at",
            "decided_by",
            "decision_reason",
            "done",
        ],
    )
    if status is not None:
        df = df[df["status"] == status]
    return df.reset_index(drop=True)


def _revision(conn: Conn, revision_id: int) -> dict[str, Any]:
    df = revisions(conn)
    row = df[df["revision_id"] == revision_id]
    if row.empty:
        raise ValueError(f"no revision {revision_id}")
    rev = {str(k): v for k, v in row.iloc[0].items()}
    if rev["status"] != "proposed":
        raise ValueError(f"revision {revision_id} is already {rev['status']}")
    return rev


def _check_revision(conn: Conn, st: PositionState, action: str, new: dict[str, Any]) -> None:
    """Refuse (``ValueError``) a revision the book cannot apply, before anything is written."""
    pending = st.status in ("draft", "approved")
    if pending and action not in PENDING_ACTIONS:
        raise ValueError(
            f"position {st.position_id} is a pending entry; {action!r} needs an open position"
        )
    if not pending and action not in OPEN_ACTIONS:
        raise ValueError(
            f"position {st.position_id} is {st.status}; {action!r} is for a pending entry"
        )
    busy = action in ("tighten_stop", "take_partial", "exit", "add") and not pending
    work = working(orders(conn, st.position_id)) if busy else pd.DataFrame()
    if busy and (work["purpose"] == "exit").any():
        raise ValueError(
            f"position {st.position_id} has an exit order working: record its fill or end it first"
        )
    if action == "add" and not pending and (work["purpose"] == "add").any():
        raise ValueError(
            f"position {st.position_id} already has an add order working: record its fill or"
            " end it first"
        )
    if action == "tighten_stop":
        price = float(new["stop"])
        if st.stop is not None and price <= st.stop:
            raise ValueError(
                f"new stop {price} is not above the current stop {st.stop}: never lower a stop"
            )
    elif action == "take_partial" and int(new.get("qty") or 0) <= 0:
        raise ValueError("take_partial needs a positive qty")
    elif action == "revise_target" and not any(k in new for k in ("target", "target_2")):
        raise ValueError("revise_target needs a new target")
    elif action == "add":
        if int(new.get("qty") or 0) <= 0 or new.get("entry") is None:
            raise ValueError("add needs a positive qty and an entry price")
        if st.shares <= 0:
            raise ValueError(f"position {st.position_id} is {st.status}; nothing to add to")
        over = add_breaches(conn, st, int(new["qty"]), float(new["entry"]), new.get("stop"))
        if over:
            raise ValueError("add breaks the portfolio limits: " + "; ".join(over))
    elif action == "amend_entry":
        if not any(new.get(k) is not None for k in ("price", "stop", "qty")):
            raise ValueError("amend_entry needs a new entry price, stop or qty")
        pe = pending_entry(conn, st.position_id)
        entry = new.get("price", pe["price"] if pe else None)
        stop = new.get("stop", _planned_stop(conn, st.position_id))
        if entry is not None and stop is not None and float(stop) >= float(entry):
            raise ValueError(f"stop {stop} is not below the entry {entry}")


def add_breaches(
    conn: Conn,
    st: PositionState,
    qty: int,
    entry: float,
    stop: float | None = None,
    rules: Rules | None = None,
) -> list[str]:
    """The portfolio limits an add of ``qty`` at ``entry`` (the whole position's stop moved to
    ``stop``, never lowered) would break, empty when it fits (ADR 0025). The book holds no live
    prices: equity is cash plus the open positions at cost, and this position is valued at
    ``entry`` after the add. Checks ``max_position_pct``, ``max_sector_pct``, ``max_heat`` and
    the ``cash_buffer``; liquidity (``max_adv_pct``) is the agent's to check."""
    from solaraalpha.portfolio.risk import sector_of  # local import: risk depends on book

    rules = rules or load_rules()
    states = [position_state(conn, int(p)) for p in positions(conn)["position_id"]]
    held = [s for s in states if s.status == "open" and s.shares > 0]
    cost = {s.position_id: to_account(s.avg_cost * s.shares, s.currency) for s in held}
    realised = sum(s.realised_pnl for s in states)
    cash_now = cash_balance(conn) + realised - sum(cost.values())
    eq = cash_now + sum(cost.values())
    if eq <= 0:
        return [f"equity £{eq:,.0f}: nothing to size against"]
    add_cost = to_account(entry * qty, st.currency)
    shares = st.shares + qty
    value = to_account(entry * shares, st.currency)
    stops = [float(x) for x in (stop, st.stop) if x is not None]
    new_stop = max(stops) if stops else None
    avg = (st.avg_cost * st.shares + entry * qty) / shares
    risk = (
        max(0.0, to_account((avg - new_stop) * shares, st.currency))
        if new_stop is not None
        else value
    )
    others = [s for s in held if s.position_id != st.position_id]
    sector = sector_of(st.ticker)
    sector_value = value + sum(cost[s.position_id] for s in others if sector_of(s.ticker) == sector)
    out = []
    if value > rules.max_position_pct * eq:
        out.append(
            f"position {value / eq:.0%} of equity > max_position_pct {rules.max_position_pct:.0%}"
        )
    if sector_value > rules.max_sector_pct * eq:
        out.append(
            f"sector {sector} {sector_value / eq:.0%} > max_sector_pct {rules.max_sector_pct:.0%}"
        )
    heat = (risk + sum(s.risk_at_stop for s in others)) / eq
    if heat > rules.max_heat:
        out.append(f"heat {heat:.1%} > max_heat {rules.max_heat:.1%}")
    if cash_now - add_cost < rules.cash_buffer * eq:
        out.append(
            f"cash £{cash_now - add_cost:,.0f} after the add < cash_buffer {rules.cash_buffer:.0%}"
        )
    return out


def _resize(conn: Conn, st: PositionState, entry: float, stop: float) -> int | None:
    """Shares for a moved entry / stop that keep the drafted plan's risk budget (its
    ``risk_amount``), and its value when a cap other than risk bound it; None when the plan has
    no budget to keep."""
    plan = _drafted_plan(conn, st.position_id)
    budget = plan.get("risk_amount")
    if budget is None or entry <= stop:
        return None
    qty = math.floor(float(budget) / to_account(entry - stop, st.currency))
    if plan.get("capped_by") not in (None, "risk") and plan.get("position_value"):
        qty = min(qty, math.floor(float(plan["position_value"]) / to_account(entry, st.currency)))
    return max(qty, 0)


def accept_revision(
    conn: Conn, revision_id: int, actor: str = OWNER, at: str | None = None
) -> dict[str, Any]:
    """Accept a revision: the orders it implies, all ``approved``, then a ``revision_accepted``
    event carrying what was done (``done``, which :func:`undo_revision` reverses).

    Open positions: tighten_stop: replace the stop, ratchet only, + ``stop_raised``;
    take_partial: an exit order for the qty; exit: an exit order with reason thesis_broken;
    revise_target: a ``revised`` event; add: an ``add`` buy order for ``qty`` at ``entry`` beside
    the stop (one order per unit, ADR 0031), the stop raised to ``stop`` when that is higher
    (never lower): the add's fill re-sizes the stop to the whole position.
    Pending entries (ADR 0024): amend_entry: :func:`amend_position` with the new entry price /
    stop / qty (qty re-sized to the plan's risk budget when not given); cancel: the position
    ``cancelled`` and its open orders with it. Nothing is written when the book refuses it."""
    rev = _revision(conn, revision_id)
    pid, action, new = int(rev["position_id"]), str(rev["action"]), dict(rev["new"] or {})
    t = iso(at)
    st = position_state(conn, pid)
    _check_revision(conn, st, action, new)
    done: dict[str, Any] = {"revision_id": revision_id, "action": action}
    if action == "tighten_stop":
        price = float(new["stop"])
        od = orders(conn, pid)
        live = od[(od["purpose"] == "stop") & od["status"].isin(LIVE_ORDER)]
        if live.empty:
            done["stop_order_id"] = add_order(
                conn, pid, -st.side, "stop", st.shares, price, "stop", None, "approved", actor, at=t
            )
        else:
            done["stop_order_id"] = replace_order(
                conn, int(live["order_id"].iloc[-1]), None, price, "revision accepted", actor, t
            )
        done |= {"old_stop": st.stop, "new_stop": price}
        position_event(
            conn,
            pid,
            "stop_raised",
            {"old": st.stop, "new": price, "revision_id": revision_id},
            actor,
            "",
            t,
        )
    elif action == "take_partial":
        done["exit_order_id"] = exit_position(
            conn,
            pid,
            "discretionary",
            str(new.get("type", "market")),
            new.get("price"),
            int(new["qty"]),
            actor,
            t,
        )
    elif action == "exit":
        done["exit_order_id"] = exit_position(
            conn,
            pid,
            "thesis_broken",
            str(new.get("type", "market")),
            new.get("price"),
            None,
            actor,
            t,
        )
    elif action == "revise_target":
        payload = {k: new[k] for k in ("target", "target_2") if k in new}
        done |= {"old_target": (rev["old"] or {}).get("target"), **payload}
        position_event(conn, pid, "revised", {**payload, "revision_id": revision_id}, actor, "", t)
    elif action == "add":
        qty = int(new["qty"])
        stop = float(new["stop"]) if new.get("stop") is not None else st.stop
        if stop is not None and (st.stop is None or stop > st.stop + 1e-9):
            od = orders(conn, pid)
            live = od[(od["purpose"] == "stop") & od["status"].isin(LIVE_ORDER)]
            if not live.empty:
                done["stop_order_id"] = replace_order(
                    conn, int(live["order_id"].iloc[-1]), None, stop, "raised for the add", actor, t
                )
            position_event(
                conn,
                pid,
                "stop_raised",
                {"old": st.stop, "new": stop, "revision_id": revision_id},
                actor,
                "",
                t,
            )
        done["add_order_id"] = add_order(
            conn,
            pid,
            st.side,
            str(new.get("type", "limit")),
            qty,
            float(new["entry"]),
            "add",
            new.get("valid_until"),
            "approved",
            actor,
            f"add accepted (revision {revision_id})",
            at=t,
        )
    elif action == "amend_entry":
        pe = pending_entry(conn, pid)
        planned = _planned_stop(conn, pid)
        to_price, to_stop, to_qty = new.get("price"), new.get("stop"), new.get("qty")
        if to_qty is None and pe is not None and (to_price is not None or to_stop is not None):
            entry_px = float(to_price) if to_price is not None else pe["price"]
            stop_px = float(to_stop) if to_stop is not None else planned
            if entry_px is not None and stop_px is not None:
                to_qty = _resize(conn, st, float(entry_px), float(stop_px))
        if to_qty is not None and pe is not None and int(to_qty) == pe["qty"]:
            to_qty = None
        if to_qty is not None and int(to_qty) <= 0:
            raise ValueError("amend_entry leaves no shares at the plan's risk budget")
        done["amended"] = amend_position(
            conn,
            pid,
            int(to_qty) if to_qty is not None else None,
            float(to_price) if to_price is not None else None,
            float(to_stop) if to_stop is not None else None,
            f"revision {revision_id} ({actor})",
            actor,
        )
        done["was_placed"] = entry_ever_placed(conn, pid)
    elif action == "cancel":
        pe = pending_entry(conn, pid)
        done["was_placed"] = entry_ever_placed(conn, pid)
        od = orders(conn, pid)
        done["cancelled_orders"] = [
            int(o) for o in od.loc[od["status"].isin(OPEN_ORDER), "order_id"]
        ]
    accepted = {"revision_id": revision_id, "action": action, "done": done}
    position_event(conn, pid, "revision_accepted", accepted, actor, "", t)
    if action == "cancel":  # after the acceptance: a cancelled position takes no more events
        cancel_open_orders(
            conn, pid, reason=f"cancelled: revision {revision_id}", actor=actor, at=t
        )
        position_event(
            conn, pid, "cancelled", {"revision_id": revision_id}, actor, str(rev["reason"]), t
        )
    return done


def undo_revision(
    conn: Conn, revision_id: int, reason: str, actor: str = OWNER, at: str | None = None
) -> dict[str, Any]:
    """Reverse an accepted revision (the owner's undo of an auto-applied agent change, ADR
    0024): tighten_stop: the stop order back at the old stop; revise_target: the old target;
    amend_entry: the entry order and stop back as they were; take_partial / exit / add: the
    order it created cancelled if it has not filled. A cancelled pending entry cannot be
    reopened (re-plan it: ``plan.py --run N --replan``). Records ``revision_undone``."""
    if not reason:
        raise ValueError("an undo needs a reason")
    df = revisions(conn)
    row = df[df["revision_id"] == revision_id]
    if row.empty:
        raise ValueError(f"no revision {revision_id}")
    rev = {str(k): v for k, v in row.iloc[0].items()}
    if rev["status"] != "accepted":
        raise ValueError(
            f"revision {revision_id} is {rev['status']}; only an accepted one can be undone"
        )
    pid, action, done = int(rev["position_id"]), str(rev["action"]), dict(rev["done"] or {})
    old = dict(rev["old"] or {})
    t = iso(at)
    out: dict[str, Any] = {"revision_id": revision_id, "action": action}
    if action == "cancel":
        raise ValueError(
            "a cancelled entry cannot be reopened: re-plan it with `plan.py --run N --replan`"
        )
    if action == "tighten_stop":
        if old.get("stop") is None:
            raise ValueError("there was no stop before this revision to restore")
        od = orders(conn, pid)
        live = od[(od["purpose"] == "stop") & od["status"].isin(LIVE_ORDER)]
        if live.empty:
            raise ValueError(f"position {pid} has no live stop order to restore")
        out["stop_order_id"] = replace_order(
            conn,
            int(live["order_id"].iloc[-1]),
            None,
            float(old["stop"]),
            f"undo revision {revision_id}: {reason}",
            actor,
            t,
        )
        out["restored_stop"] = float(old["stop"])
    elif action == "revise_target":
        restored = {"target": old.get("target"), "target_2": None}
        position_event(
            conn, pid, "revised", {**restored, "revision_id": revision_id}, actor, reason, t
        )
        out["restored_target"] = old.get("target")
    elif action == "amend_entry":
        amended = done.get("amended") or {}
        before = amended.get("old") or {}
        out["amended"] = amend_position(
            conn,
            pid,
            int(before["qty"]) if before.get("qty") is not None else None,
            float(before["price"]) if before.get("price") is not None else None,
            float(before["stop"]) if before.get("stop") is not None else None,
            f"undo revision {revision_id}: {reason}",
            actor,
        )
    else:  # take_partial / exit / add: cancel the order it created while it is unfilled
        key = "add_order_id" if action == "add" else "exit_order_id"
        oid = done.get(key)
        if oid is not None and order_status(conn, int(oid)) == "cancelled":
            # replaced since (the execution agent's type / price): undo the order now standing
            purpose = str(_order(conn, int(oid))["purpose"])
            od = orders(conn, pid)
            later = od[
                (od["purpose"] == purpose)
                & (od["order_id"] > int(oid))
                & od["status"].isin(OPEN_ORDER)
            ]
            oid = int(later["order_id"].iloc[-1]) if not later.empty else oid
        if (
            oid is None
            or order_status(conn, int(oid)) not in OPEN_ORDER
            or filled_qty(conn, int(oid))
        ):
            raise ValueError(f"the {action} order has filled (or is gone): nothing to undo")
        cancel_order(conn, int(oid), f"undo revision {revision_id}", actor, t)
        out["cancelled_order"] = int(oid)
        out["stop_order_id"] = protect(conn, pid, actor=actor, at=t)  # the stop back on
    position_event(
        conn, pid, "revision_undone", {"revision_id": revision_id, "done": out}, actor, reason, t
    )
    return out


def decline_revision(
    conn: Conn, revision_id: int, reason: str, actor: str = OWNER, at: str | None = None
) -> None:
    if not reason:
        raise ValueError("declining needs a reason")
    rev = _revision(conn, revision_id)
    position_event(
        conn,
        int(rev["position_id"]),
        "revision_declined",
        {"revision_id": revision_id},
        actor,
        reason,
        at,
    )
