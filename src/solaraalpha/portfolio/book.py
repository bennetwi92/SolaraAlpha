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
    status     TEXT NOT NULL,                 -- draft / approved / placed / part_filled / filled /
                                              -- not_filled / cancelled / expired
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
    "revision_proposed": frozenset({"open"}),
    "revision_accepted": frozenset({"open"}),
    "revision_declined": frozenset({"open"}),
    "closed": frozenset({"open"}),
}
ORDER_TRANSITIONS: dict[str, frozenset[str | None]] = {
    "draft": frozenset({None}),
    "approved": frozenset({None, "draft"}),  # a protective stop is born approved
    "placed": frozenset({"approved"}),
    "part_filled": frozenset({"approved", "placed", "part_filled"}),
    "filled": frozenset({"approved", "placed", "part_filled"}),
    "not_filled": frozenset({"approved", "placed", "part_filled"}),
    "cancelled": frozenset({"draft", "approved", "placed", "part_filled"}),
    "expired": frozenset({"approved", "placed"}),
}
OPEN_ORDER = frozenset({"draft", "approved", "placed", "part_filled"})  # may still (part) fill
LIVE_ORDER = frozenset({"approved", "placed", "part_filled"})  # at (or going to) the broker
FILLABLE = frozenset({"approved", "placed", "part_filled"})


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
    order_event(conn, order_id, "filled" if done == total else "part_filled", actor=actor, at=t)
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
    stop: float | None  # price of the latest live stop order
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

    shares, avg, realised, costs = 0, 0.0, 0.0, 0.0
    entry_qty, entry_cost = 0, 0.0
    for f in fl.to_dict("records"):
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

    od = orders(conn, position_id)
    stops = od[(od["purpose"] == "stop") & od["status"].isin(LIVE_ORDER)]
    stop = float(stops["price"].iloc[-1]) if not stops.empty else None
    initial = _planned_stop(conn, position_id)
    if initial is None:
        first = od[od["purpose"] == "stop"]
        initial = float(first["price"].iloc[0]) if not first.empty else None

    risk = to_account(max(0.0, side * (avg - stop) * shares), ccy) if stop and shares else 0.0
    r_unit = (
        to_account(abs(entry_avg - initial) * entry_qty, ccy)
        if entry_qty and initial is not None
        else 0.0
    )
    ev = position_events(conn, position_id)
    opened = ev.loc[ev["kind"] == "opened", "at"]
    return PositionState(
        position_id=position_id,
        ticker=str(pos["ticker"]),
        side=side,
        currency=ccy,
        status=position_status(conn, position_id),
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


def _planned_stop(conn: Conn, position_id: int) -> float | None:
    """The stop in the latest ``drafted`` / ``amended`` payload, if any."""
    ev = position_events(conn, position_id)
    for p in reversed(list(ev.loc[ev["kind"].isin(["drafted", "amended"]), "payload"])):
        if p.get("stop") is not None:
            return float(p["stop"])
    return None
