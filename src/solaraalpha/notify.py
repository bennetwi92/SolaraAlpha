"""Owner notifications: say something only when something needs the owner (ADR 0021).

:func:`daily_message` has content only when a daily run fired an owner-action trigger, a plan
revision was proposed since the run started, or an agent recorded an ERROR; one line per item
with the command to act on it. :func:`weekly_message` is the weekly pack's headline sections
(new plans, open positions, portfolio, stale orders, errors) with a link to the job summary.
Channels are one ``send(markdown)`` function each; only ``stdout`` and the GitHub ``inbox``
issue (posted by the workflow with ``gh``) exist. Story map S7.8, S12.7.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.triggers import OWNER_KINDS
from solaraalpha.store import Conn, query

WEEKLY_SECTIONS = ("New plans", "Open positions", "Stale orders", "Portfolio", "Errors")
INBOX_LABEL = "inbox"
INBOX_TITLE = "Solara inbox"


def _px(v: Any) -> str:
    try:
        return f"{float(v):,.1f}"
    except (TypeError, ValueError):
        return "-"


def owner_actions(conn: Conn, run_id: int) -> list[str]:
    """One line per owner-action trigger of the run, with the command to act on it."""
    trig = store.load_triggers(run_id, conn=conn)
    lines = []
    for t in trig.to_dict("records") if not trig.empty else []:
        kind, d, pid, oid = str(t["kind"]), t["detail"], t["position_id"], t["order_id"]
        if kind not in OWNER_KINDS:
            continue
        pid_s = int(pid) if pd.notna(pid) else "-"
        if kind == "stop_close":
            lines.append(
                f"- **{t['ticker']}** closed {_px(d['close'])} below the stop {_px(d['stop'])} (position {pid_s}): `book.py exit {pid_s} --reason stop`, then `fill` the stop order"
            )
        elif kind == "stop_touched":
            lines.append(
                f"- **{t['ticker']}** traded through the stop {_px(d['stop'])}, closed {_px(d['close'])} (position {pid_s}): check whether the broker stop filled"
            )
        elif kind == "target_hit":
            lines.append(
                f"- **{t['ticker']}** reached the target {_px(d['target'])} (position {pid_s}): `book.py exit {pid_s} --reason target` or trail"
            )
        elif kind == "entry_break":
            lines.append(
                f"- **{t['ticker']}** closed {_px(d['close'])} above the trigger {_px(d['trigger'])}: place entry order {int(oid)} — `book.py placed {int(oid)} --ref ...`"
            )
        elif kind == "order_stale":
            lines.append(
                f"- **{t['ticker']}** order {int(oid)} was valid until {d['valid_until']} with no fill: `book.py fill {int(oid)} --qty N --price P`, or `book.py unfilled {int(oid)}` / `book.py expire`"
            )
        elif kind == "event_due":
            lines.append(
                f"- **{t['ticker']}** scheduled event {d['event']} in {d['sessions']} session(s) (position {pid_s}): policy **{d['policy']}**"
            )
        elif kind == "dividend":
            lines.append(
                f"- **{t['ticker']}** ex-dividend {t['at']}: £{float(d['amount_gbp']):,.2f} (position {pid_s}): `book.py cash dividend {t['ticker']} {float(d['amount_gbp']):.2f} --at {t['at']}`"
            )
        elif kind == "split":
            lines.append(
                f"- **{t['ticker']}** split {d['ratio']} on {t['at']} (position {pid_s}): `book.py split {pid_s} --ratio {d['ratio']} --at {t['at']}`"
            )
        else:
            lines.append(
                f"- **{t['ticker']}** {kind} (position {pid_s}): {json.dumps(d, default=str)}"
            )
    return lines


def new_revisions(conn: Conn, since: str) -> list[str]:
    """Plan revisions proposed at or after ``since`` and still undecided."""
    rv = book.revisions(conn, status="proposed")
    if rv.empty:
        return []
    rv = rv[rv["at"].astype(str) >= since]
    pos = book.positions(conn).set_index("position_id")["ticker"]
    return [
        f"- **{pos.get(int(r['position_id']), '?')}** (position {int(r['position_id'])}): {r['actor']} proposes **{r['action']}** {json.dumps(r['new'], default=str)} — {r['reason']}: `book.py accept {int(r['revision_id'])}` / `book.py decline {int(r['revision_id'])} --reason ...`"
        for r in rv.to_dict("records")
    ]


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


def daily_message(
    conn: Conn, run_id: int, only: tuple[str, ...] = ("actions", "revisions", "errors")
) -> str | None:
    """Markdown for the daily inbox, or None when nothing needs the owner (or no such run)."""
    try:
        started = str(store.run_info(run_id, conn=conn)["started_at"])
    except ValueError:
        return None
    parts: list[str] = []
    if "actions" in only:
        acts = owner_actions(conn, run_id)
        if acts:
            parts += ["### Owner actions", "", *acts, ""]
    if "revisions" in only:
        revs = new_revisions(conn, started)
        if revs:
            parts += ["### Revision proposals", "", *revs, ""]
    if "errors" in only:
        errs = errors(conn, run_id)
        if errs:
            parts += ["### Errors", "", *errs, ""]
    if not parts:
        return None
    return (
        "\n".join([f"## Daily check — run {run_id} ({started[:16]} UTC)", "", *parts]).rstrip()
        + "\n"
    )


def weekly_message(conn: Conn, run_id: int, summary_url: str | None = None) -> str | None:
    """The weekly pack's headline sections for the inbox (None when the run has no pack)."""
    from solaraalpha.portfolio.weekly_pack import build_pack, render_pack

    try:
        text = render_pack(build_pack(conn, run_id))
    except ValueError:
        return None
    keep: list[str] = []
    include = False
    for line in text.splitlines():
        if line.startswith("# "):
            keep.append(line)
            continue
        if line.startswith("## "):
            include = line[3:].strip() in WEEKLY_SECTIONS
        if include:
            keep.append(line)
    if summary_url:
        keep += ["", f"Full pack and pipeline report: {summary_url}"]
    return "\n".join(keep).rstrip() + "\n"


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
