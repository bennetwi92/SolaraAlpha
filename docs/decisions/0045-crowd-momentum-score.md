# 0045 — The crowd-momentum score: the US screen's rank

**Status:** accepted as a placeholder (2026-11-23), until the pre-registered test (step 3 of the
US build) passes or fails it. Implements the screen of ADR 0043 / 0044 (technicals lead).
Replaces the AIM rank (technical trend, levels compression, fundamentals growth; ADR 0022).

**Decision.** A `momentum` toolkit (`analysis/momentum.py`) scores each stock on **daily bars**,
using only that session and earlier, from four parts, each in [-1, +1]:

- **trend:** four ±1 votes. The close is above its 20-day average, the 20-day is above the 50-day,
  the 50-day is rising over 20 sessions, and the close is above its 200-day average (a listing
  younger than 200 sessions gets the first three only).
- **high:** nearness to the 52-week high. +1 at the high, 0 at 10% below it, -1 at 20% or more below.
- **volume:** log2 of the last 5 sessions' mean volume over the prior 50 sessions' median.
- **strength:** the excess return over the market (`^NDX`), the mean of 20 and 60 sessions,
  where a 10% excess return scores +1.

`crowd` is the mean of the four parts, and it is the screen's rank (`fit.RANK_FIELDS`). The
toolkit runs in the screen stage. Its bands (`crowd >= 0.5` ACT, `>= 0` WATCH) are information
only, as every band has been since ADR 0022. Context for the agents goes beside the score, not
in it: a 20-day breakout, a new 52-week high, the excess return over the sector ETF, the ATR,
and how far the close is stretched above its 20-day average in ATRs (`extended` past 3). That
last one is ADR 0044's "careful what we buy" check. `scripts/momentum.py` prints the ranking.

**Why these parts.** Each one answers a question in ADR 0043: is it trending, is it at or near
new highs, is the crowd arriving (volume), and is it leading the market? The scales are round
numbers chosen before looking at any outcome, and nothing was tuned.

**First reading (2026-11-23, no outcomes looked at).** The Nasdaq-100 was 8% down over 20
sessions and 14% off its high. The former leaders in semiconductors, storage and AI hardware
were 50–60% off their highs, and the top of the ranking was defensive stocks (Mondelez, Xcel,
Costco, Walmart) beating a falling index. As built, the score ranks *relative* strength, so in
a falling market it picks low-beta defensives, not crowd favourites. Whether the screen should
stand aside in a falling market (the regime card, ADR 0019 / #142), or measure strength net of
beta, is a question for the test and the owner. It is not a change made from one day's list.

**Consequences.** The weekly pipeline still runs the AIM-era weekly toolkits beside this one
until the daily scan (ADR 0040 slice 3). The test in step 3 fixes its pass bar and holdout
before it looks at forward returns.
