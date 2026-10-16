"""Event pack for one stock: recent catalysts (with market reaction) and expected events.

Gathers the facts an analyst (the `events` agent skill) needs to anticipate and interpret
events; it does not interpret them. Writes ``pack.json`` and the full text of each recent
material announcement under ``--out`` (default ``data/processed/events/<ticker>/<date>/``).

uv run python scripts/events.py JET2.L [--days 7] [--horizon 16] [--lei LEI] [--no-news]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from solaraalpha.analysis.events import build_pack


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

    pack, out = build_pack(
        args.ticker,
        days=args.days,
        horizon=args.horizon,
        lei=args.lei,
        news_query=args.news_query,
        news=not args.no_news,
        out=args.out,
    )
    _print(pack, out)


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
