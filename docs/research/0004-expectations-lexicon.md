# Research 0004: Expectations wording in NSM filings: lexicon accuracy (issue #223, #214 R1)

*Pre-registered 2026-10-20 20:02 UTC, before any sample text was read. Follows ADR 0029. The
drift test is pre-registered here but **not run**; it runs only if the lexicon passes its
accuracy bar.*

## Question

UK companies say in fixed phrases how trading compares with market expectations ("materially
ahead", "in line", "below"). On AIM, where consensus is thin and paywalled, that wording is the
estimate revision (#214 F1). Before it routes anything (catalyst Tier 1, a screen field), the PM
asked for its precision on a hand-labelled sample (#214, PM review point 6): "in line with
*upgraded* expectations", "ahead of last year" and boilerplate outlook sentences will misfire.

Step 1 (this note): how accurately does a fixed word list classify results and trading-update
texts? Step 2 (pre-registered below, not run): does the class predict `burst_10w`?

## Pre-registration, step 1: lexicon accuracy

- **Lexicon (frozen, v1):** `scripts/research/r1_lexicon.py`, sha256
  `2fc6297a8fc18a29acdfffc99aa9658928fb3e0f8517c7be34a4d72c170a0393` at freezing. In short:
  - A sentence is an *anchor* when it contains `expectation(s)`, `consensus` or `guidance`.
  - A direction phrase counts only if it ends within 60 characters before an anchor word in the
    same sentence. The closest phrase wins; ties go strong before weak, directional before
    neutral.
  - +2: {materially, significantly, substantially, comfortably, considerably, meaningfully,
    well, strongly} + {ahead of, above, exceed…, higher than}. −2: the same adverbs + {below,
    behind, short of, lower than}.
  - +1: ahead of, above, exceed…, upper / top / higher end of, outperform…, beat, higher than,
    upgrade(d), raise(d). −1: below, behind, short of, lower / bottom end of, fall / fell short,
    not meet, not be met, lower than, reduced, downgrade(d).
  - 0: in line, consistent with, on track, meet… / met, on course, achieve…, unchanged,
    reiterat…, confirm…, maintain….
  - Document class, two variants: `first` (the first anchored sentence in text order) and
    `extreme` (the largest |score|, ties to the first). No match = NONE.
- **Sample:** NSM filings of today's 104 watchlist issuers published 2022-12-16 to 2025-08-08
  (in-sample window; no holdout-period text). Stratified, seed 223, at most one filing per
  ticker per stratum: 25 trading statements (`TST`), 15 final or preliminary results (`FR`,
  `PRE`), 10 half-year reports (`IR`). 50 texts, fetched with `announcements.fetch_text`.
- **Hand labels (rubric fixed now):** the company's own characterisation of performance against
  market (or board / management) expectations or guidance, as a reader of the headline and
  summary would take the news: +2 materially ahead / upgrade, +1 ahead, 0 in line or
  reiterated, −1 below / lower end, −2 materially below / profit warning; NONE when the text
  makes no such statement. When a filing speaks of both the year just ended and the current
  year, the current-year (forward) statement decides. "In line with expectations that were
  raised in an earlier announcement" is 0; an upgrade made in this filing is +1 or +2.
- **Labelling procedure:** labels are written for all 50 texts and saved before the lexicon is
  run on any of them. The labeller is the senior-quant agent (Claude), reading the first part
  of each text plus every sentence that mentions expectations, consensus, guidance, forecasts,
  outlook, ahead, in line or below. This is a measurement of the word list, not a backtest, so
  a model reading the text is allowed here; it never feeds a return test (ADR 0029). The owner
  or PM should spot-check 10 labels.
- **Measures:** 4-class accuracy (POS = +1/+2, NEU = 0, NEG = −1/−2, NONE); exact 6-class
  accuracy; POS precision and recall; NEG precision; each with a 90% Wilson interval; the
  confusion matrix; every miss listed.
- **Bar ("adequate"):** POS precision ≥ 0.80 **and** 4-class accuracy ≥ 0.75 (point estimates),
  on the better of the two variants, which is then the only one carried to step 2. Two variants
  are tried; both are reported.
- **Size caveat stated in advance:** with ~10–15 predicted positives, a 90% Wilson interval on
  precision is about ±0.15–0.20 wide. 50 labels can reject a bad lexicon, not certify a good one.

## Pre-registration, step 2: expectations drift (not run)

- **Events:** every `TST`, `FR`, `PRE`, `IR` and `AGM` filing of the watchlist issuers, NSM only,
  classified by the frozen lexicon (the variant chosen in step 1). No LLM, no headlines.
- **Signal week:** the Friday on or after publication (a filing after Friday's close belongs to
  the next week). Entry and target as ADR 0029: `burst_10w` from the next session's open, 3
  daily ATR (2 and 4 alongside); secondary `ret_8w`.
- **Signal:** class +2 and class ≥ +1 (2 variants); negative classes as a mirror check, not a
  pass/fail variant.
- **Null ("results week", #214):** every stock-week with any of those filings, whatever the
  class; the signal must beat it, and a vol + ADV logistic null, per ADR 0029.
- **Statistic:** the difference in `burst_10w` rate (and mean `ret_8w`) between positive-class
  events and the results-week null, vol-adjusted; 90% moving-block bootstrap over weeks (16-week
  blocks). Bonferroni for 2 variants (95% per variant).
- **Bar:** detection tier as ADR 0029 (interval excluding zero after adjustment; same sign in at
  least 5 of 6 in-sample half-years). Economics tier waits for the simulator.
- **Power (approximate, to be replaced by the real event count):** with roughly 3–4 such filings
  per issuer a year, about 100 issuers and 2.6 in-sample years, there are about 800–1,000
  events. If 15% are positive (~130), the SE of a burst-rate difference against a ~20% base is
  about √(0.2 × 0.8 / 130) ≈ 3.5 pp before clustering; with a design effect of about 2, about
  5 pp. The MDE at 80% power, Bonferroni 2 variants, is then about (2.24 + 0.84) × 5 ≈ **15 pp**
  on the burst rate. Only a large effect is detectable.

## Data and method

- **Filings:** `fetch_announcements` for every watchlist issuer with an LEI. 94 of 104 resolved;
  ECO, GRP, MKA, PTAL, SEE, THX and UPR have no LEI on GLEIF (non-UK incorporated), and GHH and
  YU are not found on the LSE endpoint.
- **Sample:** drawn as pre-registered: 25 `TST`, 15 `FR` (no `PRE` was drawn), 10 `IR`; 50
  texts, 2.07 MB, from 42 issuers, published 2023-01-19 to 2025-07-30.
- **Order of work:** labels were written for all 50 texts and saved (`0004-labels.csv`, sha256
  `23c3a58d2296a035861afd9eaa6983fb3810f62eaa82dc04c24b476839c7c967`) at 20:09 UTC, before the lexicon was run on any of them. The lexicon file
  was unchanged from its frozen hash. Ambiguous cases carry a note in the CSV.
- **Hand-label distribution:** POS 8 (+2: 2, +1: 6), NEU 27, NEG 2, NONE 13. Positives are
  rarer than the 10–15 guessed in advance, so precision rests on very few cases.

## Findings

### 1. The lexicon fails its pre-registered bar on precision

| measure | `first` (first anchored sentence) | `extreme` (largest abs score) | bar |
|---|---|---|---|
| 4-class accuracy (POS / NEU / NEG / NONE) | **42 / 50 = 0.84** [0.74, 0.91] | 39 / 50 = 0.78 [0.67, 0.86] | ≥ 0.75 |
| exact 6-class accuracy | 41 / 50 = 0.82 [0.71, 0.89] | 38 / 50 = 0.76 [0.65, 0.84] | — |
| **POS precision** | **7 / 11 = 0.64** [0.39, 0.82] | 7 / 15 = 0.47 [0.28, 0.67] | **≥ 0.80** |
| POS recall | 7 / 8 = 0.88 [0.59, 0.97] | 7 / 8 = 0.88 [0.59, 0.97] | — |
| NEG precision | 1 / 1 | 1 / 1 | — (n too small) |

90% Wilson intervals in brackets.

**Result: not adequate.** `first` is the better variant: it passes on accuracy but fails on
precision. More than a third of its "ahead of expectations" calls are not positive news for the
company. Per the pre-registration, **the drift test was not run.**

Confusion matrix, variant `first` (rows: hand label, columns: lexicon):

| hand \ lexicon | NEG | NEU | NONE | POS |
|---|---|---|---|---|
| NEG | 1 | 1 | 0 | 0 |
| NEU | 0 | 24 | 1 | **2** |
| NONE | 0 | 1 | 10 | **2** |
| POS | 0 | 1 | 0 | 7 |

### 2. Every false positive has the same cause: "ahead" about the wrong subject or period

| filing | sentence the lexicon used | why it is not group news |
|---|---|---|
| KGH.L FR 2024-07 | acquisitions "both are trading ahead of expectations" | a subsidiary; the group was "in line" |
| AOM.L FR 2023-07 | EBITDA "ahead of management expectations" (year just ended) | the current year is "in line with Board expectations" |
| HSP.L FR 2025-07 | "allowed us to raise our expectations … in our most recent trading update" | refers back to an earlier upgrade; nothing new |
| MSI.L FR 2023-06 | "sales and profitability exceeding expectations" | one segment, not the group |

The PM's warnings were right in kind (#214 review point 6). "Ahead of last year" did not misfire,
because of the 60-character anchor window. Sub-unit, past-period and backward-looking sentences
did.

### 3. The misses point to two mechanical bugs and one design gap

- **Footnote markers break the anchor.** "ahead of current market expectations¹" is text
  `expectations1`, and `\bexpectations?\b` fails on it. BRCK.L's TST (+1) was read as 0 for
  this reason. This is a bug, not a judgement.
- **Missing neutral verbs:** "well-placed to deliver market expectations" (CER.L IR) is not
  matched, because `deliver` is not in the neutral list.
- **Design gap:** FEVR.L cut its FY23 revenue guidance with numbers ("we now expect to deliver
  FY23 revenue of between £380m to £390m"), with no direction word. A word list cannot see a
  guidance cut stated as a new range. Negatives were 2 of 50, so negative-class accuracy is
  unmeasured.

## Caveats

- **50 labels, one labeller.** The labeller is the agent, not a person. Ten labels should be
  spot-checked by the owner or PM. The ambiguous cases are noted in the CSV (`n` 1, 12, 14, 27,
  29, 30, 35, 36, 48).
- **The forward rule decides several labels** (AOM, TRB FR, FEVR). A rubric that scored the year
  just ended would turn two false positives into true positives. That is a choice about what
  the signal should mean, not a fix. For a 1–10 week trade, the forward statement is the news.
- **Few positives.** POS precision rests on 11 predictions. Its interval [0.39, 0.82] only just
  reaches the bar, so even a 0.80 point estimate would not certify the lexicon.
- **Strata.** No `PRE` filing was drawn, and AGM statements were not sampled. Both are in the
  step-2 event set.

## Recommendation

**To the PM and the owner: the expectations lexicon does not route anything yet.** Do not
build it into the catalyst Tier 1 or the screen (#214 F6). The agents keep reading the wording as
they do today.

Next steps, in order:

1. **Version 2, pre-registered as a new version.** v1 stays recorded as failed. v2 changes:
   - anchors tolerate footnote digits;
   - `deliver…` joins the neutral list;
   - backward references are dropped ("in our most recent / previous trading update",
     "as previously announced");
   - sentences whose subject is a segment, division, acquisition or "business" are dropped
     unless the sentence also names the group or the company;
   - a sentence in a "current trading" or "outlook" block, or naming the current financial
     year, outranks one about the year just ended.

   Owner time: none. Senior quant: half a day.
2. **A fresh sample** of 50 (seed 224, same strata plus 5 `PRE` and 5 `AGM`). v2 must not be
   scored on the 50 texts used to design it. Same bar: POS precision ≥ 0.80 and 4-class accuracy
   ≥ 0.75.
3. **Only then the drift test** (step 2 above), on about **925 in-sample events** (368 `TST`,
   247 `FR` / `PRE`, 216 `IR`, 94 `AGM` from 93 issuers, 2.64 years: 3.7 per issuer a year).
   - At the observed 16% positive share, that is about 150 positive events.
   - The in-sample burst base rate is 38.5% (research 0002). With it, the MDE is about
     √(0.385 × 0.615 / 150) × √2 × 3.08 ≈ **17 pp**, a little above the 15 pp guessed in
     advance.
4. **Guidance cuts stated as numbers** need a second rule: the new range vs the last stated
   range, parsed from the filing pair. That is a separate item, not part of v2.

## Reproduction

```
uv run python scripts/research/r1_lexicon.py sample <dir of <ticker>.ann.parquet> OUT
uv run python scripts/research/r1_lexicon.py score docs/research/0004-labels.csv OUT
```

The filings frames come from `fetch_announcements(resolve_issuer(t).lei)` for each watchlist
ticker, saved as `<ticker>.ann.parquet` in a scratch directory. The sample is deterministic
given the same filings (seed 223). NSM texts can be re-fetched by `id` from the labels CSV. No
DB writes; nothing written under `data/`.
