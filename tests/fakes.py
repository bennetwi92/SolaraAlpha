"""Test doubles shared across test modules."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from solaraalpha import turso
from solaraalpha.config import settings


class FakeTurso:
    """Hrana-over-HTTP endpoint backed by an in-memory SQLite database."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.posts = 0

    def post(self, url: str, json: dict[str, Any], headers: dict[str, str], timeout: int) -> Any:
        assert url == "https://fake.turso.io/v2/pipeline"
        assert headers["Authorization"] == "Bearer t0ken"
        self.posts += 1
        results = []
        for req in json["requests"]:
            if req["type"] == "close":
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            stmt = req["stmt"]
            try:
                cur = self.db.execute(stmt["sql"], [_arg(a) for a in stmt["args"]])
            except sqlite3.Error as e:
                results.append({"type": "error", "error": {"message": str(e)}})
                continue
            result = {
                "cols": [{"name": d[0]} for d in cur.description or []],
                "rows": [[_value(v) for v in row] for row in cur.fetchall()],
                "affected_row_count": cur.rowcount,
                "last_insert_rowid": str(cur.lastrowid),
            }
            results.append({"type": "ok", "response": {"type": "execute", "result": result}})
        body = {"baton": "b", "base_url": None, "results": results}
        return _Response(_json_roundtrip(body))


class _Response:
    status_code = 200

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def json(self) -> dict[str, Any]:
        return self.body


def _arg(a: dict[str, Any]) -> Any:
    return a["value"] if a["type"] == "float" else turso._decode(a)  # as sent, no ±MAX mapping


def _value(v: Any) -> dict[str, Any]:
    if isinstance(v, float) and not math.isfinite(v):
        return {"type": "float", "value": None}  # what Turso returns for ±inf
    return turso._encode(v)


def _json_roundtrip(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(body, allow_nan=False))
    return out


def start_fake_turso(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeTurso:
    """Point ``settings`` at a :class:`FakeTurso`, so ``store.connect()`` with no path uses it."""
    server = FakeTurso()
    monkeypatch.setattr(turso, "session", lambda: server)
    monkeypatch.setattr(settings, "turso_url", "libsql://fake.turso.io")
    monkeypatch.setattr(settings, "turso_token", SecretStr("t0ken"))
    monkeypatch.setattr(settings, "data_dir", tmp_path)  # a stray local write would land here
    return server
