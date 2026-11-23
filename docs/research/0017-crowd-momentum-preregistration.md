# Research 0017: Crowd momentum in the Nasdaq-100: pre-registration and result

*Pre-registered 2026-11-23, before any forward return of these rules was computed (script sha256 `80a6194700ed40fcaf01605d78f14f880d83a3e6fc1234dada2ed916826f3e81` at freezing). The ADR 0045
score and its two variants were chosen by the owner ("let's try both", 2026-11-23). Script:
`scripts/research/r15_crowd_momentum.py`; the score is `analysis/momentum.py` (`crowd_panel`, the
vectorised `momentum_card`, tested equal to it). Nothing in the pipeline changes on the result.*

## The question

Does a high crowd-momentum score (ADR 0045: trend, nearness to the 52-week high, volume,
strength vs the Nasdaq-100) pick Nasdaq-100 stocks that do better over the next 3–15 sessions
than the rest, after costs? The first live reading (2026-11-23) put defensives at the top of a
falling market. So two variants are tested beside the score as built: one that stands aside
when the market is risk-off, and one that measures strength net of beta.

## Protocol for the US desk (in place of ADR 0029's AIM settings)

ADR 0029's target (`burst_10w`), its holdout and its weekly settings were written for AIM. ADR
0040 slice 4 asks for daily ones. This note fixes them for the US desk and keeps 0029's
principles: one target, a pass bar set in advance, a locked holdout, and variants counted.
*Owner sign-off needed on these settings* (0029: changes need the owner). The owner asked for
this test and agreed the three choices it rests on: the score, its two variants, and the 3–15
session horizon.

- **Universe:** today's Nasdaq-100 (`config/watchlist.toml`, 101 lines). Each stock is scored
  only on dates it has prices and at least 70 clean sessions behind it. **Survivorship bias:**
  today's members include stocks that joined after big rises (for example APP and PLTR), so
  every momentum test on them looks better than it should. Comparing against the same
  universe's equal-weight mean takes out part of that bias, but not all. Live trading is the
  real out-of-sample test.
- **Data:** 5 years of Yahoo daily bars (2021-11-23 to 2026-11-23), passed through
  `check_prices`, `clean_prices` and `trim_to_consistent`, with `^NDX` as the market.
  Split-adjusted open, high, low and close; dividends are left out (under 0.3% over 10 sessions
  for most names).
- **Signal dates:** every session from **2022-11-23** (one year of warm-up, so the 52-week high
  and the 200-day average are real).
- **In-sample:** signal dates whose 15-session forward window ends on or before **2025-11-21**.
- **Holdout (locked):** signal dates from **2025-11-24** on. The script does not compute them
  without `--holdout`, and no one runs that until a variant has passed in-sample and the owner
  signs off. The holdout gate is a sign check: the same sign as in-sample and at least half
  the size.
- **Target:** the 10-session forward return, entering at the **next session's open** and
  exiting at the close 10 sessions after the signal. The signal session's close is never the
  entry. The 5 and 15-session returns are reported alongside, not tested.
- **No trade management in this test:** there are no stops, partials or trails. This tests the
  ranking only. The agents' trade management is measured live, against the shadow book.

**Variants (4):**

| id | score | market gate |
|---|---|---|
| A | `crowd` as built (ADR 0045) | none |
| B | `crowd` | stand aside (cash, no cost) on a `^NDX` risk-off day: drawdown from the 20-day high below -8% or a 3-sigma down day (`analysis/regime.py` rules, unchanged) |
| C | `crowd` with strength net of beta: excess = return − β × market return over 20 and 60 sessions, β from the prior 120 sessions' daily returns | none |
| D | C's score | B's gate |

**Pass bar (fixed now).** A variant passes in-sample only if both tiers hold.

*Detection* (the cross-section; B shares A's score, D shares C's):
- mean daily Spearman rank IC between the score and the 10-session forward return > 0, with a
  Newey-West t (9 lags, for the overlapping windows) of **at least 2.5**. That is stricter than 2
  because four variants are tried;
- the mean IC positive in at least **5 of 6** equal consecutive blocks of the in-sample dates.

*Economics* (every signal date, overlapping): buy the **top 10** by score, equal weight; count
**0.35% cost** per round trip (0.15% FX fee each way plus 0.05% spread), and cash on a gated
day; compare with the equal-weight mean of every scored stock that date.
- mean net excess over the universe > 0 with a Newey-West t (9 lags) of **at least 2.0**;
- mean net absolute return > 0;
- the mean net excess stays > 0 with the 3 stocks that contributed most removed from the
  universe and the ranking re-run.

**Power (minimum detectable effect).** The in-sample period is about 730 signal dates, or about
73 independent 10-session windows. Cross-sectional IC across 100 large caps typically varies by
about 0.15 a day, so a t of 2.5 needs a mean IC of about **0.045** or more. A top-10 portfolio's
10-session excess varies by about 3–4%, so a t of 2 needs about **0.8% a trade** or more. A
fail means "not shown above these sizes", not "no effect".

## Result (in-sample, 2026-11-23): all four variants fail

Run on 737 signal dates (2022-11-23 to 2025-10-31), 99 stocks scored (two listings are too
young), 44 of the dates risk-off. The script matched its frozen hash. The holdout was not
computed and stays locked, since no variant passed.

| variant | mean IC (10 sessions) | t (NW) | blocks positive | top-10 net, per trade | universe | excess | t (NW) | excess, best 3 dropped | verdict |
|---|---|---|---|---|---|---|---|---|---|
| A as built | -0.013 | -0.60 | 2/6 | +0.96% | +1.16% | -0.20% | -0.63 | -0.48% | fail |
| B risk-off gate | (A's) | | | +0.92% | +1.16% | -0.24% | -0.76 | -0.52% | fail |
| C net of beta | -0.010 | -0.51 | 2/6 | +0.89% | +1.16% | -0.26% | -0.91 | -0.52% | fail |
| D both | (C's) | | | +0.85% | +1.16% | -0.31% | -1.03 | -0.56% | fail |

Mean IC by year (as built / net of beta): 2022 (from 23 Nov) +0.064 / +0.069; 2023 -0.065 /
-0.062; 2024 +0.019 / +0.024; 2025 +0.002 / +0.002. At 5 and 15 sessions the IC is the same
sign and size (-0.006 to -0.011). The best contributors were SNDK, RKLB, APP and MSTR.

**What it means.** The crowd score does not rank Nasdaq-100 stocks by their next 1–3 weeks.
The top ten made money in absolute terms (+0.9% per 10 sessions after cost, in a rising
market), but less than simply holding the whole index equally (+1.2%), so the ranking cost
returns. Standing aside on risk-off days did not help. After those 44 days the market bounced:
the universe gained +3.7% over the next 10 sessions on average, and the top ten +0.8% after
cost. Sitting in cash gave that up. Taking beta out made no difference. Nothing here is close to the bar.
The effect is not shown above an IC of about 0.045 or 0.8% a trade, and the point estimates
are slightly negative.

This fits what is documented elsewhere. In US large caps, momentum earns over 3–12 months and
skips the latest month, while over days to weeks the latest winners tend to *reverse*
(Jegadeesh 1990; Lehmann 1990). A price-only "already going up on volume" score buys exactly
that short-term reversal. The same pattern showed on AIM (research 0001, 0015).

**What it does not test.** The agents' judgement of the story, and the trade management (the
entry level, the stop, the partial, the trail) are not tested here. A ranking with no edge can
still be the pool the agents pick from, but nothing here says the top of it is a better pool
than the rest.

**Next candidates (each needs its own pre-registration).** Effects documented at the 3–15
session horizon that fit "narrative backed by technicals":
1. **Earnings and news gaps:** a large gap up on several times normal volume (a price-and-volume
   proxy for a news day, testable on prices alone), followed by drift. This is post-earnings-
   announcement drift, the best-documented short-horizon effect, and the crowd plus narrative
   trade in its plainest form.
2. **Pullbacks inside strong trends:** buy a short dip in a stock with strong 6–12 month
   momentum. This uses the short-term reversal rather than fighting it.
