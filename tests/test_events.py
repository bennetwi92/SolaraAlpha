from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from solaraalpha.analysis.events import (
    event_moves,
    expected_events,
    material,
    reaction_session,
    reactions,
)


def _ann(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["published", "type_code", "category", "headline"])
    df["published"] = pd.to_datetime(df["published"], utc=True)
    df["id"] = [f"id{i}" for i in range(len(df))]
    return df


def _daily(n: int = 120, start: str = "2026-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n, tz="UTC", name="date")
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_material_drops_routine() -> None:
    ann = _ann([("2026-01-05", "POS", "", ""), ("2026-01-05", "TST", "Financial Results", "")])
    assert list(material(ann)["type_code"]) == ["TST"]


def test_reaction_session_cutoff() -> None:
    sessions = pd.bdate_range("2026-03-02", periods=10, tz="UTC")  # Mon 2 Mar
    # 07:00 London (GMT in March) before the open -> same session
    assert reaction_session(pd.Timestamp("2026-03-03 07:00", tz="UTC"), sessions) == sessions[1]
    # 16:30 London is the close -> next session
    assert reaction_session(pd.Timestamp("2026-03-03 16:30", tz="UTC"), sessions) == sessions[2]
    # Saturday -> Monday
    assert reaction_session(pd.Timestamp("2026-03-07 09:00", tz="UTC"), sessions) == sessions[5]
    # BST: 15:45 UTC is 16:45 London, after the close
    bst = pd.bdate_range("2026-06-01", periods=5, tz="UTC")
    assert reaction_session(pd.Timestamp("2026-06-01 15:45", tz="UTC"), bst) == bst[1]
    assert reaction_session(pd.Timestamp("2026-06-30", tz="UTC"), bst) is None


def test_reactions() -> None:
    daily = _daily()
    i = 80
    daily.loc[daily.index[i:], ["open", "high", "low", "close", "adj_close"]] *= 1.10
    daily.loc[daily.index[i], "volume"] = 5000.0
    s = daily.index[i]
    ann = _ann(
        [
            (f"{s:%Y-%m-%d} 07:00", "TST", "Financial Results", "Trading update"),
            ("2030-01-01 07:00", "TST", "Financial Results", "future"),
        ]
    )
    r = reactions(ann, daily)
    first = r.iloc[0]
    assert first["session"] == s
    expected_ret = daily["adj_close"].iloc[i] / daily["adj_close"].iloc[i - 1] - 1
    assert first["ret"] == pytest.approx(expected_ret)
    assert first["gap"] == pytest.approx(expected_ret)
    assert first["z"] > 5
    assert first["volume_ratio"] == pytest.approx(5.0)
    assert first["since"] == pytest.approx(
        daily["adj_close"].iloc[-1] / daily["adj_close"].iloc[i] - 1
    )
    assert pd.isna(r.iloc[1]["session"]) and np.isnan(r.iloc[1]["ret"])


def test_reactions_no_lookahead() -> None:
    daily = _daily()
    s = daily.index[80]
    ann = _ann([(f"{s:%Y-%m-%d} 07:00", "TST", "Financial Results", "")])
    full = reactions(ann, daily).iloc[0]
    later = daily.copy()
    later.loc[later.index[81:], ["open", "close", "adj_close"]] *= (
        3  # change everything after the reaction session
    )
    changed = reactions(ann, later).iloc[0]
    for col in ["gap", "ret", "z", "volume_ratio", "pre"]:
        assert changed[col] == pytest.approx(full[col])


HISTORY = _ann(
    [
        ("2023-07-06 06:00", "FR", "Financial Results", "Final Results"),
        ("2024-07-11 06:00", "FR", "Financial Results", "Final Results"),
        ("2025-07-09 06:00", "FR", "Financial Results", "Final Results"),
        ("2024-09-05 06:00", "AGM", "General Meetings", "AGM Statement"),
        ("2025-09-04 06:00", "AGM", "General Meetings", "AGM Statement"),
        ("2025-02-19 07:00", "TST", "Financial Results", "Trading Update"),  # one year only
        ("2025-07-28 06:00", "MSCL", "Miscellaneous", "Buyback"),  # not scheduled
    ]
)


def test_expected_events() -> None:
    ev = expected_events(HISTORY, "2026-06-15", horizon_weeks=16)
    assert list(ev["headline"]) == ["Final Results", "AGM Statement"]
    fr = ev.iloc[0]
    assert fr["window_start"] == pd.Timestamp("2026-07-06 06:00", tz="UTC")
    assert fr["window_end"] == pd.Timestamp("2026-07-11 06:00", tz="UTC")
    assert fr["years"] == 3
    assert fr["past"] == ["2023-07-06", "2024-07-11", "2025-07-09"]


def test_expected_events_drops_published_and_ignores_future() -> None:
    done = pd.concat(
        [HISTORY, _ann([("2026-07-08 06:00", "PRE", "Financial Results", "Final Results")])]
    )
    ev = expected_events(done, "2026-07-10", horizon_weeks=16)
    assert list(ev["headline"]) == ["AGM Statement"]
    # as of before the 2026 filing, the same input still expects it (no look-ahead)
    ev = expected_events(done, "2026-07-01", horizon_weeks=16)
    assert "Final Results" in set(ev["headline"])


def test_expected_events_kept_through_the_notice_and_run_up() -> None:
    run_up = pd.concat(
        [
            HISTORY,
            _ann(
                [
                    ("2026-06-20 06:00", "NOR", "Financial Results", "Notice of Results"),
                    ("2026-06-25 06:00", "TST", "Financial Results", "Pre-close Update"),
                    ("2026-06-26 06:00", "NOAGM", "General Meetings", "Notice of AGM"),
                ]
            ),
        ]
    )
    ev = expected_events(run_up, "2026-07-01", horizon_weeks=16)
    assert "Final Results" in set(ev["headline"])  # the notice confirms the date, not the event


def test_expected_events_overdue_stays_until_filed() -> None:
    ev = expected_events(HISTORY, "2026-07-14", horizon_weeks=4)
    fr = ev.iloc[0]
    assert fr["headline"] == "Final Results"
    assert fr["window_start"] == pd.Timestamp("2026-07-14", tz="UTC")


def test_expected_events_move() -> None:
    hist = HISTORY.assign(ret=[0.05, -0.03, 0.08, 0.01, 0.0, 0.0, 0.0])
    ev = expected_events(hist, "2026-06-15", horizon_weeks=16)
    assert ev.iloc[0]["median_abs_move"] == pytest.approx(0.05)


def test_event_moves() -> None:
    ann = _ann(
        [
            ("2026-02-02 07:00", "TST", "Financial Results", "Trading Update"),
            ("2026-02-02 07:01", "IR", "Financial Results", "Half-year Report"),  # same session
            ("2026-03-02 07:00", "NOR", "Financial Results", "Notice of Results"),  # a date
            ("2026-04-01 07:00", "FR", "Financial Results", "Final Results"),
            ("2026-04-02 07:00", "UPD", "Operational updates", "Contract"),  # not results
        ]
    )
    ann["session"] = pd.to_datetime(
        ["2026-02-02", "2026-02-02", "2026-03-02", "2026-04-01", "2026-04-02"], utc=True
    )
    ann["ret"] = [-0.30, -0.30, 0.50, 0.10, -0.60]
    got = event_moves(ann)
    assert got["n"] == 2 and got["worst"] == pytest.approx(-0.30)
    assert got["p90_abs"] == pytest.approx(0.28)  # between 0.10 and 0.30
    assert event_moves(ann.drop(columns="ret"))["n"] == 0
