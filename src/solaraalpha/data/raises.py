"""Equity raises from NSM filing text: the fields research 0013 splits on (#285).

Research only. Rules v1, as pre-registered in research 0013, applied to the plain text of a
raise's filings (``announcements.fetch_text``), joined in publication order:

- :func:`issue_price`: the first "N pence / Np / £N per (new / ordinary / placing / ...) share";
- :func:`use_class`: ``working_capital`` when the sentences about the proceeds name working
  capital, going concern, liquidity, "fund operations", "repay" or "refinanc" and none of
  acquisition, expansion, growth, capacity, "accelerate"; else ``other``;
- :func:`directors_in`: the text says a director or PDMR subscribed or took part.
"""

from __future__ import annotations

import re

NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
SHARE = (
    r"(?:\s+in\s+cash)?\s+(?:per|for\s+each|each)\s+(?:new\s+|ordinary\s+|placing\s+|"
    r"subscription\s+|retail\s+(?:offer\s+)?|offer\s+|open\s+offer\s+|issue\s+)*"
    r"(?:ordinary\s+)?share"
)
PENCE = re.compile(NUM + r"\s*(?:pence|p)\b" + SHARE, re.I)
POUNDS = re.compile(r"£\s?" + NUM + SHARE, re.I)
PROCEEDS = re.compile(r"[^.]*\b(?:proceeds|funds raised|net proceeds|use of)\b[^.]*\.", re.I)
WORKING = re.compile(
    r"working capital|going concern|liquidity|fund (?:its |the company's )?operations|\brepay"
    r"|refinanc",
    re.I,
)
GROWTH = re.compile(r"acquisition|expansion|growth|capacity|accelerat", re.I)
DIRECTORS = re.compile(
    r"(?:director|pdmr|chief executive|chairman|board)s?[^.]{0,120}"
    r"\b(?:subscrib|participat|took part|taking part)"
    r"|(?:subscri|participat)[^.]{0,60}\bby (?:certain )?(?:of the )?(?:director|board|pdmr)",
    re.I,
)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def issue_price(text: str) -> float | None:
    """The issue price in pence: the first match of a pence or pounds price per share."""
    p, q = PENCE.search(text), POUNDS.search(text)
    if p and (not q or p.start() <= q.start()):
        return _num(p.group(1))
    if q:
        return _num(q.group(1)) * 100
    return None


def use_class(text: str) -> str:
    """``working_capital`` or ``other`` from the sentences about the proceeds."""
    about = " ".join(PROCEEDS.findall(text))
    if WORKING.search(about) and not GROWTH.search(about):
        return "working_capital"
    return "other"


def directors_in(text: str) -> bool:
    """The text says a director or PDMR subscribed or took part."""
    return bool(DIRECTORS.search(text))
