# Research 0012: The AIM leavers ledger and the survivorship bias bound

*2026-10-29. A data note, not a signal test: no forward return was computed for it. Issue #291,
epic #283. It also fixes how every note on the wide universe (research 0011) reports the bias of
seeing only the companies still trading. Nothing in the pipeline changes.*

## The answer in one paragraph

**333 companies left AIM between January 2022 and 28 October 2026**, about 80 a year:
141 takeovers, 94 cancellations by choice, 34 administrations, 25 moves to the Main Market,
19 suspensions that ended in cancellation, and 20 other. The list comes from the London Stock
Exchange's own AIM notices on the NSM. It matches every outside count found: all 48 leavers in
six months with published totals, and exactly the 92 that UHY Hacker Young counted for the year
to 15 October 2024. On a hand-labelled random sample, the way each company left was classified
correctly 38 times out of 40 (95%, bar 90%). 235 leavers fall in the in-sample span, so the
survivors-only universe misses about one company in three that was on AIM at the start of it.
The missing names are mostly takeovers (good for a long) and failures (bad), and the note below
fixes how each test reports that.

## The source: the LSE's own notices

The NSM carries the Exchange's AIM market notices under the filer "AIM". Two kinds name a leaver:

- **"Cancellation - X"** (type `AMO`): "NOTICE OF CANCELLATION OF ADMISSION TO TRADING ON AIM".
  Each one gives the company, its **LEI** and the cancelled lines (ISINs). If it cites "AIM Rule
  1", the nominated adviser went and was not replaced.
- **"Readmission - X"**: a cancellation "following" an event, with the event named. A *Transfer
  to the Main Market* or *to the Specialist Fund Segment* is a leaver. A *Reverse Takeover*, a
  *Redomicile* or a new holding company by *Scheme of Arrangement* stays on AIM and is not.

From January 2022 to 28 October 2026 the NSM holds 310 cancellation notices and 51 readmission
notices (25 of them moves). None of the 1,212 daily "AIM Notice" documents mentions a cancellation
the others miss. The headline searches suggested in #291 ("Cancellation of admission to trading on
AIM" and the like) find only a fraction of these: that phrase gives 11 hits. Searching the
Exchange's own notices is the complete route. Three cancellations came straight back to AIM within
days (a redomicile and two false starts), so they are not counted.

**How each company ended** comes from its own filings in the year before the notice (by LEI, else
by name for older filings that carry no LEI; 8 leavers have none at all). The rules, in order, are
in `data.leavers.classify`:

| how | rule (company headlines in the year before) | count |
|---|---|---|
| `takeover` | a scheme / offer headline (scheme effective, court sanction, Rule 2.7, recommended offer, offer unconditional), else offer-period filings (Form 8, scheme document, possible offer) | 141 |
| `administration` | administration, receivership, insolvency, creditors' voluntary liquidation, winding-up | 34 |
| `suspension` | the notice cites AIM Rule 1 (no nomad), or a nomad resignation or suspension headline with no cancellation proposal | 19 |
| `cancellation` | a cancellation proposal or vote, a members' voluntary liquidation or wind-down, last day of dealings, matched-bargain facility | 94 |
| `main_market` | the readmission notice names a transfer to the Main Market or the SFS | 25 |
| `other` | none of these (no filings, or none that explains it) | 20 |

**Offer price:** for a takeover, the latest offer filing in the year whose text names a cash price
per target share ("X pence in cash for each Scheme Share"). An increased offer supersedes the
first. All-share deals and mixed cash-and-share deals get none. 96 of the 141 takeovers have a
price, and the 17 hand-checked takeovers all got the right price or correctly got none.

The ledger is `config/aim_leavers.csv`: LEI, name, ISINs, last filing date, cancellation date,
how, offer price (pence), the source filing, the LSE notice, the AIM rule cited, and the number
of filings read. It is built by `scripts/leavers.py --write`, by hand and never in CI (about 700
NSM reads).

| year | takeover | cancellation | administration | suspension | Main Market | other | all |
|---|---|---|---|---|---|---|---|
| 2022 | 24 | 14 | 2 | 4 | 5 | 3 | 52 |
| 2023 | 32 | 22 | 11 | 2 | 3 | 4 | 74 |
| 2024 | 36 | 22 | 13 | 5 | 2 | 5 | 83 |
| 2025 | 35 | 31 | 6 | 4 | 6 | 3 | 85 |
| 2026 (to 28 Oct) | 14 | 5 | 2 | 4 | 9 | 5 | 39 |

## Coverage against outside counts

The LSE's monthly AIM statistics are blocked from the cloud (research 0011). Free outside counts
that could be checked:

| source | period | their count | ledger |
|---|---|---|---|
| UK Investor Magazine, "Why companies left AIM in …" | Nov 2023 / Jan 2024 / Jun 2024 / Aug 2024 / Dec 2024 | 9 / 10 / 5 / 9 / 5 | 9 / 10 / 5 / 9 / 5 |
| Investor's Champion, "AIM says farewell…" | Apr 2024 | 10 | 10 (9 cancellations + Atalaya to the Main Market) |
| UHY Hacker Young (press, Oct 2024) | year to 15 Oct 2024 | 92 | 92 |

The January 2024 list is public in full, and all ten names match. Every other month counts as an
exact match. The Main Market moves are the one place a cancellation-only list falls short (it
showed April 2024 at 9), which is why the readmission notices are read too.

## Classification accuracy

40 leavers were drawn at random (seed 291) and labelled by reading each one's filings, before the
rules' answer was compared (labels: `docs/research/0012-labels.csv`). The rules were written after
reading the unclassified ("other") names of the whole ledger, and 3 of those are in the sample.

- **38 of 40 right (95%)**, so the 90% bar is met. The misses: Sensyne Health (offer-period filings,
  but it left by a shareholder vote; the rules said takeover) and Nexxen (left for a Nasdaq-only
  listing, and no headline names it; the rules said other).
- **Offer prices:** 17 of 17 takeovers right (14 cash prices, 3 all-share or mixed with no price).
  Kape's is the sterling equivalent of a dollar offer, as its filing states.

## How wide-universe notes report the survivorship bias

The research universe (research 0011) is every AIM company still trading on 2026-10-28. Yahoo has
no prices for leavers, so a leaver's stock-weeks cannot be tested. Every note on the wide
universe reports three things.

1. **The count.** How many leavers fall in the note's window, by `how`. For the ADR 0029
   in-sample span (2022-12-16 to 2025-10-17) that is 235: 101 takeovers, 72 cancellations,
   29 administrations, 11 suspensions, 11 Main Market moves, 11 other. A typical in-sample Friday
   has about 300 universe stocks past the £10k floor, and these 235 were on AIM at some point in
   the span, so the gap is large.
2. **For an event test whose signal comes from the NSM** (director buys, placings: the NSM still
   holds leavers' filings), count the signal events among leavers in the window. Then recompute the
   result twice with those events filled in at their known ends:
   - **takeover** with a cash price: `burst_10w` = 1 if the first offer announcement falls within
     the event's 50 sessions, else unknown;
   - **administration** or **suspension** with the suspension or appointment within the 50
     sessions: `burst_10w` = 0, `ret_8w` = −100%;
   - **everything else** (cancellation by choice, other, an end beyond the window): unknown.

   The **low bound** sets every unknown to 0, the **high bound** to 1, and the note gives the
   statistic and its interval under both, next to the survivors-only result. If both bounds
   keep the result's sign and the pass or fail, the bias cannot change the verdict; if not, the
   note says the result depends on the missing names.
3. **For a test whose signal needs prices** (52-week high, gains overhang, lottery stocks), the
   leavers' signals cannot be computed. The note reports the leaver share of stock-weeks in the
   window (the weeks each leaver was on AIM, against the panel's stock-weeks) and the result
   reweighted as if those weeks had the panel's base rate, then 0 (all failures) and then 1.
   That is a crude bound, and it says only how far the missing names could move the answer.

Two notes on the bound:

- **Main Market movers are not lost.** They still trade, so Yahoo has their prices. Their AIM
  years can be added to a panel by ticker (11 in the in-sample span). Until a note does that, it
  counts them as missing like the others.
- **The ledger is all of AIM**, including names Trading212 never offered and names under the
  £10k floor. Using it in full makes the bound conservative.

## Caveats

- The ledger reads the NSM's *current* search index, an undocumented endpoint (`docs/data-sources.md`).
- `other` (20) and `suspension` (19) are coarse. A "suspension" ending can still have paid
  holders something (an asset sale, a return of capital), so the bound treats these as failures
  only when the suspension falls inside the event's window.
- The offer price is the price the leaving holders received, not a traded price. A holder who
  bought before the bid and held got it, so it is the right end value for a long.

## Sources

- London Stock Exchange AIM notices, via the FCA National Storage Mechanism (filer "AIM").
- UK Investor Magazine, "Why companies left AIM in November 2023 / January 2024 / June 2024 /
  August 2024 / December 2024" (ukinvestormagazine.co.uk).
- Investor's Champion, "AIM says farewell to plenty of companies", 10 May 2024.
- UHY Hacker Young, "92 companies leave AIM in past year", October 2024 (uhy-uk.com).
