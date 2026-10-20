"""The shadow book: what plain systematic rules would have done beside every agent decision.

Recording only (docs/decisions/0028): one append-only row per (scan run, baseline, ticker) in
the ``shadow`` table of the results DB, written at decision time from the run's saved cards,
labels, price snapshot and agent verdicts. Nothing here is traded or simulated; a later
simulator replays the rows, applying fit against each baseline's own shadow holdings.

Two moments write rows: :func:`record` after ``scan.py --save`` (the screen baselines, which
need no agent) and again after ``plan.py`` (the baselines paired with the research and PM
decisions). Each call writes only the baselines not yet recorded for the run, so re-runs and
re-plans never rewrite a decision. Only full weekly scans are recorded.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.agents import pm_decision, trade_levels
from solaraalpha.analysis.technical import atr
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent
from solaraalpha.log import get_logger
from solaraalpha.portfolio.rules import Rules, load_rules
from solaraalpha.store import Conn, query

log = get_logger(__name__)

SPEC = "0028"  # the ADR that froze the baselines
ATR_SESSIONS = 14  # daily ATR(14), Wilder, as triggers.py
BREAKOUT_WEEKS = 4  # buy-stop above the high of the last 4 completed weeks
BREAKOUT_ATR = 0.1  # ...plus this many daily ATRs
STOP_ATR = 2.0  # systematic initial stop: entry minus this many daily ATRs
FLAT_RISK = 0.02  # frozen in ADR 0028 (the rules' risk_per_trade on 2026-10-20)
TOP_N = 5
RANDOM_N = 2
CHANDELIER = "chandelier_3atr"  # trailing: highest daily close since entry - 3 ATR, ratcheted
AGENT = "agent"  # the monitor's management, as it happened

SCREEN_BASELINES = ("top5_rank", "random2_shortlist")
AGENT_BASELINES = (
    "agent",
    "flat2_rank",
    "entry_breakout",
    "stop_2atr",
    "stop_levels",
    "trail_chandelier",
)
BASELINES = SCREEN_BASELINES + AGENT_BASELINES

SHADOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow (
    run_id      INTEGER NOT NULL REFERENCES runs,  -- the weekly scan run the decision is from
    baseline    TEXT NOT NULL,     -- docs/decisions/0028 ids
    ticker      TEXT NOT NULL,
    priority    INTEGER NOT NULL,  -- the order the baseline fills slots in (1 = first)
    entry_type  TEXT,              -- stop (buy the break) / limit (at or below), NULL: skipped
    entry       REAL,              -- quote currency, as the cards
    stop        REAL,
    risk_pct    REAL NOT NULL,     -- fraction of equity lost at the stop
    valid_days  INTEGER,           -- entry order validity
    trail       TEXT NOT NULL,     -- chandelier_3atr / agent
    card_date   TEXT,              -- YYYY-MM-DD: the Friday the inputs describe
    inputs      TEXT NOT NULL,     -- JSON: the numbers the rule used
    note        TEXT NOT NULL,     -- why entry / stop are NULL, or ''
    spec        TEXT NOT NULL,     -- the ADR the rule comes from
    git_sha     TEXT NOT NULL,
    recorded_at TEXT NOT NULL,     -- ISO-8601 UTC
    PRIMARY KEY (run_id, baseline, ticker)
);
"""


@dataclass(frozen=True)
class ShadowRow:
    baseline: str
    ticker: str
    priority: int
    risk_pct: float
    trail: str
    entry_type: str | None = None
    entry: float | None = None
    stop: float | None = None
    valid_days: int | None = None
    card_date: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class Inputs:
    """What the systematic rules read for one ticker in one run."""

    card_date: pd.Timestamp | None
    atr: float  # daily ATR(14) at the card date
    high_4w: float  # highest daily high of the last 4 completed weeks
    levels_stop: float  # the levels card's stop

    def as_dict(self) -> dict[str, Any]:
        return {
            "atr": _num(self.atr),
            "high_4w": _num(self.high_4w),
            "levels_stop": _num(self.levels_stop),
        }

    @property
    def breakout(self) -> float:
        return self.high_4w + BREAKOUT_ATR * self.atr


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def weekly_scan(conn: Conn, run_id: int) -> bool:
    """Whether ``run_id`` is a full weekly scan (``scan.py`` over the watchlist, no toolkit)."""
    info = store.run_info(run_id, conn=conn)
    args = json.loads(str(info["args"]) or "{}")
    return info["script"] == "scan" and not args.get("tickers") and not args.get("toolkit")


def inputs_for(conn: Conn, run_id: int, ticker: str, as_seen: str) -> Inputs:
    """The rule inputs for ``ticker`` from the run's cards and price snapshot as seen at
    ``as_seen`` (the run start), cut to the card date: nothing after it is read."""
    cards = store.load_cards(run_id, ticker, conn=conn)
    date = next((cards[m]["date"] for m in ("technical", "levels") if m in cards), None)
    lv_stop = _num((cards.get("levels") or {}).get("stop"))
    nan = math.nan
    if date is None:
        return Inputs(None, nan, nan, lv_stop if lv_stop is not None else nan)
    raw = store.load_prices(ticker, as_seen=as_seen, conn=conn)
    end = pd.Timestamp(date).normalize() + pd.Timedelta(days=1)
    raw = raw[raw.index < end]
    a = h = nan
    if len(raw):
        daily = trim_to_consistent(clean_prices(raw), check_prices(raw))
        if len(daily) > ATR_SESSIONS:
            a = float(atr(daily, ATR_SESSIONS).iloc[-1])
        weekly = to_weekly(daily, as_of=end)
        if len(weekly) >= BREAKOUT_WEEKS:
            h = float(weekly["high"].iloc[-BREAKOUT_WEEKS:].max())
    return Inputs(pd.Timestamp(date), a, h, lv_stop if lv_stop is not None else nan)


def _row(
    baseline: str,
    ticker: str,
    priority: int,
    x: Inputs,
    entry_type: str,
    entry: float,
    stop: float,
    risk_pct: float,
    trail: str,
    valid_days: int | None,
    extra: dict[str, Any] | None = None,
) -> ShadowRow:
    """A row, or a skipped row (entry and stop NULL, the reason in ``note``) when the rule
    cannot price it."""
    inputs = {**x.as_dict(), **(extra or {})}
    date = f"{x.card_date:%Y-%m-%d}" if x.card_date is not None else None
    e, s = _num(entry), _num(stop)
    note = ""
    if x.card_date is None:
        note = "no technical or levels card in the run"
    elif e is None or s is None:
        note = "rule inputs missing (no price snapshot, ATR or level)"
    elif s >= e:
        note = f"stop {s:.4g} at or above entry {e:.4g}"
    if note:
        return ShadowRow(
            baseline,
            ticker,
            priority,
            risk_pct,
            trail,
            None,
            None,
            None,
            valid_days,
            date,
            inputs,
            note,
        )
    return ShadowRow(
        baseline, ticker, priority, risk_pct, trail, entry_type, e, s, valid_days, date, inputs
    )


def _systematic(
    baseline: str, ticker: str, priority: int, x: Inputs, rules: Rules, extra: dict[str, Any]
) -> ShadowRow:
    entry = x.breakout
    return _row(
        baseline,
        ticker,
        priority,
        x,
        "stop",
        entry,
        entry - STOP_ATR * x.atr,
        FLAT_RISK,
        CHANDELIER,
        rules.entry_validity_days,
        extra,
    )


def screen_rows(conn: Conn, run_id: int, rules: Rules, as_seen: str) -> list[ShadowRow]:
    """``top5_rank`` and ``random2_shortlist`` from the run's screen labels."""
    df = query(
        "SELECT ticker, label, rank FROM labels WHERE run_id = ? AND stage = 'screen'",
        conn,
        [run_id],
    )
    if df.empty:
        return []
    ranked = df.dropna(subset=["rank"]).sort_values(["rank", "ticker"])
    rank = {str(t): int(r) for t, r in zip(ranked["ticker"], ranked["rank"], strict=True)}
    out: list[ShadowRow] = []
    cache: dict[str, Inputs] = {}

    def x(t: str) -> Inputs:
        if t not in cache:
            cache[t] = inputs_for(conn, run_id, t, as_seen)
        return cache[t]

    for i, t in enumerate([t for t in rank if rank[t] <= TOP_N], 1):
        out.append(_systematic("top5_rank", t, i, x(t), rules, {"rank": rank[t]}))
    shortlist = sorted(str(t) for t in df.loc[df["label"] == "ACT", "ticker"])
    picks = random.Random(run_id).sample(shortlist, min(RANDOM_N, len(shortlist)))
    for i, t in enumerate(picks, 1):
        out.append(_systematic("random2_shortlist", t, i, x(t), rules, {"rank": rank.get(t)}))
    return out


def agent_rows(conn: Conn, run_id: int, rules: Rules, as_seen: str) -> list[ShadowRow]:
    """The baselines paired with the research and PM decisions (empty until research ran)."""
    from solaraalpha.portfolio.planner import final_labels

    researched = query(
        "SELECT COUNT(*) AS n FROM labels WHERE run_id = ? AND stage = 'research'", conn, [run_id]
    )
    if int(researched["n"].iloc[0]) == 0:
        return []
    acts = [c for c in final_labels(conn, run_id) if c.stage == "research" and c.label == "ACT"]
    levels = {
        c.ticker: lv
        for c in acts
        if (lv := trade_levels(store.load_cards(run_id, c.ticker, conn=conn).get("research")))
    }
    names = [c.ticker for c in acts if c.ticker in levels]  # screen-rank order
    rank = {c.ticker: c.rank for c in acts}
    pm = pm_decision(conn, run_id)
    if pm is not None:
        funded = [(str(a["ticker"]), float(a["risk_pct"])) for a in pm["allocation"]]
    else:
        funded = [(t, rules.risk_per_trade) for t in names]
    out: list[ShadowRow] = []
    for i, (t, risk) in enumerate(funded, 1):
        lv = levels.get(t)
        x = inputs_for(conn, run_id, t, as_seen)
        extra: dict[str, Any] = {"rank": rank.get(t), "pm": pm is not None}
        if lv is None:
            out.append(_row("agent", t, i, x, "", math.nan, math.nan, risk, AGENT, None, extra))
            continue
        vd = lv.valid_days or rules.entry_validity_days
        out.append(_row("agent", t, i, x, lv.entry_type, lv.entry, lv.stop, risk, AGENT, vd, extra))
    for i, t in enumerate(names, 1):
        lv, x = levels[t], inputs_for(conn, run_id, t, as_seen)
        vd = lv.valid_days or rules.entry_validity_days
        paired: dict[str, Any] = {
            "rank": rank.get(t),
            "agent_entry": lv.entry,
            "agent_stop": lv.stop,
        }
        e, s, et = lv.entry, lv.stop, lv.entry_type
        out += [
            _row("flat2_rank", t, i, x, et, e, s, FLAT_RISK, AGENT, vd, paired),
            _row(
                "entry_breakout",
                t,
                i,
                x,
                "stop",
                x.breakout,
                s,
                FLAT_RISK,
                AGENT,
                rules.entry_validity_days,
                paired,
            ),
            _row("stop_2atr", t, i, x, et, e, e - STOP_ATR * x.atr, FLAT_RISK, AGENT, vd, paired),
            _row("stop_levels", t, i, x, et, e, x.levels_stop, FLAT_RISK, AGENT, vd, paired),
            _row("trail_chandelier", t, i, x, et, e, s, FLAT_RISK, CHANDELIER, vd, paired),
        ]
    return out


def record(conn: Conn, run_id: int, rules: Rules | None = None) -> dict[str, int]:
    """Write every baseline not yet recorded for ``run_id`` (``baseline -> rows written``;
    empty when nothing is due or the run is not a full weekly scan)."""
    conn.executescript(SHADOW_SCHEMA)
    if not weekly_scan(conn, run_id):
        log.info("run %s is not a full weekly scan: no shadow rows", run_id)
        return {}
    rules = rules or load_rules()
    as_seen = str(store.run_info(run_id, conn=conn)["started_at"])
    have = set(
        query("SELECT DISTINCT baseline FROM shadow WHERE run_id = ?", conn, [run_id])["baseline"]
    )
    # Each group is written once per run: its first decision stands (re-runs, re-plans).
    rows = [] if have & set(SCREEN_BASELINES) else screen_rows(conn, run_id, rules, as_seen)
    if not have & set(AGENT_BASELINES):
        rows += agent_rows(conn, run_id, rules, as_seen)
    sha, now = store.git_sha(), datetime.now(UTC).isoformat()
    with conn:
        conn.executemany(
            "INSERT INTO shadow VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    r.baseline,
                    r.ticker,
                    r.priority,
                    r.entry_type,
                    r.entry,
                    r.stop,
                    r.risk_pct,
                    r.valid_days,
                    r.trail,
                    r.card_date,
                    json.dumps(r.inputs, default=str),
                    r.note,
                    SPEC,
                    sha,
                    now,
                )
                for r in rows
            ],
        )
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.baseline] = counts.get(r.baseline, 0) + 1
    return counts


def load(conn: Conn, run_id: int | None = None) -> pd.DataFrame:
    """Shadow rows (``inputs`` parsed), by run, baseline and priority."""
    conn.executescript(SHADOW_SCHEMA)
    sql, params = "SELECT * FROM shadow", []
    if run_id is not None:
        sql, params = sql + " WHERE run_id = ?", [run_id]
    df = query(sql + " ORDER BY run_id, baseline, priority, ticker", conn, params)
    if not df.empty:
        df["inputs"] = df["inputs"].map(json.loads)
    return df


def summary(counts: dict[str, int]) -> str:
    """One line for the scripts' output."""
    if not counts:
        return "shadow book: nothing new to record"
    return "shadow book: " + ", ".join(f"{b} {n}" for b, n in counts.items())
