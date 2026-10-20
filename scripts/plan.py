"""Draft positions and orders from a saved scan run's ACT stocks.

uv run python scripts/plan.py --run RUN_ID [--dry-run] [--replan] [--db PATH]

Final label per ticker = the latest stage present in `labels` for the run (research > validate
> screen); ACT tickers are planned and sized (portfolio.plan), fit-checked against the book
(portfolio.fit) and the accepted plans written as draft positions with a draft entry order, the
plan in the `drafted` payload, every card of the run frozen as entry conditions and the
research's kill criteria as thesis conditions. Rejected-by-fit plans are reported, not written.
With the PM's verdict for the run (ADR 0027) only its allocation is planned, at its risk per
trade and in its order, and the drafts are approved by the PM (nothing for the owner to approve).
Then the shadow book (ADR 0028) records the baselines paired with the research and PM decisions
(once per run). Re-running for the same run skips tickers already drafted from it (with
--replan, not those whose draft was rejected or cancelled: re-plan after a rules change);
--dry-run writes nothing.
Then: `book.py approve|reject ID`, `book.py tickets`.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd

from solaraalpha.portfolio import book, shadow
from solaraalpha.portfolio.planner import PlanRun, plan_run
from solaraalpha.portfolio.rules import load_rules


def render(out: PlanRun, dry_run: bool) -> str:
    lines = [f"Run {out.run_id}: {len(out.candidates)} labelled tickers"]
    for c in out.candidates:
        lines.append(f"  {c.ticker:<10} {c.label:<6} decided by {c.stage}")
    if out.pm is not None:
        lines.append(
            "\nPM allocation: "
            + (
                ", ".join(f"{a['ticker']} {float(a['risk_pct']):.1%}" for a in out.pm["allocation"])
                or "none"
            )
            + (
                f"; rotating out positions {', '.join(map(str, out.rotating))}"
                if out.rotating
                else ""
            )
        )
    if out.equity is not None:
        lines.append(
            f"\nEquity £{out.equity.equity:,.0f}  cash £{out.equity.cash:,.0f}  open positions {out.equity.positions}"
        )
    lines.append("\nPlans:")
    if out.plans:
        rows = []
        for p in out.plans:
            rows.append(
                {
                    "ticker": p.ticker,
                    "entry": f"{p.entry_type} {p.entry:,.1f}"
                    + (" (wait for break)" if p.wait_for_break else ""),
                    "stop": f"{p.stop:,.1f}",
                    "targets": f"{p.target:,.1f} / {p.target_2:,.1f}",
                    "next level": "blue sky"
                    if math.isnan(p.next_level)
                    else f"{p.next_level:,.1f}",
                    "R:R": f"{p.reward_risk:.1f}",
                    "shares": p.shares,
                    "value": f"£{p.position_value:,.0f}",
                    "risk": f"£{p.risk_amount:,.0f}",
                    "capped_by": p.capped_by,
                    "flags": ", ".join([*p.flags, *out.report.flags.get(p.ticker, [])]) or "-",
                }
            )
        lines.append("  " + pd.DataFrame(rows).to_string(index=False).replace("\n", "\n  "))
        lines += [
            f"  {p.ticker} stop ({p.stop_source}): {p.stop_reason}"
            for p in out.plans
            if p.stop_reason
        ]
    else:
        lines.append("  (none)")
    for ticker, why in out.plan_errors:
        lines.append(f"  no plan for {ticker}: {why}")
    lines.append("\nRejected by fit:")
    lines += [f"  {p.ticker}: {why}" for p, why in out.report.rejected] or ["  (none)"]
    if out.skipped:
        lines.append(f"\nAlready drafted from this run: {', '.join(out.skipped)}")
    if dry_run:
        lines.append("\nDry run: nothing written.")
    else:
        lines.append(
            "\nDrafted: "
            + (", ".join(f"{t} -> position {pid}" for t, pid in out.written.items()) or "none")
        )
        if out.approved:
            lines.append(
                "Approved by the PM as drafted: positions " + ", ".join(map(str, out.approved))
            )
        if (out.written or out.approved) and out.pm is not None:
            lines.append("Approved by the PM: place them in Trading212 (`book.py tickets`)")
        elif out.written:
            lines.append(
                "Next: `book.py show ID`, then `book.py approve ID` or `book.py reject ID --reason ...`"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--run", type=int, required=True, help="scan run id (scan.py --save)")
    p.add_argument("--dry-run", action="store_true", help="plan and fit-check, write nothing")
    p.add_argument(
        "--replan", action="store_true", help="plan again tickers whose draft was rejected"
    )
    p.add_argument("--db", type=Path, default=None, help="local DB file (default: configured DB)")
    args = p.parse_args(argv)
    rules = load_rules()
    conn = book.connect(args.db)
    shadow_line = ""
    try:
        with conn:
            out = plan_run(conn, args.run, rules, dry_run=args.dry_run, replan=args.replan)
        if not args.dry_run:  # the baselines paired with the agents' decisions (ADR 0028)
            shadow_line = shadow.summary(shadow.record(conn, args.run, rules))
    finally:
        conn.close()
    if rules.warning:
        print(rules.warning)
    print(render(out, args.dry_run))
    if shadow_line:
        print(shadow_line)


if __name__ == "__main__":
    main()
