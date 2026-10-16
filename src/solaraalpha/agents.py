"""What agents write back: a verdict with its evidence, validated and recorded in the DB.

The validator, research, events, monitor and post-mortem agents all produce the same shape: a
label with a confidence and reasons, some scores, kill criteria and the sources relied on. They
write a JSON file matching :class:`Verdict` and call ``scripts/record.py``, which validates it
and records it in one transaction (:func:`record_verdict`): a ``labels`` row for the gating
stages, a card under ``model = stage`` in ``results`` (dated with the run's screen week so it
joins the other cards) and one ``evidence`` row per source. **Agents can only downgrade**: a
``validate`` or ``research`` label must be no better than the previous stage's label for that
run and ticker (ACT > WATCH > PASS); ERROR is always allowed. See docs/decisions/0016.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from solaraalpha import store
from solaraalpha.pipeline import SCREEN_TOOLKITS
from solaraalpha.store import Conn, query

Label = Literal["ACT", "WATCH", "PASS", "ERROR"]
Stage = Literal["validate", "research", "events", "monitor", "postmortem"]
LABEL_STAGES = ("events", "validate", "research")  # stages that write a labels row
RANK = {"ACT": 3, "WATCH": 2, "PASS": 1}  # ERROR is outside the order
PREVIOUS = {"validate": ("screen",), "research": ("validate", "screen")}


class Evidence(BaseModel):
    """One source an agent relied on, with its discretionary impact."""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    publisher: str = ""
    published: datetime | None = None
    retrieved: datetime
    kind: Literal["filing", "news", "web", "data"]
    section: str  # e.g. "recent_catalysts"
    summary: str
    impact_score: float | None = Field(default=None, ge=-1, le=1)
    impact_confidence: float | None = Field(default=None, ge=0, le=1)
    driver: str | None = None
    horizon_weeks: int | None = None
    nsm_id: str | None = None  # dedupes filings across runs


class KillCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str
    kind: Literal["machine", "agent"]
    evidence_index: int | None = None  # index into the verdict's evidence list


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Stage
    ticker: str
    run_id: int
    label: Label
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]
    scores: dict[str, float] = Field(default_factory=dict)
    kill_criteria: list[KillCriterion] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    note_md: str = ""
    checks: dict[str, str] = Field(default_factory=dict)

    @field_validator("scores")
    @classmethod
    def _bounded(cls, scores: dict[str, float]) -> dict[str, float]:
        for k, v in scores.items():
            if not k.endswith("_risk") and not -1 <= v <= 1:
                raise ValueError(f"score {k!r} = {v} is outside [-1, 1]")
        return scores

    @field_validator("kill_criteria")
    @classmethod
    def _indices(cls, kcs: list[KillCriterion], info: Any) -> list[KillCriterion]:
        return kcs


class DowngradeError(ValueError):
    """An agent tried to improve a label."""


def load_verdict(text: str) -> Verdict:
    """Parse and validate a verdict JSON document (``pydantic.ValidationError`` on failure)."""
    return Verdict.model_validate_json(text)


def previous_label(conn: Conn, run_id: int, ticker: str, stage: str) -> str | None:
    """The label the agent is allowed to downgrade from: the nearest earlier stage present."""
    for prev in PREVIOUS.get(stage, ()):
        row = conn.execute(
            "SELECT label FROM labels WHERE run_id = ? AND ticker = ? AND stage = ?",
            (run_id, ticker, prev),
        ).fetchone()
        if row:
            return str(row[0])
    return None


def check_downgrade(verdict: Verdict, previous: str | None) -> None:
    if verdict.label == "ERROR" or previous is None or previous == "ERROR":
        return
    if RANK[verdict.label] > RANK[previous]:
        raise DowngradeError(
            f"{verdict.stage} may not upgrade {verdict.ticker} from {previous} to {verdict.label}"
            " (agents only downgrade)"
        )


def card_date(conn: Conn, run_id: int, ticker: str) -> str:
    """The screen week of ``ticker`` in ``run_id`` (any screening toolkit's card date), else
    the run's start date, so the agent's card joins the other cards on ``date``."""
    marks = ", ".join("?" * len(SCREEN_TOOLKITS))
    row = conn.execute(
        f"SELECT date FROM results WHERE run_id = ? AND ticker = ? AND model IN ({marks})"
        " ORDER BY date DESC LIMIT 1",
        (run_id, ticker, *SCREEN_TOOLKITS),
    ).fetchone()
    if row:
        return str(row[0])
    return str(store.run_info(run_id, conn=conn)["started_at"])[:10]


def record_verdict(conn: Conn, verdict: Verdict, actor: str) -> dict[str, Any]:
    """Record ``verdict`` under ``actor`` (``agent:<name>``) in one transaction. Returns
    ``{"label": bool, "date": str, "evidence_ids": [...]}``.

    Raises :class:`DowngradeError` for an upgrade and ``ValueError`` if that stage was already
    recorded for the run and ticker (append-only: a verdict is never replaced).
    """
    store.run_info(verdict.run_id, conn=conn)  # the run must exist
    v = verdict
    with conn:
        prev = previous_label(conn, v.run_id, v.ticker, v.stage)
        check_downgrade(v, prev)
        wrote_label = False
        if v.stage in LABEL_STAGES:
            existing = conn.execute(
                "SELECT label FROM labels WHERE run_id = ? AND ticker = ? AND stage = ?",
                (v.run_id, v.ticker, v.stage),
            ).fetchone()
            if existing:
                raise ValueError(
                    f"{v.stage} already recorded for {v.ticker} in run {v.run_id} ({existing[0]})"
                )
            conn.execute(
                "INSERT INTO labels VALUES (?, ?, ?, ?, ?, ?, ?)",
                (v.run_id, v.ticker, v.stage, v.label, v.confidence, "; ".join(v.reasons), actor),
            )
            wrote_label = True
        ids = [save_evidence(conn, v.run_id, v.ticker, v.stage, actor, e) for e in v.evidence]
        criteria = [
            {
                "condition": k.condition,
                "kind": k.kind,
                "evidence_id": ids[k.evidence_index]
                if k.evidence_index is not None and 0 <= k.evidence_index < len(ids)
                else None,
            }
            for k in v.kill_criteria
        ]
        date = card_date(conn, v.run_id, v.ticker)
        fields: dict[str, Any] = {
            "label": v.label,
            "confidence": v.confidence,
            **v.scores,
            "reasons": "; ".join(v.reasons),
            "kill_criteria": json.dumps(criteria),
            "checks": json.dumps(v.checks),
            "note_md": v.note_md,
            "actor": actor,
            "evidence_ids": json.dumps(ids),
        }
        store._save_card(conn, v.run_id, v.ticker, v.stage, {"date": date, **fields})
    return {"label": wrote_label, "date": date, "evidence_ids": ids}


def save_evidence(conn: Conn, run_id: int, ticker: str, stage: str, actor: str, e: Evidence) -> int:
    cur = conn.execute(
        "INSERT INTO evidence (run_id, ticker, stage, actor, url, title, publisher, published,"
        " retrieved, kind, section, summary, impact_score, impact_confidence, driver,"
        " horizon_weeks, nsm_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            ticker,
            stage,
            actor,
            e.url,
            e.title,
            e.publisher,
            e.published.isoformat() if e.published else None,
            e.retrieved.isoformat(),
            e.kind,
            e.section,
            e.summary,
            e.impact_score,
            e.impact_confidence,
            e.driver,
            e.horizon_weeks,
            e.nsm_id,
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def prior(conn: Conn, ticker: str, stage: str = "research", limit: int = 3) -> dict[str, Any]:
    """Earlier work on ``ticker``: the latest ``limit`` verdicts of ``stage`` (with their
    evidence) and every filing already interpreted (``nsm_id`` set, any stage)."""
    cards = query(
        "SELECT x.run_id, r.started_at, x.date, x.field, x.value, x.text FROM results x"
        " JOIN runs r USING (run_id) WHERE x.ticker = ? AND x.model = ?"
        " ORDER BY x.run_id DESC, x.rowid",
        conn,
        [ticker, stage],
    )
    verdicts: list[dict[str, Any]] = []
    for run_id, g in cards.groupby("run_id", sort=False):
        card: dict[str, Any] = {
            "run_id": int(str(run_id)),
            "started_at": str(g["started_at"].iloc[0]),
            "date": str(g["date"].iloc[0]),
        }
        for r in g.to_dict("records"):
            card[str(r["field"])] = r["text"] if pd.notna(r["text"]) else r["value"]
        ev = store.load_evidence(ticker, int(str(run_id)), stage, conn=conn)
        card["evidence"] = ev.to_dict("records")
        verdicts.append(card)
        if len(verdicts) >= limit:
            break
    filings = query(
        "SELECT DISTINCT nsm_id, title, run_id, stage FROM evidence"
        " WHERE ticker = ? AND nsm_id IS NOT NULL ORDER BY run_id",
        conn,
        [ticker],
    )
    return {
        "ticker": ticker,
        "stage": stage,
        "verdicts": verdicts,
        "filings": filings.to_dict("records"),
    }


REVISION_KEYS = (
    "action",
    "stop",
    "target",
    "target_2",
    "qty",
    "type",
    "price",
    "trigger",
    "structural",
)


def record_revision(conn: Conn, verdict: Verdict, position_id: int, actor: str) -> dict[str, Any]:
    """Record a monitor verdict (card + evidence) and, unless the action is ``hold``, propose
    the plan revision it carries in ``checks`` (``action``, ``stop`` / ``target`` / ``qty`` /
    ``type`` / ``price`` as strings, ``trigger``, ``structural``). Returns the record result
    plus ``revision_id`` (None for hold)."""
    if verdict.stage != "monitor":
        raise ValueError(f"a revision needs stage 'monitor', got {verdict.stage!r}")
    action = verdict.checks.get("action", "hold")
    out = record_verdict(conn, verdict, actor)
    out["revision_id"] = None
    if action == "hold":
        return out
    new: dict[str, Any] = {}
    for key in ("stop", "target", "target_2", "price"):
        if verdict.checks.get(key) not in (None, ""):
            new[key] = float(verdict.checks[key])
    if verdict.checks.get("qty") not in (None, ""):
        new["qty"] = int(float(verdict.checks["qty"]))
    if verdict.checks.get("type"):
        new["type"] = verdict.checks["type"]
    from solaraalpha.portfolio import book  # local import: book depends on store, not on agents

    with conn:
        out["revision_id"] = book.propose_revision(
            conn,
            position_id,
            action,
            new,
            "; ".join(verdict.reasons),
            actor,
            out["evidence_ids"],
            verdict.checks.get("trigger", ""),
        )
    return out


def error_verdict(ticker: str, run_id: int, stage: str, reason: str) -> Verdict:
    """The ERROR verdict the unattended runner records when a skill invocation failed."""
    return Verdict(
        stage=stage,
        ticker=ticker,
        run_id=run_id,
        label="ERROR",
        confidence=0.0,
        reasons=[reason],
        note_md=f"Agent run failed: {reason}",
    )


def queue(conn: Conn, run_id: int, stage: str, cap: int) -> list[str]:
    """What the unattended agents should work in ``run_id`` for ``stage``, best first, at most
    ``cap`` items: ``validate`` → the screen-ACT tickers; ``research`` → tickers whose final
    label is ACT; ``monitor`` → position ids with an agent trigger in that daily run. Tickers
    are ordered by ``rank_score`` over the run's cards (ties by ticker); those already recorded
    for the stage are left out."""
    from solaraalpha.monitoring import positions_to_review
    from solaraalpha.portfolio.fit import rank_score
    from solaraalpha.portfolio.planner import final_labels

    if stage == "monitor":
        return [str(p) for p in positions_to_review(conn, run_id)[:cap]]
    if stage == "validate":
        df = query(
            "SELECT ticker FROM labels WHERE run_id = ? AND stage = 'screen' AND label = 'ACT'",
            conn,
            [run_id],
        )
        tickers = [str(t) for t in df["ticker"]]
    elif stage == "research":
        tickers = [c.ticker for c in final_labels(conn, run_id) if c.label == "ACT"]
    else:
        raise ValueError(f"no queue for stage {stage!r}")
    done = set(
        query("SELECT ticker FROM labels WHERE run_id = ? AND stage = ?", conn, [run_id, stage])[
            "ticker"
        ]
    )
    scored = []
    for t in tickers:
        if t in done:
            continue
        score = rank_score(store.load_cards(run_id, t, conn=conn))
        scored.append((-(score if score == score else -float("inf")), t))
    return [t for _, t in sorted(scored)][:cap]


def outcome(conn: Conn, run_id: int, stage: str) -> dict[str, int]:
    """Counts of labels for ``stage`` in ``run_id`` (``total`` and ``errors``); for ``monitor``
    the cards instead of labels."""
    if stage == "monitor":
        df = query(
            "SELECT ticker, text FROM results WHERE run_id = ? AND model = 'monitor' AND field = 'label'",
            conn,
            [run_id],
        )
        return {
            "total": len(df),
            "errors": int((df["text"] == "ERROR").sum()) if len(df) else 0,
        }
    df = query("SELECT label FROM labels WHERE run_id = ? AND stage = ?", conn, [run_id, stage])
    return {"total": len(df), "errors": int((df["label"] == "ERROR").sum()) if len(df) else 0}
