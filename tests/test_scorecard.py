from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha import store, track_record
from solaraalpha.analysis.scorecard import (
    HORIZONS,
    forward_returns,
    label_scorecard,
    toolkit_scorecard,
    weekly_adj,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import learn

START = "2026-01-05"  # a Monday


def _daily(
    ticker: str, weekly_growth: float, weeks: int = 30, currency: str = "GBp"
) -> pd.DataFrame:
    """Daily bars whose Friday closes compound at ``weekly_growth`` per week; week 0 closes at 100."""
    idx = pd.bdate_range(START, periods=weeks * 5, tz="UTC", name="date")
    week = np.arange(len(idx)) // 5
    close = 100.0 * (1 + weekly_growth) ** week
    close = close + (np.arange(len(idx)) % 5) * 0.01  # not flat inside the week
    df = pd.DataFrame(
        {
            "open": np.r_[close[0], close[:-1]],  # each session opens at the last close: no gaps
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "adj_close": close,
            "volume": 1e5,
        },
        index=idx,
    )
    df.attrs = {"ticker": ticker, "currency": currency}
    return df


def _friday(week: int) -> pd.Timestamp:
    return pd.Timestamp(START, tz="UTC") + pd.Timedelta(days=4 + 7 * week)


def test_forward_returns_enter_at_mondays_open() -> None:
    d = _daily("A.L", 0.0)
    monday = d.index[d.index > _friday(2)][0]
    d.loc[monday, "open"] = 110.0  # Monday gaps up 10% on the news: a Friday entry was not there
    labels = pd.DataFrame(
        {
            "run_id": [1],
            "ticker": ["A.L"],
            "stage": ["screen"],
            "label": ["ACT"],
            "date": [str(_friday(2).date())],
        }
    )
    fwd = forward_returns(labels, {"A.L": d}, horizons=(1,), as_of=_friday(40))
    assert fwd.iloc[0]["ret_1w"] == pytest.approx(float(d["close"][_friday(3)]) / 110.0 - 1)


def test_forward_returns_from_the_card_week() -> None:
    prices = {"A.L": _daily("A.L", 0.01), "B.L": _daily("B.L", -0.02)}
    bench = {"^FTAS": _daily("^FTAS", 0.005, currency="GBP")}
    labels = pd.DataFrame(
        {
            "run_id": [1, 1],
            "ticker": ["A.L", "B.L"],
            "stage": ["screen", "screen"],
            "label": ["ACT", "PASS"],
            "date": [str(_friday(2).date()), str(_friday(2).date())],
        }
    )
    fwd = forward_returns(
        labels, prices, bench=bench, market_of={"A.L": "^FTAS", "B.L": "^FTAS"}, as_of=_friday(40)
    )
    a = fwd.iloc[0]
    for n in HORIZONS:
        assert a[f"ret_{n}w"] == pytest.approx(1.01**n - 1, rel=1e-3)
        assert a[f"exc_{n}w"] == pytest.approx((1.01**n - 1) - (1.005**n - 1), rel=1e-2)
    assert fwd.iloc[1]["ret_4w"] == pytest.approx(0.98**4 - 1, rel=1e-3)
    assert fwd["date"].iloc[0] == _friday(2)


def test_no_look_ahead_boundary_and_nan_tails() -> None:
    prices = {"A.L": _daily("A.L", 0.01, weeks=30)}
    labels = pd.DataFrame(
        {
            "run_id": [1, 2, 3],
            "ticker": ["A.L"] * 3,
            "stage": ["screen"] * 3,
            "label": ["ACT"] * 3,
            "date": [str(_friday(w).date()) for w in (10, 20, 28)],
        }
    )
    # as of the Friday of week 14: the week-14 bar is not complete yet, so 4w from week 10 is NaN
    fwd = forward_returns(labels, prices, as_of=_friday(14))
    r = fwd.iloc[0]
    assert not math.isnan(r["ret_2w"]) and math.isnan(r["ret_4w"]) and math.isnan(r["ret_8w"])
    # one day later the bar is complete and the 4w return exists
    fwd = forward_returns(labels, prices, as_of=_friday(14) + pd.Timedelta(days=1))
    assert fwd.iloc[0]["ret_4w"] == pytest.approx(1.01**4 - 1, rel=1e-3)
    # with all data: week 20 has 2/4/8 but not 16 (data ends at week 29); week 28 has none
    fwd = forward_returns(labels, prices, as_of=_friday(40))
    assert not math.isnan(fwd.iloc[1]["ret_8w"]) and math.isnan(fwd.iloc[1]["ret_16w"])
    assert all(math.isnan(fwd.iloc[2][f"ret_{n}w"]) for n in HORIZONS)
    assert fwd["exc_2w"].isna().all()  # no benchmark given
    # an unknown ticker scores NaN, and a label date with no bar scores NaN
    odd = pd.DataFrame(
        {
            "run_id": [9, 9],
            "ticker": ["Z.L", "A.L"],
            "stage": ["screen", "screen"],
            "label": ["ACT", "ACT"],
            "date": [str(_friday(1).date()), "2025-01-03"],
        }
    )
    assert forward_returns(odd, prices, as_of=_friday(40))["ret_2w"].isna().all()
    assert len(weekly_adj(prices["A.L"], as_of=_friday(40))) == 30


def test_grouping_by_label_and_toolkit() -> None:
    prices = {"A.L": _daily("A.L", 0.01), "B.L": _daily("B.L", -0.02), "C.L": _daily("C.L", 0.0)}
    labels = pd.DataFrame(
        {
            "run_id": [1, 1, 1, 1, 2],
            "ticker": ["A.L", "B.L", "C.L", "A.L", "A.L"],
            "stage": ["screen", "screen", "screen", "validate", "screen"],
            "label": ["ACT", "PASS", "WATCH", "ACT", "ERROR"],
            "date": [str(_friday(2).date())] * 4 + [str(_friday(28).date())],
        }
    )
    fwd = forward_returns(labels, prices, as_of=_friday(40))
    by = label_scorecard(fwd)
    assert list(zip(by["stage"], by["label"], strict=True)) == [
        ("screen", "ACT"),
        ("screen", "WATCH"),
        ("screen", "PASS"),
        ("validate", "ACT"),
    ]  # ERROR rows are not scored
    act = by.iloc[0]
    assert act["count"] == 1 and act["n_4w"] == 1 and act["hit_4w"] == 1.0
    assert act["mean_4w"] == pytest.approx(1.01**4 - 1, rel=1e-3)
    assert by.iloc[2]["hit_4w"] == 0.0 and math.isnan(by.iloc[2]["excess_4w"])
    steps = pd.DataFrame(
        {
            "run_id": [1, 1, 1, 1, 1],
            "ticker": ["A.L", "A.L", "B.L", "C.L", "C.L"],
            "toolkit": ["technical", "levels", "technical", "technical", "levels"],
            "status": ["act", "act", "pass", "watch", "error"],
        }
    )
    tk = toolkit_scorecard(steps, fwd)
    assert list(zip(tk["toolkit"], tk["label"], strict=True)) == [
        ("levels", "ACT"),
        ("technical", "ACT"),
        ("technical", "WATCH"),
        ("technical", "PASS"),
    ]
    assert tk.iloc[3]["mean_4w"] == pytest.approx(0.98**4 - 1, rel=1e-3)
    assert label_scorecard(fwd.iloc[0:0]).empty and toolkit_scorecard(steps, fwd.iloc[0:0]).empty


def test_learn_scorecard_script_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "solara.db"
    frames = {"A.L": _daily("A.L", 0.01), "B.L": _daily("B.L", -0.02)}
    conn = store.connect(db)
    with conn:
        run_id = store.start_run("scan", {}, conn=conn)
        for t, status in (("A.L", "act"), ("B.L", "pass")):
            store.save_ticker(
                run_id,
                t,
                cards={"technical": {"date": str(_friday(2).date()), "score": 0.5}},
                daily=frames[t],
                steps=[("screen", "technical", status, "")],
                conn=conn,
            )
        store.save_label(run_id, "A.L", "screen", "ACT", "", conn=conn)
        store.save_label(run_id, "B.L", "screen", "PASS", "", conn=conn)
    conn.close()
    # the benchmark has no saved history: it is fetched
    monkeypatch.setattr(track_record, "fetch_daily", lambda t: _daily(t, 0.005, currency="GBP"))
    learn.main(["scorecard", "--db", str(db)])
    out = capsys.readouterr().out
    assert "2 labels scored" in out and "By stage x label:" in out and "By toolkit x band:" in out
    assert "screen   ACT" in out and "technical   ACT" in out
    learn.main(["scorecard", "--db", str(db), "--since", "2030-01-01"])
    assert "No labels to score yet" in capsys.readouterr().out
