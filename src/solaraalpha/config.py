"""Central settings, loaded from environment variables / `.env` (prefix ``SOLARA_``).

Usage::

    from solaraalpha.config import settings
    settings.data_dir
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOLARA_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = REPO_ROOT / "data"
    log_level: str = "INFO"

    @cached_property
    def raw_dir(self) -> Path:
        """Untouched downloads from external sources."""
        return self._ensure(self.data_dir / "raw")

    @cached_property
    def processed_dir(self) -> Path:
        """Cleaned / derived datasets."""
        return self._ensure(self.data_dir / "processed")

    @cached_property
    def cache_dir(self) -> Path:
        """Disposable cache for API responses; safe to delete."""
        return self._ensure(self.data_dir / "cache")

    @cached_property
    def db_path(self) -> Path:
        """Results database (SQLite): runs, model outputs, input snapshots."""
        return self._ensure(self.data_dir) / "solara.db"

    @staticmethod
    def _ensure(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
