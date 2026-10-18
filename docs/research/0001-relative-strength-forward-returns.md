# Research 0001: Relative strength vs forward returns (issue #195)

*2026-10-18. Input to ADR 0022 and the RS bands (ADR 0011, 0014). Bands are not changed here.*

## Question

In the 2026-10-18 backtest proxy (every 8th week, 50 stocks, 5 years), `relative_strength` ACT
weeks averaged −0.5% over 8 weeks (excess −1.7%), against +4.1% for WATCH and +2.4% for PASS.
This note asks whether that result is real, what drives it, and how RS should be used in the
funnel. The screen ranks candidates and the agents make the final call (ADR 0022).

## Data and method

- **Universe:** today's FTSE AIM UK 50 (`config/watchlist.toml`) and today's
  `config/benchmarks.toml`. Every stock has a market, industry and peer comparison.
- **Every week, not every 8th.** `compare()` from `analysis/relative_strength.py` returns the
  whole weekly RS frame, and every column in it is a trailing rolling statistic. So each week's
  value uses only that week's and earlier closes, and the panel needs one fetch per ticker
  instead of a replay per week. Trend and momentum come from `technical.scores()` over the full
  history (also causal).
- **Panel:** 10,269 stock-weeks, 50 stocks, 209 weeks (2022-10-21 to 2026-10-16), after the 52-week
  tracking-error warm-up.
- **Check against the full replay:** all 1,228 rows of the bt8 replay match. The RS label agrees
  on 99.7% of them, and the largest RS gap is 0.14 (from `trim_to_consistent` on full versus
  sliced history). The bt8 replay itself reproduces the issue's numbers: ACT −0.5% (n 480),
  PASS +2.4% (575), WATCH +4.1% (123).
- **Outcomes:**
  - `ret_Nw`: forward total return over N = 4, 8 or 16 weeks.
  - `exc_Nw`: the same, minus FTSE All-Share.
  - `xs_Nw`: the same, minus that week's universe mean. This is the relevant measure for a
    ranking.
  - `big_Nw`: the share of weeks where `ret_Nw >= +15%`, the size of move the strategy wants.
- **Noise:** each 90% interval in brackets is a moving-block bootstrap over weeks. The blocks are
  16 weeks long and all stocks in a week are resampled together, which covers both overlapping
  windows and cross-sectional correlation. The rank IC is the mean weekly Spearman correlation
  between a signal and the forward return. There are only about 26 independent 8-week periods.
- Bands are as coded:
  - ACT: `rs > 0`.
  - WATCH: `rs >= -0.25` and (`rs_high` or `peer_top_half`).
  - PASS: everything else.
- Uptrend means `trend >= 0.25` and `momentum > 0`.

## Findings

### 1. The headline gap is mostly sampling noise; the bands have no edge either way

| band | n | ret 4w | ret 8w | xs 8w | ret 16w | xs 16w | P(8w ≥ +15%) |
|---|---|---|---|---|---|---|---|
| all | 10,269 | +0.8 | +1.5 [−0.2, +3.0] | 0 | +2.9 | 0 | 15.6% |
| ACT | 4,304 | +0.5 | +1.2 [−0.6, +2.9] | −0.2 [−1.0, +0.8] | +1.9 | −0.3 [−1.3, +1.1] | 13.4% [10.6, 16.2] |
| WATCH | 962 | +1.4 | +2.2 [+0.2, +4.1] | +0.3 [−0.7, +1.5] | +2.5 | −0.6 [−3.0, +1.5] | 18.4% [14.5, 22.4] |
| PASS | 5,003 | +0.9 | +1.7 [−0.3, +3.3] | +0.1 [−0.6, +0.7] | +3.7 | +0.3 [−0.6, +1.2] | 16.8% [12.9, 20.0] |

- **The gap shrinks.** Over every week, ACT minus PASS on `xs_8w` is −0.3 percentage points
  (pp), against about −2.9 pp in the replay.
- **It depends on the sample.** The panel can be cut into eight every-8th-week samples, one per
  starting week. Their ACT minus PASS gap ranges from −1.9 to +0.7 pp, and six of the eight are
  within ±0.3 pp.
- **The years disagree.** ACT minus PASS was −1.3 pp in 2022, −2.7 in 2023, +1.0 in 2024,
  +1.2 in 2025 and −0.4 in 2026.
- **ACT is not selective.** `rs > 0` labels 42% of all stock-weeks ACT.

Taken together, RS above zero is not a filter in either direction.

### 2. A small negative tilt: short-term reversal, not "RS doesn't work"

Mean weekly rank IC against forward return, with 90% bootstrap intervals:

| signal | 4w | 8w | 16w |
|---|---|---|---|
| `rs` | −0.054 [−0.080, −0.025] | −0.058 [−0.088, −0.023] | −0.032 [−0.070, +0.010] |
| `market` | −0.044 | −0.049 | −0.028 |
| `industry` | −0.058 | −0.062 | −0.030 |
| `peers` | −0.050 | −0.050 | −0.028 |
| market z, 4w leg | −0.049 [−0.073, −0.022] | −0.058 [−0.089, −0.028] | −0.034 |
| market z, 13w leg | −0.034 | −0.042 | −0.033 |
| market z, 26w leg | −0.019 [−0.051, +0.020] | −0.020 [−0.061, +0.028] | +0.001 [−0.051, +0.062] |
| own past 4w return | −0.044 | −0.053 | −0.033 |
| change in rs over 4w (`d4`) | −0.047 | −0.050 [−0.072, −0.028] | −0.029 |
| `trend` | −0.008 | −0.013 | −0.004 |

- **The negative IC is small but consistent.** It is negative in 4 of 5 years at 8 weeks (2025 is
  flat at −0.005). Within each stock, RS and the next 8-week return are negatively correlated
  for 41 of 50 stocks (82%). Part of that is a known finite-sample bias, but it points the same
  way.
- **The short legs drive it.** The 4-week leg and the stock's own past 4-week return correlate
  0.91 and have the same IC. The 26-week leg is flat. ADR 0011 already flagged this: single-stock
  1-month returns reverse (Jegadeesh 1990). For AIM small-caps this is short-term mean
  reversion: names that have just outperformed give some of it back over the next 1–2 months.
- **The horizon matters.** The tilt fades by 16 weeks and does not reach significance.
- **Volatility scaling is not the cause.** Within each volatility tercile the IC of `rs` stays
  negative (low −0.024, mid −0.082, high −0.043).

### 3. No single component drives it

- **The components overlap.** Market and industry scores correlate 0.88 (Spearman), and peers
  correlate 0.68 with each. All three have similar ICs (−0.044 to −0.062).
- **The peer extreme is the clearest bucket.** Stocks with peer score above 0.5 had `xs_8w` of
  −1.2% [−2.4, −0.1] (n 1,922).
- **Sector leadership on its own is weak.** This is the industry index versus the market over 13
  weeks, a property of the sector rather than the stock. Its 8-week IC is +0.029 [−0.012, +0.058],
  a different sign from stock RS but not significant. A stock that beats a leading sector did no
  better (`xs_8w` −0.9%).

### 4. Uptrend versus no uptrend: the reversal sits outside uptrends

| subset | n | IC of `rs`, 8w |
|---|---|---|
| uptrend | 3,144 | −0.006 [−0.059, +0.043] |
| no uptrend | 7,125 | −0.093 [−0.120, −0.057] |

| group | n | stocks | ret 8w | xs 8w | xs 16w | P(16w ≥ +15%) |
|---|---|---|---|---|---|---|
| `rs > 0.5`, no uptrend | 257 | 47 | +0.3 | −2.6 [−4.4, +0.1] | −2.0 | 23.6% |
| ACT, uptrend | 2,567 | 50 | +0.8 | +0.0 [−1.2, +1.2] | −0.7 | 19.9% |
| `rs_high` (RS line at 52w high) | 443 | 40 | +3.0 | +1.7 [−1.0, +4.5] | +2.6 [−0.5, +6.2] | 25.0% |
| market z, 26w leg, pinned at +1 | 959 | 46 | +2.9 [+0.5, +5.1] | +1.5 [−0.8, +3.8] | +2.1 [−1.4, +5.4] | 25.5% |
| `rs <= −0.5` (laggards) | 2,557 | 50 | +2.3 | +0.8 [−0.3, +1.9] | +2.4 [+0.4, +4.8] | 24.3% |
| all | 10,269 | 50 | +1.5 | 0 | 0 | 21.2% |

- **A spike in RS without an uptrend tends to reverse.** In uptrends RS is neutral.
- **Sustained leadership has the right sign.** Both `rs_high` (97% of its weeks are in uptrends)
  and a pinned 26-week market leg lean positive at 16 weeks. They also produce +15% moves more
  often than the base rate. Neither result is significant, and the samples are thin (443 and 959
  stock-weeks).
- **Rising RS is not a buy signal.** `d4` has a negative IC. The best change bucket was
  "`rs <= 0` and rising": `xs_16w` +1.2% [−0.7, +2.9]. That is a rebound, not momentum.

### 5. The ACT averages are driven by a few stocks and years

- **A few names hold the ACT mean up.** GMET (+20.5% mean `xs_8w`), IQE (+22.9%) and RKH (+11.0%)
  supply most of the positive total. Without those three, the ACT mean `xs_8w` falls from −0.2%
  to −1.5%.
- **A few names pull it down.** Without the worst five (EKF, CAML, MIDW, TFW, DOTD), it rises
  to +0.35%.
- **The median ACT week underperforms.** Median `xs_8w` is −1.8% for ACT and −1.1% for PASS, and
  44% of ACT weeks beat the universe against 46% of PASS weeks. A typical ACT week trails
  slightly, and the mean is lifted by a few big trending winners.
- **Big moves are rarer under ACT.** In the low and mid volatility terciles, 9–10% of ACT weeks
  reach +15% in 8 weeks, against 12–16% for PASS and WATCH. In the high tercile it is 21% against
  23–25%. Losers rebound more often than leaders extend.
- **Time:** see the year split in finding 1. The 2023 drawdown and rebound decides most of the
  sign.

## Caveats

- **Survivorship (large).** These are today's AIM 50 constituents, and peers and benchmarks are
  today's too. A stock that fell and was later demoted or delisted is missing, so laggards look
  better than they were (they had to recover to be in today's index). Leaders that later
  collapsed are also missing. This bias probably explains much of the laggard rebound in
  findings 4 and 5. The sign of the RS effect is less certain than the magnitudes suggest. A
  point-in-time constituent list would be needed to settle it.
- **Overlap and sample size.** 10,269 rows sounds large, but there are about 26 independent
  8-week periods and 50 names. Every interval above is wide, and only the IC of `rs` and of its
  short legs clearly excludes zero.
- **Short history.** Four years with warm-up, including one strong regime (the 2023 small-cap
  sell-off and rebound).
- **Price indices.** The benchmarks are price indices while the stock is total return, a small
  bias in the stock's favour (ADR 0011). It does not affect `xs_Nw`.
- **Forward returns are close to close.** They include no stop, target or exit rule, so they are
  not trade P&L.

## What RS is useful for in the funnel

RS is not a gate and not an improving-RS trigger. In this sample it has two jobs:

1. **Leadership context.** A sustained leader (RS line at its 52-week high, or the 26-week leg at
   its cap) in an uptrend is the "continuing an uptrend / new highs" set-up in ADR 0022. It has
   the right sign and more big moves. It is worth showing to the agents and at most giving a small
   ranking bonus.
2. **Overextension warning.** A short-term RS spike (4-week leg high, or a large `d4`) without an
   uptrend tends to give back gains over 4–8 weeks. That is a question for the agents: is there a
   catalyst behind the move that the market has not priced in? It should not count as a reason
   to rank a stock higher.

The comparison set (market, industry, peers) matters less than the horizon. Industry versus
market is useful sector context for the macro agent, not a stock score.

## Recommendation for the owner

**RS is context with a leadership flag. It should be neither an improving-RS signal nor a gate.**
Concretely (for the owner to accept; nothing is changed here):

- **Drop `rs > 0` from the ACT clauses,** or give `rs` zero weight in the combined ranking score.
  It labels 42% of weeks ACT, has no edge, and its rank IC is slightly negative.
- **If RS enters the ranking at all, use the slow, leadership part only.** For example, add a
  small bonus for `rs_high == true and trend >= 0.25`. Or score only the 26-week leg (the 13- and
  26-week legs if smoothing is wanted) and remove the 4-week leg, as ADR 0011 anticipated. This
  would need the card to expose the per-horizon z values, a small code change for later.
- **Add an agent-facing flag, not a filter.** Flag "short-term RS spike outside an uptrend"
  (4-week market z ≥ 0.5 with trend < 0.25, or momentum ≤ 0), worded as "recent outperformance
  without a trend; check the catalyst and whether it is priced in".
- **Keep `rs_high`, `peer_top_half`, the 13-week excess figures and the industry-versus-market
  view on the card for the agents.**
- **Do not build an improving-RS signal.** Its rank IC is about −0.05 at 4–8 weeks.
- **Re-test after about 6 months of live scans, or once point-in-time constituents are
  available.** The leadership results are suggestive (+1.5 to +2.6% at 16 weeks) but not
  significant, and survivorship may flatter the laggard rebound.

*Reproduction: the panel was built with throwaway scratch scripts that call `compare()`,
`technical.scores()` and `fetch_daily` directly (one fetch per ticker, about 30 s on 8 cores). It
was checked against the bt8 replay as described under Data and method. No repo code was
changed.*
