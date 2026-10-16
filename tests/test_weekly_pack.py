from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from scanfix import build_scan_db
from solaraalpha import store
from solaraalpha.portfolio import book
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
    assert "- entry stop @ 1,560p; stop 1,380p; target 1,950p, 2,250p; R:R 2.2" in text
    assert "- 41 shares, value £640, risk £74 (0.74% of equity); valid until 2026-10-16" in text
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
