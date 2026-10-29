# Research 0013: Placings event study: pre-registration (issue #285)

*Pre-registered 2026-10-29. Follows ADR 0029 and research 0010 (edge E1). **Not run.** No return
was computed for it: the event counts below use filing metadata and the £10k floor only.*

## Question

After an AIM company raises equity (a placing, subscription, open offer or retail offer), does the
price recover, settle or keep falling over the next 10 weeks, and which raises behave differently?
The new shares are sold at a discount to a few funds, some of which sell quickly, onto a thin
market. Whoever supplies liquidity to forced or impatient sellers should be paid for it
(Grossman & Miller 1988; Coval & Stafford 2007). The opposite prior is just as strong: seasoned
issues underperform afterwards (Barnes & Walker 2006 for UK issues). The test is **two-sided**. A
positive drift would be a situation route (buy once the overhang clears); a negative one would be
an exclusion rule (#287). Both are useful to a long-only book.

## Design: the event needs no text parsing

Research 0005 failed on parsing free-text notices (#284). Here the main test uses only filing
metadata. Text is parsed only for the three splits, and a split runs only if its field passes the
accuracy check.

## Events (metadata only)

- **Universe:** `config/research_universe.toml` (554 AIM companies Trading212 offers), with
  filings by LEI. A stock-week counts only when that Friday's 20-session median traded value is at
  least £10k (`analysis.universe`). Panel: `build_panel.py PANEL research`.
- **Raise filings:** the issuer's NSM filings whose headline matches `financing.RAISE` (placing,
  fundraise, capital or equity raise, subscription, retail offer, PrimaryBid, RetailBook, open
  offer, rights issue, bookbuild), or whose NSM type is `ROI` ("Result of Equity Issue"). Filings
  matching `NOISE` are left out: option or warrant exercises, EBT, treasury, block listing,
  convertibles and loan notes, subscription agreements, a director's subscription on its own, an
  investment in another company.
- **One raise:** an issuer's raise filings within 14 days of each other (`financing.RAISE_GAP_DAYS`).
- **Event date:** the raise's first `ROI` filing (the result, when the price and take-up are
  final), else its first raise filing (a placing announced already priced). **Signal Friday:** the
  Friday on or after that publication. One event per stock per 8 weeks (the earliest kept).
- **Entries (2, fixed in advance):** **E0** buys at Monday's open after the signal Friday.
  **E4** buys 4 weeks later ("once the overhang clears"): the stock-week 4 Fridays after the
  signal Friday, if it is still past the floor.

## Pre-step: event accuracy (must pass first)

40 events sampled at random (seed 285) from the in-sample events past the floor are labelled by
hand from their filings. Each one is checked on two things: is it a primary equity raise by the
issuer for cash, and were the price and size public by the event date?

- **Bar:** both right on at least 90% of the 40 (36).
- **Fields for the splits,** checked on the true raises in the same 40, each with a 90% bar:
  - the issue price (within 1%);
  - the use-of-proceeds class (below);
  - whether directors took part.

  A split runs only if its field passes.
- **If the event check fails:** the rules are fixed once (version 2, recorded here) and checked on
  a fresh sample (seed 2852). If v2 fails too, the test does not run. The same single fix covers
  any field that fails.

## Splits (3, fixed in advance; parsed from the raise's own filings)

1. **Discount ≥ 20% vs < 20%.** Discount = 1 − issue price ÷ the close of the last session before
   the raise's first filing. The issue price is the first "N pence / £N per (new / ordinary /
   placing) share" in the raise's filings.
2. **Use of proceeds: working capital / going concern vs the rest.** "Working capital" means the
   raise's filings name working capital, going concern, liquidity, "fund operations", "repay" or
   "refinanc" in the sentences about the proceeds, and none of acquisition, expansion, growth,
   capacity, "accelerate". Everything else is "the rest".
3. **Directors in vs not.** The raise's filings say a director (or PDMR) subscribed or took part,
   or the issuer filed a `DSH` notice within 7 days after the event date.

Retail offers (yes/no) and size (% of shares in issue) are reported but not tested.

## Target, null and bar

- **Target:** `burst_10w` at 3 ATR (2 and 4 ATR alongside), from the entry's Monday open (ADR
  0029). Secondary: `ret_8w`. In-sample signal Fridays only (2022-12-16 to 2025-08-08); the panel's
  later weeks are dropped on load and never read. The holdout stays locked.
- **Null 1, vol + ADV:** a logistic on the weekly ranks of daily ATR % and log ADV, fitted on all
  universe stock-weeks past the floor. Residual = `burst_3` − the null-1 probability.
- **Statistic (the two entries):** mean residual over event weeks − mean residual over all other
  stock-weeks past the floor.
- **Statistic (the three splits, at E0):** mean residual in the first group − mean residual in
  the second.
- **Interval:** a moving-block bootstrap over weeks (16-week blocks, all stocks in a week
  together, 2,000 draws, seed 285). It is two-sided at 98% per test: 90% family-wise, Bonferroni
  for **5 tests** (2 entries + 3 splits).
- **Pass (detection tier), per test:**
  - the interval excludes zero;
  - the same sign in at least 5 of the 6 in-sample half-years (as in 0005);
  - the result survives dropping the 3 tickers with the largest summed residual on that side of
    zero (point estimate keeps its sign, interval still excludes zero).

  The economics tier waits for `analysis/simulate.py`.
- **Size split:** every result is also reported for aim100 / rest / micro. These are descriptive;
  no pass is claimed on them.
- **Survivorship:** the bias bound of research 0012, for an NSM-signal event test. Raises by
  leavers in `config/aim_leavers.csv` are found by the same rule from their filings and filled in
  at their known ends, low and high.

## Power (metadata only)

| measure | value |
|---|---|
| raises (events), in-sample signal Fridays | **661** in 257 issuers |
| with a Result of Equity Issue (`ROI`) filing | 32% |
| on a stock-week past the £10k floor | **285** |
| of which aim100 / rest / micro | 46 / 47 / 192 |
| by signal year | 2022: 2, 2023: 84, 2024: 122, 2025: 77 |

MDE at 80% power for the burst-rate difference (base rate 0.385 and design effect 2 kept from
0005, since the wide panel's rate is not looked at before the test; Bonferroni 5, z = 2.33):

| test | events | SE (pp) | MDE (pp) |
|---|---|---|---|
| an entry, all raises | 285 | 4.1 | **13** |
| an entry, if the floor and the 8-week rule leave 200 | 200 | 4.9 | **15** |
| a split, about 140 each side | 2 × 140 | 8.2 | **26** |

The entries can detect a large effect. The splits can only detect a very large one, so they are
there to describe, and a pass on them would need to be striking.

## What a pass or a fail would mean

- **E0 or E4 passes, positive:** a "post-placing" situation route goes to the catalyst work
  (#214) as a candidate, after the holdout sign check and the owner's sign-off. It never becomes a
  hard filter (ADR 0022).
- **Negative:** a candidate exclusion rule for #287 ("no new entries within N weeks after a
  raise"), with the same holdout check.
- **Fail:** the note says "not shown above the MDE". Raises stay as context in the financing card
  and research §7, as today.

## Caveats

- Many raises come with results, an acquisition or a trading update. The event is the whole
  package, not the raise alone.
- The event date is when the raise is final. A placing that leaked or was announced the day before
  has already moved, and E0 cannot capture that day.
- Survivors only: companies whose raise failed to save them and that later left AIM are in the
  bias bound, not the test.

## Pre-step result (2026-10-29, before any outcome)

40 events (seed 285) were labelled by hand from their filings before the rules' reading was
compared. Labels: `docs/research/0013-labels.csv`.

| check | result | bar | consequence |
|---|---|---|---|
| event right (a primary raise for cash; price and size public by the event date) | **36 of 40 (90%)** | 90% | **passes**: E0 and E4 run |
| issue price within 1% (34 raises priced in pence) | 27 of 34 (79%) | 90% | fails: the discount split does not run |
| use-of-proceeds class (36 raises) | 27 of 36 (75%) | 90% | fails: the use-of-proceeds split does not run |
| directors took part (36 raises) | 33 of 36 (92%) | 90% | **passes**: the directors split runs |

- **The 4 wrong events.** Two are raises by a portfolio company or investee, not by the issuer
  (Tern, OptiBiotix). One is a status update on a subscription that had not completed (EQTEC).
  One is shares issued to amortise a bond, not for cash (Faron).
- **7 of the 36 right events are late.** Their date is a later result (a retail-offer result, a
  general-meeting result) 5 to 41 days after the price was already public, because the first
  `ROI` filing in the raise came after the placing announcement. They pass the check as written.
  It means E0 enters after the first reaction for about 1 raise in 5. E4 is little affected.
- **No v2.** The pre-registration allowed one fix for a failed field. It is not used: the two
  failed splits had an MDE of about 26 pp, too weak to justify another round of rules and labels.
  The Bonferroni count stays at 5 (the conservative side), with 3 tests run: E0, E4 and the
  directors split.

## Results (2026-10-29): no test passes

Run with `r11_placings.py run` on the research panel. 34,104 stock-weeks past the £10k floor
(324 stocks, 139 in-sample Fridays). 572 raises remained after the one-per-8-weeks rule, and 247
of them fall on a tested stock-week (past the floor, with price history). Residual = `burst_3` −
null 1. Intervals are 98% (Bonferroni 5).

| test | events | estimate (pp) | 98% interval | half-years same sign | without the best 3 | pass |
|---|---|---|---|---|---|---|
| **E0**: buy the Monday after the raise | 247 | **−7.8** | −15.8 to +1.6 | 5 of 6 | −5.5 (−13.8 to +4.4) | no |
| **E4**: buy 4 weeks later | 247 | +2.2 | −5.4 to +6.8 | 4 of 6 | +0.3 (−8.5 to +5.4) | no |
| **Directors in vs not** (at E0) | 126 vs 121 | +5.1 | −10.3 to +14.5 | 3 of 6 | +1.2 (−16.1 to +11.4) | no |

Alongside (not tested):

- **2 and 4 ATR:** E0 is −5.1 and −8.9 pp, E4 +4.4 and +1.5 pp. Every interval includes zero.
- **Burst rates:** after E0, 48.2% of raises burst, against 55.9% that null 1 expects for stocks
  this volatile and liquid. After E4, 55.5% against 53.3%. Other stock-weeks: 41.9%.
- **`ret_8w`:** after E0 the median is −4.6% (others −2.0%), after E4 −4.0%. The means are
  positive (+2.7%, +5.5%), pulled up by a few large winners.
- **By size (descriptive):**

  | entry | aim100 | rest | micro |
  |---|---|---|---|
  | E0 (pp) | −6.1 (n 45) | −8.4 (38) | −8.8 (164; interval −16.4 to +0.8) |
  | E4 (pp) | −1.8 | +5.4 | +1.9 |

**Survivorship bound (research 0012).** 113 raises by companies that later left AIM (other
than to the Main Market) fall in the window: 38 by companies that later cancelled, 33 that went
into administration, 31 later taken over, 6 suspended and 5 other. That is a third as many again
as the 247 tested. Only 8 have a known end inside the 10 weeks (4 bursts from a takeover, 4
failures). Filled in at their known ends, with every unknown set to 0 and then to 1, the E0
estimate moves from **−21.8 pp to +7.4 pp**. This is an approximation: the leavers' null-1
probability is taken as the E0 average. The missing names cannot turn the fail into a pass, but
they could make the negative reading much stronger (`r11_placings.py bound`). Raises are where the survivor bias is
largest, because many raises are a last attempt to stay afloat.

### What it says

- **No edge was shown.** None of the three tests clears the bar, so the note's verdict is "not
  shown above the MDE" (13 pp for the entries, 26 pp for the split).
- **E0 leans the way the post-issue literature says, not the liquidity-supply story.** Buying
  right after a raise bursts less often than the stock's volatility predicts, in 5 of 6
  half-years. Dropping the best 3 names barely changes it, and the leavers can only make it
  worse. It does not clear the interval, so it is not a rule.
- **Waiting 4 weeks (E4) shows nothing either way.** There is no sign that the overhang clears
  into a burst.
- **Directors taking part tells nothing measurable.**

## Recommendation (to the PM and the owner)

- **No new situation route** from placings. The edge-E1 idea (paid for supplying liquidity after
  a raise) is not supported in-sample.
- **Raises stay as context and as a caution.** The financing card already shows the last raise.
  The research and PM agents should treat "raised in the last few weeks" as a reason for a
  stricter entry, not a reason to buy, which is how the E0 estimate leans. That is advice, not a
  rule.
- **The holdout stays locked.** No test passed, so ADR 0029 does not open it.
- If the owner wants the negative E0 tested as an exclusion, it has to be pre-registered as such
  in #287, with this note named as where the idea came from. A test of the same in-sample weeks
  cannot count as independent evidence; only the holdout or the live record can confirm it.
