"""Fresh-catalyst flag for the weekly shortlist: which stocks have something new or coming.

ADR 0022: a catalyst, recent or upcoming, is essential, and whether it counts is the agents'
judgement. This module only *finds* candidates cheaply so a stock with fresh news can reach the
agents even when it ranks outside the top N (#193). It never judges quality.

A stock has a fresh catalyst when, per stock:

* a **material filing** (FCA NSM, routine admin dropped by :func:`analysis.events.material`)
  was published since ``since``;
* a **scheduled event** (results, trading update, AGM) is expected within ``horizon_weeks``,
  projected from the issuer's own filing history by :func:`analysis.events.expected_events`
  (an overdue one counts too);
* a **press headline** (Google News RSS, discovery only) appeared since ``since``.

Calls per stock: LSE instrument + GLEIF (ticker → LEI, :func:`resolve_issuer`), one NSM search
for ``LOOKBACK_YEARS`` of filings (one page for most issuers; it feeds both the recent filings
and the expected events), and one Google News query. Metadata and headlines only: no filing
text is fetched. A failed source logs a warning and the stock goes on with what it has.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, datetime

import pandas as pd

from solaraalpha.analysis.events import _short_name, expected_events, material
from solaraalpha.data.announcements import fetch_announcements, resolve_issuer
from solaraalpha.data.news import fetch_news
from solaraalpha.log import get_logger

log = get_logger(__name__)

DEFAULT_DAYS = 7  # "since the last weekly run" when ``since`` is not given
HORIZON_WEEKS = 10  # the strategy's 1-10 week holding window (ADR 0022)


def fresh_catalysts(
    tickers: Iterable[str],
    since: pd.Timestamp | None = None,
    horizon_weeks: int = HORIZON_WEEKS,
    *,
    now: pd.Timestamp | None = None,
    news: bool = True,
) -> dict[str, str]:
    """Ticker -> one-line reason, for the ``tickers`` with a fresh catalyst (in input order).

    ``since`` defaults to :data:`DEFAULT_DAYS` before ``now`` (default: the current time); naive
    timestamps are read as UTC. The reason joins what was found with ``"; "``, filings first,
    e.g. ``"RNS 2026-10-15: Trading Update (+1 more); event expected ~2026-11-20 (last: Half-year
    Report)"``.
    ``news=False`` skips the headline search. Stocks with nothing fresh are left out.
    """
    now = _utc(now) if now is not None else pd.Timestamp(datetime.now(UTC))
    since = _utc(since) if since is not None else now - pd.Timedelta(days=DEFAULT_DAYS)
    out: dict[str, str] = {}
    for ticker in tickers:
        reason = catalyst_reason(ticker, since, now, horizon_weeks, news=news)
        if reason:
            out[ticker] = reason
    return out


def catalyst_reason(
    ticker: str,
    since: pd.Timestamp,
    now: pd.Timestamp,
    horizon_weeks: int = HORIZON_WEEKS,
    news: bool = True,
) -> str | None:
    """The one-line reason for one stock, or None. Each source failing is a warning, not an
    error: the reason is built from the sources that answered."""
    parts: list[str] = []
    name = ticker.removesuffix(".L")
    try:
        issuer = resolve_issuer(ticker)
        name = _short_name(issuer.name) or name
        ann = fetch_announcements(issuer.lei, end=f"{now:%Y-%m-%dT%H:%M:%S}")
        parts += filing_parts(ann, since, now) + event_parts(ann, now, horizon_weeks)
    except Exception as e:
        log.warning("%s: filings unavailable (%s: %s)", ticker, type(e).__name__, e)
    if news:
        try:
            days = max(1, math.ceil((now - since).total_seconds() / 86400))
            parts += news_parts(fetch_news(f'"{name}"', days=days), since)
        except Exception as e:
            log.warning("%s: news unavailable (%s: %s)", ticker, type(e).__name__, e)
    return "; ".join(parts) or None


def filing_parts(ann: pd.DataFrame, since: pd.Timestamp, now: pd.Timestamp) -> list[str]:
    """``["RNS <date>: <headline> (+n more)"]`` for the material filings in ``[since, now]``, or
    ``[]``. The headline shown is the latest outside the ``Holdings`` category (major holdings,
    director dealings) when there is one, so a TR-1 doesn't hide a trading update; all count."""
    if ann.empty:
        return []
    new = material(ann[ann["published"].between(since, now)]).sort_values("published")
    if new.empty:
        return []
    business = new[new["category"] != "Holdings"]
    last = (business if not business.empty else new).iloc[-1]
    more = f" (+{len(new) - 1} more)" if len(new) > 1 else ""
    return [f"RNS {last['published']:%Y-%m-%d}: {last['headline']}{more}"]


def event_parts(ann: pd.DataFrame, now: pd.Timestamp, horizon_weeks: int) -> list[str]:
    """``["event expected ~<date> (last: <last cycle's headline>)"]`` for the soonest scheduled
    event within ``horizon_weeks`` of ``now`` (``"due now"`` when overdue), or ``[]``."""
    if ann.empty:
        return []
    ev = expected_events(ann, now, horizon_weeks=horizon_weeks)
    if ev.empty:
        return []
    nxt = ev.sort_values("likely").iloc[0]
    when = "due now" if nxt["likely"] <= now else f"expected ~{nxt['likely']:%Y-%m-%d}"
    return [f"event {when} (last: {nxt['headline']})"]


def news_parts(headlines: pd.DataFrame, since: pd.Timestamp) -> list[str]:
    """``["n headlines, latest <date>: <headline>"]`` for headlines since ``since``, or ``[]``."""
    if headlines.empty:
        return []
    new = headlines[headlines["published"] >= since].sort_values("published")
    if new.empty:
        return []
    last = new.iloc[-1]
    n = f"{len(new)} headlines" if len(new) > 1 else "1 headline"
    return [f"{n}, latest {last['published']:%Y-%m-%d}: {last['headline']}"]


def _utc(ts: pd.Timestamp | str) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
