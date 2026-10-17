"""Register of per-stock value models the research agent calls (config/value_models.toml).

A value model is stock-specific (ADR 0009: Jet2 today); the register maps a ticker to the
skill that turns news into driver shocks and the script that does the arithmetic. Research
(`.claude/skills/research/SKILL.md`) checks :func:`value_model` and, when there is one, runs
the script and scores ``value`` in its note. Issue #127.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUE_MODELS = REPO_ROOT / "config" / "value_models.toml"
SKILLS = REPO_ROOT / ".claude" / "skills"


@dataclass(frozen=True)
class ValueModel:
    ticker: str
    skill: str  # .claude/skills/<skill>/SKILL.md
    script: str  # repo-relative, run with `uv run python <script>`
    note: str = ""

    @property
    def skill_path(self) -> Path:
        return SKILLS / self.skill / "SKILL.md"

    @property
    def script_path(self) -> Path:
        return REPO_ROOT / self.script


def load_value_models(path: Path = VALUE_MODELS) -> dict[str, ValueModel]:
    """``ticker -> ValueModel`` from the TOML register (empty when the file is missing)."""
    if not path.exists():
        return {}
    raw = tomllib.loads(path.read_text())
    out = {}
    for ticker, entry in raw.items():
        if not isinstance(entry, dict) or "skill" not in entry or "script" not in entry:
            raise ValueError(f"value_models.toml: {ticker!r} needs 'skill' and 'script'")
        out[str(ticker)] = ValueModel(
            str(ticker), str(entry["skill"]), str(entry["script"]), str(entry.get("note", ""))
        )
    return out


def value_model(ticker: str, path: Path = VALUE_MODELS) -> ValueModel | None:
    return load_value_models(path).get(ticker)
