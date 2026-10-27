from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd
import pytest

from fakes import FakeTurso
from solaraalpha import store
from solaraalpha.portfolio import rules
from solaraalpha.toolkits import REGISTRY, Inputs, Toolkit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scan

DATE = pd.Timestamp("2026-10-09", tz="UTC")


@dataclass(frozen=True)
class Card:
    date: pd.Timestamp
    score: float
    trend: float  # technical's rank field: the combined score is `score`
    adv_gbp_20: float = 1e6  # liquidity past the floor


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every real toolkit replaced by a stub judged on `score`: ACT > 0, WATCH > -0.5, else PASS.
    events raises for ERR.L. The shortlist is the top 2 by score."""
    scores = {"UP.L": 0.5, "MID.L": -0.3, "DOWN.L": -0.9, "ERR.L": 0.9}

    def stub(real: str) -> Toolkit:
        def run(x: Inputs, **params: object) -> dict:
            if real == "events" and x.ticker == "ERR.L":
                raise RuntimeError("NSM down")
            return {real: Card(DATE, scores[x.ticker], scores[x.ticker])}

        bands = ((), ()) if real in ("events", "liquidity") else (("score > 0",), ("score > -0.5",))
        return Toolkit(real, run, bands[0], bands[1], real)

    for real in ("technical", "levels", "relative_strength", "liquidity", "fundamentals", "events"):
        monkeypatch.setitem(REGISTRY, real, stub(real))
    base = rules.load_rules()
    monkeypatch.setattr(rules, "load_rules", lambda: replace(base, shortlist_size=2))
    monkeypatch.setattr(scan, "fresh_catalysts", lambda tickers: {})  # no network


def test_scan_labels_report_and_save(
    stubs: None, fake_turso: FakeTurso, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report, run_file = tmp_path / "report.md", tmp_path / "run_id"
    scan.main(
        [
            "UP.L",
            "MID.L",
            "DOWN.L",
            "ERR.L",
            "--save",
            "--report",
            str(report),
            "--run-id-file",
            str(run_file),
        ]
    )  # ERR.L errored (events): reported, not fatal
    out = capsys.readouterr().out
    assert "UP.L       ACT " in out and "MID.L      WATCH" in out and "DOWN.L     PASS" in out
    assert "saved run 1" in out and run_file.read_text() == "1\n"

    md = report.read_text()
    assert "| UP.L | ACT | ACT | ACT | ACT | ACT | ACT | **ACT** |" in md
    assert "| MID.L | WATCH | WATCH | WATCH | ACT | WATCH | - | **WATCH** |" in md
    assert "| DOWN.L | pass | pass | pass | ACT | pass | - | **PASS** |" in md  # band PASS
    assert "| ERR.L | ACT | ACT | ACT | ACT | ACT | ERROR | **ACT** |" in md
    assert "**Shortlist (2, to the agents):**\n- ERR.L: rank 1\n- UP.L: rank 2\n" in md
    assert (
        "- MID.L: rank 3, not shortlisted; technical WATCH: failed score > 0.0 (score=-0.30);"
        " levels WATCH" in md
    )
    assert "- ERR.L `events`: RuntimeError: NSM down" in md

    labels = store.load_labels(run_id=1)
    assert list(labels["ticker"]) == ["UP.L", "MID.L", "DOWN.L", "ERR.L"]
    assert list(labels["label"]) == ["ACT", "WATCH", "PASS", "ACT"]
    assert set(labels["stage"]) == {"screen"} and set(labels["actor"]) == {"pipeline"}
    assert labels["confidence"].isna().all()
    assert store.shortlist(1) == ["ERR.L", "UP.L"]  # ranked 1, 2
    assert store.shortlist(1, labels=("WATCH",)) == ["MID.L"]
    assert list(labels["rank"]) == [2, 3, 4, 1]
    assert list(labels["shortlist"].fillna("-")) == ["rank 2", "-", "-", "rank 1"]
    steps = store.load_steps("MID.L")
    assert list(steps["status"]) == ["watch", "watch", "watch", "act", "watch", "skip"]
    assert steps["reason"].iloc[-1] == "not shortlisted"


def test_scan_one_toolkit_prints_label(stubs: None, capsys: pytest.CaptureFixture[str]) -> None:
    scan.main(["MID.L", "--toolkit", "technical"])
    out = capsys.readouterr().out
    assert "MID.L      WATCH  technical:WATCH" in out


def test_scan_fails_only_when_every_ticker_errored(stubs: None, fake_turso: FakeTurso) -> None:
    with pytest.raises(SystemExit) as e:
        scan.main(["ERR.L"])
    assert e.value.code == 1


def test_scan_list_shows_bands(capsys: pytest.CaptureFixture[str]) -> None:
    scan.main(["--list"])
    out = capsys.readouterr().out
    assert "1. screen" in out and "runs on all" in out and "runs on all but ERROR" in out
    assert "runs on the shortlist" in out and "top 12 + up to 3 on a catalyst" in out
    assert "act:   trend >= 0.25 | momentum > 0 | entry >= 0" in out
    assert "watch: new_low == false" in out  # levels (#194)
    assert "(always, once run)" in out
