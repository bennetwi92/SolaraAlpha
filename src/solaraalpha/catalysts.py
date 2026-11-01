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
* a **press headline** (Google News RSS, discovery only) that names the company
  (:func:`named`) appeared since ``since``.

Each catalyst is **tiered** deterministically from its NSM type code and headline (#222, #214
F6), so the capped catalyst slots go to information before noise (:func:`pipeline.screen` fills
them by tier, then rank):

* **tier 1, information**: results, trading updates, contracts, regulatory approvals,
  agreements and acquisitions, operational updates (:data:`INFORMATION`);
* **tier 2, scheduled**: a results / trading update / AGM expected in 2-6 weeks, or overdue;
* **tier 3, discovery**: any other material filing, an event further out, a press headline;
* **negative** (tier 4): a placing or fundraise, a Nomad or auditor change, results delayed, a
  suspension, a profit warning in the headline (:data:`NEGATIVE`). Shown as ``warning: ...``,
  never an exclusion (ADR 0022): a stock with only warnings ranks after every other catalyst.

The reason starts with its tier (``tier 1: ...``, ``negative: ...``; :func:`tier_of` reads it
back) and is saved with the shortlist reason. Wording against expectations ("ahead of") is not
read here: that is the expectations signal, tested first (#223).

Calls per stock: ticker → LEI from config/issuers.toml (:func:`resolve_issuer`), one NSM search
for ``LOOKBACK_YEARS`` of filings (one page for most issuers; it feeds both the recent filings
and the expected events), and one Google News query. Metadata and headlines only: no filing
text is fetched. A failed source logs a warning and the stock goes on with what it has.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import UTC, datetime

import pandas as pd

from solaraalpha.analysis.events import _short_name, expected_events, material
from solaraalpha.analysis.financing import RAISE
from solaraalpha.data.announcements import cached, fetch_announcements, remember, resolve_issuer
from solaraalpha.data.news import fetch_news
from solaraalpha.log import get_logger

log = get_logger(__name__)

DEFAULT_DAYS = 7  # "since the last weekly run" when ``since`` is not given
HORIZON_WEEKS = 10  # the strategy's 1-10 week holding window (ADR 0022)
NEGATIVE_TIER = 4
SCHEDULED_WEEKS = (2, 6)  # an expected event this many weeks out is tier 2
# Tier 1 NSM type codes: filings that carry information about the business. Director dealings
# (DSH) are not: by type they are mostly option grants and share vesting; open-market buying
# needs the filing text (the director-cluster signal, R6, tested first: #223).
INFORMATION = frozenset(
    {"TST", "FR", "PRE", "IR", "ACS", "CNT", "REA", "AGR", "ACQ", "JVE", "DIS", "UPD", "DRL"}
)
# A headline that carries information whatever the NSM type it was filed under: companies file
# contract wins and operational news as MSCL ("$7m Production Orders from US Defence Prime",
# CNC.L, scan run 26 — #305). Admin results ("Result of AGM") are not.
INFORMATION_HEADLINE = re.compile(
    r"\b(?:contracts?|orders?|awards?|awarded|acquisition|acquires|disposal|agreement"
    r"|partnership|licen[cs]e|approval|trading (?:update|statement)|results|production"
    r"|drilling|resource|reserves?|operational update|quarterly (?:update|report))\b",
    re.I,
)
ADMIN_HEADLINE = re.compile(r"^result of\b|court order|\bmeeting\b|form 8\.3", re.I)
NEGATIVE_CODES = frozenset({"SUS"})  # suspension
NEGATIVE = re.compile(
    r"nomad|nominated adviser|auditor|delay|postpone|suspen|going concern|profit warning"
    r"|below (?:market |board )?expectations",
    re.I,
)

Part = tuple[int, str]  # (tier, text)

# A firm offer or a scheme for the company (not a Rule 2.4 possible offer, which can still
# burst): the price is pinned to the offer, so the stock is off the shortlist (#306).
FIRM_OFFER = re.compile(
    r"rule 2\.7|recommended (?:final )?(?:cash |share |all-share )?(?:offer|acquisition)"
    r"|scheme (?:document|of arrangement)|court (?:meeting|sanction)|sanction of (?:the )?scheme"
    r"|offer (?:document|unconditional|declared)",
    re.I,
)
OFFER_ENDED = re.compile(r"laps|withdraw|terminat|will not proceed|no intention to make", re.I)
OFFER_MONTHS = 9  # a firm offer older than this with no news is not treated as live


def classify(type_code: str, headline: str) -> int:
    """Tier of one material filing: :data:`NEGATIVE_TIER` for a raise or a red flag, 1 for an
    :data:`INFORMATION` type or an :data:`INFORMATION_HEADLINE` (not an admin one), else 3."""
    if type_code in NEGATIVE_CODES or NEGATIVE.search(headline) or RAISE.search(headline):
        return NEGATIVE_TIER
    if type_code in INFORMATION:
        return 1
    news = INFORMATION_HEADLINE.search(headline) and not ADMIN_HEADLINE.search(headline)
    return 1 if news else 3


def tier_of(reason: str) -> int:
    """The tier a :func:`catalyst_reason` starts with (``tier 2: ...`` -> 2, ``negative: ...``
    -> :data:`NEGATIVE_TIER`); 3 for a reason with no tag."""
    if reason.startswith("negative:"):
        return NEGATIVE_TIER
    m = re.match(r"tier (\d):", reason)
    return int(m.group(1)) if m else 3


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
    timestamps are read as UTC. The reason is the best tier, then what was found joined with
    ``"; "`` (filings first, warnings last), e.g. ``"tier 1: RNS 2026-10-15: Trading Update (+1
    more); event expected ~2026-11-20 (last: Half-year Report)"``.
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
    """The one-line reason for one stock, tagged with its tier, or None. Each source failing is
    a warning, not an error: the reason is built from the sources that answered."""
    parts: list[Part] = []
    name = ""
    try:
        issuer = resolve_issuer(ticker)
        name = _short_name(issuer.name)
        ann = fetch_announcements(issuer.lei, end=f"{now:%Y-%m-%dT%H:%M:%S}")
        if abs(pd.Timestamp(datetime.now(UTC)) - now) < pd.Timedelta(hours=1):
            remember(issuer.lei, ann)  # a current fetch: the events stage reuses it (#303)
        parts += filing_parts(ann, since, now) + event_parts(ann, now, horizon_weeks)
    except Exception as e:
        log.warning("%s: filings unavailable (%s: %s)", ticker, type(e).__name__, e)
    # No legal name, no news: a search on the bare ticker (FRAN, NET, CNC) finds other things.
    if news and name:
        try:
            days = max(1, math.ceil((now - since).total_seconds() / 86400))
            parts += news_parts(named(fetch_news(f'"{name}"', days=days), name), since)
        except Exception as e:
            log.warning("%s: news unavailable (%s: %s)", ticker, type(e).__name__, e)
    return join_parts(parts)


def offer_period(ann: pd.DataFrame, now: pd.Timestamp) -> str | None:
    """``"in an offer period: RNS <date>: <headline>"`` when the filings up to ``now`` hold a
    firm offer / scheme (:data:`FIRM_OFFER`) in the last :data:`OFFER_MONTHS` with no later
    lapse or withdrawal (:data:`OFFER_ENDED`), else None."""
    if ann.empty:
        return None
    window = ann[
        (ann["published"] > now - pd.DateOffset(months=OFFER_MONTHS)) & (ann["published"] <= now)
    ]
    firm = window[window["headline"].fillna("").str.contains(FIRM_OFFER)]
    if firm.empty:
        return None
    last = firm.iloc[-1]
    later = window[window["published"] > last["published"]]
    if later["headline"].fillna("").str.contains(OFFER_ENDED).any():
        return None
    return f"in an offer period: RNS {last['published']:%Y-%m-%d}: {last['headline']}"


def offer_periods(tickers: Iterable[str], now: pd.Timestamp | None = None) -> dict[str, str]:
    """Ticker -> :func:`offer_period` reason, from the filings this process already fetched
    (the catalyst pass): no NSM search of its own. Stocks without cached filings are left out."""
    now = _utc(now) if now is not None else pd.Timestamp(datetime.now(UTC))
    out: dict[str, str] = {}
    for ticker in tickers:
        try:
            ann = cached(resolve_issuer(ticker).lei)
        except Exception:
            continue
        if ann is not None and (why := offer_period(ann, now)):
            out[ticker] = why
    return out


def join_parts(parts: list[Part]) -> str | None:
    """``"tier N: a; b; warning: c"``: the best non-negative tier (``negative:`` when there are
    only warnings), positives first, warnings last; None when there are no parts."""
    if not parts:
        return None
    good = [text for t, text in parts if t < NEGATIVE_TIER]
    bad = [f"warning: {text}" for t, text in parts if t >= NEGATIVE_TIER]
    best = min((t for t, _ in parts if t < NEGATIVE_TIER), default=None)
    tag = f"tier {best}" if best is not None else "negative"
    return f"{tag}: " + "; ".join(good + bad)


def filing_parts(ann: pd.DataFrame, since: pd.Timestamp, now: pd.Timestamp) -> list[Part]:
    """Parts for the material filings in ``[since, now]`` (:func:`classify`): ``"RNS <date>:
    <headline> (+n more)"`` for the latest filing of the best positive tier, counting the other
    positive ones, and the latest negative filing as its own part; ``[]`` if none."""
    if ann.empty:
        return []
    new = material(ann[ann["published"].between(since, now)]).sort_values("published")
    if new.empty:
        return []
    tiers = pd.Series(
        [classify(str(c), str(h)) for c, h in zip(new["type_code"], new["headline"], strict=True)],
        index=new.index,
    )
    out: list[Part] = []
    good = tiers[tiers < NEGATIVE_TIER]
    if len(good):
        best = int(good.min())
        last = new.loc[good.index[good == best]].iloc[-1]
        more = f" (+{len(good) - 1} more)" if len(good) > 1 else ""
        out.append((best, f"RNS {last['published']:%Y-%m-%d}: {last['headline']}{more}"))
    bad = new.loc[tiers.index[tiers >= NEGATIVE_TIER]]
    if not bad.empty:
        last = bad.iloc[-1]
        out.append((NEGATIVE_TIER, f"RNS {last['published']:%Y-%m-%d}: {last['headline']}"))
    return out


def event_parts(ann: pd.DataFrame, now: pd.Timestamp, horizon_weeks: int) -> list[Part]:
    """``"event expected ~<date> (last: <last cycle's headline>)"`` for the soonest scheduled
    event within ``horizon_weeks`` of ``now`` (``"due now"`` when overdue), or ``[]``: tier 2
    when it is :data:`SCHEDULED_WEEKS` out or overdue, else 3."""
    if ann.empty:
        return []
    ev = expected_events(ann, now, horizon_weeks=horizon_weeks)
    if ev.empty:
        return []
    nxt = ev.sort_values("likely").iloc[0]
    lo, hi = SCHEDULED_WEEKS
    due = nxt["likely"] <= now
    tier = 2 if due or lo <= (nxt["likely"] - now).days / 7 <= hi else 3
    when = "due now" if due else f"expected ~{nxt['likely']:%Y-%m-%d}"
    return [(tier, f"event {when} (last: {nxt['headline']})")]


def named(headlines: pd.DataFrame, name: str) -> pd.DataFrame:
    """The headlines that name the company: its short legal name as whole words, any case,
    without a leading "The" or a trailing "Group" / "Holdings" ("Keystone Law" for KEYSTONE LAW
    GROUP). Google matches the quoted name anywhere in an article, so a headline about something
    else ("Fran", "CNC machining") would otherwise count as the company's news."""
    words = name.split()
    if words and words[0].upper() == "THE":
        words = words[1:]
    while len(words) > 1 and words[-1].upper() in {"GROUP", "HOLDINGS"}:
        words = words[:-1]
    if headlines.empty or not words:
        return headlines.iloc[:0]
    pat = re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, words)) + r"(?!\w)", re.I)
    return headlines[headlines["headline"].map(lambda h: bool(pat.search(str(h))))]


def news_parts(headlines: pd.DataFrame, since: pd.Timestamp) -> list[Part]:
    """``"n headlines, latest <date>: <headline>"`` (tier 3: discovery, unverified) for the
    headlines since ``since``, or ``[]``."""
    if headlines.empty:
        return []
    new = headlines[headlines["published"] >= since].sort_values("published")
    if new.empty:
        return []
    last = new.iloc[-1]
    n = f"{len(new)} headlines" if len(new) > 1 else "1 headline"
    return [(3, f"{n}, latest {last['published']:%Y-%m-%d}: {last['headline']}")]


def _utc(ts: pd.Timestamp | str) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
