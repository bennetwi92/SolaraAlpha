from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import toolkits
from solaraalpha.pipeline import (
    Clause,
    Condition,
    Stage,
    Step,
    band,
    combine,
    floor,
    rank_act,
    run_pipeline,
    run_toolkit,
    screen,
    screen_label,
    watch_on,
    watchlist,
)
from solaraalpha.toolkits import REGISTRY, Inputs, Toolkit

DATE = pd.Timestamp("2026-10-09", tz="UTC")


@dataclass(frozen=True)
class Card:
    date: pd.Timestamp
    score: float


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Toolkits a, b, c (ACT when score > 0, WATCH when score > -0.5, else PASS), boom (raises);
    logs each call. UP.L scores 0.5, MID.L -0.3, DOWN.L -0.9, others 1.0."""
    calls: list[tuple[str, str]] = []
    scores = {"UP.L": 0.5, "MID.L": -0.3, "DOWN.L": -0.9}

    def make(id: str) -> Toolkit:
        def run(x: Inputs) -> dict:
            calls.append((id, x.ticker))
            if id == "boom":
                raise RuntimeError("source down")
            return {id: Card(DATE, scores.get(x.ticker, 1.0))}

        return Toolkit(id, run, ("score > 0",), ("score > -0.5",), id)

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
        ("gap is nan", True),
        ("score is nan", False),
        ("missing is nan", True),
    ],
)
def test_condition(text: str, ok: bool) -> None:
    card = {"score": 0.5, "flag": True, "label": "hold", "gap": math.nan, "missing": None}
    assert Condition.parse(text).check(card) is ok
    assert str(Condition.parse(text)).replace(".0", "") == text.replace(".0", "")


def test_condition_nan_fails_and_bad_input_raises() -> None:
    assert not Condition.parse("x > -1").check({"x": math.nan})
    assert not Condition.parse("x < 1").check({"x": None})
    with pytest.raises(KeyError):
        Condition.parse("y > 0").check({"x": 1.0})
    with pytest.raises(ValueError, match="bad condition"):
        Condition.parse("x is big")


def test_clause_or() -> None:
    c = Clause.parse("reward_risk >= 1.5 or coil_pending == true")
    assert c.fields == ["reward_risk", "coil_pending"]
    assert c.check({"reward_risk": 1.0, "coil_pending": True})
    assert c.check({"reward_risk": 2.0, "coil_pending": False})
    assert not c.check({"reward_risk": math.nan, "coil_pending": False})
    assert str(c) == "reward_risk >= 1.5 or coil_pending == true"
    single = Clause.parse("eps_revision >= 0 or eps_revision is nan")
    assert single.check({"eps_revision": math.nan}) and not single.check({"eps_revision": -0.1})
    assert Clause.parse("x > 0").conditions == (Condition("x", ">", 0.0),)


def test_combine_precedence() -> None:
    assert combine(["ACT", "ACT"]) == "ACT"
    assert combine(["ACT", "WATCH"]) == "WATCH"
    assert combine(["WATCH", "PASS", "ACT"]) == "PASS"
    assert combine(["PASS", "ERROR", "ACT"]) == "ERROR"
    assert combine([]) == "ACT"


def _tk(id: str, act: tuple[str, ...], watch: tuple[str, ...]) -> Toolkit:
    return Toolkit(id, lambda x: {}, act, watch, id)


def _band(
    monkeypatch: pytest.MonkeyPatch, toolkit: str, **fields: float | bool | None
) -> tuple[str, str]:
    @dataclass(frozen=True)
    class C:
        date: pd.Timestamp
        __annotations__.update({k: object for k in fields})

    card = C(**{"date": DATE, **fields})  # type: ignore[arg-type]
    return band({toolkit: card}, toolkit)


@pytest.mark.parametrize(
    ("fields", "label"),
    [
        (dict(trend=0.25, momentum=0.01, entry=0.0, risk_pct=0.15), "ACT"),
        (dict(trend=0.3, momentum=0.2, entry=-0.4, risk_pct=0.1), "WATCH"),  # extended
        (dict(trend=0.0, momentum=-0.25, entry=0.5, risk_pct=0.1), "WATCH"),
        (dict(trend=-0.01, momentum=0.2, entry=0.5, risk_pct=0.1), "PASS"),
        (dict(trend=0.3, momentum=-0.3, entry=0.5, risk_pct=0.1), "PASS"),
        (dict(trend=0.3, momentum=0.2, entry=0.5, risk_pct=0.4), "ACT"),  # volatile is fine
    ],
)
def test_technical_bands(monkeypatch: pytest.MonkeyPatch, fields: dict, label: str) -> None:
    assert _band(monkeypatch, "technical", **fields)[0] == label


@pytest.mark.parametrize(
    ("fields", "label"),
    [
        # context, not a gate (#194): compressed or a new high is ACT, reward_risk never counts
        (dict(compressed=True, new_high=False, new_low=False, reward_risk=0.3), "ACT"),
        (dict(compressed=False, new_high=True, new_low=False, reward_risk=math.inf), "ACT"),
        (dict(compressed=False, new_high=False, new_low=False, reward_risk=5.0), "WATCH"),
        (dict(compressed=False, new_high=False, new_low=False, reward_risk=math.nan), "WATCH"),
        (dict(compressed=False, new_high=False, new_low=True, reward_risk=3.0), "PASS"),
    ],
)
def test_levels_bands(monkeypatch: pytest.MonkeyPatch, fields: dict, label: str) -> None:
    assert _band(monkeypatch, "levels", **fields)[0] == label


@pytest.mark.parametrize(
    "fields",
    [
        dict(rs=0.01, rs_high=False, peer_top_half=False),
        dict(rs=-0.26, rs_high=False, peer_top_half=False),
        dict(rs=math.nan, rs_high=False, peer_top_half=False),
    ],
)
def test_relative_strength_has_no_bands(monkeypatch: pytest.MonkeyPatch, fields: dict) -> None:
    # context for the agents only (docs/research/0001, #210 phase 0): ACT whenever it runs
    assert _band(monkeypatch, "relative_strength", **fields) == ("ACT", "ran")


@pytest.mark.parametrize(
    ("fields", "label"),
    [
        (dict(growth=0.1, quality=0.0, eps_revision=0.0), "ACT"),
        (dict(growth=0.1, quality=0.0, eps_revision=math.nan), "ACT"),  # no revisions data
        (dict(growth=0.1, quality=0.0, eps_revision=-0.1), "PASS"),  # revisions falling
        (dict(growth=0.0, quality=0.0, eps_revision=0.1), "WATCH"),  # revisions rising
        (dict(growth=0.0, quality=0.0, eps_revision=0.0), "PASS"),
        (dict(growth=0.5, quality=-0.5, eps_revision=0.1), "PASS"),  # quality veto
    ],
)
def test_fundamentals_bands(monkeypatch: pytest.MonkeyPatch, fields: dict, label: str) -> None:
    assert _band(monkeypatch, "fundamentals", **fields)[0] == label


def test_band_reasons_and_no_clause_toolkit(monkeypatch: pytest.MonkeyPatch) -> None:
    label, reason = _band(
        monkeypatch, "technical", trend=0.3, momentum=0.2, entry=-0.41, risk_pct=0.1
    )
    assert (label, reason) == ("WATCH", "failed entry >= 0.0 (entry=-0.41)")
    label, reason = _band(
        monkeypatch, "technical", trend=-0.2, momentum=-0.3, entry=0.1, risk_pct=0.1
    )
    assert label == "PASS" and reason.startswith(
        "failed trend >= 0.0; momentum >= -0.25 (trend=-0.20"
    )
    assert band({}, "events") == ("ACT", "ran")


def test_stages_flow_by_running_label(fake: list[tuple[str, str]]) -> None:
    stages = (
        Stage("one", ("a", "b")),
        Stage("two", ("c",), frozenset({"ACT", "WATCH"})),
        Stage("three", ("a",), frozenset({"ACT"})),
    )
    got = run_pipeline([Inputs("UP.L"), Inputs("MID.L"), Inputs("DOWN.L")], stages)
    assert [(s.toolkit, s.status) for s in got["UP.L"]] == [
        ("a", "act"),
        ("b", "act"),
        ("c", "act"),
        ("a", "act"),
    ]
    assert [(s.toolkit, s.status) for s in got["MID.L"]] == [
        ("a", "watch"),
        ("b", "watch"),
        ("c", "watch"),  # WATCH still runs stage two
        ("a", "skip"),  # but not stage three
    ]
    assert [(s.toolkit, s.status) for s in got["DOWN.L"]] == [
        ("a", "pass"),
        ("b", "pass"),
        ("c", "skip"),
        ("a", "skip"),
    ]
    # stage by stage: every toolkit of stage one ran on every stock before stage two started
    assert fake[:6] == [
        ("a", "UP.L"),
        ("b", "UP.L"),
        ("a", "MID.L"),
        ("b", "MID.L"),
        ("a", "DOWN.L"),
        ("b", "DOWN.L"),
    ]
    assert got["DOWN.L"][0].reason == "failed score > -0.5 (score=-0.90)"
    assert got["DOWN.L"][2].reason == "running label PASS"
    assert got["MID.L"][3].reason == "running label WATCH"


def test_errors_are_isolated(fake: list[tuple[str, str]]) -> None:
    got = run_pipeline([Inputs("UP.L")], (Stage("one", ("boom", "a")), Stage("two", ("c",))))
    boom, a, c = got["UP.L"]
    assert boom.status == "error" and "source down" in boom.reason
    assert a.status == "act"
    assert c.status == "act"  # stage two runs on every label by default


def test_missing_verdict_field_is_an_error(
    fake: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(REGISTRY, "a", Toolkit("a", REGISTRY["a"].run, ("nope > 1",), (), "a"))
    assert run_toolkit("a", Inputs("UP.L")).status == "error"


def test_screen_label(fake: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    scores = {"UP.L": 0.5, "MID.L": -0.3, "DOWN.L": -0.9}

    def stub(real: str) -> Toolkit:
        def run(x: Inputs) -> dict:
            return {real: Card(DATE, scores[x.ticker])}

        return Toolkit(real, run, ("score > 0",), ("score > -0.5",), real)

    for real in ("technical", "levels", "relative_strength", "fundamentals"):
        monkeypatch.setitem(REGISTRY, real, stub(real))
    monkeypatch.setitem(
        REGISTRY,
        "liquidity",
        Toolkit("liquidity", lambda x: {"liquidity": Card(DATE, 0.0)}, (), (), "l"),
    )
    monkeypatch.setitem(REGISTRY, "events", Toolkit("events", REGISTRY["boom"].run, (), (), "e"))
    got = run_pipeline([Inputs("UP.L"), Inputs("MID.L"), Inputs("DOWN.L")])
    assert screen_label(got["UP.L"]) == (
        "ACT",
        "technical ACT; levels ACT; relative_strength ACT; fundamentals ACT",
    )
    label, reason = screen_label(got["MID.L"])
    assert label == "WATCH"
    assert reason.startswith("technical WATCH: failed score > 0.0 (score=-0.30); levels WATCH")
    assert screen_label(got["DOWN.L"])[0] == "PASS"
    assert [s.status for s in got["MID.L"]] == ["watch", "watch", "watch", "act", "watch", "skip"]
    # fundamentals runs on every ticker without an ERROR (#192), PASS included
    assert [s.status for s in got["DOWN.L"]] == ["pass", "pass", "pass", "act", "pass", "skip"]
    # events runs on the shortlist only; no adv_gbp_20 in these cards: nothing passes the floor
    assert got["UP.L"][-1].status == "skip" and got["UP.L"][-1].reason == "not shortlisted"
    assert screen_label(got["UP.L"])[0] == "ACT"
    assert screen_label([]) == ("ERROR", "no toolkit ran")


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
    assert tech.status in ("act", "watch", "pass") and set(tech.cards) == {"technical", "position"}
    assert lev.status in ("act", "watch", "pass") and "compressed" in lev.reason


def _step(toolkit: str, label: str, reason: str = "", **fields: object) -> Step:
    @dataclass(frozen=True)
    class C:
        date: pd.Timestamp
        __annotations__.update({k: object for k in fields})

    card = C(**{"date": DATE, **fields})  # type: ignore[arg-type]
    return Step("screen", toolkit, label.lower(), reason, {toolkit: card})


def test_watch_on_per_failing_toolkit() -> None:
    steps = [
        _step("technical", "WATCH", "failed entry >= 0", entry=-0.41, ma10=1480.5, ma40=1300.0),
        _step("levels", "WATCH", "coil", coil_pending=True, trigger=1520.0, reward_risk=1.6),
        _step("relative_strength", "ACT", rs=0.3),
        _step("fundamentals", "WATCH", "failed growth > 0", eps_revision=0.12, growth=-0.1),
    ]
    assert watch_on(steps) == [
        {"toolkit": "technical", "kind": "pullback", "level": 1480.5, "entry": -0.41},
        {"toolkit": "levels", "kind": "break", "trigger": 1520.0},
        {"toolkit": "fundamentals", "kind": "revisions", "eps_revision": 0.12},
    ]
    assert watch_on(
        [
            _step("levels", "WATCH", "loose", coil_pending=False, trigger=None, compression=0.2),
            _step("relative_strength", "WATCH", "rs", rs=-0.1),
            _step("technical", "WATCH", "failed trend >= 0.25", entry=0.3, trend=0.1, ma10=1.0),
        ]
    ) == [
        {"toolkit": "levels", "kind": "compression", "compression": 0.2},
        {"toolkit": "relative_strength", "kind": "rs", "rs": -0.1},
        {"toolkit": "technical", "kind": "technical", "reason": "failed trend >= 0.25"},
    ]
    assert watch_on([_step("technical", "ACT", entry=0.5), _step("events", "WATCH", "x")]) is None
    assert watch_on([]) is None
    # NaN fields become None (JSON null)
    got = watch_on([_step("fundamentals", "WATCH", "", eps_revision=float("nan"))])
    assert got == [{"toolkit": "fundamentals", "kind": "revisions", "eps_revision": None}]


def test_rank_act_is_deterministic() -> None:
    @dataclass(frozen=True)
    class T:
        date: pd.Timestamp
        trend: float

    cards = {
        "B.L": {"technical": T(DATE, 0.5)},
        "A.L": {"technical": T(DATE, 0.5)},  # tie with B.L: ticker order
        "Z.L": {"technical": T(DATE, float("nan"))},  # no score: last
        "C.L": {"technical": T(DATE, 0.9)},
        "D.L": {},  # no cards: last, after Z.L by ticker? no: both NaN, ticker order
    }
    assert rank_act(cards) == {"C.L": 1, "A.L": 2, "B.L": 3, "D.L": 4, "Z.L": 5}
    assert rank_act(dict(reversed(list(cards.items())))) == rank_act(cards)
    assert rank_act({}) == {}


def _ticker(trend: float, label: str = "ACT", adv: float | None = 1e6) -> list[Step]:
    steps = [_step("technical", label, f"technical {label.lower()}", trend=trend)]
    if adv is not None:
        steps.append(_step("liquidity", "ACT", "ran", adv_gbp_20=adv))
    return steps


def test_screen_ranks_a_floor_into_a_shortlist_with_catalysts() -> None:
    steps = {
        "A.L": _ticker(0.9),
        "B.L": _ticker(0.5, "PASS"),  # a band PASS still ranks: bands are information
        "C.L": _ticker(0.3),  # all-ACT bands, but outside the top 2: WATCH
        "D.L": _ticker(0.1, "WATCH"),  # outside the top 2, joins on a catalyst
        "E.L": _ticker(0.0),  # a catalyst too, but past the catalyst cap
        "I.L": _ticker(0.95, adv=1_000.0),  # best score, too thin to trade: below the floor
        "N.L": _ticker(0.8, adv=None),  # no liquidity card
        "X.L": [Step("screen", "technical", "error", "RuntimeError: no data")],
    }
    catalysts = {"A.L": "results beat", "D.L": "contract win", "E.L": "new CEO", "I.L": "bid"}
    got = screen(steps, size=2, catalysts=catalysts, catalyst_cap=1, min_adv_gbp=25_000)
    short = {t: s.shortlist for t, s in got.items() if s.shortlist}
    assert short == {
        "A.L": "rank 1; catalyst: results beat",
        "B.L": "rank 2",
        "D.L": "catalyst: contract win",
    }
    assert all(got[t].label == "ACT" for t in short)
    assert {t: s.rank for t, s in got.items()} == {
        "A.L": 1,
        "B.L": 2,
        "C.L": 3,
        "D.L": 4,
        "E.L": 5,
        "I.L": None,
        "N.L": None,
        "X.L": None,
    }
    assert got["B.L"].reason == "shortlist rank 2; technical PASS: technical pass"
    assert (got["C.L"].label, got["C.L"].reason) == (
        "WATCH",
        "rank 3, not shortlisted; technical ACT",
    )
    assert got["E.L"].label == "WATCH" and "(catalyst: new CEO)" in got["E.L"].reason
    assert got["I.L"].label == "PASS"
    assert got["I.L"].reason.startswith(
        "floor: adv_gbp_20 1,000 < min_adv_gbp 25,000 (catalyst: bid)"
    )
    assert got["N.L"].label == "PASS" and got["N.L"].reason.startswith("floor: no liquidity")
    assert got["X.L"].label == "ERROR" and got["X.L"].shortlist is None
    # deterministic, and the defaults come from config/portfolio.toml
    assert screen(dict(reversed(list(steps.items()))), 2, catalysts, 1, 25_000) == got
    assert len([s for s in screen(steps).values() if s.shortlist]) == 5  # top 10 > 5 ranked
    assert floor(steps["A.L"], 25_000) is None


def test_pipeline_runs_shortlist_stages_on_the_shortlist(
    fake: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    @dataclass(frozen=True)
    class Tech:
        date: pd.Timestamp
        score: float
        trend: float

    @dataclass(frozen=True)
    class Liq:
        date: pd.Timestamp
        adv_gbp_20: float

    trend = {"UP.L": 0.5, "MID.L": -0.3, "DOWN.L": -0.9}

    def tech(x: Inputs) -> dict:
        return {"technical": Tech(DATE, trend[x.ticker], trend[x.ticker])}

    def liq(x: Inputs) -> dict:
        return {"liquidity": Liq(DATE, 1e6)}

    monkeypatch.setitem(REGISTRY, "technical", Toolkit("technical", tech, ("score > 0",), (), "t"))
    monkeypatch.setitem(REGISTRY, "liquidity", Toolkit("liquidity", liq, (), (), "l"))
    stages = (Stage("screen", ("technical", "liquidity")), Stage("late", ("a",), on_shortlist=True))
    inputs = [Inputs("UP.L"), Inputs("MID.L"), Inputs("DOWN.L")]
    got = run_pipeline(inputs, stages, size=1, catalyst_cap=1, min_adv_gbp=0.0)
    assert [s.status for s in got["UP.L"]] == ["act", "act", "act"]  # rank 1: shortlisted
    assert got["MID.L"][-1].status == "skip" and got["MID.L"][-1].reason == "not shortlisted"
    # a catalyst brings DOWN.L (band PASS, rank 3) onto the shortlist
    got = run_pipeline(inputs, stages, {"DOWN.L": "bid"}, size=1, catalyst_cap=1, min_adv_gbp=0.0)
    assert got["DOWN.L"][-1].status == "pass"  # stub a: DOWN.L scores -0.9 -> PASS, but it ran
    assert got["MID.L"][-1].status == "skip"


def test_liquidity_error_makes_the_screen_label_error() -> None:
    ok = [_step("technical", "ACT"), _step("levels", "ACT"), _step("relative_strength", "ACT")]
    assert screen_label([*ok, _step("liquidity", "ACT")])[0] == "ACT"
    err = Step("screen", "liquidity", "error", "NotImplementedError: FX", {})
    assert screen_label([*ok, err])[0] == "ERROR"
