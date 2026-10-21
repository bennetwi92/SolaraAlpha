"""What the owner does in Trading212: the book's orders as step-by-step broker instructions.

Trading212 (Stocks & Shares ISA) takes market, limit, stop (market) and stop-limit orders, holds
**one open order per stock**, and sets price alerts. Nothing waits for the owner's approval (ADR
0026): every order the book has approved and the owner has not yet marked placed is a
:class:`Task` here, in the order to do it. A task cancels the order its new one replaces (a stop
moved, the stop taken off for an exit or an add) and says what to place once it fills (the stop
for the shares bought, the stop back on after a part sale), with the price alerts for the targets.
The owner reports back with ``book.py placed`` / ``fill`` (or by telling Claude).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from solaraalpha.portfolio import book
from solaraalpha.store import Conn, query

BROKER = "Trading212"
TYPE_NAMES = {"market": "Market order", "limit": "Limit order", "stop": "Stop order"}


@dataclass
class Task:
    """One stock's to-do list at the broker."""

    ticker: str
    position_id: int
    title: str
    steps: list[str]
    why: str = ""
    alerts: list[str] = field(default_factory=list)
    reply: str = ""  # what to tell back (the book commands)
    undo: str = ""  # how to reverse an automatic change
    at: str = ""  # when its newest order was created (ISO)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def price(v: Any, ccy: str) -> str:
    f = _num(v)
    return book.fmt_price(f, ccy) if f is not None else "-"


def order_text(side: int, type: str, qty: int, px: Any, ccy: str) -> str:
    """``Buy 120 · Stop order · stop price 1,540p``, as Trading212 asks for it."""
    out = f"{'Buy' if side == 1 else 'Sell'} {int(qty):,} · {TYPE_NAMES.get(type, type)}"
    if type in ("limit", "stop"):
        out += f" · {type} price {price(px, ccy)}"
    return out


def _bold(o: dict[str, Any], ccy: str) -> str:
    return f"**{order_text(int(o['side']), str(o['type']), int(o['qty']), o['price'], ccy)}**"


def targets(conn: Conn, position_id: int) -> list[tuple[float, str]]:
    """The position's target levels (latest plan / revision) with their label, e.g.
    ``(1748.0, "target +15%")``; the move is measured from the planned entry or the cost."""
    ev = book.position_events(conn, position_id)
    payloads = list(ev.loc[ev["kind"].isin(["drafted", "amended", "revised"]), "payload"])
    st = book.position_state(conn, position_id)
    base = st.avg_cost if st.shares else None
    if base is None or math.isnan(base):
        base = next((_num(p.get("entry")) for p in reversed(payloads) if p.get("entry")), None)
    out = []
    for key in ("target", "target_2"):
        lvl = next((_num(p.get(key)) for p in reversed(payloads) if key in p), None)
        if lvl is None:
            continue
        label = "target" if key == "target" else "second target"
        if base:
            label += f" {lvl / base - 1:+.0%}"
        out.append((lvl, label))
    return out


def alert_lines(conn: Conn, position_id: int, ccy: str) -> list[str]:
    return [f"{price(lvl, ccy)} ({label})" for lvl, label in targets(conn, position_id)]


def _placed_ids(conn: Conn) -> set[int]:
    df = query("SELECT DISTINCT order_id FROM order_events WHERE status = 'placed'", conn)
    return {int(x) for x in df["order_id"]}


def _cancels(conn: Conn) -> pd.DataFrame:
    """Every ``cancelled`` / ``expired`` order event (``order_id``, ``at``, ``actor``, ``reason``)."""
    return query(
        "SELECT order_id, at, actor, reason FROM order_events"
        " WHERE status IN ('cancelled', 'expired') ORDER BY event_id",
        conn,
    )


def _revision_for(conn: Conn) -> dict[int, dict[str, Any]]:
    """order id -> the accepted revision that created it (for the why and the undo)."""
    rv = book.revisions(conn, status="accepted")
    out: dict[int, dict[str, Any]] = {}
    for r in rv.to_dict("records"):
        done = r["done"] or {}
        ids = [done.get(k) for k in ("stop_order_id", "exit_order_id", "add_order_id")]
        ids.append(((done.get("amended") or {}).get("new") or {}).get("order_id"))
        for oid in ids:
            if oid is not None:
                out[int(oid)] = {str(k): v for k, v in r.items()}
    return out


def _who(actor: str) -> str:
    if actor.startswith(book.AGENT_PREFIX):
        return f"the {actor.removeprefix(book.AGENT_PREFIX)} agent"
    return "the weekly review" if actor == book.PIPELINE else "you"


def tasks(conn: Conn) -> list[Task]:
    """Every approved order not yet placed, grouped by stock, as the steps to take in
    Trading212 (oldest position first)."""
    od = book.orders(conn)
    todo = od[od["status"] == "approved"]
    if todo.empty:
        return []
    pos = book.positions(conn).set_index("position_id")
    placed = _placed_ids(conn)
    cancels = _cancels(conn)
    revs = _revision_for(conn)
    out = []
    for pid, group in todo.groupby("position_id", sort=True):
        pid = int(str(pid))
        p = pos.loc[pid]
        ccy, ticker = str(p["currency"]), str(p["ticker"])
        st = book.position_state(conn, pid)
        mine = od[od["position_id"] == pid].set_index("order_id")
        task = Task(ticker, pid, "", [])
        for rec in group.to_dict("records"):
            o = {str(k): v for k, v in rec.items()}
            oid = int(o["order_id"])
            task.at = max(task.at, str(o["created_at"]))
            # the placed orders it replaces: cancelled in the same booking (same time)
            gone = cancels[
                (cancels["at"] == o["created_at"])
                & cancels["order_id"].isin([i for i in mine.index if i in placed and i != oid])
            ]
            for g in gone["order_id"]:
                old = mine.loc[int(g)]
                what = {"stop": "stop order", "entry": "buy order"}.get(
                    str(old["purpose"]), "order"
                )
                task.steps.append(f"Cancel your {what} #{int(g)} ({_bold(dict(old), ccy)})")
            _steps(conn, task, o, st, ccy, mine, bool(len(gone)))
            rev = revs.get(oid)
            if rev:
                task.why = f"{rev['reason']} ({_who(str(rev['actor']))})"
                if book.auto_applied(str(rev["actor"])):
                    task.undo = f"book.py undo {int(rev['revision_id'])} --reason ..."
        out.append(task)
    return out


def _steps(
    conn: Conn,
    task: Task,
    o: dict[str, Any],
    st: book.PositionState,
    ccy: str,
    mine: pd.DataFrame,
    replaces: bool,
) -> None:
    oid, purpose, qty = int(o["order_id"]), str(o["purpose"]), int(o["qty"])
    level = st.stop if st.stop is not None else st.initial_stop
    if purpose == "entry":
        plan = book._drafted_plan(conn, task.position_id)
        task.title = "Change the entry" if replaces else "New trade"
        sales = book.waiting_on(conn, task.position_id)
        if sales:  # a PM rotation: the sale pays for it, so the buy waits for the sale
            pos = book.positions(conn).set_index("position_id")["ticker"]
            names = " and ".join(f"{pos[i]} (position {i})" for i in sales)
            task.steps.append(
                f"Don't place it yet: selling {names} pays for it. Once that sale has filled"
                f" I'll size it to the cash you have and tell you to place {_bold(o, ccy)}"
            )
            task.alerts = alert_lines(conn, task.position_id, ccy)
            if not task.why:
                stop = book._planned_stop(conn, task.position_id)
                task.why = _entry_why(conn, task.position_id, plan, o, stop, ccy)
            task.reply = "nothing yet"
            return
        if plan.get("wait_for_break") and not replaces:
            task.steps.append(
                f"Don't place it yet: when a daily close is above {price(o['price'], ccy)},"
                f" I'll tell you to place {_bold(o, ccy)}"
            )
        else:
            task.steps.append(f"Place {_bold(o, ccy)} (#{oid}), good till cancelled")
        stop = book._planned_stop(conn, task.position_id)
        if stop is not None:
            task.steps.append(
                "When it fills, place the stop for the shares you got:"
                f" **Sell {qty:,} · Stop order · stop price {price(stop, ccy)}**"
            )
        task.alerts = alert_lines(conn, task.position_id, ccy)
        if not task.why:
            task.why = _entry_why(conn, task.position_id, plan, o, stop, ccy)
        task.reply = f"`placed {oid}`; when it fills: `fill {oid} --qty N --price P`"
    elif purpose == "stop":
        old = _replaced_stop(mine, o) if replaces else None
        if old is None:
            task.title = task.title or "Put the stop on"
        elif float(o["price"]) > old + 1e-9:
            task.title = (
                task.title or f"Raise the stop ({price(old, ccy)} → {price(o['price'], ccy)})"
            )
        else:
            task.title = task.title or "Replace the stop"
        task.steps.append(f"Place {_bold(o, ccy)} (#{oid}), good till cancelled")
        if not task.why:
            task.why = "protects the shares you hold"
        task.reply = _join(task.reply, f"`placed {oid}`")
    elif purpose == "exit":
        rest = st.shares - qty
        task.title = (
            "Sell everything" if rest <= 0 else f"Sell {qty:,} of your {st.shares:,} shares"
        )
        task.steps.append(f"Place {_bold(o, ccy)} (#{oid})")
        if rest > 0 and level is not None:
            task.steps.append(
                f"When it has filled, put the stop back on the rest:"
                f" **Sell {rest:,} · Stop order · stop price {price(level, ccy)}**"
            )
        task.reply = f"`placed {oid}`; when it fills: `fill {oid} --qty N --price P`"
    elif purpose == "add":
        task.title = f"Add {qty:,} shares to a winner"
        task.steps.append(f"Place {_bold(o, ccy)} (#{oid})")
        if level is not None:
            task.steps.append(
                "When it fills, put the stop back on everything:"
                f" **Sell {st.shares + qty:,} · Stop order · stop price {price(level, ccy)}**."
                " If it hasn't filled by the close, cancel it and put back"
                f" **Sell {st.shares:,} · Stop order · stop price {price(level, ccy)}**"
            )
        task.reply = f"`placed {oid}`; then `fill {oid} --qty N --price P` or `unfilled {oid}`"
    else:
        task.title = task.title or f"Place a {purpose} order"
        task.steps.append(f"Place {_bold(o, ccy)} (#{oid})")
        task.reply = _join(task.reply, f"`placed {oid}`")


def _join(a: str, b: str) -> str:
    return f"{a}; {b}" if a else b


def _replaced_stop(mine: pd.DataFrame, o: dict[str, Any]) -> float | None:
    """The price of the stop order this one follows (the latest earlier stop order)."""
    earlier = mine[(mine["purpose"] == "stop") & (mine.index < int(o["order_id"]))]
    return float(earlier["price"].iloc[-1]) if not earlier.empty else None


def _entry_why(
    conn: Conn,
    position_id: int,
    plan: dict[str, Any],
    o: dict[str, Any],
    stop: float | None,
    ccy: str,
) -> str:
    pos = book.positions(conn).set_index("position_id").loc[position_id]
    thesis = str(pos["thesis"] or "").strip()
    if len(thesis) > 240:
        thesis = thesis[:237].rsplit(" ", 1)[0] + "…"
    bits = [thesis] if thesis else []
    risk = _num(plan.get("risk_amount"))
    if risk is None and stop is not None and _num(o["price"]) is not None:
        risk = book.to_account((float(o["price"]) - stop) * int(o["qty"]), ccy)
    if risk is not None:
        bits.append(f"You lose about £{risk:,.0f} if the stop is hit.")
    if plan.get("stop_reason"):
        bits.append(f"Stop: {plan['stop_reason']}")
    if pd.notna(o.get("valid_until")) and o.get("valid_until"):
        day = pd.Timestamp(str(o["valid_until"])).strftime("%a %-d %b")
        bits.append(f"If it hasn't filled by {day}, I'll tell you to cancel it.")
    return " ".join(b if b.endswith((".", "!", "?", "…")) else b + "." for b in bits)


def cancels_since(conn: Conn, since: str) -> list[Task]:
    """Pending entries an agent (or the pipeline) cancelled at or after ``since``: the last order
    of each kind that reached Trading212 is cancelled there too (one task per stock)."""
    ev = book.all_position_events(conn, since)
    if ev.empty:
        return []
    gone = ev[(ev["kind"] == "cancelled") & (ev["actor"] != book.OWNER)]
    placed = _placed_ids(conn)
    pos = book.positions(conn).set_index("position_id")
    out = []
    for pid in dict.fromkeys(int(x) for x in gone["position_id"]):
        od = book.orders(conn, pid)
        sent = od[od["order_id"].isin(placed)]
        ccy, ticker = str(pos.loc[pid, "currency"]), str(pos.loc[pid, "ticker"])
        task = Task(ticker, pid, "Cancel an order", [], at=str(gone["at"].iloc[-1]))
        for _, g in sent.groupby("purpose", sort=False):
            last = g.iloc[-1]
            if last["status"] not in ("cancelled", "expired"):
                continue
            what = {"stop": "stop order", "entry": "buy order"}.get(str(last["purpose"]), "order")
            task.steps.append(
                f"Cancel your {what} #{int(last['order_id'])} ({_bold(dict(last), ccy)})"
                " if it is still there"
            )
        reason = gone.loc[gone["position_id"] == pid, "reason"].iloc[-1]
        task.why = f"{reason} (the plan was cancelled)" if reason else "the plan was cancelled"
        if task.steps:
            out.append(task)
    return out


def render_task(i: int, t: Task) -> list[str]:
    """One task as numbered markdown steps."""
    lines = [f"### {i}. {t.ticker} — {t.title}", ""]
    lines += [f"{n}. {s}" for n, s in enumerate(t.steps, 1)]
    if t.alerts:
        lines.append(f"{len(t.steps) + 1}. Set price alerts at " + " and ".join(t.alerts))
    lines.append("")
    if t.why:
        lines += [f"Why: {t.why}", ""]
    if t.reply:
        lines += [f"Then tell me: {t.reply}", ""]
    return lines
