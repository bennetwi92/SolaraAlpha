# Research 0015: Pure trend following with weekly rotation: pre-registration and result

*Pre-registered 2026-11-11, before any forward return was computed (script sha256 `345f305234c18426f2b9e17200eb0430c79b93891c34dfc7d30c9a42bd9afde3` at freezing). Follows ADR 0029, with the
target changed at the owner's request (below). Script: `scripts/research/r13_trend_rotation.py`.
Nothing in the pipeline is changed.*

## The owner's hypothesis

"If a stock has been going up with a good trend for the last n weeks, I'd expect it to continue
for the next week at least. So I can rotate quickly, capturing small gains regularly." (owner,
2026-11-11)

This differs from the screen's set-up (bursts of +15–30% over 1–10 weeks). It is a **one-week
continuation** claim, traded as a **weekly rotation**: hold the strongest trends, sell them as
soon as others are stronger. So the target here is the next week's return, not `burst_10w`
(ADR 0029's target). `burst_10w` is not tested.

The prior evidence is mixed. Over 3–12 months, momentum is one of the most robust effects in
equities, the UK included (Jegadeesh & Titman 1993; Asness, Moskowitz & Pedersen 2013). At one
week to one month, single stocks tend to **reverse** (Jegadeesh 1990; Lehmann 1990), most of all
small, illiquid ones. Research 0001 found the same on AIM: the stock's own past 4-week return
had an IC of −0.044 against the next 8 weeks. Weekly rotation also pays the spread often, and AIM
spreads are wide.

## Pre-registration

*Written before any forward return was computed. The holdout (signal Fridays 2025-10-24 to
2026-10-16) is not computed.*

- **Universe:** `config/research_universe.toml` (every AIM company Trading212 offers). These are
  today's survivors. Daily Yahoo bars from 2021-10-20 go through `check_prices`, `clean_prices`
  and `trim_to_consistent`, then weekly bars (`to_weekly`). A stock-week counts only if that
  Friday's 20-session median traded value is at least the live `min_adv_gbp` (£25k): what the
  book could trade.
- **Signal Fridays:** 2022-12-16 to 2025-10-03. The last week's exit, Monday 2025-10-13, falls
  before the in-sample end (2025-10-17).
- **Timing:** the signal uses closes up to Friday *t*. You buy at Monday's open after *t* and
  sell (or keep) at the next Monday's open. **Target `y`** = adjusted open of the first session
  after Friday *t+1* ÷ adjusted open of the first session after Friday *t* − 1.
- **Signal, three variants (n = 4, 13, 26 weeks):** `s_n = ln(C_t / C_{t−n}) / (σ₂₆ × √n)`.
  `C` is the Friday adjusted close. `σ₂₆` is the standard deviation of the last 26 weekly log
  returns. This scores a *good* trend: a steady climb ranks above one with the same gain on wild
  swings. Bonferroni over 3 variants, so all intervals are 96.7%.
- **Test 1 (detection):** the mean weekly Spearman IC of `s_n` against `y`, with a 96.7%
  moving-block bootstrap (16-week blocks). **Pass:** lower bound > 0; positive in at least 5 of
  the 6 half-years; and with the 3 names that contribute most removed, the IC is still positive
  with its interval above zero.
- **Test 2 (economics: the strategy itself):** each Friday, take the top 8 names by `s_n` (the
  book's 8 positions), only those with `s_n > 0`, at 1/8 of the book each. Empty slots hold cash
  at 0%. Names still in the top 8 are kept with no trade. A name is bought or sold at Monday's
  open, and each side costs its spread: **2× costs** in ADR 0029's terms, i.e. the full
  max(60-session median, 60-session mean) Corwin-Schultz spread per side, as of Friday *t*.
  1× is reported too. Weights reset to equal each week. **Excess** = the book's weekly return
  after costs minus the equal-weight return of every eligible stock that week, gross (holding
  everything trades almost never). **Pass:** mean weekly excess > 0 with its 96.7% block
  bootstrap interval above zero; positive in at least 5 of the 6 half-years; and still above zero
  with the 3 names that added most removed from the universe and the rotation re-run.
- **A variant passes only if both tests pass.** Reported alongside, not part of the bar:
  - the annualised return after costs, maximum drawdown, weeks held per position, names traded
    a week and the hit rate (share of held weeks that rose);
  - the FTSE AIM All-Share (`^FTAI`) over the same weeks;
  - the owner's literal question: how often the next week is up for the top 10% by `s_n`, for
    stocks up in each of their last 4 weeks, and for everyone.
- **Half-years:** H1 2023 (from 2022-12-16), H2 2023, H1 2024, H2 2024, H1 2025, and
  H2 2025 to 2025-10-03.
- **Biases stated in advance:** survivors only (research 0012: about one company in three that
  was on AIM in 2022 has since left, mostly takeovers and failures). Monday-open fills at the
  printed open. One trade a week. A £625 slot is under 5% of £25k ADV, so the 5% ADV cap never
  binds.
- **If it passes:** the holdout gate (the same sign, at least half the size) runs only after the
  owner signs off. If it fails, the note says so and nothing changes.

## Result (2026-11-11): no variant passes; costs make weekly rotation a heavy loser

Run with `r13_trend_rotation.py run`: **30,460 stock-weeks** past £25k (353 stocks, 147
Fridays, a median of 204 stocks a Friday). `count` gave a minimum detectable IC of about 0.02.
The data-quality trim (`trim_to_consistent`, mostly recent `stale` days in thin names) dropped
74 stocks that would have had 2,383 stock-weeks past the floor: 7% of the total.

### The answer in one paragraph

**A stock that has risen steadily over the last 4, 13 or 26 weeks is no more likely to rise next
week than any other AIM stock.** Next week was up 45% of the time for the top 10% of trends,
against 43.6% for everyone (and 47% for stocks up four weeks running, with a *lower* mean
return). The rank IC is zero or slightly negative at 4 and 13 weeks, the short-term reversal
research 0001 found. The weekly rotation is the bigger problem. It buys 2–4 new names a week, and
the strongest trends are the widest-spread stocks. So it pays **1.9–4.6% a week in spread** (2×
costs), against a gross edge of at most +0.6% a week. After costs every variant loses money in
every half-year.

### Test 1: weekly IC against next week's return

| variant | IC | 96.7% interval | positive half-years | halves | without the best 3 | pass |
|---|---|---|---|---|---|---|
| s4 | −0.009 | −0.017 to +0.003 | 1 of 6 | −0.026 / −0.003 / −0.006 / +0.000 / −0.005 / −0.014 | −0.012 (−0.019 to −0.001) | no |
| s13 | −0.011 | −0.023 to +0.003 | 2 of 6 | −0.030 / −0.028 / +0.003 / +0.001 / −0.005 / −0.003 | −0.013 (−0.026 to +0.002) | no |
| s26 | +0.009 | −0.007 to +0.024 | 4 of 6 | −0.007 / −0.026 / +0.034 / +0.023 / +0.018 / +0.017 | +0.005 (−0.011 to +0.022) | no |

### Test 2: the top-8 weekly rotation, after 2× costs, against the equal-weight universe

| variant | weekly excess | 96.7% interval | positive half-years | halves (%) | without the best 3 | pass |
|---|---|---|---|---|---|---|
| s4 | −4.06% | −4.68 to −3.17 | 0 of 6 | −4.48 / −3.83 / −3.48 / −2.72 / −4.03 / −7.21 | −4.26% | no |
| s13 | −2.63% | −3.13 to −1.89 | 0 of 6 | −2.77 / −3.51 / −2.67 / −1.99 / −1.34 / −4.24 | −2.71% | no |
| s26 | −1.82% | −2.67 to −1.16 | 0 of 6 | −2.86 / −0.79 / −1.85 / −1.54 / −1.88 / −1.91 | −2.27% | no |

### In money terms (reported, not the bar)

| variant | a year, before costs | 1× costs | 2× costs | max drawdown (2×) | cost a week (2×) | names bought a week | weeks held | weeks up |
|---|---|---|---|---|---|---|---|---|
| s4 | +42.2% | −57.9% | −87.9% | −99.8% | 4.64% | 4.4 | 1.8 | 46.7% |
| s13 | +3.7% | −47.3% | −73.6% | −97.7% | 2.59% | 2.8 | 2.9 | 44.6% |
| s26 | +11.4% | −32.5% | −59.4% | −93.0% | 1.92% | 2.3 | 3.5 | 44.8% |

Over the same 147 weeks, the equal-weight eligible universe made +9.0% a year (max drawdown
−20.5%) and the FTSE AIM All-Share −1.6% a year (−28.1%).

### The owner's question: how often is next week up?

| group | stock-weeks | next week up | mean next week |
|---|---|---|---|
| everyone | 30,460 | 43.6% | +0.21% |
| top 10% by s4 | 3,069 | 45.0% | +0.37% |
| top 10% by s13 | 3,069 | 44.6% | +0.11% |
| top 10% by s26 | 3,050 | 45.0% | +0.05% |
| up in each of the last 4 weeks | 1,258 | 47.0% | −0.05% |

5.2% of next weeks are flat (no change), counted as not up.

### One exploratory number (not pre-registered, not a pass)

Before costs, the s4 rotation beat the universe by **+0.59% a week** (96.7% interval −0.02 to
+1.19; +0.36% without IMM, EPP and EEE; positive in 5 of 6 half-years). That is the classic
short-term "winners keep winning a little" tail: a few names that kept running. To break even,
each trade side could cost at most **0.54%**, i.e. a full spread of about 1.1%. The names it
picks have an estimated spread near 4%. The Corwin-Schultz proxy overstates spreads on volatile
stocks, but not fourfold. Weekly rotation of AIM trend names cannot pay for itself at Trading212
spreads. This was found after the test, so it is a lead, not a result.

### Reading

- **The hypothesis as stated fails.** There is no next-week continuation to capture. The 4- and
  13-week trends lean towards reversal, as research 0001 found.
- **Turnover is the killer, not the signal.** On AIM a strategy has to hold long enough for the
  move to dwarf the spread. The burst set-up (+15–30% over 1–10 weeks) does this by design; weekly
  rotation does not.
- **What is still open:** a slow trend strategy (enter on a strong trend, hold with a wide
  trailing stop and turn over a few names a month) was not tested here. Its cost per week would
  be a fraction of these, and research 0001 found the 26-week leg flat rather than negative. That
  would be a new pre-registration, and a `trend_only` row in the shadow book could record it
  live.
- The holdout was not touched. Nothing passed, so there is nothing to take to it.
