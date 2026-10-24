"""Record an agent's output in the results DB: the one door agents write through.

uv run python scripts/record.py verdict FILE [--actor agent:NAME] [--run N | --new-run] [--position ID] [--db PATH]
uv run python scripts/record.py verdict pm.json --actor agent:pm                  # the PM's allocation + rotations
uv run python scripts/record.py revision FILE --position ID [--actor agent:monitor] [--db PATH]
uv run python scripts/record.py prior TICKER [--stage research] [--limit 3] [--db PATH]
uv run python scripts/record.py schema
uv run python scripts/record.py error TICKER --run N --stage S --reason TEXT     # runner: skill failed
uv run python scripts/record.py queue --run N --stage validate|research|monitor|postmortem|macro  # runner
uv run python scripts/record.py members --run N --sector "_SECTOR:Travel & Leisure"   # tickers in a macro group
uv run python scripts/record.py latest-run [--script scan|daily] [--since ISO]   # runner: which run
uv run python scripts/record.py outcome --run N --stage S                        # runner: exit 1 if all ERROR
uv run python scripts/record.py usage --run N --ticker T --stage S --file execution.json [--status success|failure] [--error TEXT]

verdict validates FILE against the Verdict schema (`schema` prints it) and, in one transaction,
writes a `labels` row (events / validate / research), a card under model = stage dated with the
run's screen week, and one `evidence` row per source (ids printed). Agents can only downgrade:
a validate / research label may not be better than the previous stage's (ACT > WATCH > PASS);
ERROR is always allowed. Violations and invalid files exit 2. --run N overrides the file's
run_id; --new-run starts a run named after the stage (a standalone `/events JET2.L`) and
records under it. revision records a monitor verdict (card + evidence) and proposes the plan
revision in its `checks` (action hold / tighten_stop / take_partial / exit / revise_target, or
for a pending entry keep / amend_entry / cancel) as a `revision_proposed` event; an agent's
revision is applied at once (ADR 0024) and the owner can reverse it with `book.py undo`. prior prints the ticker's latest
verdicts (with their age in days and the latest evidence `retrieved` time, for the research
skill's delta mode) and evidence and every filing already interpreted (nsm_id), so a run reuses
earlier work instead of re-interpreting it. See docs/decisions/0016.

A pm verdict (ticker _BOOK, ADR 0027) is checked against the run and the book (allocated tickers
final-ACT with research trade levels, risk_pct inside risk_per_trade_range, rotated positions
open or pending, renewed entries lapsing before the next weekly run) and applied at once: its
rotations (open: exit at market; pending: cancel), its renewals (the entry stays working until the
next weekly run) and the lapses (every other pending entry valid until before the next weekly run
is cancelled, ADR 0034).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from solaraalpha import agents, store


def _verdict(args: argparse.Namespace) -> None:
    try:
        verdict = agents.load_verdict(Path(args.file).read_text())
    except ValidationError as e:
        print(f"error: {args.file} is not a valid verdict:")
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "(root)"
            print(f"  {loc}: {err['msg']}")
        sys.exit(2)
    actor = args.actor or f"agent:{verdict.stage}"
    if args.new_run:
        run_id = store.start_run(verdict.stage, {"ticker": verdict.ticker, "actor": actor}, args.db)
        verdict = verdict.model_copy(update={"run_id": run_id})
    elif args.run is not None:
        verdict = verdict.model_copy(update={"run_id": args.run})
    if verdict.stage == "pm":
        from solaraalpha.portfolio import book

        conn = book.connect(args.db)
    else:
        conn = store.connect(args.db)
    try:
        if verdict.stage == "pm":
            out = agents.record_book(conn, verdict, actor)
        else:
            out = agents.record_verdict(conn, verdict, actor, args.position)
    except (agents.DowngradeError, ValueError) as e:
        print(f"error: {e}")
        sys.exit(2)
    finally:
        conn.close()
    what = f"{verdict.stage} {verdict.label} ({verdict.confidence:.2f})"
    print(f"recorded {what} for {verdict.ticker} in run {verdict.run_id}, dated {out['date']}")
    if out["label"]:
        print("labels row written")
    print(f"evidence ids: {', '.join(map(str, out['evidence_ids'])) or 'none'}")
    for a in verdict.allocation or []:
        print(f"allocated {a.ticker} at {a.risk_pct:.1%} risk: {a.reason}")
    for kind in ("rotations", "renewals", "lapsed"):
        for r in out.get(kind, []):
            line = f"{kind.removesuffix('s')}: position {r['position_id']} {r['action']} (revision {r['revision_id']}) {r['status']}"
            print(line + (f": {r['reason']}" if r["status"] == "declined" else ""))


def _revision(args: argparse.Namespace) -> None:
    try:
        verdict = agents.load_verdict(Path(args.file).read_text())
    except ValidationError as e:
        print(f"error: {args.file} is not a valid verdict:")
        for err in e.errors():
            print(f"  {'.'.join(str(x) for x in err['loc']) or '(root)'}: {err['msg']}")
        sys.exit(2)
    actor = args.actor or "agent:monitor"
    conn = store.connect(args.db)
    try:
        conn.executescript(
            __import__("solaraalpha.portfolio.book", fromlist=["BOOK_SCHEMA"]).BOOK_SCHEMA
        )
        out = agents.record_revision(conn, verdict, args.position, actor)
        status, why = "proposed", None
        if out["revision_id"] is not None:
            from solaraalpha.portfolio import book

            revs = book.revisions(conn, args.position)
            row = revs[revs["revision_id"] == out["revision_id"]]
            if len(row):
                status, why = str(row["status"].iloc[0]), row["decision_reason"].iloc[0]
    except ValueError as e:
        print(f"error: {e}")
        sys.exit(2)
    finally:
        conn.close()
    action = verdict.checks.get("action", "hold")
    print(
        f"recorded monitor {verdict.label} ({verdict.confidence:.2f}) for {verdict.ticker}, position {args.position}: {action}"
    )
    print(f"evidence ids: {', '.join(map(str, out['evidence_ids'])) or 'none'}")
    rid = out["revision_id"]
    if rid is not None and status == "accepted":  # an agent's revision is auto-applied (0024)
        print(
            f"revision {rid} applied to the book; the owner can reverse it: `book.py undo {rid} --reason ...`"
        )
    elif rid is not None and status == "declined":
        print(f"revision {rid} not applied: {why}")
    elif rid is not None:
        print(
            f"revision {rid} proposed: `book.py accept {rid}` or `book.py decline {rid} --reason ...`"
        )


def _usage(args: argparse.Namespace) -> None:
    """Record an agent invocation's cost and runtime from the action's execution file; a
    missing or unreadable file still records the invocation (with empty usage)."""
    data: object = {}
    path = Path(args.file) if args.file else None
    if path is not None and path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"warning: {path} is not JSON ({e}); recording the invocation without usage")
    elif path is not None:
        print(f"warning: {path} not found; recording the invocation without usage")
    usage = agents.parse_usage(data)
    store.save_agent_run(
        args.run, args.ticker, args.stage, usage, args.status, args.error, path=args.db
    )
    cost = f"${usage['cost_usd']:.2f}" if usage["cost_usd"] is not None else "n/a"
    secs = f"{usage['seconds']:.0f}s" if usage["seconds"] is not None else "n/a"
    print(
        f"recorded {args.stage} {args.ticker} run {args.run}: {args.status}, {secs}, {usage['turns'] or 'n/a'} turns, cost {cost}"
    )


def _members(args: argparse.Namespace) -> None:
    conn = store.connect(args.db)
    try:
        for t in agents.sector_members(conn, args.run, args.sector):
            print(t)
    finally:
        conn.close()


def _prior(args: argparse.Namespace) -> None:
    conn = store.connect(args.db)
    try:
        p = agents.prior(conn, args.ticker, args.stage, args.limit)
    finally:
        conn.close()
    print(f"{args.ticker}: {len(p['verdicts'])} prior {args.stage} verdict(s)")
    for v in p["verdicts"]:
        scores = {
            k: v[k]
            for k in v
            if k not in agents.Verdict.model_fields
            and k not in ("run_id", "started_at", "date", "actor", "evidence_ids", "evidence")
            and isinstance(v[k], float)
        }
        print(
            f"\n== run {v['run_id']} ({str(v['started_at'])[:16]}, {v['age_days']} days ago), card date {v['date']}:"
            f" {v.get('label')} ({v.get('confidence')}); evidence retrieved to {str(v.get('retrieved_to') or '-')[:10]}"
        )
        if scores:
            print("  scores: " + ", ".join(f"{k}={x:+.2f}" for k, x in scores.items()))
        print(f"  reasons: {v.get('reasons', '')}")
        if v.get("kill_criteria") and v["kill_criteria"] != "[]":
            print(f"  kill criteria: {v['kill_criteria']}")
        for e in v["evidence"]:
            pub = str(e["published"])[:10] if e["published"] else "undated"
            print(f"  - [{e['kind']}] {e['title']} ({e['publisher'] or '-'}, {pub}) {e['url']}")
            print(
                f"      {e['section']}: {e['summary'][:120]}"
                + (f" | impact {e['impact_score']:+.2f}" if e["impact_score"] is not None else "")
            )
        if v.get("note_md"):
            print("\n" + str(v["note_md"]))
    print(
        "\nFilings already interpreted (nsm_id): "
        + (
            ", ".join(f"{f['nsm_id']} ({f['stage']}, run {f['run_id']})" for f in p["filings"])
            or "none"
        )
    )


def _error(args: argparse.Namespace) -> None:
    verdict = agents.error_verdict(args.ticker, args.run, args.stage, args.reason)
    conn = store.connect(args.db)
    try:
        if args.if_missing and (
            conn.execute(
                "SELECT 1 FROM labels WHERE run_id = ? AND ticker = ? AND stage = ?",
                (args.run, args.ticker, args.stage),
            ).fetchone()
            or conn.execute(
                "SELECT 1 FROM results WHERE run_id = ? AND ticker = ? AND model = ? LIMIT 1",
                (args.run, args.ticker, args.stage),
            ).fetchone()
        ):
            print(f"{args.stage} verdict already recorded for {args.ticker} in run {args.run}")
            return
        agents.record_verdict(conn, verdict, args.actor or f"agent:{args.stage}", args.position)
    except ValueError as e:
        print(f"error: {e}")
        sys.exit(2)
    finally:
        conn.close()
    print(f"recorded {args.stage} ERROR for {args.ticker} in run {args.run}: {args.reason}")


def _queue(args: argparse.Namespace) -> None:
    from solaraalpha.portfolio.book import connect as book_connect
    from solaraalpha.portfolio.rules import load_rules

    cap = args.cap if args.cap is not None else load_rules().max_agent_tickers
    conn = book_connect(args.db)
    try:
        items = agents.queue(conn, args.run, args.stage, cap)
    finally:
        conn.close()
    for item in items:
        print(item)


def _latest_run(args: argparse.Namespace) -> None:
    """The latest run of ``--script``; for ``scan`` the latest full weekly scan (a one-stock or
    one-toolkit scan is never the week's run). ``--since``: exit 3 when that run started before
    it (the scan that just finished was not a full one)."""
    conn = store.connect(args.db)
    try:
        if args.script == "scan":
            runs = store.weekly_scans(conn)
            run_id = runs[-1] if runs else None
        else:
            row = conn.execute(
                "SELECT run_id FROM runs WHERE script = ? ORDER BY run_id DESC LIMIT 1",
                (args.script,),
            ).fetchone()
            run_id = int(row[0]) if row else None
        started = str(store.run_info(run_id, conn=conn)["started_at"]) if run_id else None
    finally:
        conn.close()
    if run_id is None:
        sys.exit(f"no {args.script} run in the DB")
    if args.since and pd.Timestamp(started) < pd.Timestamp(args.since):
        print(
            f"no full {args.script} run since {args.since} (latest: run {run_id})", file=sys.stderr
        )
        sys.exit(3)
    print(run_id)


def _outcome(args: argparse.Namespace) -> None:
    conn = store.connect(args.db)
    try:
        counts = agents.outcome(conn, args.run, args.stage)
    finally:
        conn.close()
    print(f"{args.stage} in run {args.run}: {counts['total']} recorded, {counts['errors']} ERROR")
    if counts["total"] and counts["errors"] == counts["total"]:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verdict", help="validate and record a verdict JSON file")
    v.add_argument("file")
    v.add_argument("--actor", help="default agent:<stage>")
    v.add_argument(
        "--run", type=int, default=None, help="record under this run instead of the file's"
    )
    v.add_argument("--new-run", action="store_true", help="start a run named after the stage")
    v.add_argument("--position", type=int, help="postmortem: the closed position it is about")
    v.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    rv = sub.add_parser("revision", help="record a monitor verdict and propose its revision")
    rv.add_argument("file")
    rv.add_argument("--position", type=int, required=True)
    rv.add_argument("--actor", help="default agent:monitor")
    rv.add_argument("--db", type=Path, default=None)
    pr = sub.add_parser("prior", help="earlier verdicts, evidence and interpreted filings")
    pr.add_argument("ticker")
    pr.add_argument("--stage", default="research")
    pr.add_argument("--limit", type=int, default=3)
    pr.add_argument("--db", type=Path, default=None)
    sub.add_parser("schema", help="print the Verdict JSON schema")
    er = sub.add_parser("error", help="record an ERROR verdict without an agent")
    er.add_argument("ticker")
    er.add_argument("--run", type=int, required=True)
    er.add_argument(
        "--stage",
        required=True,
        choices=("validate", "research", "events", "monitor", "postmortem", "macro", "pm"),
    )
    er.add_argument("--reason", required=True)
    er.add_argument("--position", type=int, help="postmortem: the closed position")
    er.add_argument("--actor")
    er.add_argument(
        "--if-missing",
        action="store_true",
        help="only when the stage has no verdict for the ticker in the run (an agent that ended without recording one)",
    )
    er.add_argument("--db", type=Path, default=None)
    qu = sub.add_parser(
        "queue", help="tickers (or position ids) the agents should work, best first"
    )
    qu.add_argument("--run", type=int, required=True)
    qu.add_argument(
        "--stage",
        required=True,
        choices=("validate", "research", "monitor", "postmortem", "macro"),
    )
    qu.add_argument("--cap", type=int, default=None, help="default Rules.max_agent_tickers")
    qu.add_argument("--db", type=Path, default=None)
    us = sub.add_parser("usage", help="record an agent invocation's cost and runtime (#139)")
    us.add_argument("--run", type=int, required=True)
    us.add_argument("--ticker", required=True)
    us.add_argument(
        "--stage",
        required=True,
        choices=("validate", "research", "events", "monitor", "postmortem", "macro", "pm"),
    )
    us.add_argument("--file", help="claude-code-action execution_file (JSON); missing = no usage")
    us.add_argument("--status", choices=("success", "failure"), default="success")
    us.add_argument("--error", default=None)
    us.add_argument("--db", type=Path, default=None)
    mb = sub.add_parser("members", help="the run's ACT tickers in a _SECTOR:<name> group (#140)")
    mb.add_argument("--run", type=int, required=True)
    mb.add_argument("--sector", required=True)
    mb.add_argument("--db", type=Path, default=None)
    lr = sub.add_parser("latest-run", help="the latest run id of a script")
    lr.add_argument("--script", default="scan")
    lr.add_argument("--since", help="ISO time: exit 3 if the run started before it")
    lr.add_argument("--db", type=Path, default=None)
    oc = sub.add_parser("outcome", help="counts per stage; exit 1 when every item errored")
    oc.add_argument("--run", type=int, required=True)
    oc.add_argument("--stage", required=True)
    oc.add_argument("--db", type=Path, default=None)
    args = p.parse_args(argv)
    if args.cmd == "usage":
        _usage(args)
        return
    if args.cmd == "members":
        _members(args)
        return
    if args.cmd == "schema":
        print(json.dumps(agents.Verdict.model_json_schema(), indent=2))
    elif args.cmd == "verdict":
        _verdict(args)
    elif args.cmd == "revision":
        _revision(args)
    elif args.cmd == "error":
        _error(args)
    elif args.cmd == "queue":
        _queue(args)
    elif args.cmd == "latest-run":
        _latest_run(args)
    elif args.cmd == "outcome":
        _outcome(args)
    else:
        _prior(args)


if __name__ == "__main__":
    main()
