from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from fakes import FakeTurso
from solaraalpha import store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.book import PositionState
from solaraalpha.portfolio.review import review
from solaraalpha.portfolio.rules import Rules, load_rules
from solaraalpha.toolkits import REGISTRY, Inputs, Toolkit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scan

DATE = pd.Timestamp("2026-10-09", tz="UTC")


def rules(**over: object) -> Rules:
    return Rules(**{**load_rules().__dict__, **over})


def state(stop: float | None = 1400.0) -> PositionState:
    return PositionState(
        1,
        "JET2.L",
        1,
        "GBp",
        "open",
        100,
        1500.0,
        -5.0,
        5.0,
        stop,
        1400.0,
        100.0,
        100.0,
        100,
        1500.0,
        "2026-09-14T09:00:00+00:00",
    )


def position(
    stop: float = 1450.0,
    action: str = "hold",
    weeks: int = 4,
    trend: float = 0.5,
    momentum: float = 0.3,
) -> dict:
    return {
        "date": DATE,
        "weeks_held": weeks,
        "r_multiple": 0.8,
        "stop": stop,
        "action": action,
        "trend": trend,
        "momentum": momentum,
    }


THEN = {
    "technical": {"trend": 0.6, "momentum": 0.4},
    "levels": {"structure": 0.5},
    "relative_strength": {"rs": 0.3},
    "fundamentals": {"growth": 0.2, "quality": 0.1},
}
NOW = {
    "technical": {"date": DATE, "trend": 0.6, "momentum": 0.4},
    "levels": {"structure": 0.5},
    "relative_strength": {"rs": 0.3},
    "fundamentals": {"growth": 0.2, "quality": 0.1},
}


def test_stop_ratchets_up_and_never_down() -> None:
    c = review(state(1400.0), THEN, {**NOW, "position": position(stop=1450.0)}, rules())
    assert (c.old_stop, c.new_stop, c.stop_moved) == (1400.0, 1450.0, True)
    assert c.action == "hold" and "stop raised 1,400.0 → 1,450.0" in c.reason
    c = review(state(1400.0), THEN, {**NOW, "position": position(stop=1350.0)}, rules())
    assert (c.new_stop, c.stop_moved) == (1400.0, False)
    c = review(state(None), THEN, {**NOW, "position": position(stop=1350.0)}, rules())
    assert (
        math.isnan(c.old_stop) and c.new_stop == 1350.0 and c.stop_moved and "stop set" in c.reason
    )
    c = review(state(1400.0), THEN, {**NOW, "position": position(stop=math.nan)}, rules())
    assert c.new_stop == 1400.0 and not c.stop_moved


def test_deterioration_threshold() -> None:
    tech: dict[str, object] = {"date": DATE, "trend": 0.36, "momentum": 0.4}
    now = {**NOW, "technical": tech, "relative_strength": {"rs": 0.05}}
    c = review(state(), THEN, {**now, "position": position(stop=1400.0)}, rules())
    assert (
        c.deteriorated == "rs +0.30→+0.05" and c.action == "review"
    )  # trend fell 0.24: not enough
    tech["trend"] = 0.35
    c = review(state(), THEN, {**now, "position": position(stop=1400.0)}, rules())
    assert c.deteriorated == "trend +0.60→+0.35, rs +0.30→+0.05"
    c = review(state(), {}, {**NOW, "position": position(stop=1400.0)}, rules())
    assert c.deteriorated == "" and c.action == "hold"  # no entry conditions: nothing to compare


def test_horizon_rule_and_position_actions() -> None:
    assert (
        review(state(), THEN, {**NOW, "position": position(weeks=10, trend=-0.2)}, rules()).action
        == "hold"
    )
    c = review(
        state(), THEN, {**NOW, "position": position(weeks=17, trend=-0.1, momentum=0.2)}, rules()
    )
    assert c.action == "exit" and c.past_horizon and "past the 16-week horizon" in c.reason
    c = review(
        state(), THEN, {**NOW, "position": position(weeks=17, trend=0.3, momentum=0.0)}, rules()
    )
    assert c.action == "exit"  # stalled momentum beyond the horizon
    c = review(
        state(), THEN, {**NOW, "position": position(weeks=17, trend=0.3, momentum=0.1)}, rules()
    )
    assert c.action == "hold" and c.past_horizon and "still climbing" in c.reason
    assert (
        review(state(), THEN, {**NOW, "position": position(action="exit", weeks=3)}, rules()).action
        == "exit"
    )
    c = review(state(), THEN, {**NOW, "position": position(action="review", weeks=3)}, rules())
    assert c.action == "review" and "turned against" in c.reason
    assert (
        review(
            state(),
            THEN,
            {**NOW, "position": position(weeks=17, trend=0.3, momentum=0.1)},
            rules(horizon_weeks=(2, 20)),
        ).past_horizon
        is False
    )


@dataclass(frozen=True)
class Card:
    date: pd.Timestamp
    score: float


@dataclass(frozen=True)
class Pos:
    date: pd.Timestamp
    weeks_held: int
    r_multiple: float
    stop: float
    action: str
    trend: float
    momentum: float


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """Stub toolkits; technical returns a position card with a chandelier stop when given an entry."""
    chandelier = {"stop": 1550.0}

    def stub(real: str) -> Toolkit:
        def run(x: Inputs, **params: object) -> dict:
            cards: dict[str, object] = {real: Card(DATE, 0.5)}
            if real == "technical" and params.get("entry_date"):
                cards["position"] = Pos(DATE, 3, 0.5, chandelier["stop"], "hold", 0.5, 0.1)
            return cards

        return Toolkit(real, run, ("score > 0",), ("score > -0.5",), real)

    for real in ("technical", "levels", "relative_strength", "fundamentals", "events"):
        monkeypatch.setitem(REGISTRY, real, stub(real))
    return chandelier


def test_scan_reviews_open_positions_and_replaces_the_stop(
    stubs: dict[str, float],
    fake_turso: FakeTurso,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = book.connect()
    with conn:
        pid, oid = book.draft_position(
            conn, "HELD.L", 1500.0, 1400.0, 100, at="2026-09-13T18:00:00+00:00"
        )
        book.add_entry_conditions(conn, pid, 0, {"technical": {"trend": 0.9}})
        book.approve_position(conn, pid)
        book.fill(conn, oid, 100, 1500.0, at="2026-09-14T09:00:00+00:00")
    conn.close()
    report = tmp_path / "r.md"
    scan.main(["UP.L", "--save", "--report", str(report)])  # HELD.L is not in the scan's tickers
    out = capsys.readouterr().out
    assert "HELD.L     review hold   set-up intact; stop raised 1,400.0 → 1,550.0" in out
    md = report.read_text()
    assert "**Open positions (weekly review):**" in md
    assert "- HELD.L (position 1): **hold**" in md and "new stop order 3 approved" in md
    assert "| HELD.L |" not in md  # not a screen row
    conn = book.connect()
    st = book.position_state(conn, pid)
    assert st.stop == 1550.0
    od = book.orders(conn, pid)
    stops = od[od["purpose"] == "stop"]
    assert list(stops["status"]) == ["cancelled", "approved"] and list(stops["price"]) == [
        1400.0,
        1550.0,
    ]
    assert "stop_raised" in set(book.position_events(conn, pid)["kind"])
    conn.close()
    steps = store.load_steps("HELD.L")
    assert set(steps["stage"]) == {"review"} and list(steps["toolkit"]) == [
        "technical",
        "levels",
        "relative_strength",
        "fundamentals",
    ]
    assert store.load_labels(
        ticker="HELD.L"
    ).empty  # no screen label for a position outside the watchlist
    cards = store.load_cards(1, "HELD.L")
    assert (
        cards["review"]["action"] == "hold"
        and cards["review"]["new_stop"] == 1550.0
        and cards["review"]["position_id"] == 1
    )
    assert cards["review"]["deteriorated"] == ""  # the stub cards have no trend field to compare

    # a second scan with an unchanged chandelier does not touch the stop (ratchet only)
    scan.main(["UP.L", "--save"])
    capsys.readouterr()
    conn = book.connect()
    assert len(book.orders(conn, pid)) == 3
    conn.close()
    # ... and a lower chandelier never lowers it
    stubs["stop"] = 1500.0
    scan.main(["UP.L", "--save"])
    capsys.readouterr()
    conn = book.connect()
    assert book.position_state(conn, pid).stop == 1550.0
    conn.close()
