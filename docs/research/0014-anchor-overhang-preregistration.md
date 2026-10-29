# Research 0014: The 52-week-high anchor and the capital-gains overhang: pre-registration (issue #286)

*Pre-registered 2026-10-29. Follows ADR 0029 and research 0010 (edges B1, B2). **Not run.** No
return was computed for it: the counts below use prices, volumes and filing dates only.*

## Question

Two behavioural reasons a chart pattern could work, rather than just a pattern:

- **Anchoring to the 52-week high** (George & Hwang 2004). Traders treat the high as a ceiling,
  under-react to good news near it, and the move continues once the high gives way.
- **Disposition effect / capital-gains overhang** (Shefrin & Statman 1985; Grinblatt & Han 2005).
  Holders sell winners early and hold losers. A stock whose holders sit on large unrealised gains
  has had its selling done; one whose holders are under water meets supply at their cost.

Do they predict a +15% burst in the next 10 weeks on AIM? And if they do, is it new information
beyond the technical `trend` and the levels `new_high` flag the screen already has? Those priors
come from US data, not from this universe.

## Signals (per stock and Friday, from data up to that Friday only)

Weekly bars are built from the cleaned daily bars (`clean_prices`, `trim_to_consistent`,
`to_weekly`), as in the panel. Code: `scripts/research/r12_anchor_overhang.py build`.

- **`nearness`** = weekly close ÷ the highest weekly high of the last 52 weeks, this week
  included.
- **`cgo`** (capital-gains overhang) = (close − RP) ÷ close. RP is Grinblatt & Han's
  turnover-weighted reference price over the previous **52 weeks**: the weight on the close k
  weeks ago is V(t−k) × Π over j < k of (1 − V(t−j)), normalised to sum to 1. V is weekly volume ÷
  the ordinary share count of the latest annual statement public at the time
  (`analysis.universe.shares_at`; before the first one is public, the earliest statement's count
  is used), capped at 1.
- **`good_news`** = the stock had a business-news NSM filing (research 0007's type list) whose
  first tradable session falls in the signal week with a reaction of z ≥ +2 against its 60-day
  volatility (`events.reactions`; not market-adjusted, unlike 0007).
- **`new_high`** = weekly close above the highest weekly high of the prior 52 weeks, the levels
  definition. Used only for the overlap check.

## Variants (3, Bonferroni counted)

1. **V1 `nearness`**, continuous: a rank IC.
2. **V2 `cgo`**, continuous: a rank IC.
3. **V3 the interaction event**: `nearness` ≥ 0.95 **and** `good_news` in the signal week.
   This tests the anchoring story directly: good news near the high, which the anchor says is
   under-reacted to.

## Target, null and bar

- **Universe and weeks:** the research panel (`build_panel.py PANEL research`), stock-weeks past
  the £10k floor, in-sample signal Fridays 2022-12-16 to 2025-08-08. The panel's later weeks are
  dropped on load and never read; the holdout stays locked.
- **Target:** `burst_10w` at 3 ATR from Monday's open (ADR 0029); 2 and 4 ATR alongside.
  Secondary: `xs_8w` (`ret_8w` minus that Friday's mean).
- **Null 1, vol + ADV:** a logistic on the weekly ranks of daily ATR % and log ADV, fitted on all
  stock-weeks. Residual = `burst_3` − the null-1 probability.
- **V1, V2 statistic:** each Friday, the Pearson correlation of the signal's cross-sectional
  rank with the residual (a rank IC net of the null); the mean over Fridays.
- **V3 statistic:** mean residual on event stock-weeks − mean residual on all other stock-weeks.
- **Interval:** a moving-block bootstrap over weeks (16-week blocks, 2,000 draws, seed 286); for
  V3, all stocks in a week move together. It is two-sided at **96.7% per variant**: 90%
  family-wise, Bonferroni for 3.
- **Pass (detection tier), per variant:**
  - the interval excludes zero;
  - the same sign in at least 5 of the 6 in-sample half-years;
  - the result survives dropping the 3 tickers that contribute most in the estimate's
    direction. For V1 and V2 that is the largest Σ (rank − 0.5) × residual; for V3 the largest
    summed residual. The point estimate must keep its sign and the interval still exclude zero.

  The economics tier waits for `analysis/simulate.py`.
- **Overlap (reported, not part of the bar):** for V1 and V2, the partial IC. That is the rank
  IC of the signal's rank after regressing it each Friday on the rank of technical `trend`, and
  for `nearness` also on `new_high`. For V3, the estimate when the comparison is restricted to
  `new_high` weeks. This says whether a passing signal is new information or `trend` /
  `new_high` again.
- **Size split:** each statistic is also reported for aim100 / rest / micro. Descriptive; no pass
  is claimed on them.
- **Survivorship:** these are price signals, so a leaver's signal cannot be computed (no prices).
  The note reports the leavers' share of stock-weeks in the window (research 0012, case 3) and
  says how far it could move the result.

## Power (no outcomes used)

| measure | value |
|---|---|
| stock-weeks past £10k with both signals | **34,104** (324 stocks, 139 Fridays) |
| stocks per Friday (median) | 244 |
| V3 events (`nearness` ≥ 0.95 and good news that week) | **152** in 98 stocks |
| good-news weeks in all | 1,130 |
| Spearman correlation: `nearness`–`cgo` / `nearness`–`trend` / `cgo`–`trend` | 0.81 / 0.71 / 0.83 |

- **MDE for V1 and V2.** A weekly IC on about 244 stocks has a standard error of about 1/√244 ≈
  0.064. The 10-week target overlaps across Fridays, leaving about 17 independent periods
  (ADR 0029), so the mean IC has a standard error of about 0.064/√17 ≈ 0.016. With z = 2.13
  (Bonferroni 3) and 80% power, the **MDE is about IC 0.05**.
- **MDE for V3.** 152 events, base rate 0.385, design effect 2: standard error ≈ 5.6 pp, so the
  **MDE is about 17 pp**.
- **Overlap with `trend`.** The signals correlate 0.7–0.8 with `trend`, which showed no edge
  (research 0001, #210). Even a pass may be mostly `trend`, which is why the partial IC is
  reported.

## What a pass or a fail would mean

- **V1 or V2 passes with a partial IC that keeps its sign:** a candidate for the levels card as
  agent context (an "anchor" or "overhang" line), after the holdout sign check and the owner's
  sign-off. Not a rank weight (the owner's decision, ADR 0029).
- **V3 passes:** a candidate situation route ("good news near the high") for the catalyst work
  (#214).
- **A negative V2** (holders under water predict fewer bursts) would be an exclusion candidate
  for #287.
- **Fail:** "not shown above the MDE". `new_high` and blue sky stay as levels context, as today.

## Results (2026-10-29): no variant passes; V3 comes close and fails on consistency

Run with `r12_anchor_overhang.py run` on the research panel: 34,104 stock-weeks past the £10k
floor (324 stocks, 139 in-sample Fridays). Intervals are 96.7% (Bonferroni 3).

| variant | estimate | 96.7% interval | half-years same sign | without the best 3 | pass |
|---|---|---|---|---|---|
| **V1 `nearness`** (rank IC) | −0.025 | −0.044 to +0.006 | 5 of 6 | −0.021 (−0.040 to +0.010) | no |
| **V2 `cgo`** (rank IC) | −0.005 | −0.022 to +0.013 | 4 of 6 | −0.002 (−0.018 to +0.017) | no |
| **V3 near the high + good news** (residual, pp) | **+8.9** | **+3.5 to +16.2** | **4 of 6** | +6.4 (+2.0 to +14.0) | **no** |

- **V3 clears two of the three conditions and fails the third.** Its interval excludes zero and
  survives dropping the best 3 names. But it has the same sign in only 4 of 6 half-years
  (−14.6, +16.2, +13.0, +12.6, +14.4, −22.7 pp), short of the 5 required. The two negative
  half-years are the thinnest (the first and last, partly covered), but the bar is fixed in
  advance and it is not met.
- **V3 is entirely a volatility-adjusted effect.** Raw, events burst at 42.1% and other weeks at
  42.0%: no difference. The +8.9 pp is against the vol + ADV null. Stocks near their high after
  good news are less volatile than the average AIM stock, so they burst as often as the rest
  despite a lower expected rate. That is what the pre-registered target measures, but it is not a
  higher hit rate.
- **V3 at 2 and 4 ATR:** +9.9 and +12.0 pp, both intervals excluding zero. `xs_8w`: events
  +2.4% (median +0.7%) against 0.0% for the rest.
- **By size (descriptive):** the effect is concentrated in micro caps (20 events, +40.8 pp,
  interval +26.0 to +62.4). The AIM 100 (86 events, +3.8 pp) and the rest (46 events, +3.2 pp)
  show little. Twenty events is a handful of stocks.

**Overlap with what the screen already has (reported, not part of the bar):**

| | IC | 96.7% interval |
|---|---|---|
| V1 `nearness` after `trend` and `new_high` (partial) | **−0.048** | −0.070 to −0.011 |
| V2 `cgo` after `trend` (partial) | −0.023 | −0.037 to −0.006 |
| `trend` itself | +0.009 | −0.012 to +0.031 |
| V3 within `new_high` weeks only (pp) | +6.7 | −0.3 to +17.7 |

- **Being near the high, beyond what `trend` says, leans the wrong way for the anchoring
  story.** Holding `trend` and `new_high` fixed, stocks closer to their 52-week high burst
  *less* often than volatility predicts. This is the opposite of George & Hwang. Note that
  `nearness` and `cgo` correlate 0.7–0.8 with `trend`, so the partial ICs measure a small slice
  of the signal. Not tested, not a rule.
- **By size,** V1 and V2 are negative in micro caps (IC −0.056 and −0.031, both intervals
  excluding zero) and near zero elsewhere.

**Survivorship (research 0012, case 3).** 266 companies left AIM (other than to the Main Market)
in the window. Their stock-weeks on AIM in it number about 23,070, against the 34,104 tested:
**the missing names are 40% of the total.** That is a ceiling: many were under the £10k floor or
outside Trading212, so the true share past the floor is lower. These are price signals, so the
leavers' cannot be computed. A result this close to the bar (V3), resting on 20 micro-cap
events for most of its size, could move a lot if the leavers were in.

## What it says

- **No anchoring or overhang edge was shown** on the pre-registered bar.
- **V3 ("good news near the 52-week high") is the most promising result in epic #283 so far.**
  It is significant after Bonferroni and survives dropping the best names. It fails the
  half-year consistency rule, and its raw hit rate is no higher than any other week's. It is a
  candidate to watch, not a signal.
- **Plain closeness to the high is not a buy signal here.** If anything, it leans negative once
  `trend` is held fixed, and so does the overhang.

## Recommendation (to the PM and the owner)

1. **No change to the toolkits, the rank or the screen.** `new_high` and blue sky stay as levels
   context.
2. **The holdout stays locked.** ADR 0029 opens it only after an in-sample pass, and V3 did not
   pass.
3. **Record V3 as it happens, without trading on it.** "Good news near the 52-week high" can be
   tracked forward on the live scans from 2026-10-20 (ADR 0029's second out-of-sample set): a
   flag on the weekly cards or a shadow-book row (ADR 0028), recording only. Whether to build that
   is the owner's call; it is a small change.
4. **Do not promote `new_high` to anything stronger.** Its partial correlation leans the wrong
   way.

## Amendment 2026-10-29: the owner takes V3 to the holdout

*Written before any holdout week was read.*

V3 did not pass the in-sample bar. It failed one condition: the same sign in 4 of 6 half-years,
not 5. The owner signed it off as a candidate anyway (2026-10-29), so ADR 0029's holdout gate
applies to V3, and to V3 only. V1 and V2 stay closed.

- **Weeks:** holdout signal Fridays from 2025-10-24 to **2026-08-14**. Later holdout Fridays
  are left out, because their 50-session `burst_10w` window had not ended by the last price
  (2026-10-28).
- **Same everything else:** the research panel, stock-weeks past the £10k floor, V3 as defined
  above (`nearness` ≥ 0.95 and good news that week), `burst_3` from Monday's open, null 1 refitted
  on the holdout stock-weeks, and the V3 statistic.
- **Gate (ADR 0029):** the holdout estimate has the **same sign as in-sample and at least half its
  size**, so ≥ **+4.45 pp**. The 96.7% interval, the raw burst rates and the event count are
  reported but are not part of the gate.
- **One look.** This spends the holdout for V3. If V3 clears the gate, it goes to the agents as
  catalyst context (not a rank weight, ADR 0022). If not, it is recorded forward only. Either
  way the result is final for this signal on these weeks.
