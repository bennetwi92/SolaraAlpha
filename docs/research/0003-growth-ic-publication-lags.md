# Research 0003: Growth IC with real publication dates (issue #223, #214 F2 / R5)

*Pre-registered 2026-10-20, before any outcome was computed. Follows ADR 0029.*

## Question

The backtest made fundamentals known only after the statutory lag (6 months after a year end,
3 after a half-year). The NSM shows results land after about 70 and 50 days (#214 F2). #221
(`with_publication`) now dates each period from its own results filing (final, preliminary or
annual report for a year; the interim report for a half-year; never a trading update). The
growth IC in #210 (+0.038 [−0.057, +0.085] vs the 8-week return) was measured on the stale
signal. Does the IC change when the dates are real?

## Pre-registration

*Written before outcomes were computed.*

- **Signal:** `fundamentals_card(...).growth` as of each Friday, in two versions: `stat`
  (statutory lags, the #210 baseline) and `pub` (`with_publication`, statutory fallback when no
  results filing is found).
- **Universe, weeks:** today's 104 watchlist names, in-sample Fridays only (2022-12-16 to
  2025-08-08 for `burst_10w`, to 2025-08-22 for `ret_8w`), names past the £25k ADV floor with
  a non-NaN `growth`.
- **Targets:** primary `burst_10w` (ADR 0029, 3 daily ATR). Secondary `ret_8w`, to compare with
  #210.
- **Statistic:** the mean weekly Spearman IC; 90% moving-block bootstrap (16-week blocks, all
  stocks in a week together). For `burst_10w`, also the IC against the target minus a logistic
  vol + ADV null (ADR 0029 detection tier). The before-vs-after difference uses a paired
  bootstrap (same resampled weeks for both).
- **Variants counted:** 2 (`stat`, `pub`) on the primary target. Bonferroni: a 95% interval per
  variant.
- **Pass bar (detection tier only; economics needs the simulator, not built):** vol-adjusted IC
  vs `burst_10w` with the Bonferroni interval excluding zero, and the same sign in at least 5 of
  6 in-sample half-years.
- **Expected direction:** #214 predicts `pub` has a larger IC than `stat`.
- **Caveat stated in advance:** `growth` is already spent on the holdout (ADR 0029). A pass here
  is in-sample only and cannot be confirmed on the locked holdout.

## Data and method

- **Panel:** the same panel as research 0002 (`scripts/research/build_panel.py`). For each
  Friday, `fundamentals_card(fund, weekly, as_of=Friday, fx)` is computed twice: on the raw
  Yahoo frame (statutory lags), and on `with_publication(fund, filings)`. The code is as of #220
  and #221 (FX conversion, `growth_n`, NaN for undefined ratios).
- **Filings:** NSM filings for 94 issuers. For the 9 without an LEI or LSE record, `pub` equals
  `stat` (fallback).
- **Coverage:** in-sample past the floor, 11,959 stock-weeks, 99 tickers, 141 weeks. Of these,
  6,424 have a `stat` growth score and 7,285 a `pub` one.
- **Weekly IC:** computed on weeks with at least 10 names: 103 weeks (`stat`) and 126 (`pub`)
  for `burst_10w`. Earlier weeks have too few names with `growth`.
- **Vol-adjusted:** the target minus the pooled vol + ADV logistic null (research 0002), so the
  IC is of `growth` against the part of the burst that volatility and liquidity do not explain.

## Findings

### 1. Real dates change the signal often, but not its IC

| measure | value |
|---|---|
| stock-weeks where `pub` ≠ `stat` | 33% |
| Spearman of `pub` vs `stat` where both exist | 0.835 |
| stock-weeks with a growth score | `stat` 6,424 → `pub` 7,285 (+13%) |
| weeks with ≥ 10 scored names (burst) | 103 → 126 |
| mean `growth_n` | 2.44 → 2.50 |

### 2. Growth IC, before vs after

| target | `stat` (before) | `pub` (after) | `pub` − `stat`, paired |
|---|---|---|---|
| **`burst_10w` vol-adjusted (primary)** | +0.046 [−0.036, +0.111] | **+0.067 [+0.006, +0.116]** | +0.007 [−0.023, +0.036] |
| … 95% (Bonferroni, 2 variants) | [−0.050, +0.120] | **[−0.007, +0.124]** | — |
| `burst_10w` raw, 3 ATR | −0.023 [−0.108, +0.063] | +0.006 [−0.057, +0.060] | +0.001 [−0.051, +0.038] |
| `burst_10w` raw, 2 / 4 ATR | −0.018 / −0.019 | +0.011 / +0.003 | ≈ 0 |
| `ret_8w` (as in #210) | +0.057 [−0.014, +0.116] | +0.067 [+0.013, +0.115] | −0.016 [−0.084, +0.046] |
| `ret_8w`, rows where both exist | +0.058 [−0.007, +0.111] | +0.078 [+0.006, +0.145] | — |

90% moving-block intervals unless marked. The paired column uses only weeks where both
versions have an IC, so it is not the difference of the first two columns.

- **The F2 prediction is not supported.** #214 expected real dates to raise the IC. The paired
  difference is +0.007 [−0.023, +0.036] on the primary target, and about zero on every other.
  Where both versions exist, they rank stocks almost alike, so they predict alike.
- **What real dates add is coverage:** 13% more scored stock-weeks and 23 more usable weeks.
  The `pub` intervals are narrower, which is why its 90% interval now excludes zero and the
  `stat` one does not.
- **The change from #210's +0.038 is mostly code, not lags.** On the same lags (`stat`), the 8-week
  IC is now +0.057, against #210's +0.038 [−0.057, +0.085]. The difference comes from the #220
  card changes, the weekly IC rules and the floor. The data are fetched fresh, so Yahoo
  restatements also differ.
- **Growth predicts the burst only after volatility is removed.** Its raw IC with `burst_10w` is
  about zero, but +0.067 net of the vol + ADV null. Growth names are calmer, and calm names
  burst less often, so the two effects cancel in the raw number. That matches #210 Q8.

### 3. Against the pass bar (detection tier, `pub`, primary target)

| criterion | result | met? |
|---|---|---|
| vol-adjusted IC, Bonferroni (95%) interval excludes zero | +0.067 [−0.007, +0.124] | **no** (just) |
| same sign in ≥ 5 of 6 in-sample half-years | positive in 5 of 6 (2024 H1 −0.123) | yes |
| economics tier | needs `simulate.py` | not run |

**Fail, narrowly:** "not shown above the MDE" for two variants (about 0.07, ADR 0029). The
point estimate, +0.067, is at the size the programme can detect, and has the right sign in 5 of
6 half-years. It is the strongest result of any rank field so far. Compare `trend` at +0.051
after vol control on the +15% close-to-close label (#210 Q8), and `compression` at +0.000.

## Caveats

- **Spent signal.** `growth` was studied on full history (#210 Q9, research 0001), so the
  holdout cannot confirm this. Live scans from 2026-10-20 are the only clean test left.
- **Survivorship.** Today's constituents only. Growth names that later collapsed and left the
  index are missing, so the true IC is likely lower.
- **Restated figures.** Yahoo serves restated statements (ADR 0005). This is a mild look-ahead
  in both versions alike.
- **Thin early weeks.** 2023 H1 has few scored names, and its half-year IC is noisy.

## Recommendation

**To the PM and owner:**

- Keep `with_publication` (#221). It is the honest date and it adds coverage, but it does not
  sharpen the signal.
- Do not change the rank weights on this note.
- Treat `growth` as **the most promising field for the #210 phase-2/3 logistic rank model:** a
  vol-adjusted IC of about +0.07, with the right sign in 5 of 6 half-years. It goes in as a
  rank-transformed feature beside ATR and ADV, not as a standalone gate.
- Its confirmation has to come from live scans. The holdout is spent for it.

## Reproduction

```
uv run python scripts/research/build_panel.py PANEL_DIR
uv run python scripts/research/r5_rank_growth.py PANEL_DIR growth
```
