"""The monitor's pack: a triggered position, then versus now, for the monitor agent.

For each position with an agent trigger (``gap``, ``filing``) in a daily run, the pack holds the
position's state, orders and plan, the **entry conditions** frozen when it was drafted, the
cards **now** (technical with the position's entry date and price, levels, relative strength,
re-run on fresh data), a per-field then → now diff, the thesis conditions (machine ones
evaluated now), the triggers with the filing's full text, and the latest research verdict.
The agent proposes; the owner accepts or declines through ``book.py``. See docs/decisions/0018.
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

AGENT_TRIGGERS = ("gap", "filing", "thesis_hit")
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
    """Positions with an agent trigger in daily run ``run_id``."""
    marks = ", ".join("?" * len(AGENT_TRIGGERS))
    df = query(
        f"SELECT DISTINCT position_id FROM triggers WHERE run_id = ? AND kind IN ({marks})"
        " AND position_id IS NOT NULL ORDER BY position_id",
        conn,
        [run_id, *AGENT_TRIGGERS],
    )
    return [int(p) for p in df["position_id"]]


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
    return {
        "position_id": position_id,
        "ticker": state.ticker,
        "daily_run_id": run_id,
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
