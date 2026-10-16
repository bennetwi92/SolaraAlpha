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
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.analysis.events import material
from solaraalpha.analysis.fundamentals import known
from solaraalpha.analysis.technical import FAST_MA, SLOW_MA, sma
from solaraalpha.config import settings
from solaraalpha.data.announcements import fetch_announcements, resolve_issuer
from solaraalpha.data.clean import check_prices, clean_prices, to_weekly, trim_to_consistent
from solaraalpha.log import get_logger
from solaraalpha.store import Conn, query

log = get_logger(__name__)

INDEPENDENT_MODELS = ("technical", "levels", "relative_strength", "fundamentals", "position")
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
        "prices": prices_section(conn, ticker, started),
        "fundamentals": fundamentals_section(conn, ticker, started, str(date)[:10]),
        "filings": filings_section(ticker, started),
    }
    return pack


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


def write_pack(pack: dict[str, Any], out: Path | None = None) -> Path:
    out = out or settings.processed_dir / "validate" / pack["ticker"] / str(pack["run_id"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "pack.json").write_text(json.dumps(pack, indent=2, default=str))
    return out
