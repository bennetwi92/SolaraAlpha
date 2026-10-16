"""Book trades by hand: record decisions, orders and fills without writing SQL.

uv run python scripts/book.py new JET2.L --entry 1520 --stop 1400 --qty 100 [--type limit|stop|market]
                                 [--target 1800] [--valid-days 7] [--horizon 16] [--thesis TEXT]
uv run python scripts/book.py list [--status draft|approved|open|closed|rejected|cancelled]
uv run python scripts/book.py show POSITION_ID
uv run python scripts/book.py approve POSITION_ID [--reason TEXT]
uv run python scripts/book.py amend POSITION_ID [--qty N] [--price P] [--stop P] [--reason TEXT]
uv run python scripts/book.py reject POSITION_ID --reason TEXT
uv run python scripts/book.py tickets                       # approved, not yet placed orders
uv run python scripts/book.py placed ORDER_ID [--ref BROKER_REF] [--at ISO]
uv run python scripts/book.py fill ORDER_ID --qty N --price P [--at ISO] [--commission X] [--fees X]
uv run python scripts/book.py unfilled ORDER_ID [--reason TEXT]   # entry never filled
uv run python scripts/book.py cancel ORDER_ID [--reason TEXT]
uv run python scripts/book.py expire [--as-of DATE]               # entry orders past valid_until
uv run python scripts/book.py show POSITION_ID --price P          # adds R so far at price P

A first fill on an entry order opens the position and drafts its protective stop order
(approved, so it appears in `tickets` at once); later entry fills re-size that stop.

Every command is one transaction in the results DB (Turso when configured, else data/solara.db;
--db PATH forces a local file). State changes are new event rows, never updates
(solaraalpha.portfolio.book). An illegal transition prints one line and exits 2.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha.portfolio import book
from solaraalpha.portfolio.rules import load_rules
from solaraalpha.store import Conn

Handler = Callable[[Conn, argparse.Namespace], None]
COMMANDS: dict[str, Handler] = {}


def command(name: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        COMMANDS[name] = fn
        return fn

    return register


def table(df: pd.DataFrame, cols: list[str] | None = None) -> str:
    if df.empty:
        return "  (none)"
    df = df[cols] if cols else df
    return "  " + df.to_string(index=False).replace("\n", "\n  ")


def _prices(df: pd.DataFrame, ccy: str) -> pd.DataFrame:
    out = df.copy()
    for col in ("price",):
        if col in out:
            out[col] = [book.fmt_price(p, ccy) for p in out[col]]
    return out


@command("new")
def _new(conn: Conn, a: argparse.Namespace) -> None:
    pid, oid = book.draft_position(
        conn,
        a.ticker,
        None if a.type == "market" else a.entry,
        a.stop,
        a.qty,
        a.type,
        a.target,
        a.valid_days,
        a.horizon,
        a.thesis or "",
    )
    print(f"position {pid} drafted for {a.ticker}: entry order {oid} ({a.type} {a.qty})")


@command("list")
def _list(conn: Conn, a: argparse.Namespace) -> None:
    pos = book.positions(conn, a.status)
    if pos.empty:
        print("  (none)")
        return
    od = book.orders(conn)
    entry = od[od["purpose"] == "entry"].drop_duplicates("position_id", keep="last")
    entry = entry.set_index("position_id")
    rows = []
    for p in pos.to_dict("records"):
        e = entry.loc[p["position_id"]] if p["position_id"] in entry.index else None
        rows.append(
            {
                "id": p["position_id"],
                "ticker": p["ticker"],
                "status": p["status"],
                "entry order": (
                    f"{e['type']} {e['qty']} @ {book.fmt_price(e['price'], p['currency'])}"
                    f" ({e['status']})"
                    if e is not None
                    else "-"
                ),
                "updated": str(p["updated_at"])[:16],
            }
        )
    print(table(pd.DataFrame(rows)))


@command("show")
def _show(conn: Conn, a: argparse.Namespace) -> None:
    pos = book.positions(conn)
    pos = pos[pos["position_id"] == a.position_id]
    if pos.empty:
        raise ValueError(f"no position {a.position_id}")
    p = pos.iloc[0].to_dict()
    ccy = p["currency"]
    print(
        f"Position {p['position_id']}  {p['ticker']}  {p['status']}  horizon {p['horizon_weeks']}w"
        f"  run {p['opened_from_run_id'] or '-'}  created {str(p['created_at'])[:16]}"
    )
    if p["thesis"]:
        print(f"  thesis: {p['thesis']}")
    print("\nEvents:")
    ev = book.position_events(conn, a.position_id)
    ev["at"] = ev["at"].str[:16]
    ev["payload"] = ev["payload"].map(lambda d: _short(d))
    print(table(ev, ["at", "kind", "actor", "reason", "payload"]))
    print("\nOrders:")
    od = _prices(book.orders(conn, a.position_id), ccy)
    print(
        table(
            od,
            [
                "order_id",
                "purpose",
                "side",
                "type",
                "qty",
                "price",
                "valid_until",
                "status",
                "filled",
                "broker_ref",
            ],
        )
    )
    print("\nFills:")
    fl = _prices(book.fills(conn, position_id=a.position_id), ccy)
    if not fl.empty:
        fl["at"] = fl["at"].str[:16]
    print(table(fl, ["fill_id", "order_id", "at", "qty", "price", "commission", "fees"]))
    ec = book.entry_conditions(conn, a.position_id)
    if not ec.empty:
        print("\nEntry conditions:")
        ec["value"] = ec["value"].where(ec["text"].isna(), ec["text"])
        print(table(ec, ["run_id", "model", "field", "value"]))
    tc = book.thesis_conditions(conn, a.position_id)
    if not tc.empty:
        print("\nThesis conditions:")
        print(table(tc, ["condition_id", "kind", "condition", "evidence_id"]))
    print("\nState:")
    st = book.position_state(conn, a.position_id)
    print(f"  shares {st.shares}  avg cost {book.fmt_price(st.avg_cost, ccy)}  ", end="")
    print(f"stop {book.fmt_price(st.stop, ccy) if st.stop else '-'}  ", end="")
    print(
        f"risk at stop £{st.risk_at_stop:,.2f}  realised £{st.realised_pnl:,.2f}  costs £{st.costs:,.2f}"
    )
    if a.price is not None and st.shares:
        unreal = book.to_account((a.price - st.avg_cost) * st.shares * st.side, ccy)
        r = f"{unreal / st.r_unit:+.2f}R" if st.r_unit else "R unknown (no initial stop)"
        print(f"  at {book.fmt_price(a.price, ccy)}: unrealised £{unreal:,.2f}  {r}")


def _short(d: dict[str, Any]) -> str:
    s = ", ".join(f"{k}={v}" for k, v in d.items() if v is not None and k not in ("old", "new"))
    if "old" in d:
        s = f"old={d['old']} new={d['new']}"
    return s[:80]


@command("approve")
def _approve(conn: Conn, a: argparse.Namespace) -> None:
    book.approve_position(conn, a.position_id, a.reason or "")
    print(f"position {a.position_id} approved; orders: see `tickets`")


@command("amend")
def _amend(conn: Conn, a: argparse.Namespace) -> None:
    payload = book.amend_position(conn, a.position_id, a.qty, a.price, a.stop, a.reason or "")
    print(f"position {a.position_id} amended: {payload['old']} -> {payload['new']}")


@command("reject")
def _reject(conn: Conn, a: argparse.Namespace) -> None:
    book.reject_position(conn, a.position_id, a.reason)
    print(f"position {a.position_id} rejected; open orders cancelled")


@command("tickets")
def _tickets(conn: Conn, a: argparse.Namespace) -> None:
    t = book.tickets(conn)
    if t.empty:
        print("  (none)")
        return
    t["side"] = t["side"].map({1: "BUY", -1: "SELL"})
    t["price"] = [book.fmt_price(p, c) for p, c in zip(t["price"], t["currency"], strict=True)]
    print(
        table(t, ["ticker", "side", "type", "qty", "price", "valid_until", "purpose", "order_id"])
    )


@command("fill")
def _fill(conn: Conn, a: argparse.Namespace) -> None:
    out = book.fill(conn, a.order_id, a.qty, a.price, a.at, a.commission, a.fees)
    msg = f"order {a.order_id} {out['order_status']}; position holds {out['shares']} shares"
    if out["opened"]:
        msg += "; position opened"
    if out["stop_order_id"]:
        msg += f"; protective stop order {out['stop_order_id']} approved (see `tickets`)"
    print(msg)


@command("unfilled")
def _unfilled(conn: Conn, a: argparse.Namespace) -> None:
    cancelled = book.end_order(conn, a.order_id, "not_filled", a.reason or "")
    print(f"order {a.order_id} not filled" + ("; position cancelled" if cancelled else ""))


@command("cancel")
def _cancel(conn: Conn, a: argparse.Namespace) -> None:
    cancelled = book.end_order(conn, a.order_id, "cancelled", a.reason or "")
    print(f"order {a.order_id} cancelled" + ("; position cancelled" if cancelled else ""))


@command("expire")
def _expire(conn: Conn, a: argparse.Namespace) -> None:
    ids = book.expire_orders(conn, a.as_of)
    print(f"expired orders: {', '.join(map(str, ids)) or 'none'}")


@command("placed")
def _placed(conn: Conn, a: argparse.Namespace) -> None:
    book.place_order(conn, a.order_id, a.ref, a.at)
    print(f"order {a.order_id} placed" + (f" ({a.ref})" if a.ref else ""))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="draft a position with its entry order")
    n.add_argument("ticker")
    n.add_argument("--entry", type=float, help="entry price (quote currency); omit for market")
    n.add_argument("--stop", type=float, required=True, help="planned protective stop")
    n.add_argument("--qty", type=int, required=True)
    n.add_argument("--type", choices=book.ORDER_TYPES, default="limit")
    n.add_argument("--target", type=float)
    n.add_argument("--valid-days", type=int, help="default: Rules.entry_validity_days")
    n.add_argument("--horizon", type=int, help="weeks; default: Rules.horizon_weeks max")
    n.add_argument("--thesis")

    ls = sub.add_parser("list", help="positions with their current status")
    ls.add_argument("--status", choices=sorted(set(book.POSITION_STATUS.values())))

    sh = sub.add_parser("show", help="one position in full")
    sh.add_argument("position_id", type=int)
    sh.add_argument("--price", type=float, help="last price, to show unrealised P&L and R")

    ap = sub.add_parser("approve")
    ap.add_argument("position_id", type=int)
    ap.add_argument("--reason", default="")

    am = sub.add_parser("amend", help="change entry qty / price / planned stop")
    am.add_argument("position_id", type=int)
    am.add_argument("--qty", type=int)
    am.add_argument("--price", type=float)
    am.add_argument("--stop", type=float)
    am.add_argument("--reason", default="")

    rj = sub.add_parser("reject")
    rj.add_argument("position_id", type=int)
    rj.add_argument("--reason", required=True)

    sub.add_parser("tickets", help="approved, not yet placed orders")

    pl = sub.add_parser("placed", help="mark an order as placed at the broker")
    pl.add_argument("order_id", type=int)
    pl.add_argument("--ref", help="broker reference")
    pl.add_argument("--at", help="ISO time (default now)")

    fi = sub.add_parser("fill", help="record a (part) fill")
    fi.add_argument("order_id", type=int)
    fi.add_argument("--qty", type=int, required=True)
    fi.add_argument("--price", type=float, required=True, help="quote currency (pence for LSE)")
    fi.add_argument("--at", help="ISO time (default now)")
    fi.add_argument("--commission", type=float, default=0.0, help="account currency (GBP)")
    fi.add_argument("--fees", type=float, default=0.0, help="stamp duty etc., GBP")

    for name, help_ in (("unfilled", "an order that never filled"), ("cancel", "cancel an order")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("order_id", type=int)
        c.add_argument("--reason", default="")

    ex = sub.add_parser("expire", help="expire entry orders past their validity")
    ex.add_argument("--as-of", help="date (default today)")

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.cmd == "new" and args.type != "market" and args.entry is None:
        sys.exit("--entry is required unless --type market")
    conn = book.connect(args.db)
    try:
        with conn:
            COMMANDS[args.cmd](conn, args)
    except (ValueError, NotImplementedError) as e:
        print(f"error: {e}")
        sys.exit(2)
    finally:
        conn.close()
    rules = load_rules()
    if rules.warning and args.cmd in ("new", "show"):
        print(rules.warning)


if __name__ == "__main__":
    main()
