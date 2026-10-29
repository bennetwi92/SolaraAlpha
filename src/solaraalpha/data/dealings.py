"""Director / PDMR dealings from MAR Article 19 notices (NSM type ``DSH``): research 0005, #284.

Research only. Fixed rules, applied to a notice's plain text (``announcements.fetch_text``).

**v1** (0005, amendment 2026-10-29): one block per PDMR section ("Details of the person
discharging managerial responsibilities"), the name (1a), the nature (4b), the price / volume
pairs (4c, else the 4d aggregate), the date (4e); a buy by 0005's keywords on the nature. Failed
the pre-step (33/40 buy, 9/15 value).

**v2** (0005 amendment, rules v2): the same blocks, plus

- every "Nature of the transaction" in a block is its own dealing, with the price field after it
  (a block can hold a purchase and a sale);
- a notice whose opening text (before the first MAR block) names a dividend reinvestment, shares
  in lieu of fees, a subscription / placing / fundraise or a SIPP transfer is not a buy;
- a PDMR's value is their buys minus their sales in the notice; zero or less is not a buy (the
  same-day "bed and ISA / SIPP"), nor is a buy at a nil price;
- prices and volumes are told apart by their marks (£, p, pence, GBX, $): k marked prices and k
  unmarked numbers pair in order (column-laid tables); otherwise numbers pair in sequence;
  list marks ("1.", "(2)") and the space in "£0. 349" are removed first;
- a notice with no MAR block falls back to its narrative: "<name> (purchased | bought | acquired)
  N shares at (an average price of) P".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BLOCK = re.compile(r"details of (?:the )?(?:persons? discharging managerial|pdmr)", re.I)
# a template field starts a line: an optional section mark ("1 |", "1. |", "a) |", "a. |") and
# one of the template's labels
LABELS = (
    r"Details of|Name\b|Reason for|Position|Initial notification|LEI\b|Description of"
    r"|Identification code|Nature of|Prices?\s*\(s\)\s*and|Aggregated|- Aggregated|- Price|- Total"
    r"|Date of|Place of"
)
FIELD_END = re.compile(
    r"\n\s*(?:(?:[a-h]|\d{1,2}(?:\.\d)?)?\s*[.)]?\s*\|\s*)?(?:" + LABELS + ")", re.I
)
DATE = re.compile(
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    r"\s+\d{4}\b|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{1,2}-\d{1,2}\b",
    re.I,
)
NATURE = re.compile(r"Nature of (?:the )?transactions?", re.I)
PRICE_FIELD = re.compile(r"Prices?\s*\(s\)\s*and\s*volumes?\s*\(?s?\)?", re.I)
BUY = re.compile(r"purchase|acquisition|acquire|bought|\bbuy", re.I)
SELL = re.compile(r"\bsale\b|\bsell|\bsold|disposal|dispose", re.I)
NOT_BUY = re.compile(
    r"award|grant|option|exercise|vest|\bltip\b|\bsip\b|share incentive|partnership|matching"
    r"|dividend reinvestment|\bdrip\b|scrip|transfer|gift|\bsale\b|\bsell|\bsold|disposal"
    r"|subscription|subscribe|placing",
    re.I,
)
# v2: the notice's opening text names a non-market acquisition
INTRO_NOT_BUY = re.compile(
    r"reinvest|in lieu of|subscri|placing|fundrais|\bsipp\b|bed and|bed-and|scrip",
    re.I,
)
NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
MARKED = re.compile(
    r"(?:£|\$|a\$|c\$|€|usd|aud|cad|eur|gbp|gbx)\s*(" + NUM + r")"
    r"|(" + NUM + r")\s*(?:p\b|pence|gbx|gbp)",
    re.I,
)
NARRATIVE = re.compile(
    r"(?:purchased|bought|acquired)\s+(?:a total of\s+|an aggregate of\s+)?(" + NUM + r")\s+"
    r"(?:[\w-]+\s+){0,6}?shares?\b[^.]{0,120}?\bat\s+(?:an?\s+)?(?:average\s+)?(?:price\s+of\s+)?"
    r"((?:£|a\$|\$|€)?\s*" + NUM + r"\s*(?:pence|p\b)?)",
    re.I,
)
CURRENCY = {"a$": "AUD", "aud": "AUD", "c$": "CAD", "cad": "CAD", "$": "USD", "usd": "USD"}
CURRENCY |= {"€": "EUR", "eur": "EUR"}


@dataclass(frozen=True)
class Dealing:
    pdmr: str
    nature: str
    pairs: tuple[tuple[float, float], ...]  # (price, volume) as written
    unit: str  # "GBP" (pounds), "GBp" (pence), "" (unmarked) or another currency code
    date: str  # 4e as written


def _clean(text: str) -> str:
    text = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", text)  # "£0. 349"
    return re.sub(r"(?:(?<=\s)|(?<=\|)|^)\(?\d\)?[.)](?=\s)", " ", text)  # list marks "1." "(2)"


def _field(block: str, label: str | re.Pattern[str]) -> str:
    """The value after ``label |`` up to the next template field."""
    pat = label if isinstance(label, re.Pattern) else re.compile(label, re.I)
    m = re.search(pat.pattern + r"[^|\n]*(?:\||(?=\n))(.*)", block, re.I | re.S)
    if not m:
        return ""
    rest = m.group(1)
    end = FIELD_END.search(rest)
    return (rest[: end.start()] if end else rest).strip(" |\n")


def _unit(text: str) -> str:
    low = text.lower()
    if "£" in text or "gbp" in low:
        return "GBP"
    if re.search(r"\d\s*p\b|pence|gbx", low):
        return "GBp"
    for sym, code in CURRENCY.items():
        if sym in low:
            return code
    return ""


def _pairs(text: str) -> tuple[tuple[float, float], ...]:
    """Price / volume pairs from a 4c (or 4d) field, after dropping dates, header words and nil
    prices (0): k currency-marked prices and k other numbers pair in order; else in sequence."""
    text = DATE.sub(" ", re.sub(r"\(s\)", "", text))
    text = re.sub(r"\bnil\b", "0", text, flags=re.I)
    prices = [float((a or b).replace(",", "")) for a, b in MARKED.findall(text)]
    if prices:
        rest = MARKED.sub(" ", text)
        vols = [float(n.replace(",", "")) for n in re.findall(NUM, rest)]
        if len(vols) == len(prices):
            return tuple(zip(prices, vols, strict=True))
    nums = [float(n.replace(",", "")) for n in re.findall(NUM, text)]
    return tuple((nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2))


def parse_dsh(text: str) -> list[Dealing]:
    """Every dealing in a ``DSH`` notice: one per PDMR block and nature of transaction."""
    text = _clean(text)
    starts = [m.start() for m in BLOCK.finditer(text)]
    out = []
    for i, s in enumerate(starts):
        block = text[s : starts[i + 1] if i + 1 < len(starts) else len(text)]
        name = _field(block, r"(?:^|\n)\s*(?:[a-h][.)]\s*\|\s*)?Name(?: of PDMR)?\s*:?\s*")
        date = _field(block, r"Date of (?:the )?transactions?")
        cuts = [m.start() for m in NATURE.finditer(block)] or [0]
        for j, c in enumerate(cuts):
            part = block[c : cuts[j + 1] if j + 1 < len(cuts) else len(block)]
            pv = _field(part, PRICE_FIELD)
            if not _pairs(pv):
                pv = _field(part, r"Aggregated information")
            out.append(
                Dealing(
                    pdmr=name,
                    nature=_field(part, NATURE),
                    pairs=_pairs(pv),
                    unit=_unit(pv),
                    date=date,
                )
            )
    return out


def narrative(text: str) -> list[Dealing]:
    """v2 fallback for a notice with no MAR block: purchases named in its prose."""
    out = []
    for m in NARRATIVE.finditer(_clean(text)):
        vol = float(m.group(1).replace(",", ""))
        price_text = m.group(2)
        nums = re.findall(NUM, price_text)
        if nums:
            out.append(
                Dealing(
                    "",
                    "Purchase (narrative)",
                    ((float(nums[0].replace(",", "")), vol),),
                    _unit(price_text) or ("AUD" if "a$" in price_text.lower() else ""),
                    "",
                )
            )
    return out


def is_open_market_buy(nature: str) -> bool:
    """0005's rule: the nature names a purchase / acquisition / buy and none of the exclusions
    (awards, options, SIP, transfers, sales, subscriptions, placings, ...)."""
    return bool(BUY.search(nature)) and not NOT_BUY.search(nature)


def value_gbp(d: Dealing, close_pence: float | None, fx: dict[str, float] | None = None) -> float:
    """Σ price x volume in pounds: pounds as is, pence ÷ 100, an unmarked price as pence or
    pounds whichever is nearer ``close_pence`` (the stock's close that day, pence; pence when
    unknown), another currency at ``fx[code]`` (pounds per unit; NaN when missing)."""
    gross = sum(p * v for p, v in d.pairs)
    if d.unit == "GBP":
        return gross
    if d.unit == "GBp":
        return gross / 100
    if d.unit == "":
        if close_pence and d.pairs:
            p = d.pairs[0][0]
            return gross if abs(p * 100 - close_pence) < abs(p - close_pence) else gross / 100
        return gross / 100
    rate = (fx or {}).get(d.unit)
    return gross * rate if rate else float("nan")


def notice_buys(
    text: str, close_pence: float | None, fx: dict[str, float] | None = None
) -> dict[str, float]:
    """Rules v2: PDMR -> net open-market buy value (GBP) in one notice; empty when none."""
    first = BLOCK.search(text)
    if INTRO_NOT_BUY.search(text[: first.start()] if first else text[:1500]):
        return {}
    deals = parse_dsh(text) or narrative(text)
    net: dict[str, float] = {}
    for d in deals:
        v = value_gbp(d, close_pence, fx)
        if v != v:  # NaN: an unconverted currency
            continue
        if is_open_market_buy(d.nature) and any(p > 0 for p, _ in d.pairs):
            net[d.pdmr] = net.get(d.pdmr, 0.0) + v
        elif SELL.search(d.nature):
            net[d.pdmr] = net.get(d.pdmr, 0.0) - v
    return {k: v for k, v in net.items() if v > 0}
