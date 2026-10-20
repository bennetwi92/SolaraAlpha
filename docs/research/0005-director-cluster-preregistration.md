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
