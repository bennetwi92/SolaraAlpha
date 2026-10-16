"""Trade journal: what closed trades say about the set-ups and the exits.

Each closed position carries a :class:`solaraalpha.portfolio.book.CloseSummary` in its
``closed`` event (net P&L, R-multiple, holding weeks, MFE / MAE in R) and a plan in its
``drafted`` payload. :func:`journal` groups them by exit reason and by set-up type: pullback
(limit near the close), breakout (stop entry at a coil trigger) or blue sky (no target
overhead). Story map S11.5.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from solaraalpha.portfolio import book
from solaraalpha.store import Conn

SETUPS = ("pullback", "breakout", "blue_sky")
COLUMNS = [
    "position_id",
    "ticker",
    "closed_at",
    "exit_reason",
    "setup",
    "net_pnl",
    "r_multiple",
    "holding_weeks",
    "mfe_r",
    "mae_r",
]


def setup_type(plan: dict[str, Any]) -> str:
    """``blue_sky`` when the plan had no target overhead, ``breakout`` for a stop entry at the
    trigger, else ``pullback``."""
    target = plan.get("target")
    rr = plan.get("reward_risk")
    if (
        target is None
        or (isinstance(target, float) and math.isnan(target))
        or rr in ("inf", "Infinity")
        or (isinstance(rr, float) and math.isinf(rr))
    ):
        return "blue_sky"
    if plan.get("entry_type") == "stop":
        return "breakout"
    return "pullback"


def closed_trades(conn: Conn) -> pd.DataFrame:
    """One row per closed position: the close summary fields and the set-up type."""
    rows = []
    for p in book.positions(conn, status="closed").to_dict("records"):
        ev = book.position_events(conn, int(p["position_id"]))
        closed = ev[ev["kind"] == "closed"]
        if closed.empty:
            continue
        summary = dict(closed["payload"].iloc[-1])
        plans = [x for x in ev.loc[ev["kind"].isin(["drafted", "amended"]), "payload"]]
        plan = plans[-1] if plans else {}
        rows.append(
            {
                "position_id": int(p["position_id"]),
                "ticker": str(p["ticker"]),
                "closed_at": str(closed["at"].iloc[-1]),
                "exit_reason": summary.get("exit_reason", "unknown"),
                "setup": setup_type(plan),
                "net_pnl": float(summary.get("net_pnl", math.nan)),
                "r_multiple": float(summary.get("r_multiple") or math.nan),
                "holding_weeks": float(summary.get("holding_weeks", math.nan)),
                "mfe_r": float(summary.get("mfe_r") or math.nan),
                "mae_r": float(summary.get("mae_r") or math.nan),
            }
        )
    return pd.DataFrame(rows, columns=COLUMNS)


def _stats(g: pd.DataFrame) -> dict[str, float]:
    r = g["r_multiple"].dropna()
    return {
        "trades": float(len(g)),
        "hit_rate": float((g["net_pnl"] > 0).mean()) if len(g) else math.nan,
        "avg_r": float(r.mean()) if len(r) else math.nan,
        "expectancy_r": float(r.mean()) if len(r) else math.nan,  # mean R per trade
        "total_pnl": float(g["net_pnl"].sum()),
        "avg_weeks": float(g["holding_weeks"].mean()) if len(g) else math.nan,
        "avg_mfe_r": float(g["mfe_r"].mean()) if g["mfe_r"].notna().any() else math.nan,
        "avg_mae_r": float(g["mae_r"].mean()) if g["mae_r"].notna().any() else math.nan,
    }


def journal(closed: pd.DataFrame) -> pd.DataFrame:
    """Statistics by exit reason and by set-up type (plus an ``all`` row): trades, hit rate,
    average R, expectancy, total P&L, average holding weeks, average MFE / MAE in R."""
    if closed.empty:
        return pd.DataFrame()
    rows = [{"by": "all", "group": "all", **_stats(closed)}]
    for reason, g in closed.groupby("exit_reason"):
        rows.append({"by": "exit_reason", "group": str(reason), **_stats(g)})
    for setup, g in closed.groupby("setup"):
        rows.append({"by": "setup", "group": str(setup), **_stats(g)})
    return pd.DataFrame(rows)
