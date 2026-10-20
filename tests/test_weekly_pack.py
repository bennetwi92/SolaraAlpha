from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from scanfix import build_scan_db
from solaraalpha import store
from solaraalpha.portfolio import book
from solaraalpha.portfolio.rules import Rules
from solaraalpha.portfolio.weekly_pack import build_pack, latest_review_cards, render_pack

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan as plan_cli
import weekly_pack as pack_cli


@pytest.fixture
def scan_db(tmp_path: Path) -> Path:
    return build_scan_db(tmp_path)


HEADINGS = [
    "## New plans",
    "## Rejected by fit",
    "## Watch list",
    "## Open positions",
    "## Portfolio",
    "## Errors",
]


def test_pack_before_and_after_planning(
    scan_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store.save_label(
        1, "E.L", "screen", "ERROR", "technical ERROR: RuntimeError: no data", path=scan_db
    )
    conn = book.connect(scan_db)
    text = render_pack(build_pack(conn, 1))
    conn.close()
    assert text.startswith("# Weekly pack — run 1 (2026-10-11T18:17 UTC)")
    assert "placeholder portfolio rules" in text
    for h in HEADINGS:
        assert h in text
    # nothing drafted yet: the section says none, but the fit re-run shows no rejection either
    assert text.index("## New plans\n\nnone") > 0
    assert "## Rejected by fit\n\nnone" in text
    assert "- W.L: watch reason" in text
    assert "## Open positions\n\nnone" in text
    assert "- equity £10,000 = cash £10,000 + market value £0" in text
    assert "- heat 0.0% of equity (max 6%); 0 of 8 positions" in text
    assert "- E.L: technical ERROR: RuntimeError: no data" in text

    plan_cli.main(["--run", "1", "--db", str(scan_db)])
    capsys.readouterr()
    out = tmp_path / "pack.md"
    report = tmp_path / "summary.md"
    report.write_text("## Weekly pipeline\n")
    pack_cli.main(["--run", "1", "--db", str(scan_db), "--out", str(out), "--report", str(report)])
    text = out.read_text()
    assert "### A.L — position 1 (draft)" in text
    assert (
        "- entry stop @ 1,560p; stop 1,380p; target 1,794p, 2,028p; R:R 1.3; next level 1,950p"
        in text
    )
    assert "- 111 shares, value £1,732, risk £200 (2.00% of equity); valid until 2026-10-16" in text
    assert (
        "- flags: event within 9 days, event within 9 days" in text
        or "- flags: event within 9 days" in text
    )
    assert "- decided by **screen**: screen act reason" in text
    assert "- thesis: Strong winter bookings; fuel hedged." in text
    assert "uv run python scripts/book.py approve 1" in text
    # once drafted, the plan is "pending" for the fit re-run and A.L is no longer a candidate to re-plan
    assert "## Rejected by fit\n\nnone" in text
    assert report.read_text().startswith("## Weekly pipeline\n# Weekly pack")
    assert "written to" in capsys.readouterr().out


def test_open_position_with_review_card(scan_db: Path) -> None:
    conn = book.connect(scan_db)
    with conn:
        pid, oid = book.draft_position(
            conn, "W.L", 790.0, 720.0, 100, at="2026-10-04T18:00:00+00:00"
        )
        book.approve_position(conn, pid)
        book.fill(conn, oid, 100, 790.0, at="2026-10-05T09:00:00+00:00", commission=5.0)
    conn.close()
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Review:
        date: pd.Timestamp
        position_id: int
        weeks_held: float
        r_multiple: float
        old_stop: float
        new_stop: float
        stop_moved: bool
        deteriorated: str
        past_horizon: bool
        action: str
        reason: str

    rid = store.start_run("scan", {}, path=scan_db)
    card = Review(
        pd.Timestamp("2026-10-09", tz="UTC"),
        pid,
        1.0,
        0.14,
        720.0,
        735.0,
        True,
        "momentum",
        False,
        "exit",
        "past horizon",
    )
    store.save_ticker(rid, "W.L", {"review": card}, path=scan_db)
    conn = book.connect(scan_db)
    assert latest_review_cards(conn)[pid]["action"] == "exit"
    text = render_pack(build_pack(conn, 1))
    conn.close()
    assert (
        "- **W.L** (position 1): 100 shares @ 790p, last 800p, +0.14R, stop 720p, risk £70" in text
    )
    assert (
        "- review (2026-10-09): action **exit**; stop 720p → 735p (moved); deteriorated: momentum; past horizon"
        in text
    )
    assert "book.py exit 1 --reason time" in text
    assert "- equity £10,0" in text and "1 of 8 positions" in text


def test_market_regime_in_the_pack_and_plan_flags(scan_db: Path) -> None:
    from solaraalpha.analysis.regime import RegimeCard
    from solaraalpha.portfolio.planner import plan_run

    conn = book.connect(scan_db)
    assert build_pack(conn, 1).regime is None  # no daily run yet
    plans = plan_run(conn, 1, dry_run=True).plans
    assert plans and all("market risk_off" not in p.flags for p in plans)
    conn.close()
    daily = store.start_run("daily", {}, path=scan_db)
    card = RegimeCard(
        pd.Timestamp("2026-10-09", tz="UTC"),
        "^FTAS",
        3900.0,
        -0.09,
        -0.02,
        -1.2,
        0.22,
        0.95,
        "risk_off",
        "index -9.0% below its 20-day high",
    )
    store.save_ticker(daily, "^FTAS", {"regime": card}, path=scan_db)
    conn = book.connect(scan_db)
    pack = build_pack(conn, 1)
    assert (
        pack.regime is not None
        and pack.regime["regime"] == "risk_off"
        and pack.regime["index"] == "^FTAS"
    )
    text = render_pack(pack)
    assert (
        "Market regime (^FTAS, 2026-10-09): **risk_off** — index -9.0% below its 20-day high"
        in text
    )
    plans = plan_run(conn, 1, dry_run=True).plans
    assert plans and all("market risk_off" in p.flags for p in plans)
    conn.close()


def test_circuit_breaker_line_and_rejections(
    scan_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from solaraalpha.portfolio import planner as planner_mod
    from solaraalpha.portfolio import weekly_pack as pack_mod
    from solaraalpha.portfolio.rules import load_rules

    base = load_rules()
    on = Rules(**{**base.__dict__, "circuit_breaker": True, "max_drawdown_new_entries": 0.10})
    conn = book.connect(scan_db)
    text = render_pack(build_pack(conn, 1))
    assert "- drawdown n/a vs circuit-breaker limit 10%; breaker off (owner to confirm)" in text
    # a book 15% below its high with the breaker on: every plan rejected, pack says ACTIVE
    monkeypatch.setattr(pack_mod, "drawdown_from_store", lambda conn, as_seen, rules: -0.15)
    monkeypatch.setattr(planner_mod, "load_rules", lambda: on)
    pack = build_pack(conn, 1, on)
    assert pack.plan.report.breaker and pack.plan.report.accepted == []
    assert all("circuit breaker: drawdown 15% > 10%" in why for _, why in pack.plan.report.rejected)
    text = render_pack(pack)
    assert (
        "- drawdown 15.0% vs circuit-breaker limit 10%; breaker **ACTIVE: new entries stopped**"
        in text
    )
    assert "circuit breaker: drawdown 15% > 10%" in text  # under "Rejected by fit"
    conn.close()


def test_shortlist_agrees_across_queue_planner_store_and_pack(db_backend: Path | None) -> None:
    """The screen's ACT rows are the shortlist (#192): store.shortlist, the validate queue,
    final_labels and the pack read the same names in the same (rank) order; the cap trims it."""
    from solaraalpha import agents
    from solaraalpha.portfolio.planner import final_labels

    run = store.start_run("scan", {}, path=db_backend)
    for t, label, rank, why in (
        ("B.L", "ACT", 1, "rank 1"),
        ("K.L", "ACT", 12, "catalyst: contract win"),
        ("A.L", "ACT", 2, "rank 2"),
        ("W.L", "WATCH", 3, None),
        ("P.L", "PASS", None, None),
    ):
        store.save_label(run, t, "screen", label, "r", path=db_backend, rank=rank, shortlist=why)
    want = ["B.L", "A.L", "K.L"]
    assert store.shortlist(run, path=db_backend) == want
    conn = book.connect(db_backend)
    try:
        assert agents.queue(conn, run, "validate", 10) == want
        assert agents.queue(conn, run, "validate", 2) == want[:2]  # max_agent_tickers caps spend
        cands = final_labels(conn, run)
        assert [c.ticker for c in cands if c.label == "ACT"] == want
        assert {c.ticker: c.shortlist for c in cands if c.shortlist} == {
            "B.L": "rank 1",
            "A.L": "rank 2",
            "K.L": "catalyst: contract win",
        }
        text = render_pack(build_pack(conn, run))
        assert (
            "## Shortlist\n\n1. B.L: rank 1 → **ACT** (screen)\n2. A.L: rank 2 → **ACT** (screen)\n"
            "3. K.L: catalyst: contract win → **ACT** (screen)\n" in text
        )
    finally:
        conn.close()
    # the validator keeps B.L, downgrades A.L and never reaches K.L (the cap): only B.L goes on
    # to research, and K.L is reported, not planned on the screen's ACT alone
    store.save_label(run, "B.L", "validate", "ACT", "ok", 0.8, "agent:validate", path=db_backend)
    store.save_label(run, "A.L", "validate", "PASS", "no", 0.8, "agent:validate", path=db_backend)
    conn = book.connect(db_backend)
    try:
        assert agents.queue(conn, run, "research", 10) == ["B.L"]
        text = render_pack(build_pack(conn, run))
        assert "2. A.L: rank 2 → **PASS** (validate)" in text
        assert "- K.L: no plan (not reviewed by the agents (beyond max_agent_tickers))" in text
        assert "- B.L: no plan (no levels / technical card in the run)" in text
    finally:
        conn.close()
