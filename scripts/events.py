"""Event pack for one stock: recent catalysts (with market reaction) and expected events.

Gathers the facts an analyst (the `events` agent skill) needs to anticipate and interpret
events; it does not interpret them. Writes ``pack.json`` and the full text of each recent
material announcement under ``--out`` (default ``data/processed/events/<ticker>/<date>/``).

uv run python scripts/events.py JET2.L [--days 7] [--horizon 16] [--lei LEI] [--no-news]
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

from solaraalpha.analysis.events import expected_events, material, reactions
from solaraalpha.config import settings
from solaraalpha.data.announcements import (
    Issuer,
    fetch_announcements,
    fetch_text,
    resolve_issuer,
)
from solaraalpha.data.clean import check_prices, clean_prices, trim_to_consistent
from solaraalpha.data.news import fetch_news
from solaraalpha.data.prices import fetch_daily


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso", date_unit="s"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("ticker", help="Yahoo LSE ticker, e.g. JET2.L")
    p.add_argument("--days", type=int, default=7, help="look-back for new catalysts (default 7)")
    p.add_argument("--horizon", type=int, default=16, help="weeks ahead to anticipate (default 16)")
    p.add_argument("--lei", default=None, help="issuer LEI; skips the ticker lookup")
    p.add_argument("--news-query", default=None, help="Google News query (default: issuer name)")
    p.add_argument("--no-news", action="store_true", help="skip press headlines")
    p.add_argument("--out", type=Path, default=None, help="output directory")
    args = p.parse_args()

    now = pd.Timestamp(datetime.now(UTC))
    since = now - pd.Timedelta(days=args.days)
    notes: list[str] = []
    issuer = Issuer(args.ticker, "", args.lei, "", "") if args.lei else resolve_issuer(args.ticker)
    out = args.out or settings.processed_dir / "events" / args.ticker / f"{now:%Y-%m-%d}"
    (out / "text").mkdir(parents=True, exist_ok=True)

    ann = fetch_announcements(issuer.lei)
    try:
        daily = fetch_daily(args.ticker)
        issues = check_prices(daily)
        if not issues.empty:
            notes.append(f"{len(issues)} price data-quality issues; see scripts/stock_prices.py")
        daily = trim_to_consistent(clean_prices(daily), issues)
        ann = reactions(ann, daily)
        last = {"session": f"{daily.index[-1]:%Y-%m-%d}", "close": float(daily["close"].iloc[-1])}
    except requests.RequestException as e:
        notes.append(f"prices unavailable ({e}); no market reactions")
        last = {}

    recent = material(ann[ann["published"] >= since]).copy()
    paths = []
    for row in recent.itertuples():
        path = out / "text" / f"{row.id}.txt"
        path.write_text(fetch_text(row.url))
        paths.append(str(path))
    recent["text_path"] = paths
    routine = len(ann[ann["published"] >= since]) - len(recent)
    expected = expected_events(ann, now, horizon_weeks=args.horizon)
    history = material(ann[ann["category"] == "Financial Results"]).tail(12)

    news = pd.DataFrame()
    if not args.no_news:
        query = args.news_query or f'"{_short_name(issuer.name or args.ticker)}"'
        try:
            news = fetch_news(query, days=args.days)
        except requests.RequestException as e:
            notes.append(f"news unavailable ({e})")

    pack = {
        "generated": now.isoformat(),
        "window": {"since": since.isoformat(), "until": now.isoformat()},
        "issuer": vars(issuer),
        "last_price": last,
        "announcements": _records(recent),
        "routine_filings_skipped": routine,
        "expected_events": _records(expected),
        "results_history": _records(history),
        "news_query": news.attrs.get("query"),
        "news": _records(news),
        "notes": notes,
    }
    (out / "pack.json").write_text(json.dumps(pack, indent=2))
    _print(pack, out)


def _short_name(name: str) -> str:
    """'JET2 PLC' -> 'JET2'; legal suffixes would make the news search miss most articles."""
    return " ".join(
        w for w in name.split() if w.upper().strip(".") not in {"PLC", "LIMITED", "LTD"}
    )


def _print(pack: dict, out: Path) -> None:
    iss = pack["issuer"]
    print(f"{iss['ticker']}  {iss['name']}  LEI {iss['lei']}  {iss['market']}")
    print(f"window {pack['window']['since'][:10]} .. {pack['window']['until'][:10]}\n")
    print(f"Announcements ({pack['routine_filings_skipped']} routine skipped):")
    for a in pack["announcements"]:
        r = f"  ret {a['ret']:+.1%} z {a['z']:+.1f}" if a.get("ret") is not None else ""
        print(f"  {a['published'][:16]}  {a['type_code']:<5} {a['headline'][:60]}{r}")
    print("\nExpected events:")
    for e in pack["expected_events"]:
        mv = e.get("median_abs_move")
        mv_s = f"  typical move {mv:.1%}" if mv is not None else ""
        print(
            f"  {e['window_start'][:10]} .. {e['window_end'][:10]}  {e['types']:<8}"
            f" {e['headline'][:45]}  ({e['years']}y){mv_s}"
        )
    print(f"\nNews ({len(pack['news'])} headlines, query {pack['news_query']})")
    for n in pack["news"][-10:]:
        print(f"  {n['published'][:16]}  {n['source'][:20]:<20} {n['headline'][:70]}")
    for note in pack["notes"]:
        print(f"NOTE: {note}")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
