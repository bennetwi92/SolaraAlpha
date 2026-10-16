from pathlib import Path

import pytest

from solaraalpha.config import Settings


def test_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SOLARA_DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.data_dir == tmp_path
    assert s.raw_dir == tmp_path / "raw"
    assert s.raw_dir.is_dir()
