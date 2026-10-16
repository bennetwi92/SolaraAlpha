from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import graph, toolkits
from solaraalpha.graph import Condition, load_graph, load_universe, make_graph, run_graph, single
from solaraalpha.toolkits import REGISTRY, Inputs, Toolkit


@dataclass(frozen=True)
class Card:
    date: pd.Timestamp
    score: float
    flag: bool = True
    label: str = "hold"


def _card(score: float) -> Card:
    return Card(pd.Timestamp("2026-10-09", tz="UTC"), score)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Register toolkits a (score from params), b (score 0.9) and boom (raises)."""
    calls = {"a": 0, "b": 0}

    def a(x: Inputs, score: float = 0.5) -> dict:
        calls["a"] += 1
        return {"a": _card(score)}

    def b(x: Inputs) -> dict:
        calls["b"] += 1
        return {"b": _card(0.9), "extra": _card(0.0)}

    def boom(x: Inputs) -> dict:
        raise RuntimeError("source down")

    monkeypatch.setitem(REGISTRY, "a", Toolkit("a", a, ("score > 0",), "A"))
    monkeypatch.setitem(REGISTRY, "b", Toolkit("b", b, (), "B"))
    monkeypatch.setitem(REGISTRY, "boom", Toolkit("boom", boom, (), "Boom"))
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


def test_make_graph_orders_and_validates(fake: dict[str, int]) -> None:
    g = make_graph("g", {"second": {"toolkit": "b", "needs": ["first"]}, "first": {"toolkit": "a"}})
    assert list(g.nodes) == ["first", "second"]
    assert [str(c) for c in g.nodes["first"].passes] == ["score > 0.0"]  # toolkit default
    assert g.nodes["second"].passes == ()
    with pytest.raises(ValueError, match="needs unknown"):
        make_graph("g", {"a": {"needs": ["zzz"]}})
    with pytest.raises(ValueError, match="cycle"):
        make_graph("g", {"a": {"needs": ["b"]}, "b": {"needs": ["a"]}})
    with pytest.raises(KeyError, match="unknown toolkit"):
        make_graph("g", {"nope": {}})
    with pytest.raises(ValueError, match="unknown keys"):
        make_graph("g", {"a": {"need": ["b"]}})


def test_run_graph_moves_on_only_when_needs_pass(fake: dict[str, int]) -> None:
    g = make_graph(
        "g",
        {
            "a": {},
            "b": {"needs": ["a"]},
            "low": {"toolkit": "a", "params": {"score": -0.2}},
            "after_low": {"toolkit": "b", "needs": ["a", "low"]},
        },
    )
    steps = {s.node: s for s in run_graph(g, "TEST.L")}
    assert {k: s.status for k, s in steps.items()} == {
        "a": "pass",
        "b": "pass",
        "low": "fail",
        "after_low": "skip",
    }
    assert steps["low"].reason.startswith("failed score > 0.0")
    assert steps["after_low"].reason == "needs low"
    assert set(steps["b"].cards) == {"b", "extra"}
    assert fake == {"a": 2, "b": 1}  # the skipped node never ran


def test_run_graph_isolates_errors(fake: dict[str, int]) -> None:
    g = make_graph("g", {"boom": {}, "b": {"needs": ["boom"]}, "a": {}})
    steps = {s.node: s for s in run_graph(g, "TEST.L")}
    assert steps["boom"].status == "error"
    assert "source down" in steps["boom"].reason
    assert steps["b"].status == "skip"
    assert steps["a"].status == "pass"


def test_missing_verdict_field_is_an_error(fake: dict[str, int]) -> None:
    g = make_graph("g", {"a": {"passes": ["nope > 1"]}})
    assert run_graph(g, "TEST.L")[0].status == "error"


def test_load_graph_and_universe(tmp_path: Path, fake: dict[str, int]) -> None:
    (tmp_path / "graphs").mkdir()
    (tmp_path / "universes").mkdir()
    (tmp_path / "graphs" / "g1.toml").write_text(
        'description = "test"\n[nodes.a]\npasses = ["score > 0.8"]\n[nodes.b]\nneeds = ["a"]\n'
    )
    (tmp_path / "graphs" / "g2.toml").write_text("[nodes.b]\n")
    (tmp_path / "universes" / "u.toml").write_text(
        'graph = "g1"\ntickers = ["A.L", "B.L"]\n[graphs]\n"B.L" = "g2"\n'
    )
    g = load_graph("g1", tmp_path)
    assert g.description == "test"
    assert [str(c) for c in g.nodes["a"].passes] == ["score > 0.8"]
    u = load_universe("u", tmp_path)
    assert (u.graph_for("A.L"), u.graph_for("B.L")) == ("g1", "g2")
    assert graph.universes(tmp_path) == ["u"]

    (tmp_path / "universes" / "bad.toml").write_text(
        'graph = "g1"\ntickers = []\n[graphs]\n"C.L" = "g2"\n'
    )
    with pytest.raises(ValueError, match="not in tickers"):
        load_universe("bad", tmp_path)
    (tmp_path / "universes" / "bad.toml").write_text('graph = "nope"\ntickers = ["A.L"]\n')
    with pytest.raises(FileNotFoundError, match="no graph 'nope'"):
        load_universe("bad", tmp_path)


def test_repo_config_is_valid() -> None:
    """Every graph and universe checked in under config/ loads (toolkits, needs, cycles)."""
    for p in (graph.CONFIG_DIR / "graphs").glob("*.toml"):
        load_graph(p.stem)
    assert graph.universes()
    for uid in graph.universes():
        load_universe(uid)


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
    tech = run_graph(single("technical", {"entry_date": "2026-06-05"}), "TEST.L", x)[0]
    lev = run_graph(single("levels"), "TEST.L", x)[0]
    assert fetches == ["TEST.L"]
    assert x.fetched("daily") is not None and x.fetched("fund") is None
    assert tech.status in ("pass", "fail") and set(tech.cards) == {"technical", "position"}
    assert lev.status in ("pass", "fail") and "reward_risk" in lev.reason
