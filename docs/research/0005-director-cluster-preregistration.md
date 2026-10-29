# Research 0005: Director-dealing clusters: pre-registration (issue #223, #214 R6)

*Pre-registered 2026-10-20. Follows ADR 0029. **Not run.** No return was computed for this
signal; the event counts below use filing metadata only.*

## Question

Do open-market share purchases by directors (PDMRs) of AIM companies, especially by two or more
of them within a month, predict a +15% burst over the next 10 weeks? UK evidence says director
buys carry information, most in small, thinly held companies (Fidrmuc, Goergen & Renneboog
2006). Those papers are priors, not evidence for this universe.

## Signal

- **Source:** NSM filings of type `DSH` ("Director/PDMR Shareholding", the MAR Article 19
  notice), today's 104 watchlist issuers, by LEI. NSM only; no headlines, no LLM reading
  (ADR 0029, #210 Q12).
- **Parsing (fixed rules):** each notice follows the MAR template. From the text take:
  - the PDMR's name (section 1a);
  - "Nature of the transaction";
  - "Price(s) and volume(s)" (or the aggregated price and volume);
  - the transaction date.
- **An open-market buy** is a notice whose nature field contains `purchase`, `acquisition` or
  `buy`, and none of: award, grant, option, exercise, vest, LTIP, SIP, share incentive,
  partnership, matching, dividend reinvestment, DRIP, scrip, transfer, gift, sale, sell,
  disposal, subscription, placing. (Subscriptions in a placing are excluded here: a different
  event, priced at the placing.)
- **Value:** Σ price × volume, converted to GBP (pence ÷ 100; other currencies at that day's
  Yahoo FX close).
- **Variants (2, counted for the multiple-testing adjustment):**
  - **A, cluster:** at least 2 distinct PDMRs with open-market buys whose notices are
    published within 30 calendar days.
  - **B, size:** one PDMR's buys in one notice worth more than £50,000.
- **Signal week:** the Friday on or after the publication of the notice that completes the
  cluster (A) or the large buy (B). The transaction date is never used as the signal date,
  because the market sees the notice, not the trade. One signal per stock per 8 weeks (later
  ones are dropped), so events do not overlap.

## Pre-step: parse accuracy (must pass first)

The parse is checked on 40 notices sampled at random (seed 223) from the in-sample window,
labelled by hand: buy / not a buy, PDMR, value.

- **Bar:** buy / not-buy correct on at least 90%, and value within 10% on at least 90% of the
  true buys.
- If it fails, the rules are fixed once (version 2, recorded here), re-checked on a fresh
  sample, and the drift test waits.

## Target, nulls and bar

- **Target:** `burst_10w` (ADR 0029): from the first session's open after the signal Friday,
  +15% before −3 daily ATR(14), within 50 sessions. 2 and 4 ATR alongside. Secondary: `ret_8w`.
- **Weeks:** in-sample only, signal Fridays 2022-12-16 to 2025-08-08. The holdout (from
  2025-10-24) stays locked until the owner signs off a pass.
- **Null 1, vol + ADV:** a logistic model on the weekly ranks of daily ATR % and log ADV. The
  event's outcome is measured net of it (ADR 0029).
- **Null 2, "after results":** directors may deal only outside MAR closed periods, so buys
  cluster in the weeks after results. The comparison set is every stock-week within 30 days
  after a results filing (`FR`, `PRE`, `IR`) with no buy signal. Without this null, we would
  only rediscover the results week (#214).
- **Statistic:** the difference in vol-adjusted `burst_10w` between signal weeks and null 2,
  with a 90% moving-block bootstrap (16-week blocks, all stocks in a week together), Bonferroni
  for 2 variants (a 95% interval per variant).
- **Pass (detection tier):** the adjusted interval excludes zero; the same sign in at least 5 of
  the 6 in-sample half-years; the result survives dropping the best 3 names. The economics tier
  (top 3 and the 5-slot replay at 2× costs, > +0.2R) waits for `analysis/simulate.py`.

## Power

Event counts from NSM metadata, in-sample window (no returns used):

| measure | value |
|---|---|
| issuers with NSM filings (LEI resolved) | 94 of 104 |
| in-sample window | 2022-12-16 to 2025-08-08 (2.64 years) |
| `DSH` notices | **1,268** (5.1 per issuer a year; median 11 per issuer, p90 27) |
| stock-weeks with a `DSH` notice | 1,063 |
| `DSH` notices within 30 days after a results filing | 394 (31%) |

Most `DSH` notices are not open-market buys: they are awards, option exercises, SIP purchases
and sales. The buy share is unknown until the parse runs. Assume, approximately, 25–40% buys
and that clusters or > £50k buys are a quarter to a third of those. That gives roughly
**80–170 signal events** per variant in-sample, before the one-per-8-weeks rule.

MDE at 80% power for the burst-rate difference vs null 2 (base rate p = 0.385 at 3 ATR,
the in-sample panel rate in research 0002; design effect about 2 for clustering in time;
Bonferroni 2 variants, z = 2.24):

| events | SE of the rate (pp) | MDE (pp) |
|---|---|---|
| 80 | √(0.385 × 0.615 / 80) × √2 ≈ 7.7 | (2.24 + 0.84) × 7.7 ≈ **24** |
| 120 | 6.3 | **19** |
| 170 | 5.3 | **16** |

A real but modest effect (5–10 pp on the burst rate) cannot be detected here. The test can
only find a large effect, or reject. The live record and the agents' reading carry the rest.

## What a pass or a fail would mean

- **Pass:** variant A or B goes to catalyst Tier 1 (#214 F6) as a candidate, after the holdout
  sign check and the owner's sign-off. It never becomes a hard filter (ADR 0022).
- **Fail:** the note says "not shown above the MDE". Director buys stay as context in research
  §7, read by the agent as today.

## Amendment 2026-10-29: the wide universe and the size split (#284, #288)

*Written before any outcome was computed. No forward return, burst or residual for a director-buy
week has been looked at. The text above stays as pre-registered; where this amendment differs, it
replaces it.*

**Why.** The owner moved research to the whole of AIM on 2026-10-28 (#288, research 0010
amendment). The original universe (today's 104 watchlist stocks) gives roughly 80–170 events per
variant and an MDE of 16–24 pp (the power table above). The wide universe has about three times as
many stocks past the floor each week (research 0011), and director buys should matter most in the
small, thinly held names the watchlist leaves out.

**What changes.**

1. **Universe.** Every stock in `config/research_universe.toml` (snapshot 2026-10-28, cleaned
   2026-10-29: 554 AIM companies Trading212 offers, the AIM 100 among them) with a Yahoo price
   history. A stock-week counts only when that Friday's 20-session median traded value is at
   least **£10k** (the owner's research floor, `analysis.universe`), not the screen's £25k. Filings
   by the LEI in that file. The panel is `scripts/research/build_panel.py OUT research`.
2. **Size split.** Every result is also reported by size group (`analysis.universe.size_group` on
   the signal Friday): `aim100` (today's watchlist, the control), `rest`, `micro` (under £50m).
   **The pass or fail is decided on the full universe only.** The size groups are descriptive
   (same statistic and interval, no pass claimed), so the multiple-testing adjustment stays at
   2 variants.
3. **Survivorship.** The universe holds only companies still trading. The note reports the bias
   bound of research 0012 for an NSM-signal event test: the variant-A and variant-B signal events
   among the leavers in `config/aim_leavers.csv` (their `DSH` notices are still on the NSM),
   filled in at their known ends, low and high.
4. **Pre-step sample.** The 40 notices (seed 223) are drawn from the `DSH` notices of the wide
   universe published from 2022-11-16 (30 days before the first signal Friday, so clusters can
   complete) to 2025-08-08.
5. **The nulls are fitted on the wide universe.** Null 1 (logistic on the weekly ranks of daily
   ATR % and log ADV) uses every stock-week past the £10k floor. Null 2 ("within 30 days after a
   results filing": `FR`, `PRE`, `IR`) uses the wide universe's filings.

**Made explicit (unchanged in intent, written down so nothing is chosen after the fact).**

- **Statistic.** Per stock-week, residual = `burst_3` − the null-1 probability. Statistic = mean
  residual over signal weeks − mean residual over null-2 weeks. Interval: a moving-block bootstrap
  over weeks (16-week blocks, all stocks in a week together, 2,000 draws, seed 284), two-sided
  95% per variant (90% family-wise, Bonferroni for 2).
- **Half-years.** H1 2023 (with the December 2022 Fridays), H2 2023, H1 2024, H2 2024, H1 2025,
  H2 2025 (to 8 August). Pass needs the statistic to have the same sign in at least 5 of the 6.
- **Dropping the best 3 names.** Remove every week of the 3 tickers with the largest summed
  residual over signal weeks, then recompute. The point estimate must keep its sign and the
  interval must still exclude zero.
- **Parse details (rules v1).** A notice is split into one block per PDMR (each "Details of the
  person discharging managerial responsibilities" section). In each block, the name is the
  section 1a field, the nature is the 4b field, and the price and volume pairs come from 4c (else
  the 4d aggregate). A price marked £ or GBP is in pounds; one marked p or pence is in pence. An
  unmarked price is read as pence or pounds, whichever is nearer the stock's close that day.
  Other currencies (marked USD, $, EUR, €, AUD, CAD) are converted at that day's Yahoo FX close.
  A block is an open-market buy by the keyword rules above. Its value is Σ price × volume over its
  pairs. The transaction date is the 4e field (recorded, never the signal date).
- **Targets.** As above: `burst_10w` at 3 ATR from Monday's open (2 and 4 ATR alongside), `ret_8w`
  secondary. In-sample signal Fridays only (2022-12-16 to 2025-08-08). The panel holds later weeks
  too; they are dropped on load and never read. The holdout stays locked.

**Power (metadata only, wide universe, in-sample window).**

| measure | value |
|---|---|
| issuers with NSM filings read | 540 of 554 |
| window | 2022-11-16 to 2025-08-08 (2.73 years; clusters may complete from 30 days before the first Friday) |
| `DSH` notices | **4,151** (3.3 per issuer a year; median 7 per issuer, p90 18), against 1,268 on the watchlist |
| stock-weeks with a `DSH` notice | 3,547 |
| `DSH` notices within 30 days after a results filing | 1,239 (30%) |

The same assumptions as above (25–40% buys, a quarter to a third of those completing a cluster or
over £50k) give roughly **260–550 signal events per variant** before the one-per-8-weeks rule,
some of them on stock-weeks under the floor. MDE at 80% power (base rate 0.385 and design effect 2
kept from the original, since the wide panel's burst rate is not looked at before the test;
Bonferroni 2, z = 2.24):

| events | SE of the rate (pp) | MDE (pp) |
|---|---|---|
| 260 | 4.3 | **13** |
| 400 | 3.4 | **11** |
| 550 | 2.9 | **9** |

So the wide universe roughly halves the MDE (from 16–24 pp to about 9–13 pp). It still cannot
see a 5 pp effect.

## Pre-step, rules v1: failed (2026-10-29)

The 40 notices (seed 223, wide universe) were labelled by hand from their text (15 open-market
buys, 25 not), then read by rules v1. Labels: `docs/research/0005-labels-v1.csv`.

| check | result | bar |
|---|---|---|
| buy / not-buy correct | **33 of 40 (82.5%)** | 90% |
| value within 10%, true buys | **9 of 15 (60%)** | 90% |

Where v1 went wrong:

- **Not buys read as buys (5).** In each, the nature field says "purchase" or "acquisition", but
  the notice's own text says it was a dividend reinvestment (2), shares in lieu of fees, a
  placing subscription, or a bed-and-SIPP (bought and sold the same day, no change in holding).
- **Buys missed (2).** Notices with no MAR table: one has the buy only in its narrative, the other
  is an ASX Appendix 3Y.
- **Values wrong (6).** A "volumes(s)" typo in the label, "£0. 349" with a space, numbered lists
  ("1. £1.31835 2. £1.31835"), price columns listed before volume columns, and a same-day sale
  and repurchase counted as new buying.

The drift test did not run.

## Rules v2 (2026-10-29, the one fix 0005 allows)

Written after the v1 failure and before the fresh sample was drawn (`data.dealings`):

1. Every "Nature of the transaction" in a PDMR block is its own dealing, with its own price field.
2. A notice whose opening text (before the first MAR block, or its first 1,500 characters if it
   has none) names a dividend reinvestment, shares in lieu of fees, a subscription, placing or
   fundraise, a SIPP transfer or "bed and", or a scrip issue is not a buy.
3. A PDMR's value is their buys minus their sales in the notice. Zero or less is not a buy, and
   nor is a buy at a nil price.
4. Prices and volumes are told apart by their marks (£, p, pence, GBX, $, A$, €): k marked
   prices with k unmarked numbers pair in order. Otherwise the numbers pair in sequence. List
   marks and the space in "£0. 349" are removed first. The label match allows "volumes(s)".
5. A notice with no MAR block falls back to its narrative: "(purchased | bought | acquired)
   N … shares … at (an average price of) P".

**Fresh sample:** 40 notices drawn with seed 2232 from the same population, leaving out the
first 40. The bar is unchanged. If v2 fails too, the drift test does not run (0005 allows one
fix only).

## Results (2026-10-29): the pre-step failed twice, so the drift test did not run

**Rules v2 on the fresh sample** (seed 2232, 40 notices, labelled by hand before the rules were
run; `docs/research/0005-labels-v2.csv`):

| check | v1 (seed 223) | v2 (fresh, seed 2232) | bar |
|---|---|---|---|
| buy / not-buy correct | 33 of 40 (82.5%) | **34 of 40 (85%)** | 90% |
| value within 10%, true buys | 9 of 15 (60%) | **14 of 18 (78%)** | 90% |

Two of the 40 fresh labels were judgement calls. A non-executive director buying 100,000 shares
with his own money from LTIP sellers on AIMX was labelled a buy. An EBT trustee buying shares for
the company's share plans was labelled not a buy. Read the other way round, buy / not-buy would be
36 of 40 (90%), but the value check would still fail (at most 15 of 18). The failure does not
depend on either call.

What v2 still gets wrong:

- **Layout variants it was not written for.** Section marks in brackets ("(a) |", "(c) |") break
  the field boundaries, so a purchase price is lost or netted against a sale (2 notices). A "Price
  and volume" label without "(s)" is missed (1).
- **Its opening-text rule is too broad.** A notice that mentions future "Subscription Shares"
  hides the market purchase it reports (1).
- **The nature field cannot say who is buying.** An off-market acquisition next to an option
  exercise, and an EBT trustee's purchase, both read as director buys (2).

**So the drift test was not run.** 0005 allows one fix, and the amendment of 2026-10-29 said a v2
failure stops the test. No forward return, burst or residual was computed for any director-buy
week. The holdout was not touched. The fault is in reading the notices, not in the signal: this
note says nothing about whether director buys predict bursts.

**The bias bound** (research 0012) was not needed, because there is no result to bound.

## Recommendation (to the PM and the owner)

Director buys stay where they are: as context the research agent reads in §7 (ownership), notice
by notice. That is how a person reads them, and it is the reading the agents already do.

Whether to try again is the owner's call, because any path changes what 0005 pre-registered:

1. **Rules v3 and a third fresh sample.** The remaining misses are mostly mechanical
   (bracketed marks, label variants, an over-broad opening-text rule), so v3 would probably pass
   the buy check. Two misses are not mechanical: who the buyer is (an EBT, an off-market seller)
   is not in the nature field. Cost: about a day. Risk: tuning rules on samples until one passes.
2. **Hand-label every notice that the rules call a buy** (1,582 of the 4,151 under v2), and keep
   rules v2 only to drop the clear non-buys (options, SIP, grants, sales). Fixed in advance, it
   would meet the bar by construction. Cost: about a week of reading.
3. **Leave E4 for now** and move to the placings study (#285). Placings are announced in
   standard, structured RNS headlines, so the parse risk is lower.

My recommendation is **3, then 1 if the owner wants E4 back**. The pre-registered MDE is 9–13 pp
on the burst rate, so even a clean parse could only find a large effect.
