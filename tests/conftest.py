from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_cache(tmp_path: Path) -> Path:
    d = tmp_path / "cache"
    d.mkdir()
    return d
