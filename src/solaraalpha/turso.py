"""Hosted results DB: a minimal sqlite3-style connection to Turso over its HTTP API.

Speaks Hrana over HTTP (``POST /v2/pipeline``) with our shared ``requests`` session, so there
is no driver dependency and it works through the cloud sessions' HTTPS proxy. One connection is
one Hrana stream (kept open with its baton). See docs/decisions/0013.

Supported (what :mod:`solaraalpha.store` and the book use; anything else is deliberately
absent):

* ``execute(sql, params)`` -> cursor with ``fetchall`` / ``fetchone`` / iteration,
  ``description``, ``lastrowid`` (so ``INSERT`` into an ``INTEGER PRIMARY KEY`` table returns
  the new id) and ``rowcount``;
* ``executemany(sql, rows)``, batched ``BATCH`` statements per HTTP request;
* ``executescript(script)``: statements split on ``;``, so a script may hold several
  ``CREATE TABLE IF NOT EXISTS`` / ``ALTER TABLE ... ADD COLUMN`` statements but no literal ``;``
  inside a statement;
* ``with conn:`` (``BEGIN`` ... ``COMMIT`` / ``ROLLBACK``), ``commit`` / ``rollback``, ``close``;
* ``PRAGMA table_info(t)`` and ``sqlite_master`` queries behave as in SQLite.

Values: ints (incl. numpy and bool), floats (NaN -> NULL; ±inf <-> ±``MAX``), str, bytes, None.
Every new table is tested over both backends with the ``db_backend`` fixture (tests/conftest.py).
"""

from __future__ import annotations

import base64
import math
import sys
from collections.abc import Iterator
from numbers import Integral, Real
from types import TracebackType
from typing import Any

from solaraalpha.data.http import DEFAULT_TIMEOUT, session

BATCH = 500  # statements per HTTP request in executemany
# JSON has no infinity (Turso returns it as null), so ±inf travels and is stored as ±MAX.
MAX = sys.float_info.max


class TursoError(RuntimeError):
    pass


class Cursor:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        result = result or {}
        cols = result.get("cols", [])
        self.description = [(c["name"], None, None, None, None, None, None) for c in cols] or None
        self.rows = [tuple(_decode(v) for v in row) for row in result.get("rows", [])]
        rowid = result.get("last_insert_rowid")
        self.lastrowid = int(rowid) if rowid is not None else None
        self.rowcount = result.get("affected_row_count", -1)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def fetchone(self) -> Any:
        return self.rows[0] if self.rows else None

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return iter(self.rows)


class Connection:
    """sqlite3-like connection to a Turso database (``libsql://`` or ``https://`` URL)."""

    def __init__(self, url: str, token: str) -> None:
        self.url = url.replace("libsql://", "https://", 1).rstrip("/")
        self.token = token
        self.baton: str | None = None

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> Cursor:
        return Cursor(self._pipeline([_stmt(sql, params)])[0])

    def executemany(self, sql: str, rows: Any) -> Cursor:
        reqs = [_stmt(sql, r) for r in rows]
        for i in range(0, len(reqs), BATCH):
            self._pipeline(reqs[i : i + BATCH])
        return Cursor()

    def executescript(self, script: str) -> None:
        stmts = [s.strip() for s in script.split(";")]
        self._pipeline([_stmt(s) for s in stmts if s and not _comment_only(s)])

    def commit(self) -> None:
        self.execute("COMMIT")

    def rollback(self) -> None:
        self.execute("ROLLBACK")

    def close(self) -> None:
        if self.baton is not None:
            self._post([{"type": "close"}])
            self.baton = None

    def __enter__(self) -> Connection:
        self.execute("BEGIN")
        return self

    def __exit__(
        self, typ: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.rollback() if typ else self.commit()

    def _pipeline(self, reqs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for res in self._post(reqs):
            if res["type"] != "ok":
                raise TursoError(res.get("error", {}).get("message", str(res)))
            out.append(res["response"]["result"])
        return out

    def _post(self, reqs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resp = session().post(
            f"{self.url}/v2/pipeline",
            json={"baton": self.baton, "requests": reqs},
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200:
            raise TursoError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        self.baton = body.get("baton")
        if body.get("base_url"):
            self.url = body["base_url"].rstrip("/")
        return list(body["results"])


def _comment_only(sql: str) -> bool:
    return all(not line.strip() or line.strip().startswith("--") for line in sql.splitlines())


def _stmt(sql: str, params: Any = ()) -> dict[str, Any]:
    return {"type": "execute", "stmt": {"sql": sql, "args": [_encode(p) for p in params]}}


def _encode(v: Any) -> dict[str, Any]:
    """Python value -> Hrana value (NaN is stored as NULL, as sqlite3 does)."""
    if v is None or (isinstance(v, Real) and not isinstance(v, Integral) and math.isnan(v)):
        return {"type": "null"}
    if isinstance(v, Integral):  # incl. bool and numpy ints
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, Real):
        return {"type": "float", "value": max(-MAX, min(MAX, float(v)))}
    if isinstance(v, bytes):
        return {"type": "blob", "base64": base64.b64encode(v).decode()}
    return {"type": "text", "value": str(v)}


def _decode(v: dict[str, Any]) -> Any:
    match v["type"]:
        case "null":
            return None
        case "integer":
            return int(v["value"])
        case "float":
            x = float(v["value"])
            return math.copysign(math.inf, x) if abs(x) == MAX else x
        case "blob":
            return base64.b64decode(v["base64"])
        case _:
            return v["value"]
