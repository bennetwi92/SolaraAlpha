"""The monitor's pack: an open position or a pending entry, then versus now, for the monitor.

Every weekday each open position and each pending entry (a draft / approved position with an
unfilled entry order) has a ``daily_review`` in the daily run (ADR 0024), so every one gets a
pack: the position's state (``status`` ``open`` / ``pending``), orders, plan and the pending
entry order, the **entry conditions** frozen when it was drafted, the cards **now** (technical
with the position's entry date and price when open, levels, relative strength, re-run on fresh
data), a per-field then → now diff, the thesis conditions (machine ones evaluated now), the
day's triggers with the filing's full text, the latest research verdict and the levels it set
(``research_levels``), and the systematic stops (``systematic_stops``: levels, technical,
chandelier) as context only: the agent sets the stop. The agent's revision is applied as it is
recorded; the owner can undo it (``book.py undo``). See docs/decisions/0018, 0024.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.config import settings
from solaraalpha.data.announcements import fetch_text
from solaraalpha.log import get_logger
from solaraalpha.pipeline import Condition, run_toolkit
from solaraalpha.portfolio import book
from solaraalpha.store import Conn, query
from solaraalpha.toolkits import Inputs

log = get_logger(__name__)

AGENT_TRIGGERS = ("gap", "filing", "thesis_hit", "daily_review")
EVENT_TRIGGERS = ("gap", "filing", "thesis_hit")  # reviewed first when the queue is capped
DIFF_FIELDS = (
    ("technical", "trend"),
    ("technical", "momentum"),
    ("technical", "entry"),
    ("levels", "structure"),
    ("levels", "reward_risk"),
    ("relative_strength", "rs"),
    ("fundamentals", "growth"),
    ("fundamentals", "quality"),
)
NOW_TOOLKITS = ("technical", "levels", "relative_strength")


def positions_to_review(conn: Conn, run_id: int) -> list[int]:
    """Positions with an agent trigger in daily run ``run_id`` (since ADR 0024 every open
    position and pending entry has its ``daily_review``), most urgent first: an event trigger
    (gap, filing, thesis hit), then open positions, then pending entries; by id within each."""
    marks = ", ".join("?" * len(AGENT_TRIGGERS))
    df = query(
        f"SELECT position_id, kind, detail FROM triggers WHERE run_id = ? AND kind IN ({marks})"
        " AND position_id IS NOT NULL",
        conn,
        [run_id, *AGENT_TRIGGERS],
    )
    if df.empty:
        return []
    rank: dict[int, int] = {}
    for pid, kind, detail in zip(df["position_id"], df["kind"], df["detail"], strict=True):
        pid = int(pid)
        if kind in EVENT_TRIGGERS:
            r = 0
        else:
            d = json.loads(detail) if isinstance(detail, str) else (detail or {})
            r = 2 if d.get("status") == "pending" else 1
        rank[pid] = min(rank.get(pid, 9), r)
    return sorted(rank, key=lambda p: (rank[p], p))


def _jsonable(card: Any) -> dict[str, Any]:
    fields = asdict(card) if not isinstance(card, dict) else dict(card)
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if isinstance(v, pd.Timestamp):
            out[k] = v.isoformat()
        elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            out[k] = None if math.isnan(v) else ("inf" if v > 0 else "-inf")
        else:
            out[k] = v
    return out


def latest_plan(conn: Conn, position_id: int) -> dict[str, Any]:
    ev = book.position_events(conn, position_id)
    payloads = [x for x in ev.loc[ev["kind"].isin(["drafted", "amended"]), "payload"]]
    plan = dict(payloads[-1]) if payloads else {}
    for rev in ev.loc[ev["kind"] == "revised", "payload"]:
        plan.update({k: v for k, v in rev.items() if k in ("target", "target_2")})
    return plan


def now_cards(
    ticker: str, state: book.PositionState, inputs: Inputs | None = None
) -> dict[str, Any]:
    """Fresh technical (with the position's entry), levels and relative-strength cards, plus
    each toolkit's label; a toolkit that fails is reported, not fatal."""
    x = inputs or Inputs(ticker)
    entry = pd.Timestamp(state.opened_at).strftime("%Y-%m-%d") if state.opened_at else None
    out: dict[str, Any] = {"cards": {}, "labels": {}, "errors": {}}
    for tk in NOW_TOOLKITS:
        params = (
            {"entry_date": entry, "entry_price": state.entry_avg}
            if tk == "technical" and entry
            else {}
        )
        step = run_toolkit(tk, x, params)
        if step.status == "error":
            out["errors"][tk] = step.reason
            continue
        out["labels"][tk] = {"label": step.label, "reason": step.reason}
        for model, card in step.cards.items():
            out["cards"][model] = _jsonable(card)
    return out


def then_now_diff(
    then: dict[str, dict[str, Any]], now: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    diff = {}
    for model, field in DIFF_FIELDS:
        a = (then.get(model) or {}).get(field)
        b = (now.get(model) or {}).get(field)
        if a is None and b is None:
            continue
        change = (
            float(b) - float(a)
            if isinstance(a, int | float) and isinstance(b, int | float)
            else None
        )
        diff[f"{model}.{field}"] = {"then": a, "now": b, "change": change}
    return diff


def evaluate_thesis(
    conn: Conn, position_id: int, now: dict[str, dict[str, Any]], close: float | None
) -> list[dict[str, Any]]:
    """Each thesis condition; ``machine`` ones checked against the merged now-cards (plus
    ``close``), with ``hit`` True / False / None (unparseable or field unknown)."""
    merged: dict[str, Any] = {}
    for card in now.values():
        merged.update(card)
    if close is not None:
        merged["close"] = close
    out = []
    for r in book.thesis_conditions(conn, position_id).to_dict("records"):
        item: dict[str, Any] = {
            "condition_id": int(r["condition_id"]),
            "condition": str(r["condition"]),
            "kind": str(r["kind"]),
            "evidence_id": r["evidence_id"] if pd.notna(r["evidence_id"]) else None,
            "hit": None,
            "value": None,
        }
        if r["kind"] == "machine":
            try:
                cond = Condition.parse(str(r["condition"]))
                item["value"] = merged.get(cond.field)
                item["hit"] = bool(cond.check(merged))
            except (ValueError, KeyError) as e:
                item["note"] = f"not evaluable: {e}"
        out.append(item)
    return out


def latest_research(conn: Conn, ticker: str) -> dict[str, Any] | None:
    df = query(
        "SELECT x.run_id, x.date, x.field, x.value, x.text FROM results x WHERE x.ticker = ?"
        " AND x.model = 'research' AND x.run_id = (SELECT MAX(run_id) FROM results y"
        " WHERE y.ticker = x.ticker AND y.model = 'research') ORDER BY x.rowid",
        conn,
        [ticker],
    )
    if df.empty:
        return None
    card: dict[str, Any] = {"run_id": int(df["run_id"].iloc[0]), "date": str(df["date"].iloc[0])}
    for r in df.to_dict("records"):
        card[str(r["field"])] = r["text"] if pd.notna(r["text"]) else r["value"]
    return card


def research_levels(research: dict[str, Any] | None) -> dict[str, Any] | None:
    """The trade levels the research agent set (ADR 0023: ``trade_*`` card fields), None when
    the research card has none."""
    from solaraalpha.agents import trade_levels

    levels = trade_levels(research) if research else None
    return levels.model_dump() if levels is not None else None


def systematic_stops(cards_now: dict[str, Any]) -> dict[str, Any]:
    """The rule-based stops (context only: the agent decides the stop, ADR 0024): the levels
    map's stop, the technical ATR stop and, for an open position, the chandelier trail."""
    return {
        "levels": (cards_now.get("levels") or {}).get("stop"),
        "technical": (cards_now.get("technical") or {}).get("stop"),
        "chandelier": (cards_now.get("position") or {}).get("stop"),
    }


def build_pack(
    conn: Conn, position_id: int, run_id: int, inputs: Inputs | None = None, text: bool = True
) -> dict[str, Any]:
    state = book.position_state(conn, position_id)
    then_df = book.entry_conditions(conn, position_id)
    then: dict[str, dict[str, Any]] = {}
    for r in then_df.to_dict("records"):
        then.setdefault(str(r["model"]), {})[str(r["field"])] = (
            r["text"] if pd.notna(r["text"]) else r["value"]
        )
    now = now_cards(state.ticker, state, inputs)
    cards_now = now["cards"]
    close = (cards_now.get("technical") or {}).get("close")
    triggers = store.load_triggers(run_id, state.ticker, conn=conn)
    triggers = triggers[(triggers["position_id"] == position_id) | triggers["position_id"].isna()]
    trig_rows = []
    for t in triggers.to_dict("records"):
        row = {"kind": t["kind"], "at": t["at"], "detail": t["detail"]}
        if t["kind"] == "filing" and text:
            try:
                row["text"] = fetch_text(str(t["detail"]["url"]))
            except Exception as e:
                row["text"] = f"unavailable: {e}"
        trig_rows.append(row)
    research = latest_research(conn, state.ticker)
    status = "pending" if state.status in ("draft", "approved") else str(state.status)
    return {
        "position_id": position_id,
        "ticker": state.ticker,
        "daily_run_id": run_id,
        "status": status,
        "pending_entry": book.pending_entry(conn, position_id) if status == "pending" else None,
        "state": asdict(state),
        "orders": json.loads(book.orders(conn, position_id).to_json(orient="records")),
        "plan": latest_plan(conn, position_id),
        "entry_conditions": then,
        "now": cards_now,
        "now_labels": now["labels"],
        "now_errors": now["errors"],
        "diff": then_now_diff(then, cards_now),
        "thesis": evaluate_thesis(
            conn, position_id, cards_now, float(close) if close is not None else None
        ),
        "triggers": trig_rows,
        "research": research,
        "research_levels": research_levels(research),
        "systematic_stops": systematic_stops(cards_now),
        "research_evidence_ids": json.loads(research["evidence_ids"])
        if research and research.get("evidence_ids")
        else [],
    }


def write_pack(pack: dict[str, Any], out: Path | None = None) -> Path:
    out = out or settings.processed_dir / "monitor" / str(pack["position_id"]) / str(
        pack["daily_run_id"]
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "pack.json").write_text(json.dumps(pack, indent=2, default=str))
    return out
