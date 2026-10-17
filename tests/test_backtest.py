from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha.analysis.backtest import (
    GRID,
    _with_threshold,
    forward,
    replay,
    report,
    suggest,
    sweep,
)
from solaraalpha.analysis.relative_strength import Comparisons

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backtest as backtest_cli


def _weekly(close: np.ndarray, start: str = "2020-01-03", currency: str = "GBp") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(close), freq="W-FRI", tz="UTC", name="date")
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "adj_close": close,
            "volume": 1e6,
        },
        index=idx,
    )
    df.attrs = {"ticker": "T.L", "currency": currency}
    return df


def _daily_from_weekly(w: pd.DataFrame) -> pd.DataFrame:
    """A daily frame whose Friday closes are ``w`` (for the comparison fetch)."""
    idx = pd.bdate_range(end=w.index[-1], periods=len(w) * 5, tz="UTC", name="date")
    close = np.repeat(w["close"].to_numpy(), 5)[-len(idx) :]
    df = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 1e6,
        },
        index=idx,
    )
    df.attrs = {"ticker": "^M", "currency": "GBP"}
    return df


def _trending(n: int = 140, seed: int = 2) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(0.004 + 0.03 * rng.standard_normal(n)))


def test_replay_has_no_look_ahead() -> None:
    close = _trending(110)
    full = _weekly(close)
    cut = full.iloc[:90]
    bench = _weekly(
        100 * np.exp(np.cumsum(0.001 + 0.02 * np.random.default_rng(5).standard_normal(140))),
        currency="GBP",
    )
    bench_daily = _daily_from_weekly(bench)
    comp = Comparisons("^M")
    fetch = lambda t: bench_daily  # noqa: E731
    a = replay("T.L", cut, None, comp, step=3, fetch=fetch)
    b = replay("T.L", full, None, comp, end=cut.index[-1], step=3, fetch=fetch)
    assert not a.empty and len(a) == len(b)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))
    # the card for week t is the card the live toolkit would give on data ending at t
    from solaraalpha.analysis.technical import scorecard

    last = a.iloc[-1]
    live = scorecard(cut[cut.index <= last["week"]])
    assert last["technical.trend"] == pytest.approx(live.trend) and last["week"] <= cut.index[-1]
    assert set(a["label"]) <= {"ACT", "WATCH", "PASS"} and "label_relative_strength" in a
    assert "fundamentals.growth" not in a  # no fundamentals given
    # step and window
    stepped = replay("T.L", full, None, None, step=4)
    assert (
        len(stepped) <= math.ceil(len(replay("T.L", full)) / 4) + 1
        and "label_relative_strength" not in stepped
    )
    assert replay("T.L", full.iloc[:30]).empty  # inside the warm-up


def test_forward_and_sweep_find_a_known_edge() -> None:
    # 400 synthetic weeks where the stock rises when trend was strong and falls otherwise:
    # make the replayed frame directly, so the sweep is tested on its own
    rng = np.random.default_rng(1)
    weeks = pd.date_range("2018-01-05", periods=400, freq="W-FRI", tz="UTC")
    trend = rng.uniform(-0.5, 0.8, 400)
    ret8 = np.where(trend > 0.4, 0.08, -0.02) + 0.01 * rng.standard_normal(400)
    replayed = pd.DataFrame(
        {
            "week": weeks,
            "ticker": "S.L",
            "technical.trend": trend,
            "technical.momentum": 0.3,
            "technical.entry": 0.2,
            "technical.risk_pct": 0.1,
            "levels.structure": 0.5,
            "levels.reward_risk": 2.5,
            "levels.coil_pending": False,
            "relative_strength.rs": 0.2,
            "relative_strength.rs_high": True,
            "relative_strength.peer_top_half": True,
        }
    )
    fwd = pd.DataFrame(
        {
            "week": weeks,
            "ticker": "S.L",
            "ret_4w": ret8 / 2,
            "exc_4w": ret8 / 2 - 0.01,
            "ret_8w": ret8,
            "exc_8w": ret8 - 0.01,
            "ret_16w": ret8 * 1.5,
            "exc_16w": ret8 * 1.5 - 0.01,
        }
    )
    sw = sweep(replayed, fwd, {("technical", "trend"): (0.0, 0.1, 0.25, 0.4)})
    assert sw["toolkit"].tolist() == ["technical"] * 4 and sw["threshold"].tolist() == [
        0.25,
        0.0,
        0.1,
        0.4,
    ]
    assert sw["current"].tolist() == [True, False, False, False]
    by = {float(r["threshold"]): r for r in sw.to_dict("records")}
    assert by[0.4]["ret_8w"] > by[0.25]["ret_8w"] > by[0.0]["ret_8w"]  # the edge sits above 0.4
    assert by[0.4]["hit_8w"] > 0.95 and by[0.0]["n_act"] > by[0.4]["n_act"] > 30
    sg = suggest(sw)
    assert sg.iloc[0]["suggested"] == 0.4 and sg.iloc[0]["current"] == 0.25
    assert suggest(sw, min_n=10_000)["suggested"].isna().all()
    # forward returns from bars
    w = _weekly(np.array([100.0, 110.0, 121.0, 133.1, 146.41]))
    f = forward(
        w,
        horizons=(1, 2),
        bench=_weekly(np.array([100.0, 100.0, 100.0, 100.0, 100.0]), currency="GBP"),
    )
    assert f["ret_1w"].iloc[0] == pytest.approx(0.1) and f["exc_2w"].iloc[0] == pytest.approx(0.21)
    assert f["ret_2w"].isna().iloc[-2:].all() and f["week"].iloc[0] == w.index[0]
    assert _with_threshold(("trend >= 0.25", "momentum > 0"), "trend", 0.4) == (
        "trend >= 0.4",
        "momentum > 0",
    )
    assert _with_threshold(("reward_risk >= 1.5 or coil_pending == true",), "reward_risk", 3) == (
        "reward_risk >= 3 or coil_pending == true",
    )
    assert set(GRID) == {
        ("technical", "trend"),
        ("levels", "reward_risk"),
        ("relative_strength", "rs"),
    }
    text = report(replayed.assign(label="ACT", label_technical="ACT"), sw, ["S.L"], 5)
    assert "survivorship bias" in text and "| technical | trend | 0.25 | 0.4 |" in text
    assert "replayed weeks: 400" in text and "## Threshold sweep" in text
    assert "nothing here changes the code" in text
    assert "no replayed weeks to sweep" in report(pd.DataFrame(), pd.DataFrame(), [], 5)
    assert sweep(pd.DataFrame(), fwd).empty


def test_script_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    close = _trending(100)
    w = _weekly(close)
    daily = _daily_from_weekly(w)
    daily.attrs = {"ticker": "T.L", "currency": "GBp"}
    bench = _daily_from_weekly(_weekly(close[::-1], currency="GBP"))

    def fetch(t: str, start: str | None = None) -> pd.DataFrame:
        return daily if t == "T.L" else bench

    monkeypatch.setattr(backtest_cli, "fetch_daily", fetch)
    monkeypatch.setattr(
        backtest_cli, "fetch_fundamentals", lambda t: (_ for _ in ()).throw(RuntimeError("no fund"))
    )
    monkeypatch.setattr(backtest_cli, "comparisons", lambda t: Comparisons("^M"))
    out = tmp_path / "report.md"
    backtest_cli.main(["T.L", "--years", "3", "--step", "8", "--out", str(out)])
    text = capsys.readouterr().out
    assert (
        "T.L: fundamentals unavailable" in text
        and "weeks replayed" in text
        and f"written to {out}" in text
    )
    md = out.read_text()
    assert (
        md.startswith("# Screen backtest")
        and "## Suggested bands" in md
        and "| technical | trend |" in md
    )
