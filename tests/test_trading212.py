from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import SecretStr

from solaraalpha.config import settings
from solaraalpha.data import trading212 as t212

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> Any:
    return json.loads((FIX / f"t212_{name}.json").read_text())


def test_one_write_door_behind_the_trading_switch() -> None:
    """Every write goes through ``_write``, which checks ``trading_allowed`` first and uses the
    session without retries (ADR 0031); reads keep the retrying session."""
    src = inspect.getsource(t212)
    assert re.findall(r"\.(post|put|patch|delete|request)\(", src) == ["request"]
    write = inspect.getsource(t212._write)
    assert ".request(" in write and "trading_allowed()" in write and "_plain()" in write
    assert "session().get(" in src


def test_parse_summary() -> None:
    s = t212.parse_summary(_load("summary"))
    assert s == {
        "currency": "GBP",
        "total": 1000.0,
        "free": 600.0,
        "reserved": 200.0,
        "invested": 200.0,
        "unrealised": -2.0,
        "realised": 0.0,
    }


def test_parse_positions_in_pence_and_utc() -> None:
    p = t212.parse_positions(_load("positions")).iloc[0]
    assert (p["t212"], p["currency"], p["qty"], p["avg_price"]) == ("AAZl_EQ", "GBX", 50.0, 400.0)
    assert p["opened_at"] == pd.Timestamp("2026-10-21T08:40:43.114Z")


def test_parse_pending_sides_and_prices() -> None:
    df = t212.parse_pending(_load("orders"))
    sell, buy = df.iloc[0], df.iloc[1]
    assert (sell["side"], sell["type"], sell["qty"], sell["price"]) == (-1, "stop", 50.0, 340.0)
    assert (buy["side"], buy["id"], buy["status"]) == (1, "900000003", "new")


def test_parse_history_with_and_without_fill() -> None:
    items = _load("history_1")["items"] + _load("history_2")["items"]
    df = t212.parse_history(items)
    filled, cancelled = df.iloc[0], df.iloc[1]
    assert (filled["fill_qty"], filled["fill_price"], filled["price"]) == (50.0, 400.0, 410.0)
    assert filled["filled_at"] == pd.Timestamp("2026-10-21T08:40:43Z")
    assert cancelled["status"] == "cancelled" and cancelled["fill_qty"] == 0
    assert pd.isna(cancelled["price"]) and cancelled["type"] == "market"


def test_parse_dividends_and_transactions() -> None:
    d = t212.parse_dividends(_load("dividends")["items"]).iloc[0]
    assert (d["t212"], d["amount"], d["per_share"]) == ("AAZl_EQ", 2.5, 5.0)
    t = t212.parse_transactions(_load("transactions")["items"]).iloc[0]
    assert (t["type"], t["amount"], t["currency"]) == ("deposit", 1000.0, "GBP")


def test_unknown_shape_fails_loudly() -> None:
    bad = _load("orders")
    bad[0]["type"] = "TRAILING_STOP"
    with pytest.raises(KeyError):
        t212.parse_pending(bad)


def test_lse_lines_by_short_name() -> None:
    lines = t212.lse_lines(t212.parse_instruments(_load("instruments")))
    assert lines == {"AAZ.L": "AAZl_EQ", "BPM.L": "BPMl1_EQ", "AVG.L": "AVGl_EQ"}  # no US,
    # no ETF, no ambiguous TIDM
    inst = t212.parse_instruments(_load("instruments"))
    inst.loc[inst["t212"] == "AVGl_EQ", "currency"] = "EUR"
    assert "AVG.L" not in t212.lse_lines(inst)
    assert t212.lse_lines(inst, currencies=None)["AVG.L"] == "AVGl_EQ"


class _Resp:
    def __init__(
        self, payload: Any, headers: dict[str, str] | None = None, status_code: int = 200
    ) -> None:
        self.payload, self.headers, self.status_code = payload, headers or {}, status_code
        self.content = b"" if payload is None else b"x"
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Any:
        return self.payload


class _Session:
    def __init__(self, responses: list[_Resp]) -> None:
        self.responses, self.calls = responses, []  # type: ignore[var-annotated]

    def get(self, url: str, **kw: Any) -> _Resp:
        self.calls.append((url, kw))
        return self.responses.pop(0)


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "t212_key", SecretStr("k"))
    monkeypatch.setattr(settings, "t212_secret", SecretStr("s"))
    monkeypatch.setattr(settings, "t212_env", "demo")
    monkeypatch.setattr(t212, "_reset", {})


def test_pages_followed_on_demo_with_basic_auth(
    keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _Session([_Resp(_load("history_1")), _Resp(_load("history_2"))])
    monkeypatch.setattr(t212, "session", lambda: s)
    df = t212.fetch_history()
    assert len(df) == 2
    (u1, kw1), (u2, _) = s.calls
    assert u1 == "https://demo.trading212.com/api/v0/equity/history/orders"
    assert kw1["params"] == {"limit": 50} and kw1["auth"] == ("k", "s")
    assert (
        u2 == "https://demo.trading212.com/api/v0/equity/history/orders?limit=50&cursor=900000000"
    )


def test_sleeps_before_the_next_call_when_the_window_is_spent(
    keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 1_000.0
    slept: list[float] = []
    monkeypatch.setattr(t212.time, "time", lambda: now)
    monkeypatch.setattr(t212.time, "sleep", slept.append)
    spent = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1010"}
    s = _Session([_Resp(_load("summary"), spent), _Resp([]), _Resp(_load("summary"))])
    monkeypatch.setattr(t212, "session", lambda: s)
    t212.fetch_summary()
    t212.fetch_positions()  # another endpoint: no wait
    assert slept == []
    t212.fetch_summary()
    assert slept == [11.0]


def test_env_and_keys_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="keys not set"):
        t212.fetch_summary()
    monkeypatch.setattr(settings, "t212_key", SecretStr("k"))
    monkeypatch.setattr(settings, "t212_secret", SecretStr("s"))
    monkeypatch.setattr(settings, "t212_env", "paper")
    with pytest.raises(ValueError, match="demo or live"):
        t212.fetch_summary()


@pytest.mark.network
def test_live_read_endpoints() -> None:
    if not t212.configured():
        pytest.skip("Trading212 keys not set")
    assert t212.fetch_summary()["currency"] == "GBP"
    assert list(t212.fetch_positions().columns) == t212.POSITION_COLS
    assert list(t212.fetch_pending().columns) == t212.ORDER_COLS


class _Writer:
    """A no-retry session that answers each request in turn (or raises)."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers, self.calls = answers, []  # type: ignore[var-annotated]

    def request(self, method: str, url: str, **kw: Any) -> _Resp:
        self.calls.append((method, url, kw.get("json")))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    def get(self, url: str, **kw: Any) -> _Resp:
        return self.request("GET", url, **kw)


@pytest.fixture
def trading(keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "t212_trading", "demo")


def test_place_sends_once_in_pence(trading: None, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _Writer([_Resp(_load("place_limit"))])
    monkeypatch.setattr(t212, "_plain", lambda: w)
    o = t212.place("limit", "AAZl_EQ", 1, 1, 274.0)
    assert w.calls == [
        (
            "POST",
            "https://demo.trading212.com/api/v0/equity/orders/limit",
            {
                "ticker": "AAZl_EQ",
                "quantity": 1,
                "limitPrice": 274.0,
                "timeValidity": "GOOD_TILL_CANCEL",
            },
        )
    ]
    assert o["id"] == "900000010" and o["status"] == "new" and o["price"] == 274.0
    assert t212.order_body("stop", "AAZl_EQ", 73, -1, 337.0) == {
        "ticker": "AAZl_EQ",
        "quantity": -73,
        "stopPrice": 337.0,
        "timeValidity": "GOOD_TILL_CANCEL",
    }
    assert t212.order_body("market", "AAZl_EQ", 5, -1, None) == {
        "ticker": "AAZl_EQ",
        "quantity": -5,
    }


def test_write_errors(trading: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    now = 1000.0
    monkeypatch.setattr(t212.time, "time", lambda: now)
    w = _Writer(
        [
            _Resp(_load("error_not_owned"), status_code=400),
            _Resp(_load("error_rate"), {"x-ratelimit-reset": "1003"}, status_code=429),
            requests.Timeout("read timed out"),
            _Resp({"x": 1}, status_code=502),
            _Resp(None, status_code=200),
            _Resp({"type": "/api-errors/entity-not-found"}, status_code=404),
        ]
    )
    monkeypatch.setattr(t212, "_plain", lambda: w)
    monkeypatch.setattr(t212.time, "sleep", lambda s: None)
    with pytest.raises(t212.T212Error) as e:
        t212.place("limit", "AAZl_EQ", 1, -1, 510.0)
    assert e.value.status == 400 and e.value.type == "/api-errors/selling-equity-not-owned"
    assert "owned: 0.0" in e.value.detail and not e.value.rate_limited
    with pytest.raises(t212.T212Error) as e:
        t212.place("stop", "AAZl_EQ", 1, 1, 510.0)
    assert e.value.rate_limited and e.value.type == "TooManyRequests"
    assert t212._reset["https://demo.trading212.com/api/v0/equity/orders/stop"] == 1003.0
    with pytest.raises(t212.Unknown, match="Timeout"):
        t212.place("limit", "AAZl_EQ", 1, 1, 274.0)
    with pytest.raises(t212.Unknown, match="HTTP 502"):
        t212.place("limit", "AAZl_EQ", 1, 1, 274.0)
    t212.cancel("900000010")  # 200, empty body
    assert w.calls[-1][:2] == (
        "DELETE",
        "https://demo.trading212.com/api/v0/equity/orders/900000010",
    )
    assert t212.fetch_order("900000010") is None  # 404: no longer working
    assert len(w.calls) == 6  # each call sent once, none retried


def _hist_item(oid: int, created: str) -> dict[str, Any]:
    item = _load("history_1")["items"][0]
    return {**item, "order": {**item["order"], "id": oid, "createdAt": created}}


def test_history_pages_stop_at_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        "/equity/history/orders": {
            "items": [_hist_item(3, "2026-10-23T09:00:00Z")],
            "nextPagePath": "p2",
        },
        "p2": {"items": [_hist_item(2, "2026-10-10T09:00:00Z")], "nextPagePath": "p3"},
        "p3": {"items": [_hist_item(1, "2026-09-01T09:00:00Z")], "nextPagePath": None},
    }
    asked: list[str] = []

    def get(path: str, **params: Any) -> Any:
        asked.append(path)
        return pages[path]

    monkeypatch.setattr(t212, "_get", get)
    recent = t212.fetch_history(newer_than=pd.Timestamp("2026-10-20", tz="UTC"))
    assert list(recent["id"]) == ["3", "2"] and asked == ["/equity/history/orders", "p2"]
    asked.clear()
    assert len(t212.fetch_history()) == 3 and len(asked) == 3


def test_instruments_cached_per_account(monkeypatch: pytest.MonkeyPatch, tmp_cache: Path) -> None:
    calls: list[str] = []

    def get(path: str, **params: Any) -> Any:
        calls.append(path)
        return _load("instruments")

    monkeypatch.setattr(settings, "cache_dir", tmp_cache)
    monkeypatch.setattr(t212, "_get", get)
    monkeypatch.setattr(settings, "t212_env", "demo")
    t212.fetch_instruments()
    t212.fetch_instruments()
    assert len(calls) == 1  # the second call within a day is the cache
    monkeypatch.setattr(settings, "t212_env", "live")
    t212.fetch_instruments()
    assert len(calls) == 2  # never the practice account's list for live
