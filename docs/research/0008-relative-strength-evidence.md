# Research 0008: What the evidence says about the relative-strength toolkit

*2026-10-28. Follows research 0001 and ADR 0011 (amendment #210). Run under ADR 0029: Monday-open
entry, the `burst_10w` target, in-sample weeks only. Nothing in the pipeline is changed.*

## The answer in one paragraph

**The relative-strength score itself (`rs`) tells us nothing about which stocks will make a
+15% move or beat the market over the next 8 weeks.** What does carry information is *which
kind* of outperformance a stock is showing. A **short burst of outperformance without an
uptrend (`rs_spike`) is a reliable warning**: those stocks trailed the rest by about 4% over the
next 8 weeks and hit +15% less often than their volatility predicts, in every half-year tested.
**Sustained outperformance (`leadership`) leans the right way** (about +2–3% over 8 weeks) but
the evidence is weaker and rests partly on a few big winners. This backs the toolkit as it is
today: context and two flags for the agents, no weight in the rank, no gate.

## What was tested

The toolkit's own outputs, computed exactly as the pipeline does (`compare()`, `technical.scores()`,
the flag thresholds from `relative_strength.py`), every Friday for every watchlist stock:

| output | what it is |
|---|---|
| `rs` | the headline score: mean of market, industry and peer scores |
| `market_z4`, `market_z13`, `market_z26` | outperformance of the FTSE All-Share over 4, 13 and 26 weeks, scaled by the stock's tracking error |
| `leadership` | RS line at its 52-week high in an uptrend, or the 26-week leg at its cap |
| `rs_spike` | 4-week leg ≥ 0.5 while the stock is *not* in an uptrend |

These six were fixed before looking at results, and each was scored against two outcomes, so
there are 12 tests.

## Outcomes

- **`burst_10w`** (ADR 0029, the strategy's own target): buy at Monday's open. Did the stock reach
  +15% before falling 3 ATR, within 50 sessions? Volatile stocks hit +15% more often by nature, so
  each hit is compared with what a **volatility + liquidity model** expected for that stock that
  week. A signal is useful only if it beats that.
- **8-week excess return**: Monday's open to the close 8 weeks later, minus that week's average
  across the universe.

## Data

- **Universe:** today's 104-stock watchlist, with today's benchmarks and peers.
- **Usable sample:** 98 stocks had RS history (one EUR-quoted stock and a few recent listings
  dropped out). Only stock-weeks above the screen's liquidity floor are kept.
- **Size:** 12,453 stock-weeks over 149 Fridays, 2022-10-21 to 2025-08-22. That is roughly 19
  independent 8-week periods: much less than the row count suggests.
- **Holdout:** the locked holdout (signal Fridays 2025-10-24 to 2026-10-16) was never built,
  let alone read.

## How noise and luck were handled

- **Intervals:** every interval is a 90% moving-block bootstrap (16-week blocks, all stocks in a
  week resampled together), which allows for overlapping windows and for stocks moving together.
- **Verdicts:**
  - **real**: the interval excludes zero even after allowing for 12 tests (Bonferroni).
  - **suggestive**: the 90% interval excludes zero, but the adjusted one does not.
  - **no evidence**: neither excludes zero.
- **Stability:** the "half-years" column counts the half-years (of 7) whose average has the same
  sign as the overall average. ADR 0029 asks for 6 of 7.
- **Smallest detectable effect:** about IC 0.03–0.04, or about 3 pp on the burst rate. A smaller
  real effect would show up here as "no evidence".

## Results

### 1. The headline `rs` score has no edge

Rank IC is the average weekly correlation between the signal's rank and the outcome. Zero
means no information.

| signal | IC vs burst (beyond vol) | IC vs 8-week excess | verdict |
|---|---|---|---|
| `rs` | +0.007 [−0.02, +0.03] | +0.005 [−0.03, +0.03] | no evidence |
| `market_z4` (4 weeks) | +0.004 [−0.02, +0.02] | **−0.035** [−0.07, −0.02], 6/7 | short-term reversal: **real** |
| `market_z13` (13 weeks) | −0.005 [−0.03, +0.01] | +0.000 [−0.03, +0.02] | no evidence |
| `market_z26` (26 weeks) | +0.032 [+0.00, +0.05], 6/7 | +0.056 [+0.01, +0.09], 5/7 | suggestive |

The same picture in plain numbers. Ranking stocks by `rs` each week into fifths:

| `rs` fifth | hit +15% first | expected from volatility | 8-week excess |
|---|---|---|---|
| 1 (weakest) | 42.1% | 42.2% | −0.3% |
| 2 | 38.9% | 39.9% | −1.0% |
| 3 | 37.7% | 38.2% | −0.2% |
| 4 | 39.3% | 38.1% | +0.7% |
| 5 (strongest) | 39.2% | 38.7% | +0.8% |

Every fifth hits +15% at almost exactly the rate its volatility predicts.

The score averages a 4-week leg, which reverses, with a 26-week leg, which persists a little, so
the two cancel out. This matches research 0001.

### 2. `rs_spike` is a real warning

| group | share of weeks | hit +15% first | expected from vol | median 8-week excess |
|---|---|---|---|---|
| `rs_spike` | 12% | 37.8% | 39.5% | −4.4% |
| `leadership` | 15% | 42.8% | 37.9% | −0.3% |
| everything | 100% | 39.5% | 39.5% | 0 |

The gaps below are flagged weeks minus unflagged weeks, averaged week by week:

| flag | burst gap (pp, beyond vol) | 8-week excess gap | half-years | verdict |
|---|---|---|---|---|
| `rs_spike` | **−6.7** [−9.8, −4.3] | **−3.9%** [−5.0, −3.2] | 6/7 and 7/7 | **real** on both |
| `leadership` | +4.1 [−0.0, +7.5] | +2.8% [+0.6, +4.1] | 4/7 and 6/7 | suggestive on excess only |

**`rs_spike` is not a few stocks.** It fires on 91 different stocks, and the −3.9% gap is
unchanged with the three best stocks removed.

**`leadership` depends on a few big winners.**
- Its mean 8-week return is +5.8%, but its median excess is −0.3%: the typical leader does
  roughly as well as anything else.
- Removing the three biggest contributors cuts its gap from +2.8% to +1.9%.
- Its burst advantage (+4 to +5 pp) is about the size of the smallest effect this sample can
  detect.

## What this means for the toolkit

**The current design is supported.** Since #210, RS has no bands and no rank weight, and the
agents see the two flags. Each of those choices is borne out:

- **No rank weight, no gate:** correct. `rs` is no better than a coin flip after allowing for
  volatility.
- **The `rs_spike` warning** ("recent outperformance without a trend tends to give some back") is
  the toolkit's most useful output. It is now confirmed on twice the stocks of research 0001,
  with a tradeable entry.
- **`leadership` stays context.** It points the right way, but not strongly enough to earn a
  rank bonus.

## Suggestions for the owner (nothing changed here)

1. **Keep RS as context only.** Do not add `rs` back to the rank or the bands.
2. **Consider testing `rs_spike` as a rank penalty.** It is the only RS output that clears the
   detection bar. Before any change, it still needs:
   - a pre-registered test of the economics tier of ADR 0029 (top-3 and 5-slot replay, after
     costs);
   - a fresh out-of-sample check.

   The locked holdout is not a clean check for this flag: research 0001 already looked at those
   weeks. The clean check is the live scans from 2026-10-20 onwards.
3. **Optionally make the card lead with the flags.** The agents could read the flags and the
   26-week leg before the `rs` headline, since that number carries no information.

## Caveats

- **Survivorship.** Today's index members and peers: stocks that collapsed and left AIM are
  missing. This flatters laggards that rebounded, so it could exaggerate the `rs_spike`
  reversal. It cannot create the spike-versus-uptrend split on its own.
- **Short history.** About 19 independent 8-week periods, dominated by the 2023 small-cap
  sell-off and rebound.
- **Not trade P&L.** The burst target includes a stop, but the 8-week return has no stop or
  exit rule, and no costs are deducted.

## Reproduce

```
uv run python scripts/research/r8_relative_strength.py build PANEL_DIR   # ~10 min, one fetch per ticker
uv run python scripts/research/r8_relative_strength.py report PANEL_DIR  # the tables above
```

**A method note.** A first pass ranked the burst outcome *after* subtracting the volatility
expectation. That reorders the hits by their volatility, and because strong-RS stocks tend to
be less volatile, it produced a spurious "real" IC of about +0.06 for every leg. The residual is
now used unranked. The fifths table above confirms there is no burst effect in `rs`.
