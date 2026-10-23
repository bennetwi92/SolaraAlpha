#!/usr/bin/env python3
"""PreToolUse guard for the desk subagents (.claude/agents/*.md): no live writes by default.

Blocks a Bash command that would write the book or the results DB, trigger a workflow, post to
the inbox or the desk thread, or send / cancel an order at Trading212 (``execute.py``), unless
the session was started with SOLARA_ALLOW_WRITES=1 (the owner's go-ahead:
`SOLARA_ALLOW_WRITES=1 claude --agent portfolio-manager`). A script segment with `--db PATH`
writes a local file, so it is allowed, unless the path is the live local DB (data/solara.db).
Inline Python that imports the package is blocked (it can call any write). Stdlib only; reads
the hook JSON on stdin.
"""

from __future__ import annotations

import json
import os
import re
import sys

BOOK_WRITES = (
    "new|approve|amend|reject|placed|fill|unfilled|cancel|expire|cash|split|corporate-close"
    "|accept|decline|undo|exit|swap"
)
DB_WRITES = [
    re.compile(rf"book\.py\s+(?:--db[=\s]\S+\s+)?(?:{BOOK_WRITES})\b"),
    re.compile(r"book\.py\b.*\breconcile\b.*--book\b"),  # books fills from Trading212
    re.compile(r"record\.py\s+(?:verdict|revision|error|usage)\b"),
    re.compile(r"execute\.py\s+(?:--db\s+\S+\s+)?(?:hold|note|error)\b"),
    re.compile(r"plan\.py\b(?!.*--dry-run)"),
    re.compile(r"db\.py\s+push\b"),
    re.compile(r"scripts/\S+\.py\b.*--save\b"),
]
ALWAYS = [  # outward-facing: a local --db does not make these safe
    re.compile(r"\bgh\s+(?:workflow\s+run|run\s+rerun)\b"),
    re.compile(r"\bgh\s+(?:issue|pr)\s+(?:comment|create|edit|close|reopen|merge|review)\b"),
    re.compile(r"\bgh\s+api\b.*(?:-X|--method)[=\s]*(?:POST|PUT|PATCH|DELETE)", re.I),
    re.compile(r"\bgh\s+api\b.*\s(?:-[fF]|--field|--raw-field|--input)\b"),
    re.compile(r"notify\.py\b.*--channel[=\s]+github-"),
    re.compile(r"execute\.py\b.*\b(?:turn|send|cancel)\b"),  # orders at Trading212 (ADR 0031)
    re.compile(r"chat\.py\s+post\b"),  # the desk thread, sent to Telegram (ADR 0033)
]


INLINE = re.compile(r"\bpython3?\s+(?:-c\b|-\s|-$|<)")  # code not in a script: unchecked
LIVE_DB = re.compile(r"(?:^|/)data/solara\.db$")


def _local_db(segment: str) -> bool:
    """Whether ``segment`` names a ``--db`` file other than the live local DB."""
    m = re.search(r"--db[=\s]+(\S+)", segment)
    return m is not None and not LIVE_DB.search(m.group(1))


def blocked(command: str) -> str | None:
    """The reason `command` is blocked, or None when it may run."""
    if "SOLARA_ALLOW_WRITES" in command:
        return "SOLARA_ALLOW_WRITES is the owner's to set when starting the session"
    if INLINE.search(command) and "solaraalpha" in command:
        return "inline Python using the package: run the script that does it instead"
    for segment in re.split(r"&&|\|\||[;|\n]", command.replace('"', "").replace("'", "")):
        if any(p.search(segment) for p in ALWAYS):
            return f"outward-facing action: {segment.strip()}"
        if not _local_db(segment) and any(p.search(segment) for p in DB_WRITES):
            return f"writes the live book / results DB: {segment.strip()}"
    return None


def main() -> int:
    if os.environ.get("SOLARA_ALLOW_WRITES") == "1":
        return 0
    payload = json.load(sys.stdin)
    reason = blocked(payload.get("tool_input", {}).get("command", ""))
    if reason is None:
        return 0
    print(
        f"Blocked: {reason}. Live writes need the owner's go-ahead: ask them, and they start "
        "the session with SOLARA_ALLOW_WRITES=1. Use --db PATH or --dry-run for local work.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
