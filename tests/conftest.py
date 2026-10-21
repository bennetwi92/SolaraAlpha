from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from fakes import FakeTurso, start_fake_turso


@pytest.fixture(autouse=True)
def no_broker_keys(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline tests never reach the owner's Trading212 account, whatever the environment
    holds (network tests keep the keys)."""
    if request.node.get_closest_marker("network"):
        return
    from solaraalpha.config import settings

    monkeypatch.setattr(settings, "t212_key", SecretStr(""))
    monkeypatch.setattr(settings, "t212_secret", SecretStr(""))


@pytest.fixture
def tmp_cache(tmp_path: Path) -> Path:
    d = tmp_path / "cache"
    d.mkdir()
    return d


@pytest.fixture
def fake_turso(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeTurso:
    """A fake Turso endpoint that ``store.connect()`` (no path) talks to."""
    return start_fake_turso(monkeypatch, tmp_path)


@pytest.fixture(params=["local", "turso"])
def db_backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path | None:
    """The ``path`` to pass to store functions, once per backend: a ``tmp_path`` SQLite file, or
    ``None`` with ``settings`` pointed at a :class:`FakeTurso`. Every DB table is tested over both."""
    if request.param == "local":
        return tmp_path / "solara.db"
    start_fake_turso(monkeypatch, tmp_path)
    return None
