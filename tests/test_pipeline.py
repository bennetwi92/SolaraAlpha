from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import toolkits
from solaraalpha.pipeline import Condition, Stage, run_pipeline, run_toolkit, watchlist
from solaraalpha.toolkits import REGISTRY, Inputs, Toolkit


@dataclass(frozen=True)
class Card:
    date: pd.Timestamp
    score: float


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Toolkits a and b (pass when the ticker's score > 0) and boom (raises); logs each call."""
    calls: list[tuple[str, str]] = []
    scores = {"UP.L": 0.5, "DOWN.L": -0.5}

    def make(id: str) -> Toolkit:
        def run(x: Inputs) -> dict:
            calls.append((id, x.ticker))
            if id == "boom":
                raise RuntimeError("source down")
            return {id: Card(pd.Timestamp("2026-10-09", tz="UTC"), scores.get(x.ticker, 1.0))}

        return Toolkit(id, run, ("score > 0",), id)

    for id in ("a", "b", "c", "boom"):
        monkeypatch.setitem(REGISTRY, id, make(id))
    return calls


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("score >= 0.5", True),
        ("score > 0.5", False),
        ("score != -1", True),
        ("flag == true", True),
        ("flag == false", False),
        ("label == hold", True),
        ("label != hold", False),
    ],
)
def test_condition(text: str, ok: bool) -> None:
    card = {"score": 0.5, "flag": True, "label": "hold"}
    assert Condition.parse(text).check(card) is ok


def test_condition_nan_fails_and_bad_input_raises() -> None:
    assert not Condition.parse("x > -1").check({"x": math.nan})
    assert not Condition.parse("x < 1").check({"x": None})
    with pytest.raises(KeyError):
        Condition.parse("y > 0").check({"x": 1.0})
    with pytest.raises(ValueError, match="bad condition"):
        Condition.parse("x is big")


def test_stages_run_in_order_and_only_passers_move_on(fake: list[tuple[str, str]]) -> None:
    stages = (Stage("one", ("a", "b")), Stage("two", ("c",)))
    got = run_pipeline([Inputs("UP.L"), Inputs("DOWN.L")], stages)
    assert [(s.toolkit, s.status) for s in got["UP.L"]] == [
        ("a", "pass"),
        ("b", "pass"),
        ("c", "pass"),
    ]
    assert [(s.toolkit, s.status) for s in got["DOWN.L"]] == [
        ("a", "fail"),
        ("b", "fail"),
        ("c", "skip"),
    ]
    # stage by stage: both toolkits of stage one ran on every stock (a fail doesn't stop b),
    # and before stage two started
    assert fake == [("a", "UP.L"), ("b", "UP.L"), ("a", "DOWN.L"), ("b", "DOWN.L"), ("c", "UP.L")]
    assert got["DOWN.L"][0].reason.startswith("failed score > 0.0")
    assert got["DOWN.L"][2].reason == "out at one"


def test_errors_are_isolated(fake: list[tuple[str, str]]) -> None:
    got = run_pipeline([Inputs("UP.L")], (Stage("one", ("boom", "a")), Stage("two", ("c",))))
    boom, a, c = got["UP.L"]
    assert boom.status == "error" and "source down" in boom.reason
    assert a.status == "pass"
    assert c.status == "skip"


def test_missing_verdict_field_is_an_error(
    fake: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(REGISTRY, "a", Toolkit("a", REGISTRY["a"].run, ("nope > 1",), "a"))
    assert run_toolkit("a", Inputs("UP.L")).status == "error"


def test_watchlist(tmp_path: Path) -> None:
    (tmp_path / "w.toml").write_text('tickers = ["A.L", "B.L"]\n')
    assert watchlist(tmp_path / "w.toml") == ["A.L", "B.L"]
    assert watchlist()  # the checked-in one loads


def _daily(n: int = 800) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(0.002 + 0.01 * rng.standard_normal(n)))
    idx = pd.bdate_range(end="2026-10-09", periods=n, tz="UTC", name="date")
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "adj_close": close,
            "volume": 1e6,
        },
        index=idx,
    )
    df.attrs = {"ticker": "TEST.L", "currency": "GBP"}
    return df


def test_real_toolkits_share_one_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    fetches = []

    def fetch_daily(ticker: str) -> pd.DataFrame:
        fetches.append(ticker)
        return _daily()

    monkeypatch.setattr(toolkits, "fetch_daily", fetch_daily)
    x = Inputs("TEST.L")
    assert x.fetched("daily") is None
    tech = run_toolkit("technical", x, {"entry_date": "2026-06-05"})
    lev = run_toolkit("levels", x)
    assert fetches == ["TEST.L"]
    assert x.fetched("daily") is not None and x.fetched("fund") is None
    assert tech.status in ("pass", "fail") and set(tech.cards) == {"technical", "position"}
    assert lev.status in ("pass", "fail") and "reward_risk" in lev.reason
