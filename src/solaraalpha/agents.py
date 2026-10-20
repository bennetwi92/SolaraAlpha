"""What agents write back: a verdict with its evidence, validated and recorded in the DB.

The validator, research, events, monitor and post-mortem agents all produce the same shape: a
label with a confidence and reasons, some scores, kill criteria and the sources relied on. They
write a JSON file matching :class:`Verdict` and call ``scripts/record.py``, which validates it
and records it in one transaction (:func:`record_verdict`): a ``labels`` row for the gating
stages, a card under ``model = stage`` in ``results`` (dated with the run's screen week so it
joins the other cards) and one ``evidence`` row per source. **Agents can only downgrade**: a
``validate`` or ``research`` label must be no better than the previous stage's label for that
run and ticker (ACT > WATCH > PASS); ERROR is always allowed. See docs/decisions/0016.

A research ACT carries the **trade levels** (:class:`TradeLevels`: entry, entry type, stop and
why the stop sits there), saved on its card as ``trade_*`` fields; the planner sizes from them
and never sets a stop by rule (docs/decisions/0023).
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from solaraalpha import store
from solaraalpha.pipeline import SCREEN_TOOLKITS
from solaraalpha.store import Conn, query

Label = Literal["ACT", "WATCH", "PASS", "ERROR"]
Stage = Literal["validate", "research", "events", "monitor", "postmortem", "macro"]
SECTOR_PREFIX = "_SECTOR:"  # pseudo-ticker of a shared sector / macro note (#140)
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


class TradeLevels(BaseModel):
    """The agent's trade: where to enter and where the set-up is wrong (ADR 0023). Prices in
    the quote currency (pence for LSE)."""

    model_config = ConfigDict(extra="forbid")

    entry_type: Literal["limit", "stop"]  # limit: enter at or below; stop: buy the break
    entry: float = Field(gt=0)  # limit price, or the breakout trigger
    stop: float = Field(gt=0)  # below the entry: where the thesis / set-up is invalidated
    stop_reason: str = Field(min_length=1)
    valid_days: int | None = Field(default=None, ge=1, le=30)  # None: rules.entry_validity_days

    @model_validator(mode="after")
    def _stop_below_entry(self) -> TradeLevels:
        if not self.stop < self.entry:
            raise ValueError(f"stop {self.stop} must be below the entry {self.entry} (long only)")
        return self


TRADE_PREFIX = "trade_"  # card fields of a verdict's TradeLevels


def trade_levels(card: dict[str, Any] | None) -> TradeLevels | None:
    """The :class:`TradeLevels` saved on an agent's card (``load_cards`` dict), else None."""
    if not card or card.get("trade_entry") is None:
        return None
    vd = card.get("trade_valid_days")
    return TradeLevels(
        entry_type=card["trade_entry_type"],
        entry=float(card["trade_entry"]),
        stop=float(card["trade_stop"]),
        stop_reason=str(card.get("trade_stop_reason") or "-"),
        valid_days=None if vd is None or (isinstance(vd, float) and math.isnan(vd)) else int(vd),
    )


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
    trade: TradeLevels | None = None  # required on a research ACT (ADR 0023)

    @model_validator(mode="after")
    def _act_has_trade(self) -> Verdict:
        if self.stage == "research" and self.label == "ACT" and self.trade is None:
            raise ValueError("a research ACT must give `trade` (entry, entry_type, stop, reason)")
        return self

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


def record_verdict(
    conn: Conn, verdict: Verdict, actor: str, position_id: int | None = None
) -> dict[str, Any]:
    """Record ``verdict`` under ``actor`` (``agent:<name>``) in one transaction. Returns
    ``{"label": bool, "date": str, "evidence_ids": [...]}``. ``position_id`` (a post-mortem's
    closed position) is kept on the card so the runner does not repeat it.

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
                "INSERT INTO labels (run_id, ticker, stage, label, confidence, reason, actor)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (v.run_id, v.ticker, v.stage, v.label, v.confidence, "; ".join(v.reasons), actor),
            )
            wrote_label = True
        elif conn.execute(
            "SELECT 1 FROM results WHERE run_id = ? AND ticker = ? AND model = ? LIMIT 1",
            (v.run_id, v.ticker, v.stage),
        ).fetchone():
            raise ValueError(f"{v.stage} already recorded for {v.ticker} in run {v.run_id}")
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
        if v.trade is not None:
            fields |= {
                f"{TRADE_PREFIX}{k}": val
                for k, val in v.trade.model_dump().items()
                if val is not None
            }
        if position_id is not None:
            fields["position_id"] = int(position_id)
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
        retrieved = [str(x) for x in ev["retrieved"]] if "retrieved" in ev and len(ev) else []
        card["retrieved_to"] = max(retrieved) if retrieved else None
        started = pd.Timestamp(card["started_at"])
        started = started.tz_localize("UTC") if started.tz is None else started
        card["age_days"] = int((pd.Timestamp.now(tz="UTC") - started).days)
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
    "entry",
    "valid_until",
    "trigger",
    "structural",
)


def record_revision(conn: Conn, verdict: Verdict, position_id: int, actor: str) -> dict[str, Any]:
    """Record a monitor verdict (card + evidence) and, unless the action is ``hold``, propose
    the plan revision it carries in ``checks`` (``action``, ``stop`` / ``target`` / ``qty`` /
    ``type`` / ``price`` / ``entry`` / ``valid_until`` as strings, ``trigger``, ``structural``). Returns the record result
    plus ``revision_id`` (None for hold)."""
    if verdict.stage != "monitor":
        raise ValueError(f"a revision needs stage 'monitor', got {verdict.stage!r}")
    action = verdict.checks.get("action", "hold")
    out = record_verdict(conn, verdict, actor)
    out["revision_id"] = None
    if action == "hold":
        return out
    new: dict[str, Any] = {}
    for key in ("stop", "target", "target_2", "price", "entry"):
        if verdict.checks.get(key) not in (None, ""):
            new[key] = float(verdict.checks[key])
    if verdict.checks.get("qty") not in (None, ""):
        new["qty"] = int(float(verdict.checks["qty"]))
    if verdict.checks.get("type"):
        new["type"] = verdict.checks["type"]
    if verdict.checks.get("valid_until"):
        new["valid_until"] = verdict.checks["valid_until"]
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
    ``cap`` items: ``validate`` → the screen-ACT tickers (the week's shortlist, #192);
    ``research`` → tickers whose final label is ACT (once the validator has worked the run,
    only those it kept: one it never reached is not researched); ``monitor`` → position ids with an agent trigger in that daily run;
    ``postmortem`` → position ids closed since the daily run before ``run_id`` (ever, when
    there is none) with no post-mortem card yet. Tickers are ordered by ``rank_score`` over
    the run's cards (ties by ticker); those already recorded for the stage are left out."""
    from solaraalpha.monitoring import positions_to_review
    from solaraalpha.portfolio.fit import rank_score
    from solaraalpha.portfolio.planner import final_labels
    from solaraalpha.postmortem import closed_since

    if stage == "monitor":
        return [str(p) for p in positions_to_review(conn, run_id)[:cap]]
    if stage == "macro":
        return sector_groups(conn, run_id)[:cap]
    if stage == "postmortem":
        prev = conn.execute(
            "SELECT started_at FROM runs WHERE script = 'daily' AND run_id < ?"
            " ORDER BY run_id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        done = {
            int(v)
            for v in query(
                "SELECT value FROM results WHERE model = 'postmortem' AND field = 'position_id'"
                " AND value IS NOT NULL",
                conn,
            )["value"]
        }
        ids = [p for p in closed_since(conn, str(prev[0]) if prev else None) if p not in done]
        return [str(p) for p in ids[:cap]]
    if stage == "validate":
        df = query(
            "SELECT ticker, rank FROM labels WHERE run_id = ? AND stage = 'screen' AND label = 'ACT'",
            conn,
            [run_id],
        )
        tickers = [str(t) for t in df["ticker"]]
        ranks = {str(t): r for t, r in zip(df["ticker"], df["rank"], strict=True) if pd.notna(r)}
    elif stage == "research":
        labels = final_labels(conn, run_id)
        # once the validator has worked the run, a shortlisted ticker it never reached (past
        # the cap) is not researched on the screen's ACT alone (#192), as plan_run does
        reviewed = any(c.stage != "screen" for c in labels)
        cands = [c for c in labels if c.label == "ACT" and not (reviewed and c.stage == "screen")]
        tickers = [c.ticker for c in cands]
        ranks = {c.ticker: c.rank for c in cands if c.rank is not None}
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
        if t in ranks:  # the screen's stored rank (#125)
            scored.append((0, float(ranks[t]), t))
            continue
        score = rank_score(store.load_cards(run_id, t, conn=conn))
        scored.append((1, -(score if score == score else -float("inf")), t))
    return [t for _, _, t in sorted(scored)][:cap]


def sector_groups(conn: Conn, run_id: int) -> list[str]:
    """The run's screen-ACT tickers grouped by ``sector`` (config/benchmarks.toml) as
    ``_SECTOR:<name>`` pseudo-tickers, largest group first, those with a ``macro`` card in the
    run already left out (#140)."""
    from solaraalpha.portfolio.risk import sector_of

    df = query(
        "SELECT ticker FROM labels WHERE run_id = ? AND stage = 'screen' AND label = 'ACT'",
        conn,
        [run_id],
    )
    groups: dict[str, list[str]] = {}
    for t in df["ticker"]:
        groups.setdefault(sector_of(str(t)), []).append(str(t))
    done = set(
        query(
            "SELECT DISTINCT ticker FROM results WHERE run_id = ? AND model = 'macro'",
            conn,
            [run_id],
        )["ticker"]
    )
    names = sorted(groups, key=lambda s: (-len(groups[s]), s))
    return [f"{SECTOR_PREFIX}{s}" for s in names if f"{SECTOR_PREFIX}{s}" not in done]


def sector_members(conn: Conn, run_id: int, sector: str) -> list[str]:
    """The run's screen-ACT tickers in ``sector`` (the name after ``_SECTOR:``)."""
    from solaraalpha.portfolio.risk import sector_of

    name = sector.removeprefix(SECTOR_PREFIX)
    df = query(
        "SELECT ticker FROM labels WHERE run_id = ? AND stage = 'screen' AND label = 'ACT'",
        conn,
        [run_id],
    )
    return [str(t) for t in df["ticker"] if sector_of(str(t)) == name]


def outcome(conn: Conn, run_id: int, stage: str) -> dict[str, int]:
    """Counts of labels for ``stage`` in ``run_id`` (``total`` and ``errors``); for ``monitor``
    ``postmortem`` and ``macro`` the cards instead of labels."""
    if stage in ("monitor", "postmortem", "macro"):
        df = query(
            "SELECT ticker, text FROM results WHERE run_id = ? AND model = ? AND field = 'label'",
            conn,
            [run_id, stage],
        )
        return {
            "total": len(df),
            "errors": int((df["text"] == "ERROR").sum()) if len(df) else 0,
        }
    df = query("SELECT label FROM labels WHERE run_id = ? AND stage = ?", conn, [run_id, stage])
    return {"total": len(df), "errors": int((df["label"] == "ERROR").sum()) if len(df) else 0}


USAGE_KEYS = ("seconds", "turns", "input_tokens", "output_tokens", "cost_usd")


def parse_usage(data: Any) -> dict[str, float | int | None]:
    """Cost and runtime from ``claude-code-action``'s execution output (#139).

    The action writes a JSON log: a list of SDK messages whose last ``result`` message carries
    ``total_cost_usd``, ``num_turns``, ``duration_ms`` and ``usage`` (``input_tokens``,
    ``output_tokens``, cache tokens); a bare result dict is accepted too. Every field is
    optional: a missing or malformed one is None, never an error."""
    result: dict[str, Any] = {}
    if isinstance(data, list):
        for msg in reversed(data):
            if isinstance(msg, dict) and (msg.get("type") == "result" or "total_cost_usd" in msg):
                result = msg
                break
    elif isinstance(data, dict):
        result = data
    raw_usage = result.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}

    def num(v: Any) -> float | None:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f == f else None

    ms = num(result.get("duration_ms"))
    turns = num(result.get("num_turns"))
    inp = num(usage.get("input_tokens"))
    cache_parts = [
        num(usage.get("cache_read_input_tokens")),
        num(usage.get("cache_creation_input_tokens")),
    ]
    cache = sum(x for x in cache_parts if x is not None)
    out_tokens = num(usage.get("output_tokens"))
    return {
        "seconds": ms / 1000 if ms is not None else None,
        "turns": int(turns) if turns is not None else None,
        "input_tokens": int(inp + cache) if inp is not None else (int(cache) if cache else None),
        "output_tokens": int(out_tokens) if out_tokens is not None else None,
        "cost_usd": num(result.get("total_cost_usd")),
    }


def agent_usage(runs: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Cost, tokens and minutes per ISO week and per stage, plus the mean per invocation (#139)."""
    cols = ["cost_usd", "input_tokens", "output_tokens", "seconds"]
    out_cols = ["invocations", *cols, "minutes"]
    if runs.empty:
        empty = pd.DataFrame(columns=out_cols)
        return {"by_week": empty, "by_stage": empty, "per_ticker": empty}
    df = runs.copy()
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["week"] = pd.to_datetime(df["started_at"], utc=True).dt.strftime("%G-W%V")

    def agg(g: pd.DataFrame, divide: int = 1) -> dict[str, float]:
        row: dict[str, float] = {"invocations": len(g) / divide}
        for c in cols:
            s = g[c].sum(min_count=1)
            row[c] = float(s) / divide if pd.notna(s) else float("nan")
        row["minutes"] = row["seconds"] / 60 if not math.isnan(row["seconds"]) else float("nan")
        return row

    by_week = pd.DataFrame(
        [{"week": str(k), **agg(g)} for k, g in df.groupby("week", sort=True)],
        columns=["week", *out_cols],
    )
    by_stage = pd.DataFrame(
        [{"stage": str(k), **agg(g)} for k, g in df.groupby("stage", sort=True)],
        columns=["stage", *out_cols],
    )
    per_ticker = pd.DataFrame([agg(df, max(1, len(df)))], columns=out_cols)
    return {"by_week": by_week, "by_stage": by_stage, "per_ticker": per_ticker}
