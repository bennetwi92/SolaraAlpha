from __future__ import annotations

from pathlib import Path

import pytest

from solaraalpha.value_models import REPO_ROOT, load_value_models, value_model


def test_registered_value_models_point_at_real_skills_and_scripts() -> None:
    models = load_value_models()
    assert "JET2.L" in models  # the one value model today (ADR 0009)
    for ticker, m in models.items():
        assert m.ticker == ticker
        assert m.script_path.exists(), f"{ticker}: {m.script} missing"
        assert m.script_path.suffix == ".py" and m.script.startswith("scripts/")
        assert m.skill_path.exists(), f"{ticker}: skill {m.skill} missing"
        assert m.skill_path.read_text().startswith("---\nname: " + m.skill)
    assert value_model("JET2.L") is not None and value_model("NOPE.L") is None


def test_loader_edge_cases(tmp_path: Path) -> None:
    assert load_value_models(tmp_path / "missing.toml") == {}
    good = tmp_path / "vm.toml"
    good.write_text('["X.L"]\nskill = "s"\nscript = "scripts/x.py"\n')
    m = load_value_models(good)["X.L"]
    assert m.skill == "s" and m.script_path == REPO_ROOT / "scripts" / "x.py" and m.note == ""
    bad = tmp_path / "bad.toml"
    bad.write_text('["X.L"]\nskill = "s"\n')
    with pytest.raises(ValueError, match="needs 'skill' and 'script'"):
        load_value_models(bad)


def test_research_skill_documents_delta_mode_and_value_models() -> None:
    text = (REPO_ROOT / ".claude" / "skills" / "research" / "SKILL.md").read_text()
    for needle in (
        "Delta mode",
        "35 days",
        "carried from run",
        "config/value_models.toml",
        "Valuation",
        '"value"',
    ):
        assert needle in text, needle
