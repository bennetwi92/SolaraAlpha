"""AIM leavers from the FCA NSM: who left AIM, when, and how it ended (issue #291).

Research only (epic #283): it bounds the survivorship bias of the research universe, whose
companies are all still trading. The spine is the London Stock Exchange's own AIM notices, which
the NSM carries under the filer "AIM":

- ``Cancellation - <company>`` (type ``AMO``): "NOTICE OF CANCELLATION OF ADMISSION TO TRADING ON
  AIM", with the company, its LEI and the cancelled lines (ISINs). ``AIM Rule 1`` in the notice
  means the nominated adviser went and was not replaced;
- ``Readmission - <company>``: a cancellation "following" something. A transfer to the Main Market
  or the Specialist Fund Segment is a leaver; a reverse takeover, a redomicile or a new holding
  company (scheme of arrangement) stays on AIM and is not.

How each cancellation ended comes from the company's own filings before it (:func:`classify`):
takeover (with the offer price, :func:`offer_price`), administration (insolvent), suspension (the
nomad went, or a long suspension with no vote), cancellation by choice (a shareholder vote, a
solvent wind-down), else other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from solaraalpha.data.announcements import (
    NSM_DOC_BASE,
    _iso,
    _norm,
    fetch_announcements,
    fetch_text,
    parse_nsm,
)
from solaraalpha.data.announcements import _search as nsm_search
from solaraalpha.data.http import patient

LEI = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
ISIN = re.compile(r"\(([A-Z]{2}[A-Z0-9]{9}[0-9])\)")
HOWS = ("takeover", "main_market", "cancellation", "administration", "suspension", "other")
# a cancellation that is not a leaver: the company came straight back to AIM (redomicile, new
# holding company)
CONTINUING = "continuing"
LEAVER_MOVE = re.compile(r"transfer (?:from AIM )?to (?:the )?(main|specialist fund)", re.I)
# company headlines, strongest first (see classify)
TAKEOVER = re.compile(
    r"scheme (?:of arrangement )?(?:becomes |became |is )?(?:effective|sanction)|court sanction"
    r"|sanction of (?:the )?scheme|rule 2\.7|recommended (?:cash |final |all[- ]share |share )?"
    r"(?:offer|acquisition|merger|takeover)|firm offer|offer (?:declared |now |becomes )?"
    r"(?:wholly )?unconditional|compulsory acquisition|(?:cash )?offer for\b|completion of "
    r"(?:the )?(?:acquisition|takeover|merger) (?:of|by)",
    re.I,
)
OFFER_PERIOD = re.compile(
    r"scheme document|offer document|offer update|update on (?:the )?offer|form 8|rule 2\.[49]"
    r"|possible offer|statement re(?:garding)? (?:possible )?offer|offer from",
    re.I,
)
INSOLVENT = re.compile(
    r"administrat(?:ion|ors?)\b|receiver|insolven|creditors'? voluntary|winding[- ]up (?:order|"
    r"petition)|compulsory liquidation|provisional liquidat|notice of intention to appoint",
    re.I,
)
CHOICE = re.compile(
    r"cancellation(?! of (?:treasury|deferred|the deferred|shares held|options|warrants))"
    r"|delist|de-list|members'? voluntary liquidation|\bmvl\b|last day of (?:dealings|trading)"
    r"|matched bargain|asset ?match|(?:propose|proposed) to cancel|winding[- ]down|wind[- ]down",
    re.I,
)
SUSPENDED = re.compile(r"suspension|suspended", re.I)
READMITTED = re.compile(r"(?<!of )admission to (?:trading on )?aim|first day of dealings", re.I)
NOMAD_GONE = re.compile(
    r"(?:resignation|resigns?|ceased?) (?:of |as )?(?:the )?nom(?:inated )?ad", re.I
)
NOMAD_GONE2 = re.compile(r"nom(?:inated )?ad(?:viser)?\b.{0,20}(?:resign|cease)", re.I)
NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
# "<price> (in cash) for/per (each) [<word>] share": <word> one of SHARE_WORDS or the company's
# first word, so "11.6 pence per finnCap Share" (the bidder's price) is not read as the offer
SHARE_WORDS = ("scheme", "ordinary", "existing", "offer", "target")


@dataclass(frozen=True)
class Notice:
    published: pd.Timestamp
    company: str
    lei: str
    isins: tuple[str, ...]
    rule: str  # "1": the nomad went; "41", or "" when the notice names none
    reason: str  # Readmission notices: what the cancellation followed ("" for Cancellation)
    disclosure_id: str


def parse_notice(text: str, published: pd.Timestamp, disclosure_id: str) -> Notice | None:
    """One LSE cancellation / readmission notice (plain text) -> :class:`Notice`; ``None`` when no
    LEI line is found."""
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    i = next((k for k, s in enumerate(lines) if LEI.match(s)), None)
    if i is None or i == 0:
        return None
    rule = re.search(r"AIM Rule (\d+)\b", text)
    why = re.search(r"Following an? ([^,]+?) the following securities", text, re.I)
    return Notice(
        published=published,
        company=lines[i - 1],
        lei=lines[i],
        isins=tuple(dict.fromkeys(ISIN.findall(text))),
        rule=rule.group(1) if rule else "",
        reason=why.group(1).strip() if why else "",
        disclosure_id=disclosure_id,
    )


def is_leaver_move(reason: str) -> bool:
    """A readmission notice's reason that takes the company off AIM (Main Market, SFS)."""
    return bool(LEAVER_MOVE.search(reason)) or reason.strip().lower().endswith("to main")


def classify(notice: Notice, filings: pd.DataFrame) -> tuple[str, str]:
    """``(how, source disclosure id)`` for a cancellation from the company's ``filings``
    (``announcements.COLUMNS``) published in the year before the notice.

    Order: back on AIM within 10 days (an admission headline) = ``continuing``, not a leaver; a
    takeover (a scheme / offer headline, else offer-period filings: Form 8, scheme document,
    possible offer); administration (an insolvency headline); the nomad went (``AIM Rule 1`` in
    the notice, or a nomad-resignation headline without a cancellation proposal) or a suspension
    headline without one = ``suspension``; a cancellation proposal, vote or wind-down =
    ``cancellation``; else ``other``. A move to the Main Market comes from the readmission notice,
    not from here."""
    t = notice.published
    after = filings[
        (filings["published"] > t) & (filings["published"] <= t + pd.Timedelta(days=10))
    ]
    if after["headline"].map(lambda h: bool(READMITTED.search(h))).any():
        return CONTINUING, str(after["id"].iloc[0])
    f = filings[filings["published"] <= t + pd.Timedelta(days=1)]
    f = f[f["published"] >= t - pd.Timedelta(days=365)].sort_values("published")
    heads = list(zip(f["headline"], f["id"], strict=True))

    def last(pattern: re.Pattern[str]) -> str | None:
        return next((i for h, i in reversed(heads) if pattern.search(h)), None)

    if src := last(TAKEOVER) or last(OFFER_PERIOD):
        return "takeover", src
    if src := last(INSOLVENT):
        return "administration", src
    choice = last(CHOICE)
    nomad = last(NOMAD_GONE) or last(NOMAD_GONE2)
    if notice.rule == "1":
        return "suspension", nomad or notice.disclosure_id
    if (nomad or last(SUSPENDED)) and not choice:
        return "suspension", nomad or last(SUSPENDED) or ""
    if choice:
        return "cancellation", choice
    return "other", ""


def offer_price(text: str, company: str = "") -> float | None:
    """The cash offer per share in pence named in an offer announcement's ``text`` (the first
    "X pence per share" / "£X per share", the share being the target's), else ``None``."""
    words = [*SHARE_WORDS, *company.lower().split()[:1]]
    share = r"(?:each\s+)?(?:(?:" + "|".join(map(re.escape, words)) + r")\s+)?(?:share|ordinary)"
    tail = r"\s*(?:in cash\s*)?(?:for|per)\s+" + share
    if m := re.search(NUM + r"\s*(?:pence|p)\b" + tail, text, re.I):
        return float(m.group(1).replace(",", ""))
    if m := re.search(r"£\s?" + NUM + tail, text, re.I):
        return float(m.group(1).replace(",", "")) * 100
    return None


def fetch_aim_notices(start: str, end: str) -> pd.DataFrame:
    """The LSE's AIM notices (filer "AIM", types ``AMO`` and ``EXC``) published from ``start`` to
    ``end`` (ISO dates): ``published``, ``headline``, ``id``, ``url``. One query per type-year
    (the NSM index caps from + size at 10,000)."""
    rows = []
    for code in ("AMO", "EXC"):
        for year in range(int(start[:4]), int(end[:4]) + 1):
            lo = max(start, f"{year}-01-01")
            hi = min(end, f"{year}-12-31")
            offset = 0
            while True:
                hits = patient(_search, code, lo, hi, offset)
                rows += [h["_source"] for h in hits]
                if len(hits) < 1000:
                    break
                offset += 1000
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["published", "headline", "id", "url"])
    df = df[df["company"] == "AIM"].drop_duplicates("disclosure_id")
    return pd.DataFrame(
        {
            "published": pd.to_datetime(df["publication_date"], utc=True, format="ISO8601"),
            "headline": df["headline"].str.strip(),
            "id": df["disclosure_id"],
            "url": NSM_DOC_BASE + df["download_link"],
        }
    ).sort_values("published", ignore_index=True)


def _search(code: str, lo: str, hi: str, offset: int) -> list[dict]:
    body = {
        "from": offset,
        "size": 1000,
        "sort": "publication_date",
        "sortorder": "asc",
        "criteriaObj": {
            "criteria": [
                {"name": "type_code", "value": code},
                {"name": "latest_flag", "value": "Y"},
            ],
            "dateCriteria": [
                {
                    "name": "publication_date",
                    "value": {"from": f"{lo}T00:00:00Z", "to": f"{hi}T23:59:59Z"},
                }
            ],
        },
    }
    return list(nsm_search(body)["hits"]["hits"])


def leaver_notices(notices: pd.DataFrame) -> list[Notice]:
    """The leavers among ``notices`` (:func:`fetch_aim_notices`): every cancellation, and the
    readmissions that move a company off AIM. Reads each notice's text."""
    out = []
    for head, url, published, did in zip(
        notices["headline"], notices["url"], notices["published"], notices["id"], strict=True
    ):
        kind = str(head).split("-", 1)[0].strip().lower()
        if not kind.startswith(("cancellat", "readmission")):
            continue
        n = parse_notice(patient(fetch_text, str(url)), pd.Timestamp(published), str(did))
        if n is None or (kind.startswith("readmission") and not is_leaver_move(n.reason)):
            continue
        out.append(n)
    return out


def filings_for(notice: Notice, days: int = 400) -> pd.DataFrame:
    """The company's NSM filings from ``days`` before the notice to 10 days after: by its LEI,
    else (none under it: older filings often carry no LEI) by its name as filed."""
    lo, hi = notice.published - pd.Timedelta(days=days), notice.published + pd.Timedelta(days=10)
    f = patient(fetch_announcements, notice.lei, lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d"))
    if f.empty:
        f = patient(_by_name, notice.company, lo, hi)
    return f


def _by_name(name: str, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    body = {
        "from": 0,
        "size": 1000,
        "sort": "publication_date",
        "sortorder": "asc",
        "criteriaObj": {
            "criteria": [
                {"name": "company_lei", "value": [name, "", "disclose_org", "related_org"]},
                {"name": "latest_flag", "value": "Y"},
            ],
            "dateCriteria": [
                {"name": "publication_date", "value": {"from": _iso(lo), "to": _iso(hi)}}
            ],
        },
    }
    f = parse_nsm(nsm_search(body))
    return f[f["company"].map(_norm) == _norm(name)].reset_index(drop=True)


PRICED = re.compile(r"offer|scheme|acquisition|merger|rule 2\.7|unconditional", re.I)
ALL_SHARE = re.compile(r"all[- ]share|share[- ]for[- ]share|share exchange", re.I)


def takeover_price(filings: pd.DataFrame, notice: Notice) -> tuple[float | None, str]:
    """``(pence per share, source id)`` for a takeover: the latest offer filing in the year
    before the notice (an increased offer supersedes the first) whose text names a cash price
    per target share; ``(None, "")`` when none of the last 12 does (mixed consideration) or the
    deal is all-share."""
    f = filings[filings["published"] <= notice.published + pd.Timedelta(days=1)]
    f = f[f["published"] >= notice.published - pd.Timedelta(days=365)]
    f = f[f["headline"].map(lambda h: bool(PRICED.search(h)) and not h.lower().startswith("form"))]
    if f["headline"].map(lambda h: bool(ALL_SHARE.search(h))).any():
        return None, ""
    for r in f.iloc[::-1].head(12).itertuples():
        if (p := offer_price(patient(fetch_text, r.url), notice.company)) is not None:
            return p, str(r.id)
    return None, ""
