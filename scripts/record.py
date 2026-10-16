"""Record an agent's output in the results DB: the one door agents write through.

uv run python scripts/record.py verdict FILE [--actor agent:NAME] [--db PATH]
uv run python scripts/record.py prior TICKER [--stage research] [--limit 3] [--db PATH]
uv run python scripts/record.py schema

verdict validates FILE against the Verdict schema (`schema` prints it) and, in one transaction,
writes a `labels` row (events / validate / research), a card under model = stage dated with the
run's screen week, and one `evidence` row per source (ids printed). Agents can only downgrade:
a validate / research label may not be better than the previous stage's (ACT > WATCH > PASS);
ERROR is always allowed. Violations and invalid files exit 2. prior prints the ticker's latest
verdicts and evidence and every filing already interpreted (nsm_id), so a run reuses earlier
work instead of re-interpreting it. See docs/decisions/0016.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
    conn = store.connect(args.db)
    try:
        out = agents.record_verdict(conn, verdict, actor)
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
            f"\n== run {v['run_id']} ({str(v['started_at'])[:16]}), card date {v['date']}: {v.get('label')} ({v.get('confidence')})"
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


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verdict", help="validate and record a verdict JSON file")
    v.add_argument("file")
    v.add_argument("--actor", help="default agent:<stage>")
    v.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    pr = sub.add_parser("prior", help="earlier verdicts, evidence and interpreted filings")
    pr.add_argument("ticker")
    pr.add_argument("--stage", default="research")
    pr.add_argument("--limit", type=int, default=3)
    pr.add_argument("--db", type=Path, default=None)
    sub.add_parser("schema", help="print the Verdict JSON schema")
    args = p.parse_args(argv)
    if args.cmd == "schema":
        print(json.dumps(agents.Verdict.model_json_schema(), indent=2))
    elif args.cmd == "verdict":
        _verdict(args)
    else:
        _prior(args)


if __name__ == "__main__":
    main()
