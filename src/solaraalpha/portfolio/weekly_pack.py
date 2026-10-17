"""The weekly pack: one page that says what to do after Sunday's scan.

:func:`build_pack` gathers everything from one saved run and the book (plans drafted from the
run, the fit re-run, the watch list, open positions with their latest review card, the money
view, errors); :func:`render_pack` is a pure function from that data to markdown, so it can be
tested on a fixture DB and reused by the dashboard. Empty sections render as "none".
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.portfolio import book, performance, risk
from solaraalpha.portfolio.planner import Candidate, PlanRun, plan_run
from solaraalpha.portfolio.rules import Rules, load_rules
from solaraalpha.store import Conn

REVIEW_FIELDS = (
    "weeks_held",
    "r_multiple",
    "old_stop",
    "new_stop",
    "stop_moved",
    "deteriorated",
    "action",
    "reason",
)


@dataclass
class Pack:
    run_id: int
    started_at: str
    rules: Rules
    plan: PlanRun
    new_positions: list[dict[str, Any]]  # positions drafted from this run, with their payload
    open_positions: list[dict[str, Any]]  # state + latest review card (if any) per open position
    heat: float
    exposure: dict[str, float]
    breaches: list[str]
    errors: list[Candidate] = field(default_factory=list)
    watch: list[Candidate] = field(default_factory=list)
    performance: performance.Summary | None = None
    correlated: list[tuple[str, str, float]] = field(default_factory=list)
    agent_usage: dict[str, float] = field(default_factory=dict)  # minutes / cost this week (#139)
    stale_orders: list[dict[str, Any]] = field(
        default_factory=list
    )  # past validity, no fill (#130)


def latest_review_cards(conn: Conn) -> dict[int, dict[str, Any]]:
    """The latest ``review`` card per position (by ``position_id`` field), from the results."""
    df = store.query(
        "SELECT x.run_id, x.ticker, x.date, x.field, x.value, x.text FROM results x"
        " WHERE x.model = 'review' ORDER BY x.run_id, x.rowid",
        conn,
    )
    out: dict[int, dict[str, Any]] = {}
    for (run_id, ticker), g in df.groupby(["run_id", "ticker"], sort=True):
        card: dict[str, Any] = {
            "run_id": int(str(run_id)),
            "ticker": str(ticker),
            "date": str(g["date"].iloc[0]),
        }
        for r in g.to_dict("records"):
            card[str(r["field"])] = r["text"] if pd.notna(r["text"]) else r["value"]
        pid = card.get("position_id")
        if pid is not None and not (isinstance(pid, float) and math.isnan(pid)):
            out[int(pid)] = card  # ordered by run_id: the latest wins
    return out


def build_pack(conn: Conn, run_id: int, rules: Rules | None = None) -> Pack:
    rules = rules or load_rules()
    started = str(store.run_info(run_id, conn=conn)["started_at"])
    plan = plan_run(conn, run_id, rules, dry_run=True)
    positions = book.positions(conn)
    reviews = latest_review_cards(conn)

    new: list[dict[str, Any]] = []
    for p in positions[positions["opened_from_run_id"] == run_id].to_dict("records"):
        ev = book.position_events(conn, int(p["position_id"]))
        drafted = [x for x in ev.loc[ev["kind"] == "drafted", "payload"]]
        new.append({**{str(k): v for k, v in p.items()}, "payload": drafted[0] if drafted else {}})

    held = plan.held if plan.held is not None else pd.DataFrame(columns=risk.HOLDING_COLS)
    open_rows: list[dict[str, Any]] = []
    for row in held.to_dict("records"):
        st = book.position_state(conn, int(row["position_id"]))
        open_rows.append(
            {
                **{str(k): v for k, v in row.items()},
                "state": st,
                "review": reviews.get(int(row["position_id"])),
            }
        )

    eq = plan.equity
    assert eq is not None
    perf, pairs = performance_from_store(conn, started, rules)
    return Pack(
        run_id=run_id,
        started_at=started,
        rules=rules,
        plan=plan,
        new_positions=new,
        open_positions=open_rows,
        heat=risk.heat(held, eq),
        exposure={str(k): float(v) for k, v in risk.exposure(held, eq).items()},
        breaches=risk.breaches(held, eq, rules),
        errors=[c for c in plan.candidates if c.label == "ERROR"],
        watch=[c for c in plan.candidates if c.label == "WATCH"],
        performance=perf,
        correlated=pairs,
        stale_orders=json.loads(book.stale_orders(conn, started).to_json(orient="records")),
        agent_usage=agent_week(conn, started),
    )


def agent_week(conn: Conn, started: str) -> dict[str, float]:
    """Agent invocations, minutes and cost in the seven days up to ``started`` (#139)."""
    since = (pd.Timestamp(started) - pd.Timedelta(days=7)).isoformat()
    runs = store.load_agent_runs(since, conn=conn)
    if runs.empty:
        return {}
    secs = pd.to_numeric(runs["seconds"], errors="coerce").sum(min_count=1)
    cost = pd.to_numeric(runs["cost_usd"], errors="coerce").sum(min_count=1)
    return {
        "invocations": float(len(runs)),
        "minutes": float(secs) / 60 if pd.notna(secs) else float("nan"),
        "cost_usd": float(cost) if pd.notna(cost) else float("nan"),
        "failures": float((runs["status"] != "success").sum()),
    }


def performance_from_store(
    conn: Conn, as_seen: str, rules: Rules
) -> tuple[performance.Summary | None, list[tuple[str, str, float]]]:
    """Equity-curve summary and correlated holdings from the price snapshots in the DB (no
    network); None when the book has no history."""
    fl = book.fills_with_tickers(conn)
    if fl.empty:
        return None, []
    prices = {}
    for t in sorted(set(fl["ticker"])):
        px = store.load_prices(str(t), as_seen, conn=conn)
        if not px.empty:
            prices[str(t)] = px
    bench_px = store.load_prices(rules.benchmark, as_seen, conn=conn)
    bench = bench_px["close"] if not bench_px.empty else None
    summary = performance.history_from_book(conn, prices, bench)[2]
    open_tickers = sorted(set(book.positions(conn, status="open")["ticker"]))
    rets = {t: performance.weekly_returns(prices[t]) for t in open_tickers if t in prices}
    _, pairs = performance.correlation(rets)
    return summary, pairs


def _money(v: float) -> str:
    return f"£{v:,.0f}"


def _px(v: Any, ccy: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return book.fmt_price(float(v), ccy)


def render_pack(pack: Pack) -> str:
    eq = pack.plan.equity
    assert eq is not None
    rules = pack.rules
    lines = [f"# Weekly pack — run {pack.run_id} ({pack.started_at[:16]} UTC)", ""]
    if rules.warning:
        lines += [f"> **{rules.warning}**", ""]

    lines += ["## New plans", ""]
    if pack.new_positions:
        for p in pack.new_positions:
            pl, ccy, pid = p["payload"], str(p["currency"]), int(p["position_id"])
            cand = next((c for c in pack.plan.candidates if c.ticker == p["ticker"]), None)
            entry = f"{pl.get('entry_type', '?')} @ {_px(pl.get('entry'), ccy)}"
            if pl.get("wait_for_break"):
                entry += " (place once a close is above the trigger)"
            targets = (
                ", ".join(
                    _px(pl.get(k), ccy) for k in ("target", "target_2") if pl.get(k) is not None
                )
                or "blue sky"
            )
            rr = pl.get("reward_risk")
            rr_s = (
                "inf"
                if rr is None or (isinstance(rr, float) and math.isinf(rr))
                else f"{float(rr):.1f}"
            )
            risk_amt = float(pl.get("risk_amount") or 0.0)
            pct = f" ({risk_amt / eq.equity:.2%} of equity)" if eq.equity > 0 else ""
            lines += [
                f"### {p['ticker']} — position {pid} ({p['status']})",
                "",
                f"- entry {entry}; stop {_px(pl.get('stop'), ccy)}; target {targets}; R:R {rr_s}",
                f"- {pl.get('shares', pl.get('qty', '?'))} shares, value {_money(float(pl.get('position_value') or 0.0))},"
                f" risk {_money(risk_amt)}{pct}; valid until {pl.get('valid_until', '?')}",
                f"- flags: {', '.join(dict.fromkeys([*pl.get('flags', []), *pl.get('fit_flags', [])])) or 'none'}",
            ]
            if cand:
                lines.append(
                    f"- decided by **{cand.stage}**: "
                    + "; ".join(f"{s} {cand.reasons[s]}" for s in cand.reasons)
                )
            if p["thesis"]:
                lines.append(f"- thesis: {p['thesis']}")
            lines += [
                "",
                f"    uv run python scripts/book.py approve {pid}",
                f'    uv run python scripts/book.py reject {pid} --reason "..."',
                "",
            ]
    else:
        lines += ["none", ""]

    lines += ["## Rejected by fit", ""]
    rej = [f"- {p.ticker}: {why}" for p, why in pack.plan.report.rejected]
    rej += [f"- {t}: no plan ({why})" for t, why in pack.plan.plan_errors]
    lines += (rej or ["none"]) + [""]

    lines += ["## Watch list", ""]
    lines += [f"- {c.ticker}: {c.reasons.get(c.stage, '')}" for c in pack.watch] or ["none"]
    lines.append("")

    ranked = [c for c in pack.plan.candidates if c.label == "ACT"]
    if ranked:
        lines += [
            "## ACT by rank",
            "",
            *(
                f"{i}. {c.ticker}" + (f" (screen rank {c.rank})" if c.rank else "")
                for i, c in enumerate(ranked, 1)
            ),
            "",
        ]

    lines += ["## Open positions", ""]
    if pack.open_positions:
        for row in pack.open_positions:
            st: book.PositionState = row["state"]
            r = row["r"]
            r_s = f"{r:+.2f}R" if isinstance(r, float) and not math.isnan(r) else "R n/a"
            line = (
                f"- **{st.ticker}** (position {st.position_id}): {st.shares} shares @ {_px(st.avg_cost, st.currency)},"
                f" last {_px(row['last'], st.currency)}, {r_s}, stop {_px(st.stop, st.currency)},"
                f" risk {_money(float(row['risk']))}, {float(row['weeks_held']):.1f} weeks held"
            )
            if row.get("next_event"):
                line += f", next event {str(row['next_event'])[:10]}"
            lines.append(line)
            rv = row.get("review")
            if rv:
                lines.append(
                    f"  - review ({str(rv.get('date'))[:10]}): action **{rv.get('action', '?')}**; "
                    f"stop {_px(rv.get('old_stop'), st.currency)} → {_px(rv.get('new_stop'), st.currency)}"
                    f"{' (moved)' if rv.get('stop_moved') else ''}; deteriorated: {rv.get('deteriorated') or 'none'}"
                    f"; {rv.get('reason', '')}"
                )
                if rv.get("action") == "exit":
                    lines.append(
                        f"  - `uv run python scripts/book.py exit {st.position_id} --reason time`"
                    )
                if rv.get("event_due"):
                    lines.append(
                        f"  - **event due {str(rv.get('event_due'))[:10]}** ({rv.get('event_status') or 'estimated'}):"
                        f" policy **{rv.get('event_action') or 'flag'}** (decided in advance; `book.py revisions` for a proposed trim / exit)"
                    )
    else:
        lines.append("none")
    lines.append("")

    lines += ["## Stale orders", ""]
    lines += [
        f"- order {o['order_id']} {o['ticker']} {o['purpose']} {o['qty']} ({o['status']}, valid until {o['valid_until']}),"
        f" position {o['position_id']}: enter the fill, or `book.py unfilled {o['order_id']}` / `book.py expire`"
        for o in pack.stale_orders
    ] or ["none"]
    lines.append("")

    lines += ["## Portfolio", ""]
    perf = pack.performance
    if perf is not None and perf.start is not None:
        bench = f", {rules.benchmark} {perf.benchmark:+.1%}" if perf.benchmark is not None else ""
        lines.append(
            f"- performance since {perf.start:%Y-%m-%d}: P&L {_money(perf.pnl)} on net flows {_money(perf.net_flows)},"
            f" time-weighted {perf.twr:+.1%}{bench}, max drawdown {perf.max_drawdown.max_drawdown:.1%}"
        )
    if pack.correlated:
        lines.append(
            "- correlated holdings (26w > 0.7): "
            + ", ".join(f"{a}/{b} {c:.2f}" for a, b, c in pack.correlated)
        )
    au = pack.agent_usage
    if au:
        cost = f"${au['cost_usd']:.2f}" if not math.isnan(au["cost_usd"]) else "n/a (subscription)"
        mins = f"{au['minutes']:.0f}" if not math.isnan(au["minutes"]) else "n/a"
        lines.append(
            f"- agents this week: {int(au['invocations'])} invocation(s), {mins} minutes, cost {cost}"
            + (f", {int(au['failures'])} failure(s)" if au["failures"] else "")
        )
    lines += [
        f"- equity {_money(eq.equity)} = cash {_money(eq.cash)} + market value {_money(eq.market_value)}",
        f"- heat {pack.heat:.1%} of equity (max {rules.max_heat:.0%}); {eq.positions} of {rules.max_positions} positions",
        "- exposure by sector: "
        + (", ".join(f"{s} {v:.0%}" for s, v in pack.exposure.items()) or "none"),
        "- breaches: " + ("; ".join(pack.breaches) if pack.breaches else "none"),
        "",
    ]

    lines += ["## Errors", ""]
    lines += [f"- {c.ticker}: {c.reasons.get(c.stage, '')}" for c in pack.errors] or ["none"]
    lines.append("")
    return "\n".join(lines)


def pack_dict(pack: Pack) -> dict[str, Any]:
    """A JSON-able summary (for the dashboard and notifications)."""
    return {
        "run_id": pack.run_id,
        "new": [p["ticker"] for p in pack.new_positions],
        "rejected": [(p.ticker, why) for p, why in pack.plan.report.rejected],
        "watch": [c.ticker for c in pack.watch],
        "errors": [c.ticker for c in pack.errors],
        "equity": asdict(pack.plan.equity) if pack.plan.equity else None,
        "heat": pack.heat,
        "breaches": pack.breaches,
    }
