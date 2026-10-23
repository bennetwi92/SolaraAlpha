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
        "SOLARA_ALLOW_WRITES=1 uv run python scripts/book.py approve 7",
        "uv run python scripts/book.py --db /tmp/x.db list && uv run python scripts/book.py fill 3",
        "uv run python scripts/book.py swap 12",
        "uv run python scripts/execute.py send 12 --reason x",
        "uv run python scripts/execute.py --db /tmp/x.db turn",
        "uv run python scripts/execute.py cancel 12 --reason x",
        "uv run python scripts/execute.py hold 12 --until 2026-10-27T08:15Z --reason x",
    ],
)
def test_blocks_live_writes(command: str) -> None:
    assert blocked(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "uv run python scripts/book.py list",
        "uv run python scripts/execute.py log --since 2026-10-27",
        "uv run python scripts/execution_pack.py",
        "uv run python scripts/book.py show 7",
        "uv run python scripts/record.py prior JET2.L --stage research",
        "uv run python scripts/plan.py --run 9 --dry-run",
        "uv run python scripts/scan.py JET2.L",
        "uv run python scripts/book.py --db /tmp/x.db approve 7",
        "uv run python scripts/record.py verdict v.json --db /tmp/x.db",
        "uv run python scripts/db.py status",
        "uv run python scripts/notify.py --mode daily --run 9",
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
