"""Research 0004 (#223, #214 R1): the fixed expectations lexicon and its accuracy check.

uv run python scripts/research/r1_lexicon.py sample OUT_DIR      # draw the sample, fetch texts
uv run python scripts/research/r1_lexicon.py score LABELS.csv OUT_DIR  # lexicon vs hand labels

The lexicon below was frozen on 2026-10-20 before any sample text was read (pre-registration in
docs/research/0004). It reads only the company's own words in an NSM filing; no LLM, no
headlines. Research only: nothing in the pipeline imports it. Writes only under OUT_DIR.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import re
import sys
from pathlib import Path

import pandas as pd

# ------------------------------------------------------------------ the frozen lexicon (v1)
# A sentence is an *anchor* when it names expectations, consensus or guidance.
ANCHOR = re.compile(r"\b(expectations?|consensus|guidance)\b", re.I)
# Direction phrases (strong before weak, directional before neutral breaks ties). A
# phrase counts only when it ends within WINDOW characters before an anchor word in the same
# sentence ("trading ahead of the Board's expectations"), so "ahead of the AGM" or "in line
# with last year" do not count.
WINDOW = 60
_STRONG = r"(?:materially|significantly|substantially|comfortably|considerably|meaningfully|well|strongly)"
DIRECTION: tuple[tuple[int, re.Pattern[str]], ...] = (
    (-2, re.compile(_STRONG + r"\s+(?:below|behind|short\s+of|lower\s+than)", re.I)),
    (+2, re.compile(_STRONG + r"\s+(?:ahead\s+of|above|exceed\w*|higher\s+than)", re.I)),
    (
        -1,
        re.compile(
            r"\b(?:below|behind|short\s+of|lower\s+end\s+of|bottom\s+end\s+of|fall\s+short|"
            r"fell\s+short|not\s+meet|not\s+be\s+met|lower\s+than|reduced|downgraded?)\b",
            re.I,
        ),
    ),
    (
        +1,
        re.compile(
            r"\b(?:ahead\s+of|above|exceed\w*|upper\s+end\s+of|top\s+end\s+of|higher\s+end\s+of|"
            r"outperform\w*|beat|higher\s+than|upgraded?|raised?)\b",
            re.I,
        ),
    ),
    (
        0,
        re.compile(
            r"\b(?:in\s*line|consistent\s+with|on\s+track|meet\w*|met|on\s+course|achieve\w*|"
            r"unchanged|reiterat\w*|confirm\w*|maintain\w*)\b",
            re.I,
        ),
    ),
)
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def sentence_scores(text: str) -> list[tuple[int, str]]:
    """(score, sentence) for every anchored sentence with a direction phrase, in text order."""
    out = []
    for s in _SENT.split(text):
        anchors = [m.start() for m in ANCHOR.finditer(s)]
        if not anchors:
            continue
        # every phrase ending within WINDOW before an anchor; the closest to it wins (ties: the
        # DIRECTION order, so "well ahead of" beats "ahead of")
        cands = [
            (a - m.end(), rank, score)
            for rank, (score, pat) in enumerate(DIRECTION)
            for m in pat.finditer(s)
            for a in anchors
            if 0 <= a - m.end() <= WINDOW
        ]
        if cands:
            out.append((min(cands)[2], s.strip()))
    return out


def classify(text: str, variant: str = "first") -> int | None:
    """The document's class: +2 / +1 / 0 / -1 / -2 vs expectations, None when nothing matches.
    ``first`` = the first anchored sentence in text order (headline first); ``extreme`` = the
    largest |score| (ties: the first)."""
    hits = sentence_scores(text)
    if not hits:
        return None
    if variant == "first":
        return hits[0][0]
    return max(hits, key=lambda h: (abs(h[0]), -hits.index(h)))[0]


# ------------------------------------------------------------------ sample and score
LO, HI = pd.Timestamp("2022-12-16", tz="UTC"), pd.Timestamp("2025-08-08", tz="UTC")
SEED = 223


def collapse(v: int | None) -> str:
    return "NONE" if v is None else ("POS" if v > 0 else "NEG" if v < 0 else "NEU")


def wilson(k: int, n: int, z: float = 1.645) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def sample(panel: Path, out: Path) -> None:
    """Stratified random sample of in-sample filings (seed 223), at most one per ticker per
    stratum, texts fetched to OUT/texts/<id>.txt."""
    from solaraalpha.data.announcements import fetch_text

    frames = [
        pd.read_parquet(p).assign(ticker=p.name.split(".ann")[0])
        for p in panel.glob("*.ann.parquet")
    ]
    ann = pd.concat(frames, ignore_index=True)
    ann = ann[(ann["published"] >= LO) & (ann["published"] <= HI)]
    rng = random.Random(SEED)
    picks = []
    for stratum, n in (("TST", 25), ("RES", 15), ("IR", 10)):
        codes = {"TST": {"TST"}, "RES": {"FR", "PRE"}, "IR": {"IR"}}[stratum]
        pool = ann[ann["type_code"].isin(codes)].sort_values(["ticker", "published"])
        by_ticker = {t: g for t, g in pool.groupby("ticker")}
        tickers = sorted(by_ticker)
        rng.shuffle(tickers)
        for t in tickers[:n]:
            g = by_ticker[t]
            row = g.iloc[rng.randrange(len(g))]
            picks.append({"stratum": stratum, **row.to_dict()})
    (out / "texts").mkdir(parents=True, exist_ok=True)
    rows = []
    for p in picks:
        path = out / "texts" / f"{p['id']}.txt"
        if not path.exists():
            try:
                path.write_text(fetch_text(p["url"]))
            except Exception as e:
                print(f"{p['ticker']} {p['id']}: fetch failed ({e})", file=sys.stderr)
                continue
        rows.append(
            {
                k: p[k]
                for k in ("stratum", "ticker", "published", "type_code", "headline", "id", "url")
            }
        )
    pd.DataFrame(rows).to_csv(out / "sample.csv", index=False)
    print(f"{len(rows)} texts in {out}")


def score(labels: Path, out: Path) -> None:
    lab = list(csv.DictReader(labels.open()))
    for variant in ("first", "extreme"):
        rows = []
        for r in lab:
            text = (out / "texts" / f"{r['id']}.txt").read_text()
            hand = None if r["label"] == "NONE" else int(r["label"])
            rows.append((hand, classify(text, variant)))
        n = len(rows)
        exact = sum(h == m for h, m in rows)
        four = sum(collapse(h) == collapse(m) for h, m in rows)
        pred_pos = [(h, m) for h, m in rows if collapse(m) == "POS"]
        true_pos = [(h, m) for h, m in rows if collapse(h) == "POS"]
        pred_neg = [(h, m) for h, m in rows if collapse(m) == "NEG"]
        tp = sum(collapse(h) == "POS" for h, _ in pred_pos)
        tn = sum(collapse(h) == "NEG" for h, _ in pred_neg)
        print(f"\n== variant {variant}: n {n}")
        print(f"exact 6-class {exact}/{n} = {exact / n:.2f} {wilson(exact, n)}")
        print(f"4-class (POS/NEU/NEG/NONE) {four}/{n} = {four / n:.2f} {wilson(four, n)}")
        print(f"POS precision {tp}/{len(pred_pos)} {wilson(tp, len(pred_pos))}")
        print(f"POS recall {tp}/{len(true_pos)} {wilson(tp, len(true_pos))}")
        print(f"NEG precision {tn}/{len(pred_neg)} {wilson(tn, len(pred_neg))}")
        cm = pd.crosstab(
            pd.Series([collapse(h) for h, _ in rows], name="hand"),
            pd.Series([collapse(m) for _, m in rows], name="lexicon"),
        )
        print(cm)
        for r, (h, m) in zip(lab, rows, strict=True):
            if collapse(h) != collapse(m):
                print(f"  miss {r['ticker']} {r['id']} hand {h} lexicon {m}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("panel", type=Path, help="directory of <ticker>.ann.parquet filings")
    s.add_argument("out", type=Path)
    c = sub.add_parser("score")
    c.add_argument("labels", type=Path)
    c.add_argument("out", type=Path)
    a = p.parse_args(argv)
    if a.cmd == "sample":
        sample(a.panel, a.out)
    else:
        score(a.labels, a.out)


if __name__ == "__main__":
    main()
