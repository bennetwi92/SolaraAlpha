"""One self-contained HTML page over the results DB: holdings, this week's pack, history.

No server, no JavaScript framework: inline CSS, charts as base64 PNGs from matplotlib, the
weekly pack converted from markdown by :func:`md_to_html` (a tiny converter). Everything comes
from the DB (stored price snapshots, the book, the latest scan run), so it renders offline and
the workflows upload it as an artifact. Story map S10.6, issue #136.
"""

from __future__ import annotations

import base64
import html
import io
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from solaraalpha import store
from solaraalpha.analysis.scorecard import forward_returns, label_scorecard
from solaraalpha.portfolio import book, performance, risk
from solaraalpha.portfolio.journal import closed_trades
from solaraalpha.portfolio.rules import Rules, load_rules
from solaraalpha.portfolio.weekly_pack import build_pack, render_pack
from solaraalpha.store import Conn, query

CSS = """
body{font:15px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:28px 0 8px;border-bottom:1px solid #ddd;padding-bottom:4px}
h3{font-size:16px;margin:18px 0 6px}table{border-collapse:collapse;margin:8px 0;font-size:14px}
th,td{border:1px solid #ddd;padding:4px 8px;text-align:right}th{background:#f4f4f4}td:first-child,th:first-child{text-align:left}
code{background:#f4f4f4;padding:1px 4px;border-radius:3px;font-size:13px}pre{background:#f4f4f4;padding:8px;overflow-x:auto}
.muted{color:#777}.warn{color:#a00;font-weight:600}img{max-width:100%}.kpi{display:inline-block;margin:4px 18px 4px 0}
.kpi b{display:block;font-size:20px}
"""


def _fmt(v: Any, pct: bool = False, money: bool = False) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    if isinstance(v, int | float):
        if pct:
            return f"{float(v):+.1%}"
        if money:
            return f"£{float(v):,.0f}"
        return f"{float(v):,.2f}" if not float(v).is_integer() else f"{int(v):,}"
    return html.escape(str(v))


def table_html(df: pd.DataFrame, pct: Iterable[str] = (), money: Iterable[str] = ()) -> str:
    """A DataFrame as an HTML table (empty frame: a muted note)."""
    if df.empty:
        return '<p class="muted">none</p>'
    pct_s, money_s = set(pct), set(money)
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    rows = []
    for r in df.to_dict("records"):
        cells = "".join(
            f"<td>{_fmt(r[c], pct=c in pct_s, money=c in money_s)}</td>" for c in df.columns
        )
        rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


_INLINE = (
    (re.compile(r"\*\*(.+?)\*\*"), r"<b>\1</b>"),
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"(?<![\w(\"'])(https?://[^\s)]*[^\s).,;:])"), r'<a href="\1">\1</a>'),
)


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    for pattern, repl in _INLINE:
        out = pattern.sub(repl, out)
    return out


def md_to_html(md: str) -> str:
    """A tiny markdown converter for the pack: headings, bullet / numbered lists, indented
    code, pipe tables, blockquotes, bold, inline code, links and paragraphs."""
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush()
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            flush()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if line.startswith("    "):
            flush()
            block = []
            while i < len(lines) and (lines[i].startswith("    ") or not lines[i].strip()):
                if not lines[i].strip() and (
                    i + 1 >= len(lines) or not lines[i + 1].startswith("    ")
                ):
                    break
                block.append(lines[i][4:])
                i += 1
            out.append(f"<pre>{html.escape(chr(10).join(block).rstrip())}</pre>")
            continue
        if line.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                head = "".join(f"<th>{_inline(c)}</th>" for c in rows[0])
                body = "".join(
                    "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
                    for r in rows[1:]
                )
                out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
            continue
        if re.match(r"^\s*[-*]\s+", line) or re.match(r"^\s*\d+\.\s+", line):
            flush()
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            items = []
            while i < len(lines) and (
                re.match(r"^\s*[-*]\s+", lines[i]) or re.match(r"^\s*\d+\.\s+", lines[i])
            ):
                items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]))
                i += 1
                while (
                    i < len(lines)
                    and lines[i].startswith("  ")
                    and lines[i].strip()
                    and not re.match(r"^\s*([-*]|\d+\.)\s+", lines[i])
                ):
                    items[-1] += " " + lines[i].strip()
                    i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if line.startswith(">"):
            flush()
            out.append(f"<blockquote>{_inline(line.lstrip('> '))}</blockquote>")
            i += 1
            continue
        if re.fullmatch(r"<details>.*|</details>|<summary>.*", line.strip()):
            flush()
            out.append(line.strip())
            i += 1
            continue
        para.append(line.strip())
        i += 1
    flush()
    return "\n".join(out)


def _png(fig: Any) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=90, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def equity_chart(equity: pd.Series, bench: pd.Series | None = None) -> str:
    """Equity curve over drawdown, as a data URI PNG."""
    dd = performance.drawdown(equity)["drawdown"]
    fig, (a, b) = plt.subplots(
        2, 1, figsize=(10, 5), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    a.plot(equity.index, equity.to_numpy(), color="#1f77b4", linewidth=1.5, label="equity (GBP)")
    if bench is not None and len(bench):
        rebased = bench.reindex(equity.index).ffill().bfill()
        rebased = rebased / float(rebased.iloc[0]) * float(equity.iloc[0])
        a.plot(
            equity.index,
            rebased.values,
            color="#ff7f0e",
            linewidth=1.0,
            label="benchmark (rebased)",
        )
    a.legend(loc="upper left", fontsize=8)
    a.grid(alpha=0.2)
    b.fill_between(dd.index, dd.to_numpy() * 100, 0, color="#c0392b", alpha=0.5)
    b.set_ylabel("drawdown %")
    b.grid(alpha=0.2)
    fig.autofmt_xdate()
    return _png(fig)


def latest_prices(conn: Conn, tickers: Iterable[str]) -> dict[str, float]:
    """The last stored close per ticker (quote currency); tickers without a snapshot are left out."""
    out = {}
    for t in tickers:
        px = store.load_prices(t, conn=conn)
        if not px.empty:
            out[t] = float(px["close"].iloc[-1])
    return out


def latest_scan_run(conn: Conn) -> int | None:
    row = conn.execute(
        "SELECT MAX(l.run_id) FROM labels l JOIN runs r USING (run_id) WHERE r.script = 'scan'"
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def labels_with_dates(conn: Conn) -> pd.DataFrame:
    return query(
        "SELECT l.run_id, l.ticker, l.stage, l.label, (SELECT MIN(x.date) FROM results x"
        " WHERE x.run_id = l.run_id AND x.ticker = l.ticker"
        " AND x.model IN ('technical', 'levels', 'relative_strength', 'fundamentals')) AS date"
        " FROM labels l ORDER BY l.run_id, l.rowid",
        conn,
    ).dropna(subset=["date"])


def render_dashboard(conn: Conn, run_id: int | None = None, rules: Rules | None = None) -> str:
    """The whole page as one HTML string (``run_id``: the scan run for "This week"; default
    the latest with labels)."""
    rules = rules or load_rules()
    now = datetime.now(UTC)
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Solara Alpha</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>Solara Alpha</h1>",
        f"<p class='muted'>rendered {now:%Y-%m-%d %H:%M} UTC from the results DB; prices are the last stored snapshots</p>",
    ]
    if rules.warning:
        parts.append(f"<p class='warn'>{html.escape(rules.warning)}</p>")

    # holdings and portfolio
    positions = book.positions(conn)
    held_tickers = sorted(set(positions.loc[positions["status"] == "open", "ticker"]))
    prices = latest_prices(conn, held_tickers)
    held, eq, _ = risk.snapshot(conn, prices)
    parts.append("<h2>Holdings</h2>")
    parts.append(table_html(held, pct=("ret", "weight"), money=("value", "risk", "unrealised")))
    parts.append("<h2>Portfolio</h2>")
    heat = risk.heat(held, eq)
    parts.append(
        "<div>"
        + "".join(
            f"<span class='kpi'><b>{v}</b>{k}</span>"
            for k, v in (
                ("equity", f"£{eq.equity:,.0f}"),
                ("cash", f"£{eq.cash:,.0f}"),
                ("market value", f"£{eq.market_value:,.0f}"),
                ("realised P&L", f"£{eq.realised_pnl:,.0f}"),
                ("unrealised", f"£{eq.unrealised_pnl:,.0f}"),
                ("heat", f"{heat:.1%} / {rules.max_heat:.0%}"),
                ("positions", f"{eq.positions} / {rules.max_positions}"),
            )
        )
        + "</div>"
    )
    exposure = risk.exposure(held, eq)
    if len(exposure):
        parts.append("<h3>Sector exposure</h3>")
        parts.append(
            table_html(
                pd.DataFrame(
                    {"sector": [str(k) for k in exposure.index], "share": exposure.to_numpy()}
                ),
                pct=("share",),
            )
        )
    breaches = risk.breaches(held, eq, rules)
    parts.append(
        "<p class='warn'>" + "; ".join(map(html.escape, breaches)) + "</p>"
        if breaches
        else "<p class='muted'>no breaches</p>"
    )

    # equity curve from stored snapshots
    parts.append("<h2>Equity curve</h2>")
    fl = book.fills_with_tickers(conn)
    hist = {}
    for t in sorted(set(fl["ticker"])) if not fl.empty else []:
        px = store.load_prices(t, conn=conn)
        if not px.empty:
            hist[t] = px
    bench_px = store.load_prices(rules.benchmark, conn=conn)
    bench_close = bench_px["close"] if not bench_px.empty else None
    equity, _, summary = performance.history_from_book(conn, hist, bench_close)
    if summary.start is not None:
        dd = summary.max_drawdown
        parts.append(
            f"<p>{summary.start:%Y-%m-%d} → {summary.end:%Y-%m-%d}: £{summary.equity:,.0f} on net flows £{summary.net_flows:,.0f}"
            f" (P&amp;L £{summary.pnl:,.0f}); time-weighted {summary.twr:+.1%}"
            + (
                f" vs {html.escape(rules.benchmark)} {summary.benchmark:+.1%}"
                if summary.benchmark is not None
                else ""
            )
            + f"; max drawdown {dd.max_drawdown:.1%}</p>"
        )
        parts.append(
            f"<img alt='equity curve and drawdown' src='{equity_chart(equity, bench_close)}'>"
        )
    else:
        parts.append("<p class='muted'>no history yet (no cash events or fills)</p>")

    # this week
    run = run_id if run_id is not None else latest_scan_run(conn)
    parts.append("<h2>This week</h2>")
    watch_html = "<p class='muted'>none</p>"
    if run is not None:
        try:
            pack = build_pack(conn, run, rules)
            parts.append(md_to_html(render_pack(pack)))
            if pack.watch:
                watch_html = (
                    "<ul>"
                    + "".join(
                        f"<li><b>{html.escape(c.ticker)}</b>: {html.escape(c.reasons.get(c.stage, ''))}</li>"
                        for c in pack.watch
                    )
                    + "</ul>"
                )
        except Exception as e:
            parts.append(
                f"<p class='warn'>weekly pack unavailable for run {run}: {html.escape(str(e))}</p>"
            )
    else:
        parts.append("<p class='muted'>no saved scan run yet</p>")
    parts.append("<h2>Watch list</h2>")
    parts.append(watch_html)

    # history
    parts.append("<h2>History</h2>")
    parts.append("<h3>Closed trades</h3>")
    closed = closed_trades(conn)
    if not closed.empty:
        closed = closed.sort_values("closed_at", ascending=False)
        closed["closed_at"] = closed["closed_at"].str[:10]
    parts.append(table_html(closed, money=("net_pnl",)))
    parts.append("<h3>Label scorecard</h3>")
    labels = labels_with_dates(conn)
    if labels.empty:
        parts.append("<p class='muted'>no labels scored yet</p>")
    else:
        px_by = {t: store.load_prices(str(t), conn=conn) for t in sorted(set(labels["ticker"]))}
        fwd = forward_returns(labels, {t: p for t, p in px_by.items() if not p.empty})
        card = label_scorecard(fwd)
        if card.empty:
            parts.append("<p class='muted'>no scorable labels</p>")
        else:
            keep = ["stage", "label", "count"] + [
                c for c in card.columns if c.startswith(("n_", "mean_", "hit_"))
            ]
            parts.append(
                table_html(
                    card[keep], pct=tuple(c for c in keep if c.startswith(("mean_", "hit_")))
                )
            )
    parts.append("</body></html>")
    return "\n".join(parts)
