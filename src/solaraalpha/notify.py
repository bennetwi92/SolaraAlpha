"""Owner notifications: say something only when something needs the owner (ADR 0021, 0025).

Plain words first. :func:`daily_message` opens with what to do in Trading212, stock by stock
in the order to do it (:mod:`solaraalpha.portfolio.broker`: every approved order not yet placed;
nothing waits for an approval), then what to check and report back (a stop that should have
sold, an order past its date, a dividend), price alerts to change, what happened that needs
nothing, the undo commands for the automatic changes, and errors. :func:`weekly_message` is the
week's trades as the same steps, the positions held and the money in a few lines, with a link to
the full pack. Channels are one ``send(markdown)`` function each; only ``stdout`` and the GitHub
``inbox`` issue (posted by the workflow with ``gh``) exist. Story map S7.8, S12.7.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from collections.abc import Callable

import pandas as pd

from solaraalpha import store
from solaraalpha.portfolio import book, broker
from solaraalpha.portfolio.triggers import OWNER_KINDS
from solaraalpha.store import Conn, query

INBOX_LABEL = "inbox"
INBOX_TITLE = "Solara inbox"
BROKER = broker.BROKER
ONE_ORDER = "Do each stock's steps in order: Trading212 holds one order per stock."
HOW_TO_REPLY = (
    "Tell Claude what you did in plain words (“placed 15”, “15 filled 120 at 1,541p”,"
    " “15 didn't fill”), or run the same as `book.py` commands (Actions → Book)."
)


def _day(iso: str) -> str:
    return pd.Timestamp(iso).strftime("%a %-d %b %Y")


def _live_stop(conn: Conn, position_id: int | None) -> int | None:
    if position_id is None:
        return None
    od = book.orders(conn, position_id)
    live = od[(od["purpose"] == "stop") & od["status"].isin(book.LIVE_ORDER)]
    return int(live["order_id"].iloc[-1]) if not live.empty else None


def owner_items(conn: Conn, run_id: int) -> tuple[list[str], list[str]]:
    """The run's owner-action triggers as ``(checks, info)``: what to check and report back
    (with the command), and what needs nothing (for information)."""
    trig = store.load_triggers(run_id, conn=conn)
    pos = book.positions(conn).set_index("position_id")
    checks: list[str] = []
    info: list[str] = []
    for t in trig.to_dict("records") if not trig.empty else []:
        kind, d = str(t["kind"]), t["detail"]
        if kind not in OWNER_KINDS:
            continue
        pid = int(t["position_id"]) if pd.notna(t["position_id"]) else None
        oid = int(t["order_id"]) if pd.notna(t["order_id"]) else None
        ccy = str(pos["currency"].get(pid, "GBp")) if pid is not None else "GBp"
        tk = f"**{t['ticker']}**"
        stop_id = _live_stop(conn, pid)
        fill_stop = (
            f"`fill {stop_id} --qty N --price P`"
            if stop_id
            else "`fill STOP_ORDER --qty N --price P`"
        )
        if kind == "stop_close":
            checks.append(
                f"{tk} closed at {broker.price(d['close'], ccy)}, below your stop {broker.price(d['stop'], ccy)}. The stop order"
                f" should have sold it in {BROKER}: tell me the fill, {fill_stop}. If it didn't"
                " sell, sell it now and tell me the same."
            )
        elif kind == "stop_touched":
            checks.append(
                f"{tk} traded through your stop {broker.price(d['stop'], ccy)} today (closed {broker.price(d['close'], ccy)})."
                f" Check {BROKER}: if the stop sold, tell me {fill_stop}; if not, nothing to do."
            )
        elif kind == "target_hit":
            info.append(
                f"{tk} reached its target {broker.price(d['target'], ccy)}. Nothing to do: the stop and the"
                " daily review manage the exit."
            )
        elif kind == "entry_break":
            checks.append(
                f"{tk} closed at {broker.price(d['close'], ccy)}, above {broker.price(d['trigger'], ccy)}: place your buy order"
                f" #{oid} now, then tell me `placed {oid}`."
            )
        elif kind == "order_stale":
            what = ""
            if oid is not None:
                od = book.orders(conn).set_index("order_id")
                if oid in od.index:
                    what = f" ({broker._bold(dict(od.loc[oid]), ccy)})"
            checks.append(
                f"{tk} order #{oid}{what} passed its end date {d['valid_until']} with no fill"
                f" from you. If it filled, tell me `fill {oid} --qty N --price P`; if not, cancel"
                f" it in {BROKER} and tell me `unfilled {oid}`."
            )
        elif kind == "event_due":
            policy = str(d["policy"])
            then = (
                "nothing happens automatically (set `event_policy` in config/portfolio.toml to"
                " hold, trim or exit to decide it in advance)"
                if policy == "flag"
                else f"the weekly review has already arranged the {policy}"
            )
            info.append(
                f"{tk} has a scheduled event on {d['event']} ({d['sessions']} trading day(s)"
                f" away): policy **{policy}**, {then}."
            )
        elif kind == "dividend":
            checks.append(
                f"{tk} goes ex-dividend {t['at']}: about £{float(d['amount_gbp']):,.2f} on your"
                f" shares. When it lands in {BROKER}, tell me"
                f" `cash dividend {t['ticker']} {float(d['amount_gbp']):.2f} --at {t['at']}`."
            )
        elif kind == "split":
            checks.append(
                f"{tk} splits {d['ratio']} on {t['at']}: tell me"
                f" `split {pid} --ratio {d['ratio']} --at {t['at']}` and I'll re-size its orders."
            )
        else:
            info.append(f"{tk} {kind}: {json.dumps(d, default=str)}")
    return checks, info


def changes(conn: Conn, since: str) -> tuple[list[str], list[str], list[str]]:
    """Automatic changes (agents, weekly review) decided at or after ``since`` as ``(alerts,
    info, undo)``: price alerts to change, changes that need nothing at the broker (or that the
    book refused), and one undo command per applied change. The broker steps of the applied
    ones are tasks (:func:`broker.tasks`)."""
    rv = book.revisions(conn)
    alerts: list[str] = []
    info: list[str] = []
    undo: list[str] = []
    pos = book.positions(conn).set_index("position_id")
    if not rv.empty:
        rv = rv[
            rv["decided_at"].notna()
            & (rv["decided_at"].astype(str) >= since)
            & rv["actor"].astype(str).map(book.auto_applied)
        ]
    for rec in rv.to_dict("records") if not rv.empty else []:
        r = {str(k): v for k, v in rec.items()}
        pid = int(r["position_id"])
        tk = str(pos["ticker"].get(pid, "?"))
        ccy = str(pos["currency"].get(pid, "GBp"))
        who = broker._who(str(r["actor"]))
        old, new, action = r["old"] or {}, r["new"] or {}, str(r["action"])
        if r["status"] == "declined":
            info.append(
                f"**{tk}**: {who} asked for **{action.replace('_', ' ')}** but the book refused"
                f" it ({str(r['decision_reason']).removeprefix('auto-apply refused: ')}):"
                " nothing changed."
            )
            continue
        if r["status"] != "accepted":
            continue
        undo.append(
            f"- {tk} {action.replace('_', ' ')} ({who}): `book.py undo {r['revision_id']} --reason ...`"
        )
        if action == "revise_target":
            alerts.append(
                f"**{tk}**: move the target alert {broker.price(old.get('target'), ccy)}"
                f" → {broker.price(new.get('target'), ccy)} ({who}: {r['reason']})"
            )
        elif action == "cancel" and not (r["done"] or {}).get("was_placed"):
            info.append(
                f"**{tk}**: {who} cancelled the planned entry ({r['reason']}). It was never"
                f" placed, so nothing to do in {BROKER}; delete its price alerts."
            )
    closed = book.all_position_events(conn, since)
    for e in closed[closed["kind"] == "closed"].to_dict("records") if not closed.empty else []:
        alerts.append(
            f"**{pos['ticker'].get(int(e['position_id']), '?')}**: delete its price alerts (position closed)"
        )
    return alerts, info, undo


def errors(conn: Conn, run_id: int) -> list[str]:
    """ERROR labels and agent cards recorded under the run."""
    lab = query(
        "SELECT ticker, stage, reason FROM labels WHERE run_id = ? AND label = 'ERROR'",
        conn,
        [run_id],
    )
    cards = query(
        "SELECT x.ticker, x.model, y.text AS reason FROM results x LEFT JOIN results y"
        " ON y.run_id = x.run_id AND y.ticker = x.ticker AND y.model = x.model AND y.field = 'reasons'"
        " WHERE x.run_id = ? AND x.field = 'label' AND x.text = 'ERROR'",
        conn,
        [run_id],
    )
    out = [f"- {r['ticker']} `{r['stage']}`: {r['reason']}" for r in lab.to_dict("records")]
    seen = {(r["ticker"], r["stage"]) for r in lab.to_dict("records")}
    out += [
        f"- {r['ticker']} `{r['model']}`: {r['reason'] or ''}"
        for r in cards.to_dict("records")
        if (r["ticker"], r["model"]) not in seen
    ]
    return out


def _todo(tasks: list[broker.Task]) -> list[str]:
    if not tasks:
        return []
    n = len(tasks)
    lines = [
        f"**{n} {'stock needs' if n == 1 else 'stocks need'} you in {BROKER}.** {ONE_ORDER}",
        "",
    ]
    for i, t in enumerate(tasks, 1):
        lines += broker.render_task(i, t)
    return lines


def _section(title: str, items: list[str]) -> list[str]:
    return [f"### {title}", "", *[i if i.startswith("- ") else f"- {i}" for i in items], ""]


def _footer(undo: list[str]) -> list[str]:
    lines = ["---", "", HOW_TO_REPLY, ""]
    if undo:
        lines += [
            "<details><summary>Undo an automatic change</summary>",
            "",
            *undo,
            "",
            "</details>",
            "",
        ]
    return lines


def daily_message(
    conn: Conn, run_id: int, only: tuple[str, ...] = ("actions", "revisions", "errors")
) -> str | None:
    """Markdown for the daily inbox, or None when nothing needs the owner (or no such run).
    ``actions``: every order still to place and the day's owner triggers; ``revisions``: the
    automatic changes since the run started (their orders, alerts, undo); ``errors``."""
    try:
        started = str(store.run_info(run_id, conn=conn)["started_at"])
    except ValueError:
        return None
    todo = broker.tasks(conn)
    if "actions" not in only:
        todo = [t for t in todo if "revisions" in only and t.at >= started]
    if "revisions" in only:
        todo += broker.cancels_since(conn, started)
    checks, info = owner_items(conn, run_id) if "actions" in only else ([], [])
    alerts, more, undo = changes(conn, started) if "revisions" in only else ([], [], [])
    errs = errors(conn, run_id) if "errors" in only else []
    if not (todo or checks or info or alerts or more or errs):
        return None
    parts = [f"## Solara — {_day(started)}", ""]
    parts += _todo(todo) or [f"Nothing to place in {BROKER} today.", ""]
    if checks:
        parts += _section("Check and tell me", checks)
    if alerts:
        parts += _section("Price alerts", alerts)
    if info or more:
        parts += _section("For information (nothing to do)", info + more)
    if errs:
        parts += _section("Errors", errs)
    parts += _footer(undo)
    return "\n".join(parts).rstrip() + "\n"


def _money(v: float) -> str:
    return f"£{v:,.0f}"


def weekly_message(conn: Conn, run_id: int, summary_url: str | None = None) -> str | None:
    """The week in the inbox (None when the run has no pack): the new trades and anything
    else still to place, as steps; the positions held; the money; what was not traded; errors."""
    from solaraalpha.portfolio.weekly_pack import build_pack

    try:
        pack = build_pack(conn, run_id)
    except ValueError:
        return None
    eq = pack.plan.equity
    assert eq is not None
    new = [p for p in pack.new_positions if p.get("status") in ("approved", "open")]
    parts = [f"# Solara — week of {_day(pack.started_at)}", ""]
    if pack.rules.warning:
        parts += [f"> {pack.rules.warning}", ""]
    n = len(new)
    parts += [
        (
            f"**{n} new {'trade' if n == 1 else 'trades'} this week.**"
            if n
            else "**No new trades this week.**"
        ),
        "",
    ]
    todo = broker.tasks(conn) + broker.cancels_since(conn, pack.started_at)
    parts += _todo(todo) or [f"Nothing to place in {BROKER}.", ""]

    held = []
    for row in pack.open_positions:
        st: book.PositionState = row["state"]
        r = row.get("r")
        r_s = f"{r:+.1f}R" if isinstance(r, float) and not math.isnan(r) else "R n/a"
        line = (
            f"**{st.ticker}** {st.shares:,} shares · cost {broker.price(st.avg_cost, st.currency)}"
            f" · last {broker.price(row.get('last'), st.currency)} · {r_s}"
            f" · stop {broker.price(st.stop, st.currency)}"
        )
        if row.get("next_event"):
            line += f" · next event {str(row['next_event'])[:10]}"
        held.append(line)
    parts += _section("Your positions", held or ["none"])

    money = [
        f"Equity {_money(eq.equity)} (cash {_money(eq.cash)}) · {eq.positions} of"
        f" {pack.rules.max_positions} positions · you lose {pack.heat:.1%} of equity if every stop is hit"
    ]
    if pack.breaches:
        money.append("**Limits broken:** " + "; ".join(pack.breaches))
    if pack.plan.report.breaker:
        money.append("**Circuit breaker on: no new entries** (drawdown past the limit)")
    if pack.regime and pack.regime.get("regime") != "calm":
        money.append(f"Market **{pack.regime.get('regime')}**: {pack.regime.get('reason', '')}")
    parts += _section("Money", money)

    skipped = [f"{p.ticker}: {why}" for p, why in pack.plan.report.rejected]
    skipped += [f"{t}: {why}" for t, why in pack.plan.plan_errors]
    if skipped:
        parts += [
            "<details><summary>Not traded this week</summary>",
            "",
            *[f"- {s}" for s in skipped],
            "",
            "</details>",
            "",
        ]
    errs = [f"- {c.ticker}: {c.reasons.get(c.stage, '')}" for c in pack.errors]
    if errs:
        parts += ["### Errors", "", *errs, ""]
    _, _, undo = changes(conn, pack.started_at)
    parts += _footer(undo)
    if summary_url:
        parts += [f"Full pack and pipeline report: {summary_url}", ""]
    return "\n".join(parts).rstrip() + "\n"


def send_stdout(markdown: str) -> None:
    print(markdown)


def send_github_issue(markdown: str) -> None:
    """Comment on the open issue labelled ``inbox`` (created if missing) with ``gh``; needs
    ``GH_TOKEN`` / ``GITHUB_TOKEN`` with ``issues: write`` and ``GH_REPO`` (or a checkout)."""
    env = {**os.environ}
    found = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--label",
            INBOX_LABEL,
            "--state",
            "open",
            "--json",
            "number",
            "--limit",
            "1",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    numbers = json.loads(found.stdout or "[]") if found.returncode == 0 else []
    if numbers:
        number = str(numbers[0]["number"])
    else:
        subprocess.run(
            [
                "gh",
                "label",
                "create",
                INBOX_LABEL,
                "--force",
                "--description",
                "owner notifications",
            ],
            capture_output=True,
            env=env,
        )
        created = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--title",
                INBOX_TITLE,
                "--label",
                INBOX_LABEL,
                "--body",
                "Notifications from the weekly pipeline and the daily check (ADR 0021). Each comment is something that needs the owner.",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        number = created.stdout.strip().rstrip("/").rsplit("/", 1)[-1]
    subprocess.run(["gh", "issue", "comment", number, "--body", markdown], check=True, env=env)


CHANNELS: dict[str, Callable[[str], None]] = {
    "stdout": send_stdout,
    "github-issue": send_github_issue,
}


def send(markdown: str, channel: str = "stdout") -> None:
    try:
        CHANNELS[channel](markdown)
    except KeyError:
        raise ValueError(f"unknown channel {channel!r}; one of {', '.join(CHANNELS)}") from None
