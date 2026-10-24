"""The one door to Trading212: send, cancel and settle the book's orders (ADR 0031).

uv run python scripts/execute.py turn [--agent] [--report FILE] [--agent-file FILE]
uv run python scripts/execute.py send ORDER_ID [--type market|limit] [--price P] --reason TEXT
uv run python scripts/execute.py cancel ORDER_ID --reason TEXT
uv run python scripts/execute.py hold ORDER_ID --until ISO --reason TEXT
uv run python scripts/execute.py note [ORDER_ID] --reason TEXT
uv run python scripts/execute.py error --reason TEXT     # the agent's call failed (workflow)
uv run python scripts/execute.py log [--since ISO]

turn: one execution turn, code only (the 15-minute job): book what Trading212 shows, send
again what it never received, cancel the exits still working at the deadline (15:45 London),
then cancel-confirm-place until nothing changes: stops at once, exits only in market hours
(the stop swapped off them first), entries and adds as booked. --agent: a new non-stop order
waits up to 30 minutes for the execution agent; --agent-file gets `true` when the agent has
something to decide. send / cancel / hold / note are the agent's (actor agent:execute): how and
when, never what; every one needs its --reason and goes through the guards (approved in the
book, long only, per-unit sells, 10% price band, free cash, position cap, circuit breaker).

Orders go out only when SOLARA_T212_TRADING names the account in SOLARA_T212_ENV (demo; live is
the owner's to set). turn also pulls the owner's Telegram replies into the desk thread (ADR
0033). Never wrap these in another transaction: `sending` is written before each
call. --db PATH for a local file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from solaraalpha import desk_chat
from solaraalpha.portfolio import book, execution
from solaraalpha.portfolio.execution import AGENT, LiveClient, Refused


def _report(res: execution.TurnResult) -> str:
    lines = [f"## Execution turn {res.started[:16].replace('T', ' ')} UTC", ""]
    for title, items in (
        ("Booked from Trading212", res.booked),
        ("Done", res.done),
        ("Not sent", res.refused),
        ("Errors", res.errors),
        ("For the execution agent", res.agent),
    ):
        if items:
            lines += [f"### {title}", "", *[f"- {x}" for x in items], ""]
    if len(lines) == 2:
        lines.append("Nothing to do.")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    p.add_argument("--actor", default=AGENT, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    tu = sub.add_parser("turn", help="one execution turn (code only)")
    tu.add_argument("--agent", action="store_true", help="new orders wait for the agent first")
    tu.add_argument("--report", type=Path, help="append the markdown report to this file")
    tu.add_argument("--agent-file", type=Path, help="write true / false: the agent is needed")
    se = sub.add_parser("send", help="send one approved order through the guards")
    se.add_argument("order_id", type=int)
    se.add_argument("--type", choices=("market", "limit"), default=None)
    se.add_argument("--price", type=float, default=None, help="limit price (quote currency)")
    se.add_argument("--reason", required=True)
    ca = sub.add_parser("cancel", help="let an approved order lapse, or cancel a working one")
    ca.add_argument("order_id", type=int)
    ca.add_argument("--reason", required=True)
    ho = sub.add_parser("hold", help="keep an approved order back until a time")
    ho.add_argument("order_id", type=int)
    ho.add_argument("--until", required=True, help="ISO time (UTC unless an offset is given)")
    ho.add_argument("--reason", required=True)
    no = sub.add_parser("note", help="record a decision or a review (no broker call)")
    no.add_argument("order_id", type=int, nargs="?", default=None)
    no.add_argument("--reason", required=True)
    er = sub.add_parser("error", help="record that the agent's call failed")
    er.add_argument("--reason", required=True)
    lg = sub.add_parser("log", help="the executions log")
    lg.add_argument("--since", default=None)
    a = p.parse_args(argv)

    conn = book.connect(a.db)
    try:
        if a.cmd == "turn":
            desk_chat.pull(a.db)  # every 15 minutes in the session (ADR 0033)
            res = execution.turn(conn, LiveClient(conn=conn), agent=a.agent)
            text = _report(res)
            print(text)
            if a.report:
                with a.report.open("a") as f:
                    f.write(text + "\n")
            if a.agent_file:
                a.agent_file.write_text("true\n" if res.agent else "false\n")
        elif a.cmd == "send":
            client = LiveClient(conn=conn)
            now = pd.Timestamp.now(tz="UTC")
            prices = execution.prices_for(client.broker(), execution._quotes(), now)
            out = execution.agent_send(
                conn, client, a.order_id, prices.now, prices, a.actor, a.reason, a.type, a.price
            )
            print(f"{execution.describe(conn, a.order_id, out)}")
        elif a.cmd == "cancel":
            status = execution.cancel(conn, LiveClient(conn=conn), a.order_id, a.reason, a.actor)
            print(f"order {a.order_id} {status}")
        elif a.cmd == "hold":
            until = execution.hold(conn, a.order_id, a.until, a.reason, a.actor)
            print(f"order {a.order_id} held until {until}")
        elif a.cmd == "note":
            execution.record(conn, a.order_id, "note", a.reason, a.actor)
            print("noted")
        elif a.cmd == "error":
            execution.record(conn, None, "error", a.reason, execution.ACTOR, {"agent": True})
            print("recorded")
        else:
            ex = execution.executions(conn, a.since)
            for r in ex.to_dict("records"):
                oid = "" if pd.isna(r["order_id"]) else f" #{int(r['order_id'])}"
                print(f"{r['at'][:19]} {r['actor']}{oid} {r['action']}: {r['reason']}")
        conn.commit()
    except (Refused, ValueError, RuntimeError) as e:
        print(f"error: {e}")
        sys.exit(2)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
