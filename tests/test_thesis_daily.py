from __future__ import annotations

import sys
from dataclasses import dataclass, fields
from pathlib import Path

import pandas as pd
import pytest

from solaraalpha import store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.daily import run_daily, suppressed_conditions
from solaraalpha.portfolio.triggers import Trigger, thesis_hits
from solaraalpha.toolkits import CARDS, REGISTRY, Inputs, Toolkit, owner_of

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_daily import _daily, _fetchers

DATE = pd.Timestamp("2026-10-09", tz="UTC")


def test_owner_of_covers_every_card_field() -> None:
    for tk, types in CARDS.items():
        for t in types:
            for f in fields(t):
                owner = owner_of(f.name)
                assert owner is not None, f"{tk}.{f.name} has no owner"
    assert owner_of("trend") == "technical" and owner_of("close") == "technical"
    assert owner_of("support") == "levels" and owner_of("rs") == "relative_strength"
    assert owner_of("eps_revision") == "fundamentals" and owner_of("adv_gbp_20") == "liquidity"
    assert owner_of("next_event") == "events" and owner_of("nope") is None
    assert set(CARDS) <= set(REGISTRY)


@dataclass(frozen=True)
class Tech:
    date: pd.Timestamp
    trend: float
    momentum: float


def _conditions(*rows: tuple[int, str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "condition_id": [r[0] for r in rows],
            "condition": [r[1] for r in rows],
            "kind": [r[2] for r in rows],
        }
    )


def test_thesis_hits_weekly_field_daily_close_and_issues() -> None:
    conds = _conditions(
        (1, "trend < 0", "machine"),
        (2, "close < 1500", "machine"),
        (3, "momentum > 0", "machine"),
        (4, "FY27 guidance cut", "agent"),
        (5, "trend <<< 1", "machine"),
        (6, "nope_field > 1", "machine"),
    )
    cards = {"technical": Tech(DATE, -0.4, -0.2)}  # momentum > 0 does not hold
    hits, issues = thesis_hits("J", 1, conds, cards, 1402.0)
    assert [(h.kind, h.detail["condition_id"]) for h in hits] == [
        ("thesis_hit", 1),
        ("thesis_hit", 2),
    ]
    assert hits[0].detail == {
        "condition_id": 1,
        "condition": "trend < 0",
        "field": "trend",
        "value": -0.4,
    }
    assert hits[1].detail["value"] == 1402.0 and hits[1].position_id == 1
    assert (
        len(issues) == 2
        and "not evaluable" in issues[0]
        and "no toolkit card carries 'nope_field'" in issues[1]
    )
    # suppressed: not reported; no close: the close condition is an issue, the rest unaffected
    hits, issues = thesis_hits("J", 1, conds, cards, 1402.0, suppressed=frozenset({1}))
    assert [h.detail["condition_id"] for h in hits] == [2]
    hits, issues = thesis_hits("J", 1, conds, cards, None)
    assert [h.detail["condition_id"] for h in hits] == [1] and any("'close'" in i for i in issues)
    assert thesis_hits("J", 1, conds.iloc[0:0], cards, 1.0) == ([], [])


def test_daily_evaluates_thesis_and_suppresses_after_a_decision(
    db_backend: Path | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a stub technical toolkit: trend is negative now
    def run(x: Inputs, **params: object) -> dict:
        assert x.fetched("daily") is not None  # the daily job hands over its fetch: no refetch
        return {"technical": Tech(DATE, -0.3, 0.1)}

    monkeypatch.setitem(REGISTRY, "technical", Toolkit("technical", run, (), (), "stub"))
    conn = book.connect(db_backend)
    with conn:
        pid, oid = book.draft_position(
            conn, "JET2.L", 1500.0, 1400.0, 100, at="2026-10-11T18:30:00+00:00"
        )
        book.add_thesis_condition(conn, pid, "trend < 0", "machine")
        book.add_thesis_condition(conn, pid, "close < 1450", "machine")  # daily close 1402 hits
        book.add_thesis_condition(conn, pid, "close < 100", "machine")  # does not
        book.add_thesis_condition(conn, pid, "guidance cut", "agent")
        book.add_thesis_condition(conn, pid, "what?", "machine")  # unparseable
        book.add_thesis_condition(conn, pid, "zzz > 1", "machine")  # no owner
        book.approve_position(conn, pid)
        book.fill(conn, oid, 100, 1500.0, at="2026-10-12T09:00:00+00:00")
    frames = {"JET2.L": _daily(last={"close": 1402.0})}
    r = run_daily(conn, _fetchers(frames), save=True)
    hits = r.of(("thesis_hit",))
    assert [h.detail["condition"] for h in hits] == ["trend < 0", "close < 1450"]
    assert hits[0].at == "2026-10-20" and hits[1].detail["value"] == 1402.0
    assert sum("thesis:" in e for _, e in r.errors) == 2
    assert any("not evaluable" in e for _, e in r.errors) and any("'zzz'" in e for _, e in r.errors)
    saved = store.load_triggers(kind="thesis_hit", conn=conn)
    assert len(saved) == 2 and saved["position_id"].tolist() == [pid, pid]
    assert suppressed_conditions(conn, pid) == frozenset()
    # the same hits are reported again the next day ...
    r2 = run_daily(conn, _fetchers(frames), save=True)
    assert len(r2.of(("thesis_hit",))) == 2
    # ... until the owner decides on a revision for the position
    with conn:
        rid = book.propose_revision(
            conn, pid, "hold", {}, "noise", "agent:monitor", at="2026-10-21T18:00:00+00:00"
        )
        book.decline_revision(conn, rid, "holding", at="2026-10-21T18:05:00+00:00")
    assert suppressed_conditions(conn, pid) == {1, 2}
    r3 = run_daily(conn, _fetchers(frames))
    assert r3.of(("thesis_hit",)) == []
    # a monitor-pack selection sees the hit runs
    from solaraalpha.monitoring import positions_to_review

    assert positions_to_review(conn, int(saved["run_id"].iloc[0])) == [pid]
    conn.close()
    assert isinstance(hits[0], Trigger)
