from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "guard_live_writes.py"
sys.path.insert(0, str(HOOK.parent))
from guard_live_writes import blocked  # noqa: E402


@pytest.mark.parametrize(
    "command",
    [
        "uv run python scripts/book.py approve 7",
        "uv run python scripts/book.py cash deposit 5000",
        "uv run python scripts/record.py verdict pm.json --actor agent:pm",
        "uv run python scripts/plan.py --run 9",
        "uv run python scripts/scan.py --save",
        "uv run python scripts/db.py push",
        "make check && uv run python scripts/book.py undo 3 --reason x",
        "gh workflow run book.yml -f command='list'",
        "uv run python scripts/notify.py --mode daily --run 9 --channel github-issue",
        'uv run python scripts/chat.py post --as agent:pm --db x.db "hello"',
        "SOLARA_ALLOW_WRITES=1 uv run python scripts/book.py approve 7",
        "uv run python scripts/book.py --db /tmp/x.db list && uv run python scripts/book.py fill 3",
    ],
)
def test_blocks_live_writes(command: str) -> None:
    assert blocked(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "uv run python scripts/book.py list",
        "uv run python scripts/book.py show 7",
        "uv run python scripts/record.py prior JET2.L --stage research",
        "uv run python scripts/plan.py --run 9 --dry-run",
        "uv run python scripts/scan.py JET2.L",
        "uv run python scripts/book.py --db /tmp/x.db approve 7",
        "uv run python scripts/record.py verdict v.json --db /tmp/x.db",
        "uv run python scripts/db.py status",
        "uv run python scripts/notify.py --mode daily --run 9",
        "uv run python scripts/chat.py read --last 10",
        "make check",
        "grep -rn exit src/",
    ],
)
def test_allows_reads_and_local_writes(command: str) -> None:
    assert blocked(command) is None


def _run(command: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, env=env
    )


def test_hook_exit_codes() -> None:
    cmd = "uv run python scripts/book.py approve 7"
    denied = _run(cmd, {})
    assert denied.returncode == 2 and "Blocked" in denied.stderr
    assert _run(cmd, {"SOLARA_ALLOW_WRITES": "1"}).returncode == 0
    assert _run("uv run python scripts/book.py list", {}).returncode == 0
