# Research 0002: Missing = neutral in the screen rank (issue #223, #214 R5)

*Pre-registered 2026-10-20, before any outcome was computed. Follows ADR 0029. Input to a
screen change the owner decides; `rank_score` is not changed here.*

## Question

`portfolio.fit.rank_score` is the mean of technical `trend`, levels `compression` and
fundamentals `growth` **over the fields present**. A stock with no usable `growth` ranks on two
numbers, on the same scale as one ranked on three (#214 F3). The proposal is to count a missing
field as neutral (0) and always divide by 3. The PM ruled that this changes who reaches the
agents, so it is tested before it ships (#214, PM review point 3).

Two questions:

1. **How much does the weekly shortlist change?**
2. **Are the names that move in better or worse than the names that move out,** on
   `burst_10w` and the 8-week return?

## Pre-registration

*Written before outcomes were computed.*

- **Variants (2):** `current` = mean of the present fields (today's code);
  `neutral` = sum of the present fields / 3. No other variant is tried.
- **Universe and weeks:** today's 104 watchlist names (survivors), cards replayed
  point-in-time for every Friday. **In-sample only** (ADR 0029): signal Fridays 2022-12-16 to
  2025-08-08 for `burst_10w`, to 2025-08-22 for 8-week returns. Holdout weeks (from 2025-10-24)
  are not computed.
- **Inputs:** `trend` from `technical.scorecard`, `compression` from `levels_card`, `growth` from
  `fundamentals_card` with `as_of` = the Friday and NSM publication dates (`with_publication`,
  #221, as the live toolkit now runs). Sensitivity: the same with statutory lags.
- **Shortlist:** past the floor (20-session median traded value ≥ £25,000 as of the Friday, and
  at least one rank field present), the top 7 by score; ties by ticker. The catalyst route is
  not modelled (headlines are not point-in-time).
- **Target:** `burst_10w` per ADR 0029 (from the first session's open after the Friday, +15%
  before −3 daily ATR(14), within 50 sessions; a session touching both counts as a stop). 2 and
  4 ATR alongside. Secondary: 8-week total return (`ret_8w`) and `xs_8w` (minus that week's
  mean over the names past the floor).
- **Churn measures:** per week, the number of top-7 names that differ (`|neutral \ current|`),
  the share of weeks with any change, the mean Spearman correlation of the two scores, and who
  the movers are (share missing `growth`, median daily ATR %).
- **Mover test:** per week with movers, mean outcome of IN (`neutral \ current`) minus OUT
  (`current \ neutral`); averaged over weeks; 90% moving-block bootstrap over weeks (16-week
  blocks). For `burst_10w` also the vol-adjusted version: outcome minus the prediction of a
  logistic vol + ADV null (rank of daily ATR % and rank of log ADV, fitted on the in-sample
  pool).
- **Decision rule.** This is a comparability fix, not a new signal, so the bar is
  non-inferiority, not edge:
  - **Ship** if the 90% interval of IN − OUT has a lower bound above −5 pp on vol-adjusted
    `burst_10w` **and** above −2 pp on `xs_8w`.
  - **Do not ship** if the upper bound is below 0 on vol-adjusted `burst_10w` (movers in are
    clearly worse).
  - Otherwise **inconclusive**: the decision falls back to principle, and the note says so.
  - The minimum detectable effect is stated from the mover count before outcomes are read.

## Step 1 result and MDE (written before any outcome was read)

The churn step ran first, with outcomes not computed. It gives 151 mover pairs in 71 of 141
weeks (NSM dates), so the power of the mover test is fixed before outcomes:

- **Burst rate, IN − OUT:** with p ≈ 0.25, the SD of a pair difference is about √2 × 0.43 ≈ 0.61.
  With a design effect of about 3 (the same names move week after week), the effective n is
  about 50. That gives SE ≈ 0.087 and an **MDE ≈ 2.49 × 0.087 ≈ 22 pp** (80% power, 90% interval).
- **`xs_8w`, IN − OUT:** with the SD of 8-week excess returns about 0.20, the pair SD is about
  0.28. That gives SE ≈ 0.04 and an **MDE ≈ 10 pp**.
- **Consequence, stated in advance:** the non-inferiority margins (−5 pp burst, −2 pp `xs_8w`)
  are far tighter than this sample can resolve. The lower bounds will almost surely fall below
  them, so the expected verdict is **inconclusive**, and the decision falls back to principle.
  Only a large harm (movers in worse by about 20 pp on the burst rate) would be visible.

## Data and method

- **Panel:** `scripts/research/build_panel.py`. One fresh 5-year fetch per ticker (check, clean,
  trim, weekly), with cards per Friday from bars up to that Friday.
  - 103 of 104 names. GRP.L fails liquidity: its accounts and price are in EUR.
  - 11,959 stock-weeks past the floor, 99 tickers, 141 in-sample weeks (2022-12-16 to
    2025-08-22).
  - The median is 85 names past the floor each week.
- **Missing `growth`:** 41% of ranked stock-weeks lack it with NSM dates, 48% with statutory
  lags. It is missing mostly because Yahoo's statement history is short (4–5 years), so the
  year-on-year growth inputs start late, and for loss-makers with no usable EPS base.
  `trend` and `compression` are almost never missing, so in practice "missing" means "missing
  `growth`".
- The vol + ADV null for `burst_10w` (pooled in-sample logistic on weekly ranks) and the
  bootstrap follow the pre-registration. Script: `scripts/research/r5_rank_growth.py`.

**Correction to the MDE.** After the run, the in-sample base rate of `burst_10w` (3 ATR) came out
at 38.5%, not the 25% assumed above. The same arithmetic gives an MDE of about **24 pp**. The
conclusion stated in advance does not change.

## Findings

### 1. The shortlist changes in half the weeks, by about one name

| measure (in-sample, NSM dates) | value |
|---|---|
| top-7 names that differ per week | mean **1.07**, median 1 |
| weeks with 0 / 1 / 2 / 3 / 4 names changed | 70 / 24 / 23 / 15 / 9 (of 141) |
| weeks with any change | 50% |
| Spearman of the two scores, mean per week | 0.995 |
| names without `growth` in the top 7 | current 2.87 → neutral 1.80 |
| mover pairs (weeks with a `burst_10w` label) | 151, in 71 weeks |

Statutory lags give a similar picture: 0.92 names a week, 130 pairs. Every name moving out
lacks `growth` and every name moving in has it, as expected.

### 2. Missing `growth` is a history artefact in the backtest, and nearly absent live

| year | share of ranked names without `growth` | top-7 names changed per week |
|---|---|---|
| 2022 (2 weeks) | 100% | 0.0 |
| 2023 | 78% | 1.4 |
| 2024 | 28% | 1.5 |
| 2025 (to Aug) | **0%** | **0.0** |

`growth` is missing early because Yahoo's statement history starts 4–5 years back, and the
growth inputs need a year-on-year comparison. By 2025 every name past the floor has a `growth`
score.

- **Live, the change would affect little.** It would move only names whose fundamentals fail
  or are absent (an ERROR, or no Yahoo statements).
- **The test is of an artificial period.** The movers come from 2023–24, when "no growth" meant
  "short data history", not "a thin company".

### 3. Movers in did worse than movers out, but inside the noise

| IN − OUT (per week, then averaged; 90% MBB) | NSM dates | statutory lags |
|---|---|---|
| `burst_10w` vol-adjusted (primary) | **−11.2 pp** [−29.7, +8.6] | −0.2 pp [−20.1, +20.1] |
| `burst_10w` raw, 3 ATR | −15.1 pp [−34.7, +4.3] | −5.5 pp [−24.0, +13.4] |
| `burst_10w` raw, 2 / 4 ATR | −12.6 / −16.5 pp | −3.3 / −7.6 pp |
| `xs_8w` | **−1.6 pp** [−6.4, +3.1] | −0.4 pp [−6.2, +5.2] |
| `xs_8w` without the best 3 IN names | −5.3 pp [−8.5, −2.3] | −4.7 pp [−8.5, −0.4] |

| group (NSM dates) | n | tickers | median daily ATR | `burst_10w` | vol + ADV null | `xs_8w` | mean `trend` |
|---|---|---|---|---|---|---|---|
| IN | 151 | 36 | 3.7% | 31.8% | 33.4% | −0.1% | 0.72 |
| OUT | 151 | 40 | 3.8% | 49.7% | 37.1% | +3.4% | 0.87 |

The whole top 7, neutral minus current:

| outcome | value [90% MBB] |
|---|---|
| `burst_10w` | −2.8 pp [−6.4, −0.1] |
| `burst_10w` vol-adjusted | −2.2 pp [−5.7, +0.7] |
| `xs_8w` | −0.5 pp [−1.3, −0.0] |

What the tables show:

- **The names pushed out were strong-trend names** with no `growth` yet: JET2, EEE, W7L, BPM,
  HSP, YCA. Their mean `trend` is 0.87.
- **The names pulled in** have good growth (mean 0.72) but less trend and compression: TSTL,
  CER, KGH, DOTD, ABDP, RNWH.
- **The two groups have the same volatility.** The gap is not a volatility effect.
- **The years disagree.** The vol-adjusted IN − OUT burst gap was positive in 2023 H1 and H2 and
  negative in 2024 H1 and H2. There are no movers after 2024, because `growth` is then always
  present.

### 4. Verdict against the pre-registered rule: inconclusive

| rule | requirement | result | met? |
|---|---|---|---|
| ship | lower bound > −5 pp on vol-adjusted burst | −29.7 pp | no |
| ship | lower bound > −2 pp on `xs_8w` | −6.4 pp | no |
| do not ship | upper bound < 0 on vol-adjusted burst | +8.6 pp | no |

So the verdict is **inconclusive**, as the MDE predicted. Every point estimate leans against the
change. The whole-top-7 raw comparison only just excludes zero against it. Dropping the best 3
names in makes it worse.

## Caveats

- **Survivorship (large, and in a known direction here).** The names pushed out are today's
  constituents with short data histories and strong trends in 2023–24. EEE is one of the three
  names that carried the #210 Q9 rank edge, which sat in the AIM 100 additions. A survivor universe flatters exactly those names. The lean
  against "neutral" may be survivorship, not a property of the rank.
- **Few independent movers.** 151 pairs, but 36–40 tickers, the same names for weeks at a time,
  and none after 2024.
- **The catalyst route is not modelled,** and outcomes are close-to-close or the `burst_10w`
  label, not simulated trades.
- **The holdout was not read.** All figures are in-sample (2022-12-16 to 2025-08-22).

## Recommendation

**To the owner and PM: do not ship "missing = neutral" now.**

- The data cannot show it is harmless, and the point estimates lean against it.
- It would change almost nothing live, because `growth` is present for every name in 2025.
- The comparability problem in #214 F3 is real. But the cheaper fix has already shipped:
  `growth_n` on the card (#220) tells the agents and the PM how thin a score is, and the packs
  say the rank is unvalidated.
- Revisit the change only inside the #210 phase-2/3 rank model. There a missing input becomes
  an explicit indicator feature, fitted walk-forward, rather than a weighting choice.

**Decision needed (owner):** keep `rank_score` as is (my recommendation), or ship the change on
principle despite an inconclusive test.

## Reproduction

```
uv run python scripts/research/build_panel.py PANEL_DIR        # ~45 min, 4 processes
uv run python scripts/research/r5_rank_growth.py PANEL_DIR churn
uv run python scripts/research/r5_rank_growth.py PANEL_DIR movers
```

Fresh 5-year fetches on 2026-10-20 (Yahoo prices and fundamentals, NSM filings), so a later
re-run will differ slightly as Yahoo restates and history rolls forward. Nothing is written to
the DB or under `data/`. The bootstrap is seeded (223), with 2,000 replicates.
