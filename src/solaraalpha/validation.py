"""The validator's pack: what the toolkits saw, rebuilt from the DB for one run and ticker.

The validator (``.claude/skills/validate/SKILL.md``) is a second, independent opinion of the
**same data**: it gets the cards, the steps, the screen label, the price snapshot as seen at
the run (quality issues, the span kept, weekly and daily bars, the largest moves), the
fundamentals known at the card date and the recent filings' headlines. It never gets
``events``, ``research`` or other agents' cards or notes. The one live fetch (filings) is
wrapped so a failure becomes ``"unavailable"``. See docs/decisions/0016 "Validator".
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.analysis.charts import weekly_chart
from solaraalpha.analysis.events import material
from solaraalpha.analysis.fundamentals import known
from solaraalpha.analysis.levels import describe
from solaraalpha.analysis.technical import FAST_MA, SLOW_MA, sma
from solaraalpha.config import settings
from solaraalpha.data.announcements import fetch_announcements, resolve_issuer
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent
from solaraalpha.log import get_logger
from solaraalpha.portfolio.fit import RANK_NOTE
from solaraalpha.store import Conn, query

log = get_logger(__name__)

INDEPENDENT_MODELS = (
    "technical",
    "levels",
    "relative_strength",
    "fundamentals",
    "financing",
    "position",
)
WEEKS = 104
SESSIONS = 60
LARGEST_MOVES = 5
FILING_WEEKS = 13


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(df.to_json(orient="records", date_format="iso", date_unit="s"))


def build_pack(conn: Conn, ticker: str, run_id: int) -> dict[str, Any]:
    """Everything the validator may see for ``ticker`` in ``run_id``, JSON-able."""
    run = store.run_info(run_id, conn=conn)
    started = str(run["started_at"])
    cards = {
        m: {k: (v.isoformat() if isinstance(v, pd.Timestamp) else v) for k, v in c.items()}
        for m, c in store.load_cards(run_id, ticker, conn=conn).items()
        if m in INDEPENDENT_MODELS
    }
    date = next((c["date"] for c in cards.values() if "date" in c), started[:10])
    steps = query(
        "SELECT stage, toolkit, status, reason FROM steps WHERE run_id = ? AND ticker = ?"
        " ORDER BY rowid",
        conn,
        [run_id, ticker],
    )
    label = query(
        "SELECT label, reason, actor FROM labels WHERE run_id = ? AND ticker = ? AND stage = 'screen'",
        conn,
        [run_id, ticker],
    )
    pack: dict[str, Any] = {
        "ticker": ticker,
        "run_id": run_id,
        "run_started_at": started,
        "card_date": str(date)[:10],
        "cards": cards,
        "steps": _records(steps),
        "screen_label": _records(label)[0] if not label.empty else None,
        "rank_note": RANK_NOTE,
        "chart_context": chart_context(cards.get("levels")),
        "prices": prices_section(conn, ticker, started),
        "fundamentals": fundamentals_section(conn, ticker, started, str(date)[:10]),
        "filings": filings_section(ticker, started),
    }
    return pack


CONTEXT_FIELDS = (
    "compressed",
    "compression",
    "range_pctile",
    "volume_ratio",
    "wedge",
    "wedge_apex_weeks",
    "wedge_width_atr",
    "coil_pending",
    "trigger",
    "breakout",
    "new_high",
    "blue_sky",
    "from_high_52w",
    "reward_risk",
)


def chart_context(levels: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The levels card's compression / wedge / breakout / highs fields and their one-line
    ``summary``: context for the agents, not a gate (ADR 0022). ``reward_risk`` is to the next
    level only, not an estimate of the move. None without a levels card; fields an older card
    lacks are left out."""
    if not levels:
        return None
    out = {k: levels[k] for k in CONTEXT_FIELDS if k in levels}
    return out | {"summary": describe(**levels)}


def weekly_frame(conn: Conn, ticker: str, as_seen: str) -> pd.DataFrame:
    """Completed weekly bars (with ``ma10`` / ``ma40``) from the price snapshot as seen at
    ``as_seen``, after check / clean / trim; empty when there is no snapshot."""
    daily = store.load_prices(ticker, as_seen, conn=conn)
    if daily.empty:
        return pd.DataFrame()
    kept = trim_to_consistent(clean_prices(daily), check_prices(daily))
    weekly = to_weekly(kept, as_of=pd.Timestamp(as_seen))
    return weekly.assign(ma10=sma(weekly["close"], FAST_MA), ma40=sma(weekly["close"], SLOW_MA))


def prices_section(conn: Conn, ticker: str, as_seen: str) -> dict[str, Any]:
    daily = store.load_prices(ticker, as_seen, conn=conn)
    if daily.empty:
        return {"status": "no price snapshot in the DB for this run"}
    issues = check_prices(daily)
    cleaned = clean_prices(daily)
    kept = trim_to_consistent(cleaned, issues)
    weekly = to_weekly(kept, as_of=pd.Timestamp(as_seen))
    weekly = weekly.assign(ma10=sma(weekly["close"], FAST_MA), ma40=sma(weekly["close"], SLOW_MA))
    moves = weekly["adj_close"].pct_change(fill_method=None).dropna()
    largest = moves.reindex(moves.abs().sort_values(ascending=False).index[:LARGEST_MOVES])
    return {
        "currency": daily.attrs.get("currency"),
        "issues": _records(issues),
        "span": {
            "fetched_from": daily.index[0].strftime("%Y-%m-%d"),
            "fetched_to": daily.index[-1].strftime("%Y-%m-%d"),
            "kept_from": kept.index[0].strftime("%Y-%m-%d") if len(kept) else None,
            "kept_to": kept.index[-1].strftime("%Y-%m-%d") if len(kept) else None,
            "sessions_dropped": int(len(cleaned) - len(kept)),
            "weeks": len(weekly),
        },
        "weekly": _records(weekly.tail(WEEKS).reset_index()),
        "daily": _records(kept.tail(SESSIONS).reset_index()),
        "largest_weekly_moves": [
            {"week": pd.Timestamp(str(d)).strftime("%Y-%m-%d"), "return": float(r)}
            for d, r in largest.items()
        ],
    }


def fundamentals_section(conn: Conn, ticker: str, as_seen: str, card_date: str) -> dict[str, Any]:
    fund = store.load_fundamentals(ticker, as_seen, conn=conn)
    if fund.empty:
        return {"status": "no fundamentals snapshot in the DB for this run"}
    usable = known(fund, card_date)
    latest = usable.sort_values("date").groupby("field").tail(2).sort_values(["field", "date"])
    return {
        "as_of": card_date,
        "rows_known": len(usable),
        "rows_not_yet_public": int(len(fund) - len(usable)),
        "currency": str(fund["currency"].dropna().iloc[-1])
        if fund["currency"].notna().any()
        else None,
        "latest_periods": _records(latest),
    }


def filings_section(ticker: str, as_seen: str) -> dict[str, Any] | str:
    """Headlines of the material filings in the last ``FILING_WEEKS`` weeks before the run."""
    try:
        issuer = resolve_issuer(ticker)
        ann = fetch_announcements(issuer.lei, end=as_seen[:10])
    except Exception as e:
        log.warning("%s: filings unavailable (%s)", ticker, e)
        return "unavailable"
    end = pd.Timestamp(as_seen)
    ann = ann[
        (ann["published"] <= end) & (ann["published"] >= end - pd.Timedelta(weeks=FILING_WEEKS))
    ]
    ann = material(ann)
    cols = ["published", "type_code", "type", "category", "headline", "id", "url"]
    return {"issuer": issuer.name, "lei": issuer.lei, "items": _records(ann[cols])}


def write_chart(conn: Conn, pack: dict[str, Any], out: Path) -> Path | None:
    """``chart.png`` next to the pack: the weekly picture with the levels and the plan drawn
    on (None when the run has no price snapshot)."""
    weekly = weekly_frame(conn, pack["ticker"], pack["run_started_at"])
    if weekly.empty:
        return None
    cards = pack.get("cards") or {}
    return weekly_chart(
        weekly,
        cards.get("levels"),
        cards.get("technical"),
        out / "chart.png",
        title=f"{pack['ticker']} run {pack['run_id']}",
    )


RANK = {"ACT": 3, "WATCH": 2, "PASS": 1}
CHECKLIST = (
    "data_issues",
    "one_off_spikes",
    "overextension",
    "levels_stale_or_broken",
    "rs_concentration",
    "fundamentals_quality",
    "toolkit_agreement",
    "picture_agrees",
)
TOOLKIT_CHECKS = ("technical", "levels", "relative_strength", "fundamentals")


def disagreements(
    labels: pd.DataFrame, cards: Mapping[tuple[int, str], Mapping[str, Any]]
) -> pd.DataFrame:
    """One row per run and ticker where the ``validate`` label is worse than the ``screen``
    label (ERROR rows ignored): ``run_id, ticker, screen, validate, not_warranted`` (the
    toolkits the validator marked ``not warranted`` in ``checks``), ``concerns`` (the checklist
    items marked ``concern``). ``labels`` has ``run_id, ticker, stage, label`` (as
    :func:`solaraalpha.store.load_labels`); ``cards`` maps ``(run_id, ticker)`` to the validate
    card (its ``checks`` field is the JSON the agent wrote)."""
    cols = ["run_id", "ticker", "screen", "validate", "not_warranted", "concerns"]
    if labels.empty:
        return pd.DataFrame(columns=cols)
    by = {
        (int(r["run_id"]), str(r["ticker"]), str(r["stage"])): str(r["label"])
        for r in labels.to_dict("records")
    }
    rows = []
    for (run_id, ticker, stage), val in sorted(by.items()):
        if stage != "validate":
            continue
        screen = by.get((run_id, ticker, "screen"))
        if screen not in RANK or val not in RANK or RANK[val] >= RANK[screen]:
            continue
        checks = _checks(cards.get((run_id, ticker)))
        rows.append(
            {
                "run_id": run_id,
                "ticker": ticker,
                "screen": screen,
                "validate": val,
                "not_warranted": ",".join(
                    k
                    for k in TOOLKIT_CHECKS
                    if str(checks.get(k, "")).lower().startswith("not warranted")
                ),
                "concerns": ",".join(
                    k for k in CHECKLIST if str(checks.get(k, "")).lower().startswith("concern")
                ),
            }
        )
    return pd.DataFrame(rows, columns=cols)


def _checks(card: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = (card or {}).get("checks")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def disagreement_stats(dis: pd.DataFrame, labels: pd.DataFrame) -> dict[str, Any]:
    """Counts by toolkit marked not warranted, by checklist item flagged concern, and the
    disagreement rate per run (disagreements / validate labels in the run)."""
    by_toolkit: dict[str, int] = dict.fromkeys(TOOLKIT_CHECKS, 0)
    by_check: dict[str, int] = dict.fromkeys(CHECKLIST, 0)
    for r in dis.to_dict("records"):
        for k in filter(None, str(r["not_warranted"]).split(",")):
            by_toolkit[k] = by_toolkit.get(k, 0) + 1
        for k in filter(None, str(r["concerns"]).split(",")):
            by_check[k] = by_check.get(k, 0) + 1
    per_run = []
    validated = labels[labels["stage"] == "validate"] if not labels.empty else labels
    for run_id, g in validated.groupby("run_id") if not validated.empty else []:
        n = len(g)
        d = int((dis["run_id"] == run_id).sum()) if not dis.empty else 0
        per_run.append(
            {
                "run_id": int(str(run_id)),
                "validated": n,
                "disagreements": d,
                "rate": d / n if n else 0.0,
            }
        )
    return {"by_toolkit": by_toolkit, "by_check": by_check, "per_run": per_run, "total": len(dis)}


def write_pack(pack: dict[str, Any], out: Path | None = None) -> Path:
    out = out or settings.processed_dir / "validate" / pack["ticker"] / str(pack["run_id"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "pack.json").write_text(json.dumps(pack, indent=2, default=str))
    return out
