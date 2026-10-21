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


def test_no_write_endpoint_anywhere() -> None:
    src = inspect.getsource(t212)
    assert not re.search(r"\.(post|put|patch|delete|request)\(", src)
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
    def __init__(self, payload: Any, headers: dict[str, str] | None = None) -> None:
        self.payload, self.headers = payload, headers or {}

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
