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


class FakeBroker:
    """An in-memory Trading212 (GBP account, GBX lines) for the execution tests: orders work,
    fill, cancel and end in the history as the API shows them; a sell beyond the shares not
    already under a working sell is refused (``selling-equity-not-owned``, tested 2026-10-21)."""

    def __init__(self, deposit: float = 5000.0) -> None:
        import pandas as pd

        self.pd = pd
        self.working: dict[str, dict[str, Any]] = {}
        self.ended: list[dict[str, Any]] = []
        self.held: dict[str, float] = {}
        self.cost: dict[str, float] = {}
        self.deposit = deposit
        self.calls: list[tuple[Any, ...]] = []
        self.next_id = 1000
        self.fail_next: Exception | None = None  # raised by the next place, nothing placed
        self.lose_answer = False  # the next place is placed, but the answer never comes
        self.fill_on_cancel: dict[str, float] = {}  # ref -> price it fills at instead
        self.partial: dict[str, tuple[float, float]] = {}  # ref -> (qty, price) filled so far

    def ticker(self, yahoo: str) -> str | None:
        return yahoo.removesuffix(".L") + "l_EQ"

    def _yahoo(self, t212: str) -> str:
        return t212.removesuffix("l_EQ") + ".L"

    def place(
        self, type: str, t212_ticker: str, qty: int, side: int, price: float | None
    ) -> dict[str, Any]:
        from solaraalpha.data import trading212 as t212

        self.calls.append(("place", type, t212_ticker, qty, side, price))
        if self.fail_next is not None:
            e, self.fail_next = self.fail_next, None
            raise e
        if side < 0:
            selling = sum(
                o["qty"]
                for o in self.working.values()
                if o["t212"] == t212_ticker and o["side"] < 0
            )
            if selling + qty > self.held.get(t212_ticker, 0):
                raise t212.T212Error(
                    400,
                    "/api-errors/selling-equity-not-owned",
                    "Selling equity not owned",
                    f"Selling more equities than owned, owned: {self.held.get(t212_ticker, 0.0)}",
                )
        self.next_id += 1
        ref = str(self.next_id)
        o: dict[str, Any] = {
            "id": ref,
            "t212": t212_ticker,
            "currency": "GBX",
            "side": side,
            "type": type,
            "qty": float(qty),
            "price": price,
            "status": "new",
            "filled": 0.0,
            "created_at": self.pd.Timestamp.now(tz="UTC"),
        }
        self.working[ref] = o
        if self.lose_answer:
            self.lose_answer = False
            raise t212.Unknown("POST timed out")
        return dict(o)

    def cancel(self, ref: str) -> None:
        from solaraalpha.data import trading212 as t212

        self.calls.append(("cancel", ref))
        if ref not in self.working:
            raise t212.T212Error(404, "/api-errors/entity-not-found", "Not found")
        if ref in self.fill_on_cancel:  # the stop sold before the cancel landed
            self.fill(ref, price=self.fill_on_cancel.pop(ref))
            return
        o = self.working.pop(ref)
        self.ended.append({**o, "status": "cancelled", "fill": self.partial.pop(ref, None)})

    def part_fill(self, ref: str, qty: float, price: float) -> None:
        """Fill part of a working buy and leave the rest working (``filled`` on the order)."""
        o = self.working[ref]
        t = o["t212"]
        self.cost[t] = self.cost.get(t, 0.0) + qty * price
        self.held[t] = self.held.get(t, 0.0) + qty
        o["filled"] = float(qty)
        self.partial[ref] = (float(qty), float(price))

    def order(self, ref: str) -> dict[str, Any] | None:
        o = self.working.get(ref)
        return dict(o) if o else None

    def fill(self, ref: str, qty: float | None = None, price: float | None = None) -> None:
        """Fill a working order (all of it by default) and end it in the history."""
        o = self.working.pop(ref)
        q = float(qty if qty is not None else o["qty"])
        px = float(price if price is not None else o["price"])
        t = o["t212"]
        if o["side"] > 0:
            self.cost[t] = self.cost.get(t, 0.0) + q * px
            self.held[t] = self.held.get(t, 0.0) + q
        else:
            held = self.held.get(t, 0.0)
            self.cost[t] = self.cost.get(t, 0.0) * (held - q) / held if held else 0.0
            self.held[t] = held - q
        status = "filled" if q == o["qty"] else "cancelled"
        self.ended.append({**o, "status": status, "fill": (q, px)})

    def broker(self) -> Any:
        from solaraalpha.data import trading212 as t212
        from solaraalpha.portfolio.reconcile import Broker

        pd = self.pd
        now = pd.Timestamp.now(tz="UTC")
        positions = pd.DataFrame(
            [
                {
                    "t212": t,
                    "isin": "",
                    "currency": "GBX",
                    "qty": q,
                    "avg_price": self.cost[t] / q,
                    "price": self.cost[t] / q,
                    "opened_at": now,
                }
                for t, q in self.held.items()
                if q > 0
            ],
            columns=t212.POSITION_COLS,
        )
        pending = pd.DataFrame(list(self.working.values()), columns=t212.ORDER_COLS)
        rows = []
        for i, o in enumerate(self.ended):
            f = o["fill"]
            rows.append(
                {
                    **{k: o[k] for k in t212.ORDER_COLS},
                    "status": o["status"],
                    "filled": f[0] if f else 0.0,
                    "fill_id": f"f{i}" if f else None,
                    "fill_qty": f[0] if f else 0.0,
                    "fill_price": f[1] if f else None,
                    "filled_at": now if f else None,
                    "taxes": 0.0,
                }
            )
        history = pd.DataFrame(rows, columns=t212.FILL_COLS)
        tx = pd.DataFrame(
            [{"type": "deposit", "amount": self.deposit, "currency": "GBP", "at": now, "ref": ""}],
            columns=t212.TRANSACTION_COLS,
        )
        return Broker(
            positions=positions,
            pending=pending,
            history=history,
            dividends=pd.DataFrame(columns=t212.DIVIDEND_COLS),
            transactions=tx,
            to_yahoo={},
        )
