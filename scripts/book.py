"""Book trades by hand: record decisions, orders and fills without writing SQL.

uv run python scripts/book.py new JET2.L --entry 1520 --stop 1400 --qty 100 [--type limit|stop|market]
                                 [--target 1800] [--valid-days 7] [--horizon 16] [--thesis TEXT]
uv run python scripts/book.py list [--status draft|approved|open|closed|rejected|cancelled]
uv run python scripts/book.py show POSITION_ID
uv run python scripts/book.py approve POSITION_ID [--reason TEXT]
uv run python scripts/book.py amend POSITION_ID [--qty N] [--price P] [--stop P] [--reason TEXT]
uv run python scripts/book.py reject POSITION_ID --reason TEXT
uv run python scripts/book.py tickets                       # approved, not yet placed orders
uv run python scripts/book.py placed ORDER_ID [ORDER_ID ...] [--ref BROKER_REF] [--at ISO]
uv run python scripts/book.py fill ORDER_ID --qty N --price P [--at ISO] [--commission X] [--fees X]
uv run python scripts/book.py unfilled ORDER_ID [--reason TEXT]   # entry never filled
uv run python scripts/book.py cancel ORDER_ID [--reason TEXT]
uv run python scripts/book.py expire [--as-of DATE]               # entry orders past valid_until
uv run python scripts/book.py show POSITION_ID --price P          # adds R so far at price P
uv run python scripts/book.py cash deposit|withdraw AMOUNT [--at ISO] [--note TEXT]   # GBP
uv run python scripts/book.py cash dividend TICKER AMOUNT [--at ISO] [--position ID]   # GBP received
uv run python scripts/book.py split POSITION_ID --ratio NEW:OLD [--at ISO]      # split / consolidation
uv run python scripts/book.py corporate-close POSITION_ID --price P --reason takeover|delisting [--at ISO]
uv run python scripts/book.py revisions [--status proposed|accepted|declined|undone|all]   # plan changes
uv run python scripts/book.py accept REV_ID          # drafts the orders the revision implies
uv run python scripts/book.py decline REV_ID --reason TEXT
uv run python scripts/book.py undo REV_ID --reason TEXT   # reverse an applied (agent) change
uv run python scripts/book.py reconcile [--book]      # book vs Trading212 (--book: take in fills etc.)
uv run python scripts/book.py stats [--since DATE]   # drafted / approved / amended / rejected by deciding stage
uv run python scripts/book.py exit POSITION_ID --reason stop|trailing_stop|target|thesis_broken|time|discretionary|corporate_action
                                 [--type market|limit] [--price P] [--qty N]
uv run python scripts/book.py swap EXIT_ORDER_ID      # market hours: the stop off for the exit

A first fill on an entry order opens the position and drafts its protective stop order
(approved, so it appears in `tickets` at once). One order per unit of stock (ADR 0031): the sells
working on a holding never add up to more than it holds, so a buy (entry, add) works beside the
stop, a part fill on a buy cancels the rest of it, and an exit is sent only once the stop is
swapped off it (`swap`, in market hours; a partial cuts the stop to the shares left). An order
that may be at the broker is `cancelling` until it is confirmed gone (`cancel`). A stop or
exit fill that brings the shares to zero closes the position (exit reason, P&L, R-multiple,
holding weeks) and cancels its remaining orders; `show` adds MFE / MAE from daily prices.
Dividends recorded against a position count in its net P&L; a split scales the shares, the
average cost and every open order from its date; a corporate close is a synthetic exit fill of
every share at the offer (or 0) with reason `corporate_action`.

Every command is one transaction in the results DB (Turso when configured, else data/solara.db;
--db PATH forces a local file). State changes are new event rows, never updates
(solaraalpha.portfolio.book). An illegal transition prints one line and exits 2.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha.data.clean import check_prices, clean_prices
from solaraalpha.data.prices import fetch_daily
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
    if st.status == "closed":
        print("\nClose summary:")
        print(
            table(
                pd.DataFrame([asdict(_summary(conn, a.position_id, p["ticker"]))]).T.reset_index(),
                ["index", 0],
            )
        )


def _summary(conn: Conn, position_id: int, ticker: str) -> book.CloseSummary:
    """Close summary with MFE / MAE from fresh daily prices (checked and cleaned); without
    prices if the fetch fails."""
    daily = None
    try:
        raw = fetch_daily(ticker)
        daily = clean_prices(raw)
        issues = check_prices(raw)
        if not issues.empty:
            print(f"  ({len(issues)} price data-quality issues; see scripts/stock_prices.py)")
    except Exception as e:
        print(f"  (excursions unavailable: {e})")
    return book.close_summary(conn, position_id, daily)


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
    status = out["order_status"]
    if status in ("cancelled", "cancelling"):  # a part fill on a buy: the rest is cancelled
        status = "part filled, the rest cancelled (the shares held get their stop)"
    msg = f"order {a.order_id} {status}; position holds {out['shares']} shares"
    if out["opened"]:
        msg += "; position opened"
    if out["stop_order_id"]:
        msg += f"; protective stop order {out['stop_order_id']} approved (see `tickets`)"
    print(msg)
    if out["closed"] is not None:
        c = out["closed"]
        print(
            f"position closed ({c.exit_reason}): net £{c.net_pnl:,.2f}"
            f"  {c.r_multiple:+.2f}R  {c.holding_weeks:.1f} weeks; remaining orders cancelled"
        )


@command("cash")
def _cash(conn: Conn, a: argparse.Namespace) -> None:
    if a.kind == "dividend":
        if len(a.args) != 2:
            raise ValueError("cash dividend takes TICKER AMOUNT")
        ticker, amount = a.args[0], float(a.args[1])
        pid = book.record_dividend(conn, ticker, amount, a.at, a.position, a.note or "")
        where = f"position {pid}" if pid is not None else "no open position (cash only)"
        print(f"dividend £{amount:,.2f} from {ticker} recorded against {where}; ", end="")
        print(f"cash balance £{book.cash_balance(conn):,.2f}")
        return
    if len(a.args) != 1:
        raise ValueError(f"cash {a.kind} takes AMOUNT")
    amount = float(a.args[0])
    kind = {"deposit": "deposit", "withdraw": "withdrawal"}[a.kind]
    book.cash_event(conn, kind, amount, "GBP", note=a.note or "", at=a.at)
    print(f"{kind} £{amount:,.2f} recorded; cash balance £{book.cash_balance(conn):,.2f}")


@command("split")
def _split(conn: Conn, a: argparse.Namespace) -> None:
    out = book.split(conn, a.position_id, a.ratio, a.at, reason=a.reason or "")
    ccy = str(book.positions(conn).set_index("position_id").loc[a.position_id, "currency"])
    print(
        f"split {a.ratio} recorded on position {a.position_id}: now {out['shares']} shares"
        f" at avg cost {book.fmt_price(out['avg_cost'], ccy)}"
    )
    for old, new in out["orders"].items():
        print(f"  order {old} replaced by {new} at the adjusted qty / price (see `tickets`)")


@command("corporate-close")
def _corporate_close(conn: Conn, a: argparse.Namespace) -> None:
    c = book.corporate_close(conn, a.position_id, a.price, a.reason, a.at)
    print(
        f"position {a.position_id} closed ({a.reason}): net £{c.net_pnl:,.2f}"
        f"  {c.r_multiple:+.2f}R  {c.holding_weeks:.1f} weeks; remaining orders cancelled"
    )


@command("reconcile")
def _reconcile(conn: Conn, args: argparse.Namespace) -> None:
    from solaraalpha.data import trading212
    from solaraalpha.portfolio import reconcile

    if not trading212.configured():
        sys.exit("Trading212 keys not set (SOLARA_T212_KEY / SOLARA_T212_SECRET)")
    br = reconcile.fetch_broker()
    if args.book:  # what the daily job does: book the plain differences, report the rest
        booked, diffs, matched = reconcile.absorb(conn, br)
        print(reconcile.render(diffs, matched, booked))
    else:
        print(reconcile.render(*reconcile.reconcile(conn, br)))


@command("stats")
def _stats(conn: Conn, a: argparse.Namespace) -> None:
    from solaraalpha.store import query

    pos = book.positions(conn)
    if a.since:
        pos = pos[pos["created_at"] >= a.since]
    ev = book.all_position_events(conn)
    ev = ev[ev["position_id"].isin(set(pos["position_id"]))]
    labels = query("SELECT run_id, ticker, stage FROM labels", conn)
    df = book.approval_stats(pos, ev, labels)
    print(f"{len(pos)} positions" + (f" since {a.since}" if a.since else ""))
    if df.empty:
        print("  (none)")
        return
    out = df.copy()
    for col in ("avg_qty_change_pct", "avg_price_change_pct"):
        out[col] = out[col].map(lambda v: "-" if pd.isna(v) else f"{v:.1%}")
    out["rejection_reasons"] = out["rejection_reasons"].replace("", "-")
    print(table(out))
    stale = book.stale_orders(conn)
    print(f"\nstale orders (past validity, no fill): {len(stale)}")
    for o in stale.to_dict("records"):
        print(
            f"  order {o['order_id']} {o['ticker']} {o['purpose']} {o['qty']} ({o['status']}, valid until {o['valid_until']})"
        )


@command("revisions")
def _revisions(conn: Conn, a: argparse.Namespace) -> None:
    rv = book.revisions(conn, status=None if a.status == "all" else a.status)
    if rv.empty:
        print("  (none)")
        return
    rv["at"] = rv["at"].str[:16]
    rv["old"] = rv["old"].map(_short)
    rv["new"] = rv["new"].map(_short)
    rv["reason"] = rv["reason"].str[:60]
    print(
        table(
            rv,
            [
                "revision_id",
                "position_id",
                "at",
                "actor",
                "action",
                "old",
                "new",
                "reason",
                "status",
                "decided_by",
            ],
        )
    )


@command("accept")
def _accept(conn: Conn, a: argparse.Namespace) -> None:
    done = book.accept_revision(conn, a.revision_id)
    extra = ", ".join(f"{k} {v}" for k, v in done.items() if k not in ("revision_id", "action"))
    print(
        f"revision {a.revision_id} accepted ({done['action']})"
        + (f": {extra}; see `tickets`" if extra else "")
    )


@command("decline")
def _decline(conn: Conn, a: argparse.Namespace) -> None:
    book.decline_revision(conn, a.revision_id, a.reason)
    print(f"revision {a.revision_id} declined")


@command("undo")
def _undo(conn: Conn, a: argparse.Namespace) -> None:
    done = book.undo_revision(conn, a.revision_id, a.reason)
    extra = ", ".join(f"{k} {v}" for k, v in done.items() if k not in ("revision_id", "action"))
    print(
        f"revision {a.revision_id} undone ({done['action']})"
        + (f": {extra}; see `tickets`" if extra else "")
    )


@command("exit")
def _exit(conn: Conn, a: argparse.Namespace) -> None:
    oid = book.exit_position(conn, a.position_id, a.reason, a.type, a.price, a.qty)
    print(
        f"exit order {oid} approved ({a.reason}); in market hours `swap {oid}` takes the stop"
        f" off it, then place it and `fill {oid} ...`"
    )


@command("swap")
def _swap(conn: Conn, a: argparse.Namespace) -> None:
    done = book.swap_for_exit(conn, a.order_id)
    if done["cancelled"] is None:
        print(f"no live stop on exit order {a.order_id}'s position: place the exit")
        return
    msg = f"stop order {done['cancelled']} off for exit order {a.order_id}"
    if done["stop_order_id"]:
        msg += f"; stop order {done['stop_order_id']} on the shares left (see `tickets`)"
    print(msg + ". Cancel it in Trading212, then place the new orders.")


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
    for oid in a.order_id:
        book.place_order(conn, oid, a.ref, a.at)
        print(f"order {oid} placed" + (f" ({a.ref})" if a.ref else ""))


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

    pl = sub.add_parser("placed", help="mark orders as placed at the broker")
    pl.add_argument("order_id", type=int, nargs="+")
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

    ca = sub.add_parser("cash", help="record a deposit, withdrawal or dividend (GBP)")
    ca.add_argument("kind", choices=("deposit", "withdraw", "dividend"))
    ca.add_argument("args", nargs="+", metavar="[TICKER] AMOUNT")
    ca.add_argument("--at", help="ISO time (default now); for a dividend, the ex-date")
    ca.add_argument("--note", default="")
    ca.add_argument("--position", type=int, help="dividend: the position (default: open at --at)")

    sp = sub.add_parser("split", help="record a share split or consolidation on a position")
    sp.add_argument("position_id", type=int)
    sp.add_argument(
        "--ratio", required=True, help="NEW:OLD, e.g. 2:1 (split) or 1:10 (consolidation)"
    )
    sp.add_argument("--at", help="ISO time (default now)")
    sp.add_argument("--reason", default="")

    cc = sub.add_parser("corporate-close", help="close a position taken over or delisted")
    cc.add_argument("position_id", type=int)
    cc.add_argument(
        "--price", type=float, required=True, help="per share, quote currency (0 if worthless)"
    )
    cc.add_argument("--reason", required=True, choices=book.CORPORATE_CLOSES)
    cc.add_argument("--at", help="ISO time (default now)")

    rc = sub.add_parser("reconcile", help="compare the book with Trading212 (ADR 0030)")
    rc.add_argument(
        "--book",
        action="store_true",
        help="also book the fills, placed / ended orders and dividends Trading212 shows",
    )

    stt = sub.add_parser(
        "stats", help="approval statistics: drafted / approved / amended / rejected"
    )
    stt.add_argument("--since", help="positions created on or after this date (YYYY-MM-DD)")

    rv = sub.add_parser("revisions", help="plan revisions proposed by the monitor")
    rv.add_argument(
        "--status",
        choices=("proposed", "accepted", "declined", "undone", "all"),
        default="proposed",
    )
    ac = sub.add_parser("accept", help="accept a proposed revision and draft its orders")
    ac.add_argument("revision_id", type=int)
    dc = sub.add_parser("decline", help="decline a proposed revision")
    dc.add_argument("revision_id", type=int)
    dc.add_argument("--reason", required=True)
    ud = sub.add_parser("undo", help="reverse an accepted (e.g. auto-applied agent) revision")
    ud.add_argument("revision_id", type=int)
    ud.add_argument("--reason", required=True)

    xo = sub.add_parser("exit", help="draft an exit order with a tagged reason")
    xo.add_argument("position_id", type=int)
    xo.add_argument("--reason", required=True, choices=book.EXIT_REASONS)
    xo.add_argument("--type", choices=("market", "limit"), default="market")
    xo.add_argument("--price", type=float, help="limit price (quote currency)")
    xo.add_argument("--qty", type=int, help="default: all shares")
    sw = sub.add_parser("swap", help="take the stop off an approved exit (market hours)")
    sw.add_argument("order_id", type=int)

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
