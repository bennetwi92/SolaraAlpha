"""Reconcile the book against Trading212: what the broker shows that the book doesn't, and back.

Never writes to the broker (ADR 0030). :func:`reconcile` only reads; :func:`absorb` books into the
book what the broker plainly shows (fills, orders placed, orders ended, dividends paid) as actor
``broker`` and leaves the rest for the owner (ADR 0030 amendment). Each :class:`Difference` says
in plain words what differs and, when one fixes it, the ``book.py`` arguments to run
(``command``, without the ``book.py`` prefix). Checked:

- **fills** in the broker's order history against the book's fills on the same order (a stop
  that sold, an entry that filled, a fill typed in wrong);
- **orders** working at the broker against the book's live orders (placed but not yet told,
  working at the broker but ended in the book, in the book but not at the broker);
- **positions** (shares and average price) per stock;
- **dividends** and **deposits / withdrawals** against the book's cash events.

Book and broker orders are tied by the ``broker_ref`` of the book's ``placed`` event (the
Trading212 order id), else by a unique match on ticker, side, type, quantity and price (within
``PRICE_TOL``). Prices are compared in pence: the book stores the quote currency and Trading212
quotes LSE lines in GBX. Broker history from before the book's first order is ignored.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from solaraalpha.portfolio import book
from solaraalpha.store import Conn

PRICE_TOL = 0.005  # prices further apart than 0.5% are a mismatch
CASH_TOL = 0.01  # GBP
ACTOR = "broker"  # what absorb() books is the broker's record, not the owner's word
ENDED = frozenset({"filled", "not_filled", "cancelled", "expired"})


@dataclass(frozen=True)
class Difference:
    kind: str  # fill / unknown_fill / placed / order_gone / unknown_order / ended_order /
    # order_terms / position_missing / unknown_position / shares / avg_price / dividend /
    # cash
    ticker: str
    text: str
    command: str | None = None  # book.py arguments that fix it (None: check by hand)
    position_id: int | None = None
    order_id: int | None = None
    data: Mapping[str, Any] = field(default_factory=dict)  # what absorb() books (empty: nothing)


@dataclass
class Broker:
    """The broker's state, from :mod:`solaraalpha.data.trading212` (or fixtures in tests)."""

    positions: pd.DataFrame
    pending: pd.DataFrame
    history: pd.DataFrame
    dividends: pd.DataFrame
    transactions: pd.DataFrame
    to_yahoo: Mapping[str, str] = field(default_factory=dict)  # Trading212 ticker -> Yahoo

    def yahoo(self, t212: str) -> str:
        return self.to_yahoo.get(t212) or re.sub(r"l\d*_EQ$", "", t212) + ".L"


def fetch_broker() -> Broker:
    """The live broker state (six read calls, the instrument list included)."""
    from solaraalpha.data import trading212 as t212

    lines = t212.lse_lines(t212.fetch_instruments())
    return Broker(
        positions=t212.fetch_positions(),
        pending=t212.fetch_pending(),
        history=t212.fetch_history(),
        dividends=t212.fetch_dividends(),
        transactions=t212.fetch_transactions(),
        to_yahoo={v: k for k, v in lines.items()},
    )


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None or _nan(a) or _nan(b):
        return (a is None or _nan(a)) and (b is None or _nan(b))
    return abs(float(a) - float(b)) <= PRICE_TOL * max(abs(float(a)), abs(float(b)))


def _nan(v: Any) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _px(price: float | None) -> str:
    return book.fmt_price(price, "GBX")


def _side(side: int) -> str:
    return "buy" if side > 0 else "sell"


def _book_orders(conn: Conn) -> pd.DataFrame:
    od = book.orders(conn)
    pos = book.positions(conn)[["position_id", "ticker", "status"]]
    od = od.merge(pos.rename(columns={"status": "position_status"}), on="position_id")
    fl = book.fills(conn)
    if not fl.empty:
        avg = fl.assign(v=fl["qty"] * fl["price"]).groupby("order_id")[["v", "qty"]].sum()
        od["fill_price"] = od["order_id"].map(avg["v"] / avg["qty"])
    else:
        od["fill_price"] = math.nan
    return od


def _matcher(od: pd.DataFrame, br: Broker) -> Any:
    taken: set[int] = set()

    def match(o: Mapping[str, Any], statuses: frozenset[str] | None = None) -> int | None:
        by_ref = od[od["broker_ref"].astype(str) == str(o["id"])]
        if not by_ref.empty:
            oid = int(by_ref["order_id"].iloc[0])
            taken.add(oid)
            return oid
        c = od[
            (od["ticker"] == br.yahoo(str(o["t212"])))
            & (od["side"] == o["side"])
            & (od["type"] == o["type"])
            & (od["qty"] == round(float(o["qty"])))
            & (od["status"] != "draft")
            & od["broker_ref"].isna()
            & ~od["order_id"].isin(taken)
        ]
        if statuses is not None:
            c = c[c["status"].isin(statuses)]
        c = c[[_close(p, o["price"]) for p in c["price"]]]
        if len(c) != 1:
            return None
        oid = int(c["order_id"].iloc[0])
        taken.add(oid)
        return oid

    return match, taken


def reconcile(conn: Conn, br: Broker) -> tuple[list[Difference], int]:
    """Every difference between the book and the broker, and how many items matched."""
    for name, df in (("positions", br.positions), ("orders", br.pending), ("history", br.history)):
        bad = df[df["currency"].notna() & (df["currency"] != "GBX")]
        if not bad.empty:  # the book's LSE prices are pence; anything else needs FX first
            raise NotImplementedError(
                f"Trading212 {name} in {sorted(set(bad['currency']))} ({', '.join(bad['t212'])}):"
                " only GBX lines are reconciled"
            )
    od = _book_orders(conn)
    by_id = od.set_index("order_id")
    match, taken = _matcher(od, br)
    diffs: list[Difference] = []
    matched = 0
    start = pd.Timestamp(od["created_at"].min()) if not od.empty else None

    def since(ts: Any) -> bool:
        return start is None or (ts is not None and not pd.isna(ts) and pd.Timestamp(ts) >= start)

    def row(oid: int) -> dict[str, Any]:
        return {str(k): v for k, v in by_id.loc[oid].items()}

    # fills and ended orders in the broker's history
    hist = br.history[[since(t) for t in br.history["created_at"]]] if not od.empty else None
    for o in hist.to_dict("records") if hist is not None else []:
        tk = br.yahoo(str(o["t212"]))
        oid = match(o)
        if o["fill_qty"] > 0:
            when = pd.Timestamp(o["filled_at"]).isoformat()
            if oid is None:
                diffs.append(
                    Difference(
                        "unknown_fill",
                        tk,
                        f"Trading212 shows a {_side(o['side'])} of {o['fill_qty']:g} at"
                        f" {_px(o['fill_price'])} on {when[:10]} (order {o['id']}) that the book"
                        " has no order for.",
                    )
                )
                continue
            b = row(oid)
            pid = int(b["position_id"])
            missing = round(o["fill_qty"]) - int(b["filled"])
            if missing > 0:
                fees = f" --fees {o['taxes']:.2f}" if o["taxes"] else ""
                diffs.append(
                    Difference(
                        "fill",
                        tk,
                        f"order #{oid} ({b['purpose']} {_side(o['side'])} {int(b['qty'])}) filled"
                        f" {missing} at {_px(o['fill_price'])} on {when[:16]} at Trading212;"
                        " the book doesn't have it.",
                        f"fill {oid} --qty {missing} --price {o['fill_price']:g} --at {when}{fees}",
                        pid,
                        oid,
                        {
                            "qty": missing,
                            "price": float(o["fill_price"]),
                            "at": when,
                            "fees": float(o["taxes"] or 0.0),
                        },
                    )
                )
            elif missing < 0:
                diffs.append(
                    Difference(
                        "shares",
                        tk,
                        f"order #{oid} has {int(b['filled'])} filled in the book but"
                        f" {o['fill_qty']:g} at Trading212.",
                        None,
                        pid,
                        oid,
                    )
                )
            elif not _close(b["fill_price"], o["fill_price"]):
                diffs.append(
                    Difference(
                        "avg_price",
                        tk,
                        f"order #{oid} filled at {_px(b['fill_price'])} in the book but at"
                        f" {_px(o['fill_price'])} at Trading212.",
                        None,
                        pid,
                        oid,
                    )
                )
            else:
                matched += 1
        elif oid is not None:
            b = row(oid)
            if b["status"] in book.LIVE_ORDER and b["status"] != "approved":
                gone = f"order #{oid} ({b['purpose']} {_side(o['side'])} {int(b['qty'])} @ {_px(b['price'])}) is {o['status']} at Trading212"
                if b["purpose"] == "stop":
                    diffs.append(
                        Difference(
                            "order_gone",
                            tk,
                            gone + ": the shares have no stop. Place it again in Trading212.",
                            None,
                            int(b["position_id"]),
                            oid,
                        )
                    )
                else:
                    cmd = "unfilled" if b["purpose"] in book.BUY_PURPOSES else "cancel"
                    diffs.append(
                        Difference(
                            "order_gone",
                            tk,
                            gone + " but still working in the book.",
                            f"{cmd} {oid} --reason '{o['status']} at Trading212'",
                            int(b["position_id"]),
                            oid,
                            {
                                "status": "not_filled" if cmd == "unfilled" else "cancelled",
                                "reason": f"{o['status']} at Trading212",
                            },
                        )
                    )
            else:
                matched += 1

    # orders working at the broker
    for o in br.pending.to_dict("records"):
        tk = br.yahoo(str(o["t212"]))
        oid = match(o, frozenset(book.LIVE_ORDER))
        if oid is None:
            oid = match(o)  # an ended book order still working at the broker
        if oid is None:
            diffs.append(
                Difference(
                    "unknown_order",
                    tk,
                    f"a {_side(o['side'])} {o['type']} order of {o['qty']:g} @ {_px(o['price'])}"
                    f" (Trading212 order {o['id']}) is working at Trading212 but not in the book:"
                    " cancel it there, or tell me what it is.",
                )
            )
            continue
        b = row(oid)
        pid = int(b["position_id"])
        terms = round(o["qty"]) != int(b["qty"]) or not _close(b["price"], o["price"])
        if b["status"] in ENDED:
            diffs.append(
                Difference(
                    "ended_order",
                    tk,
                    f"order #{oid} is {b['status']} in the book but still working at"
                    f" Trading212 ({_side(o['side'])} {o['qty']:g} @ {_px(o['price'])}):"
                    " cancel it there.",
                    None,
                    pid,
                    oid,
                )
            )
        elif terms:
            diffs.append(
                Difference(
                    "order_terms",
                    tk,
                    f"order #{oid} is {int(b['qty'])} @ {_px(b['price'])} in the book but"
                    f" {o['qty']:g} @ {_px(o['price'])} at Trading212: amend it there.",
                    None,
                    pid,
                    oid,
                )
            )
        elif b["status"] == "approved":
            diffs.append(
                Difference(
                    "placed",
                    tk,
                    f"order #{oid} is working at Trading212 but the book still waits for you to"
                    " place it.",
                    f"placed {oid} --ref {o['id']}",
                    pid,
                    oid,
                    {"ref": str(o["id"])},
                )
            )
        else:
            matched += 1

    # placed in the book, nowhere at the broker
    for bo in od[od["status"].isin(["placed", "part_filled"])].to_dict("records"):
        if int(bo["order_id"]) in taken:
            continue
        diffs.append(
            Difference(
                "order_gone",
                str(bo["ticker"]),
                f"order #{bo['order_id']} ({bo['purpose']} {_side(bo['side'])} {int(bo['qty'])} @"
                f" {_px(bo['price'])}) is placed in the book but Trading212 has no such order:"
                " check it is still working there.",
                None,
                int(bo["position_id"]),
                int(bo["order_id"]),
            )
        )

    # holdings
    filled_tk = {d.ticker for d in diffs if d.kind == "fill"}
    held: dict[str, tuple[int, float, int]] = {}
    for pid in book.positions(conn, status="open")["position_id"]:
        st = book.position_state(conn, int(pid))
        held[st.ticker] = (st.shares, st.avg_cost, int(pid))
    at_broker = {br.yahoo(str(p["t212"])): p for p in br.positions.to_dict("records")}
    for tk in sorted(set(held) | set(at_broker)):
        if tk in filled_tk:
            continue  # the missing fill explains it
        mine, theirs = held.get(tk), at_broker.get(tk)
        if theirs is None:
            assert mine is not None
            diffs.append(
                Difference(
                    "position_missing",
                    tk,
                    f"the book holds {mine[0]} shares but Trading212 holds none: if the stop sold,"
                    " tell me its fill.",
                    None,
                    mine[2],
                )
            )
        elif mine is None:
            diffs.append(
                Difference(
                    "unknown_position",
                    tk,
                    f"Trading212 holds {theirs['qty']:g} shares (average {_px(theirs['avg_price'])})"
                    " that the book has no open position for.",
                )
            )
        elif round(theirs["qty"]) != mine[0]:
            diffs.append(
                Difference(
                    "shares",
                    tk,
                    f"the book holds {mine[0]} shares, Trading212 {theirs['qty']:g}.",
                    None,
                    mine[2],
                )
            )
        elif not _close(mine[1], theirs["avg_price"]):
            diffs.append(
                Difference(
                    "avg_price",
                    tk,
                    f"average cost {_px(mine[1])} in the book, {_px(theirs['avg_price'])} at"
                    " Trading212.",
                    None,
                    mine[2],
                )
            )
        else:
            matched += 1

    # cash: dividends, deposits and withdrawals
    cash = book.cash(conn)
    divs = cash[cash["kind"] == "dividend"]
    pos = book.positions(conn).set_index("position_id")["ticker"]
    booked = [
        (str(pos.get(int(p), "")) if pd.notna(p) else "", book.to_account(a, c))
        for p, a, c in zip(divs["position_id"], divs["amount"], divs["currency"], strict=True)
    ]
    for d in br.dividends.to_dict("records"):
        if not since(d["paid_at"]):
            continue
        tk = br.yahoo(str(d["t212"]))
        hit = next((x for x in booked if x[0] == tk and abs(x[1] - d["amount"]) <= CASH_TOL), None)
        if hit:
            booked.remove(hit)
            matched += 1
            continue
        day = pd.Timestamp(d["paid_at"]).strftime("%Y-%m-%d")
        diffs.append(
            Difference(
                "dividend",
                tk,
                f"Trading212 paid a dividend of £{d['amount']:,.2f} on {day} that the book"
                " doesn't have (use the ex-date for --at if you know it).",
                f"cash dividend {tk} {d['amount']:.2f} --at {day}",
                data={"amount": float(d["amount"]), "at": day},
            )
        )
    # deposits and withdrawals: the net flow, so a correction in the book (a deposit, its
    # withdrawal, the deposit again) is not a difference
    flows = br.transactions[br.transactions["type"].isin(["deposit", "withdrawal", "withdraw"])]
    their_net = float(
        sum(
            a if k == "deposit" else -a for k, a in zip(flows["type"], flows["amount"], strict=True)
        )
    )
    book_net = float(
        sum(
            book.to_account(a, c) * (1 if k == "deposit" else -1)
            for k, a, c in zip(cash["kind"], cash["amount"], cash["currency"], strict=True)
            if k in ("deposit", "withdrawal")
        )
    )
    gap = their_net - book_net
    if abs(gap) <= CASH_TOL:
        matched += 1
    else:
        kind = "deposit" if gap > 0 else "withdraw"
        diffs.append(
            Difference(
                "cash",
                "cash",
                f"net deposits are £{their_net:,.2f} at Trading212 but £{book_net:,.2f} in the book.",
                f"cash {kind} {abs(gap):.2f}",
            )
        )
    return diffs, matched


def _book(conn: Conn, d: Difference) -> None:
    """Book one absorbable difference as the broker (see :func:`absorb`)."""
    x, oid = d.data, d.order_id or 0
    if d.kind == "fill":
        book.fill(conn, oid, x["qty"], x["price"], x["at"], fees=x["fees"], actor=ACTOR)
    elif d.kind == "placed":
        book.place_order(conn, oid, x["ref"], actor=ACTOR)
    elif d.kind == "order_gone":
        book.end_order(conn, oid, x["status"], x["reason"], actor=ACTOR)
    elif d.kind == "dividend":
        book.record_dividend(conn, d.ticker, x["amount"], x["at"], note=f"{d.ticker} dividend")
    else:
        raise ValueError(f"not absorbable: {d.kind}")


def absorb(conn: Conn, br: Broker) -> tuple[list[Difference], list[Difference], int]:
    """Book what the broker plainly shows, then reconcile again: ``(booked, left, matched)``.

    Absorbed (actor ``broker``): fills on a book order (a stop that sold closes the position, an
    entry that filled opens it and drafts its stop), orders the owner placed but didn't report
    (with their Trading212 id), orders that ended at the broker, and dividends paid. Everything
    else (an unknown fill or order, a quantity or price mismatch, a missing stop, net deposits)
    is left for the owner. Repeats until nothing more is absorbable (placing an order can let
    its fill match), so the book ends as close to the broker as it can get on its own.
    """
    booked: list[Difference] = []
    for _ in range(5):  # converges in two passes; the bound only guards a loop
        diffs, matched = reconcile(conn, br)
        todo = [d for d in diffs if d.data]
        if not todo:
            return booked, diffs, matched
        for d in todo:
            _book(conn, d)
            booked.append(d)
    diffs, matched = reconcile(conn, br)
    return booked, diffs, matched


def booked_text(d: Difference) -> str:
    """What :func:`absorb` booked for ``d``, in plain words."""
    x = d.data
    if d.kind == "fill":
        return f"order #{d.order_id} filled {x['qty']} at {_px(x['price'])} on {x['at'][:10]}"
    if d.kind == "placed":
        return f"order #{d.order_id} placed (Trading212 order {x['ref']})"
    if d.kind == "order_gone":
        return f"order #{d.order_id} {x['status'].replace('_', ' ')} ({x['reason']})"
    return f"dividend £{x['amount']:,.2f} paid on {x['at']}"


def render(diffs: list[Difference], matched: int, booked: list[Difference] | None = None) -> str:
    """Plain-text report for ``book.py reconcile``."""
    head = [f"Booked from Trading212: {d.ticker}: {booked_text(d)}" for d in booked or []]
    if not diffs:
        return "\n".join([*head, f"Book and Trading212 agree ({matched} items matched)."])
    lines = [
        *head,
        f"{len(diffs)} difference(s) between the book and Trading212 ({matched} matched):",
    ]
    for d in diffs:
        lines.append(f"- {d.ticker}: {d.text}")
        if d.command:
            lines.append(f"    uv run python scripts/book.py {d.command}")
    return "\n".join(lines)
