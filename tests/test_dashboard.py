from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scanfix import build_scan_db
from solaraalpha import store
from solaraalpha.dashboard import md_to_html, render_dashboard, table_html
from solaraalpha.portfolio import book

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dashboard as dash_cli


def test_md_to_html_covers_the_pack_syntax() -> None:
    md = (
        "# Title\n\n## Section\n\nSome **bold** text with `code` and https://x.y/z.\n\n"
        "- one\n- two **b**\n  continued\n\n1. first\n2. second\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n    uv run python scripts/book.py approve 1\n\n> **WARNING:** careful\n\n<details><summary>more</summary>\n\nhidden\n\n</details>\n"
    )
    out = md_to_html(md)
    for needle in (
        "<h1>Title</h1>",
        "<h2>Section</h2>",
        "<b>bold</b>",
        "<code>code</code>",
        '<a href="https://x.y/z">https://x.y/z</a>',
        "<ul><li>one</li><li>two <b>b</b> continued</li></ul>",
        "<ol><li>first</li><li>second</li></ol>",
        "<table><thead><tr><th>a</th><th>b</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>",
        "<pre>uv run python scripts/book.py approve 1</pre>",
        "<blockquote><b>WARNING:</b> careful</blockquote>",
        "<details><summary>more</summary>",
        "<p>hidden</p>",
    ):
        assert needle in out, needle
    assert "<script>" not in md_to_html("<script>alert(1)</script>")  # escaped


def test_dashboard_renders_offline_on_a_fixture_db(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = build_scan_db(tmp_path)
    conn = book.connect(db)
    with conn:
        # an open position with stored prices and a closed trade for the history section
        pid, oid = book.draft_position(
            conn, "A.L", 1500.0, 1400.0, 10, opened_from_run_id=1, at="2026-10-05T18:00:00+00:00"
        )
        book.approve_position(conn, pid)
        book.fill(conn, oid, 10, 1500.0, at="2026-10-06T09:00:00+00:00", commission=5.0)
        p2, o2 = book.draft_position(conn, "W.L", 800.0, 750.0, 10, at="2026-09-01T18:00:00+00:00")
        book.approve_position(conn, p2)
        book.fill(conn, o2, 10, 800.0, at="2026-09-02T09:00:00+00:00")
        xid = book.exit_position(conn, p2, "target", "limit", 900.0)
        book.fill(conn, xid, 10, 900.0, at="2026-09-20T15:00:00+00:00")
    page = render_dashboard(conn, 1)
    conn.close()
    for heading in (
        "<h2>Holdings</h2>",
        "<h2>Portfolio</h2>",
        "<h2>Equity curve</h2>",
        "<h2>This week</h2>",
        "<h2>Watch list</h2>",
        "<h2>History</h2>",
        "<h3>Closed trades</h3>",
        "<h3>Label scorecard</h3>",
    ):
        assert heading in page, heading
    assert page.startswith("<!doctype html>") and page.rstrip().endswith("</html>")
    assert (
        "A.L" in page and "<img alt='equity curve and drawdown' src='data:image/png;base64," in page
    )
    assert "W.L" in page and "target" in page  # the closed trade
    assert "Weekly pack — run 1" in page and "<h2>New plans</h2>" in page  # the pack converted
    assert "placeholder portfolio rules" not in page  # confirmed by the owner
    assert len(page.encode()) < 2_000_000
    assert "<script" not in page.lower()

    dash_cli.main(["--db", str(db), "--out", str(tmp_path / "d.html")])
    out = capsys.readouterr().out
    assert "dashboard written to" in out and (tmp_path / "d.html").read_text().startswith(
        "<!doctype html>"
    )


def test_dashboard_on_an_empty_db(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    conn = book.connect(db)
    store.start_run("scan", {}, conn=conn)  # a run without labels: no pack
    page = render_dashboard(conn)
    conn.close()
    assert (
        "no history yet" in page
        and "no saved scan run yet" in page
        and "no labels scored yet" in page
    )
    assert "<h2>Holdings</h2>" in page
    conn = book.connect(db)
    assert table_html(book.positions(conn)) == '<p class="muted">none</p>'
    conn.close()
