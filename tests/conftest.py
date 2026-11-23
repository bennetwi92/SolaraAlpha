from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from solaraalpha.config import settings


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
def local_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``settings`` at a ``tmp_path`` DB, so ``store.connect()`` with no path uses it."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setitem(settings.__dict__, "db_path", tmp_path / "solara.db")
    return tmp_path / "solara.db"


@pytest.fixture
def db_backend(tmp_path: Path) -> Path | None:
    """The ``path`` to pass to store functions: a ``tmp_path`` SQLite file (ADR 0041)."""
    return tmp_path / "solara.db"
