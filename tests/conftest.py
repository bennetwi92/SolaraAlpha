from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from fakes import FakeTurso, start_fake_turso


@pytest.fixture(autouse=True)
def no_broker_keys(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline tests never reach the owner's Trading212 account or Telegram chat, whatever the
    environment holds (network tests keep the keys)."""
    if request.node.get_closest_marker("network"):
        return
    from solaraalpha.config import settings

    monkeypatch.setattr(settings, "t212_key", SecretStr(""))
    monkeypatch.setattr(settings, "t212_secret", SecretStr(""))
    monkeypatch.setattr(settings, "telegram_token", SecretStr(""))
    monkeypatch.setattr(settings, "telegram_chat", "")


@pytest.fixture(autouse=True)
def entries_on(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests run on the live rules with new entries on, whatever the wind-down switch in
    config/portfolio.toml says (ADR 0040): the planner and the buy guards stay under test."""
    from solaraalpha.portfolio import rules

    text = rules.RULES.read_text().replace("new_entries = false", "new_entries = true")
    path = tmp_path_factory.mktemp("rules") / "portfolio.toml"
    path.write_text(text)
    monkeypatch.setattr(rules.load_rules, "__defaults__", (path,))


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
