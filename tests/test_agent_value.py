from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solaraalpha.analysis.scorecard import MIN_GROUP, agent_value, calibration

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import learn


def _world(n: int = 120, effect: float = 0.06, seed: int = 4) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Screen-ACT stocks: the validator keeps half and downgrades half; kept ones earn ``effect``
    more over 8 weeks. Research downgrades at random (no effect)."""
    rng = np.random.default_rng(seed)
    runs = np.repeat(np.arange(1, n // 10 + 1), 10)
    tickers = np.array([f"T{i % 10}.L" for i in range(n)])
    kept = rng.random(n) < 0.5
    conf = np.where(kept, rng.uniform(0.5, 1.0, n), rng.uniform(0.0, 0.5, n))
    ret8 = 0.01 + effect * kept + 0.03 * rng.standard_normal(n)
    rows = []
    for i in range(n):
        rows.append(
            {
                "run_id": runs[i],
                "ticker": tickers[i],
                "stage": "screen",
                "label": "ACT",
                "confidence": None,
            }
        )
        rows.append(
            {
                "run_id": runs[i],
                "ticker": tickers[i],
                "stage": "validate",
                "label": "ACT" if kept[i] else "WATCH",
                "confidence": conf[i],
            }
        )
        rows.append(
            {
                "run_id": runs[i],
                "ticker": tickers[i],
                "stage": "research",
                "label": "ACT" if rng.random() < 0.5 else "PASS",
                "confidence": 0.6,
            }
        )
    labels = pd.DataFrame(rows)
    fwd_rows = []
    for i in range(n):
        base = {
            "run_id": runs[i],
            "ticker": tickers[i],
            "date": pd.Timestamp("2026-01-02", tz="UTC"),
        }
        for stage in ("screen", "validate", "research"):
            fwd_rows.append(
                {
                    **base,
                    "stage": stage,
                    "label": "ACT",
                    "ret_2w": ret8[i] / 4,
                    "exc_2w": ret8[i] / 4 - 0.002,
                    "ret_4w": ret8[i] / 2,
                    "exc_4w": ret8[i] / 2 - 0.005,
                    "ret_8w": ret8[i],
                    "exc_8w": ret8[i] - 0.01,
                    "ret_16w": np.nan,
                    "exc_16w": np.nan,
                }
            )
    return labels, pd.DataFrame(fwd_rows)


def test_agent_value_finds_the_known_effect_and_is_seeded() -> None:
    labels, fwd = _world()
    av = agent_value(labels, fwd, seed=1)
    rows = {(str(r["stage"]), int(r["horizon"])): r for r in av.to_dict("records")}
    v8 = rows[("validate", 8)]
    assert v8["n_kept"] + v8["n_downgraded"] == 120 and v8["conclusive"]
    assert v8["diff_mean"] == pytest.approx(0.06, abs=0.02)
    assert v8["diff_lo90"] > 0 and v8["diff_lo90"] < v8["diff_mean"] < v8["diff_hi90"]
    assert v8["kept_hit"] > v8["downgraded_hit"] and v8["kept_excess"] > v8["downgraded_excess"]
    r8 = rows[("research", 8)]
    assert abs(r8["diff_mean"]) < 0.03 and r8["diff_lo90"] < 0 < r8["diff_hi90"]  # no real effect
    # no data at 16 weeks: empty groups, inconclusive
    v16 = rows[("validate", 16)]
    assert v16["n_kept"] == 0 and not v16["conclusive"] and np.isnan(v16["diff_lo90"])
    # seeded: the same seed gives the same interval, another seed a (slightly) different one
    again = agent_value(labels, fwd, seed=1)
    pd.testing.assert_frame_equal(av, again)
    other = agent_value(labels, fwd, seed=2)
    assert other.to_dict("records")[0]["diff_lo90"] != av.to_dict("records")[0]["diff_lo90"]
    # small samples are flagged, not concluded
    small = agent_value(labels[labels["run_id"] <= 2], fwd[fwd["run_id"] <= 2])
    assert not small["conclusive"].any() and (small["n_kept"] + small["n_downgraded"] <= 20).all()
    assert MIN_GROUP == 20
    assert agent_value(labels.iloc[0:0], fwd).empty and agent_value(labels, fwd.iloc[0:0]).empty


def test_calibration_by_confidence_bucket() -> None:
    labels, fwd = _world()
    cal = calibration(labels, fwd)
    v = {str(r["bucket"]): r for r in cal.to_dict("records") if r["stage"] == "validate"}
    assert list(v) == ["0.00-0.50", "0.50-0.75", "0.75-1.00"]
    assert v["0.00-0.50"]["n"] == 0  # kept verdicts all carry confidence >= 0.5
    n_act = int(((labels["stage"] == "validate") & (labels["label"] == "ACT")).sum())
    assert v["0.50-0.75"]["n"] + v["0.75-1.00"]["n"] == n_act
    assert v["0.75-1.00"]["hit_rate"] > 0.8
    assert calibration(labels.drop(columns=["confidence"]), fwd).empty


def test_learn_agents_value_script(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from solaraalpha import store

    db = tmp_path / "s.db"
    conn = store.connect(db)
    with conn:
        run = store.start_run("scan", {}, conn=conn)
        store.save_ticker(
            run, "A.L", {"technical": {"date": "2026-01-02", "trend": 0.5}}, conn=conn
        )
        store.save_label(run, "A.L", "screen", "ACT", "", conn=conn)
        store.save_label(run, "A.L", "validate", "WATCH", "x", 0.7, "agent:validate", conn=conn)
    conn.close()
    idx = pd.bdate_range("2025-10-01", "2026-04-30", tz="UTC", name="date")
    close = 100.0 + (np.arange(len(idx)) % 5)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "adj_close": close,
            "volume": 1e5,
        },
        index=idx,
    )
    frame.attrs = {"ticker": "A.L", "currency": "GBp"}
    monkeypatch.setattr(learn, "fetch_daily", lambda t: frame)
    learn.main(["agents-value", "--db", str(db)])
    out = capsys.readouterr().out
    assert (
        "1 labels" not in out
        and "labels" in out
        and "too few to conclude" in out
        and "Calibration" in out
    )
