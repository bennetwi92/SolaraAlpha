"""The post-mortem pack: a closed trade's whole record for the post-mortem agent.

A closed position is a labelled example. The pack gathers what the entry said (the frozen
entry conditions and the plan), what happened (fills, every weekly review card, the plan
revisions, the daily triggers, the research and monitor verdicts with their evidence, the
close summary) and the weekly bars from the entry week - 8 to the exit week + 4. Bars are
the stored prices **as seen at the close**; bars whose week completed after the close are
labelled ``hindsight`` (and any pre-exit week with no snapshot at the time ``as_seen: false``).
The agent (``/postmortem``) writes a ``postmortem`` verdict through ``record.py``. ADR 0016.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.config import settings
from solaraalpha.data.clean import clean_prices, to_weekly
from solaraalpha.monitoring import latest_plan
from solaraalpha.portfolio import book
from solaraalpha.portfolio.journal import setup_type
from solaraalpha.store import Conn, query

WEEKS_BEFORE = 8
WEEKS_AFTER = 4
AGENT_STAGES = ("research", "monitor")
LEAD_DAYS = 14  # agent work this long before the draft still belongs to the trade


def closed_since(conn: Conn, since: str | None = None) -> list[int]:
    """Positions closed after ``since`` (ISO; None: ever), in closing order."""
    sql = "SELECT position_id FROM position_events WHERE kind = 'closed'"
    params: list[Any] = []
    if since:
        sql += " AND at > ?"
        params.append(since)
    df = query(sql + " ORDER BY at, event_id", conn, params)
    return [int(p) for p in df["position_id"]]


def _cards(conn: Conn, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """Cards from long ``results`` rows (``run_id, date, field, value, text``), one per run."""
    df = query(sql, conn, params)
    out: dict[int, dict[str, Any]] = {}
    for r in df.to_dict("records"):
        card = out.setdefault(
            int(r["run_id"]), {"run_id": int(r["run_id"]), "date": str(r["date"])}
        )
        card[str(r["field"])] = r["text"] if pd.notna(r["text"]) else r["value"]
    return [out[k] for k in sorted(out)]


def review_cards(conn: Conn, ticker: str, position_id: int) -> list[dict[str, Any]]:
    """Every weekly ``review`` card written for ``position_id`` (oldest first)."""
    return _cards(
        conn,
        "SELECT run_id, date, field, value, text FROM results WHERE model = 'review' AND ticker = ?"
        " AND run_id IN (SELECT run_id FROM results WHERE model = 'review' AND ticker = ?"
        " AND field = 'position_id' AND value = ?) ORDER BY run_id, rowid",
        [ticker, ticker, position_id],
    )


def agent_cards(conn: Conn, ticker: str, stage: str, lo: str, hi: str) -> list[dict[str, Any]]:
    """``stage`` cards (research / monitor) for ``ticker`` from runs started in ``[lo, hi]``."""
    return _cards(
        conn,
        "SELECT x.run_id, x.date, x.field, x.value, x.text FROM results x JOIN runs r USING (run_id)"
        " WHERE x.model = ? AND x.ticker = ? AND r.started_at BETWEEN ? AND ? ORDER BY x.run_id, x.rowid",
        [stage, ticker, lo, hi],
    )


def evidence_rows(conn: Conn, ticker: str, lo: str, hi: str) -> list[dict[str, Any]]:
    marks = ", ".join("?" * len(AGENT_STAGES))
    df = query(
        f"SELECT e.* FROM evidence e JOIN runs r USING (run_id) WHERE e.ticker = ? AND e.stage IN ({marks})"
        " AND r.started_at BETWEEN ? AND ? ORDER BY e.evidence_id",
        conn,
        [ticker, *AGENT_STAGES, lo, hi],
    )
    return _records(df)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(df.to_json(orient="records", date_format="iso")) if len(df) else []


def _friday(ts: pd.Timestamp) -> pd.Timestamp:
    """The Friday ending the week that contains ``ts`` (midnight UTC)."""
    d = ts.tz_convert("UTC").normalize() if ts.tz is not None else ts.tz_localize("UTC").normalize()
    return d + pd.Timedelta(days=(4 - d.weekday()) % 7)


def weekly_bars(
    conn: Conn,
    ticker: str,
    opened_at: str,
    closed_at: str,
    fetch: Callable[[str], pd.DataFrame] | None = None,
) -> list[dict[str, Any]]:
    """Weekly bars from the entry week - :data:`WEEKS_BEFORE` to the exit week +
    :data:`WEEKS_AFTER`. Bars from the prices stored as seen at the close carry
    ``as_seen: true``; later bars come from the latest stored prices (or ``fetch``). A bar whose
    week completed after the close is ``hindsight: true``."""
    opened, closed = pd.Timestamp(opened_at), pd.Timestamp(closed_at)
    start = _friday(opened) - pd.Timedelta(weeks=WEEKS_BEFORE)
    end = _friday(closed) + pd.Timedelta(weeks=WEEKS_AFTER)
    seen = store.load_prices(ticker, as_seen=closed, conn=conn)
    latest = store.load_prices(ticker, conn=conn)
    if latest.empty and fetch is not None:
        try:
            latest = clean_prices(fetch(ticker))
        except Exception:
            latest = pd.DataFrame()
    far = end + pd.Timedelta(days=1)
    w_seen = to_weekly(seen, as_of=far) if not seen.empty else pd.DataFrame()
    w_late = to_weekly(latest, as_of=far) if not latest.empty else pd.DataFrame()
    bars: dict[pd.Timestamp, dict[str, Any]] = {}
    for frame, as_seen in ((w_late, False), (w_seen, True)):
        for date, row in frame.iterrows():
            d = pd.Timestamp(str(date))
            if d < start or d > end:
                continue
            bars[d] = {
                "date": d.strftime("%Y-%m-%d"),
                **{
                    k: _num(row.get(k))
                    for k in ("open", "high", "low", "close", "adj_close", "volume")
                },
                "as_seen": as_seen,
                "hindsight": bool(d + pd.Timedelta(days=1) > closed.tz_convert("UTC")),
            }
    return [bars[k] for k in sorted(bars)]


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None if math.isnan(obj) else ("inf" if obj > 0 else "-inf")
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj


def build_pack(
    conn: Conn, position_id: int, fetch: Callable[[str], pd.DataFrame] | None = None
) -> dict[str, Any]:
    """Everything the post-mortem agent needs about one closed position (``ValueError`` if it
    is not closed)."""
    state = book.position_state(conn, position_id)
    if state.status != "closed":
        raise ValueError(
            f"position {position_id} is {state.status}; a post-mortem needs a closed one"
        )
    pos = book.positions(conn).set_index("position_id").loc[position_id].to_dict()
    ev = book.position_events(conn, position_id)
    closed_ev = ev[ev["kind"] == "closed"].iloc[-1]
    closed_at = str(closed_ev["at"])
    drafted_at = str(ev["at"].iloc[0])
    opened_at = state.opened_at or drafted_at
    lo = (pd.Timestamp(drafted_at) - pd.Timedelta(days=LEAD_DAYS)).isoformat()
    then: dict[str, dict[str, Any]] = {}
    for r in book.entry_conditions(conn, position_id).to_dict("records"):
        then.setdefault(str(r["model"]), {})[str(r["field"])] = (
            r["text"] if pd.notna(r["text"]) else r["value"]
        )
    plan = latest_plan(conn, position_id)
    raw_run = pos["opened_from_run_id"]
    opened_from = int(raw_run) if pd.notna(raw_run) else None
    pack: dict[str, Any] = {
        "position_id": position_id,
        "ticker": state.ticker,
        "opened_from_run_id": opened_from,
        "horizon_weeks": int(pos["horizon_weeks"]),
        "thesis": str(pos["thesis"]),
        "drafted_at": drafted_at,
        "opened_at": opened_at,
        "closed_at": closed_at,
        "close_summary": dict(closed_ev["payload"]),
        "state": asdict(state),
        "plan": plan,
        "setup": {
            "type": setup_type(plan),
            "entry_type": plan.get("entry_type"),
            "coil_pending": plan.get("coil_pending"),
            "blue_sky": plan.get("blue_sky"),
            "wait_for_break": plan.get("wait_for_break"),
        },
        "entry_conditions": then,
        "thesis_conditions": _records(book.thesis_conditions(conn, position_id)),
        "events": _records(ev),
        "orders": _records(book.orders(conn, position_id)),
        "fills": _records(book.fills(conn, position_id=position_id)),
        "reviews": review_cards(conn, state.ticker, position_id),
        "revisions": _records(book.revisions(conn, position_id)),
        "triggers": _records(
            query(
                "SELECT * FROM triggers WHERE position_id = ? ORDER BY at, rowid",
                conn,
                [position_id],
            )
        ),
        "research": agent_cards(conn, state.ticker, "research", lo, closed_at),
        "monitor": agent_cards(conn, state.ticker, "monitor", lo, closed_at),
        "evidence": evidence_rows(conn, state.ticker, lo, closed_at),
        "bars": weekly_bars(conn, state.ticker, opened_at, closed_at, fetch),
        "bars_note": (
            f"weekly bars from the entry week - {WEEKS_BEFORE} to the exit week + {WEEKS_AFTER};"
            " as_seen = the snapshot stored by the close; hindsight = the week completed after the close"
        ),
    }
    for t in pack["triggers"]:
        if isinstance(t.get("detail"), str):
            t["detail"] = json.loads(t["detail"])
    return _clean(pack)


def write_pack(pack: dict[str, Any], out: Path | None = None) -> Path:
    out = out or settings.processed_dir / "postmortem" / str(pack["position_id"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "pack.json").write_text(json.dumps(pack, indent=2, default=str))
    return out
