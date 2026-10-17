from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

from scanfix import build_scan_db
from solaraalpha import agents, store
from solaraalpha.portfolio import book

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import learn
import record

FIXTURE = Path(__file__).parent / "fixtures" / "claude_action_execution.json"


def test_parse_usage_from_the_action_fixture() -> None:
    # fixture shaped after claude-code-action's execution_file: the SDK message log whose
    # final "result" message carries cost, turns, duration and token usage
    data = json.loads(FIXTURE.read_text())
    u = agents.parse_usage(data)
    assert u == {
        "seconds": pytest.approx(184.23),
        "turns": 23,
        "input_tokens": 1842 + 51230 + 412880,
        "output_tokens": 6120,
        "cost_usd": pytest.approx(0.4137),
    }
    assert agents.parse_usage(data[-1]) == u  # a bare result dict
    assert agents.parse_usage(data[:-1]) == dict.fromkeys(agents.USAGE_KEYS)  # no result message


def test_parse_usage_tolerates_missing_and_bad_fields() -> None:
    assert agents.parse_usage({}) == dict.fromkeys(agents.USAGE_KEYS)
    assert agents.parse_usage(None) == dict.fromkeys(agents.USAGE_KEYS)
    assert agents.parse_usage("junk") == dict.fromkeys(agents.USAGE_KEYS)
    u = agents.parse_usage(
        {
            "type": "result",
            "num_turns": "12",
            "duration_ms": None,
            "usage": {"output_tokens": "x", "cache_read_input_tokens": 100},
        }
    )
    assert u["turns"] == 12 and u["seconds"] is None and u["output_tokens"] is None
    assert u["input_tokens"] == 100 and u["cost_usd"] is None  # a subscription run reports no cost
    assert agents.parse_usage({"total_cost_usd": float("nan")})["cost_usd"] is None


def test_usage_recorded_reported_and_in_the_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = build_scan_db(tmp_path)  # scan run 1 (started now)
    args = ["usage", "--run", "1", "--db", str(db)]
    record.main([*args, "--ticker", "A.L", "--stage", "validate", "--file", str(FIXTURE)])
    out = capsys.readouterr().out
    assert "recorded validate A.L run 1: success, 184s, 23 turns, cost $0.41" in out
    record.main(
        [
            *args,
            "--ticker",
            "A.L",
            "--stage",
            "research",
            "--file",
            str(tmp_path / "missing.json"),
            "--status",
            "failure",
            "--error",
            "turn limit",
        ]
    )
    out = capsys.readouterr().out
    assert (
        "not found; recording the invocation without usage" in out
        and "failure, n/a, n/a turns, cost n/a" in out
    )
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    record.main([*args, "--ticker", "W.L", "--stage", "validate", "--file", str(bad)])
    assert "is not JSON" in capsys.readouterr().out
    runs = store.load_agent_runs(path=db)
    assert len(runs) == 3 and runs["stage"].tolist() == ["validate", "research", "validate"]
    assert (
        runs["status"].tolist() == ["success", "failure", "success"]
        and runs["error"].iloc[1] == "turn limit"
    )
    assert runs["cost_usd"].iloc[0] == pytest.approx(0.4137) and math.isnan(
        float(runs["cost_usd"].iloc[1])
    )
    assert store.load_agent_runs("2999-01-01", path=db).empty

    use = agents.agent_usage(runs)
    assert use["by_stage"].set_index("stage").loc["validate", "invocations"] == 2
    assert use["by_stage"].set_index("stage").loc["validate", "cost_usd"] == pytest.approx(0.4137)
    assert use["by_week"]["invocations"].sum() == 3 and use["per_ticker"]["minutes"].iloc[
        0
    ] == pytest.approx(184.23 / 60 / 3)
    assert agents.agent_usage(runs.iloc[0:0])["by_week"].empty

    learn.main(["agents", "--db", str(db)])
    out = capsys.readouterr().out
    assert "3 agent invocations" in out and "Per stage:" in out and "failures: 1" in out
    learn.main(["agents", "--since", "2999-01-01", "--db", str(db)])
    assert "No agent runs recorded yet" in capsys.readouterr().out

    from solaraalpha.portfolio.weekly_pack import build_pack, render_pack

    conn = book.connect(db)
    text = render_pack(build_pack(conn, 1))
    conn.close()
    assert "- agents this week: 3 invocation(s), 3 minutes, cost $0.41, 1 failure(s)" in text
