# 0004 — Weekly levels model: support/resistance, trendlines, significant highs/lows

**Context.** Alongside the technical scores (0003) a trade plan needs *price levels*: where the
stop goes, where the targets are, and whether the reward is worth the risk. Like the technical
model it must work for any stock with no per-stock tuning, so distances are scaled by the
stock's own weekly ATR(14) and trendlines are fitted in log price.

**Decision.** `solaraalpha.analysis.levels` works on weekly bars as of the last week supplied:

| Piece | Rule |
|---|---|
| Swing pivots | week's high beats the 3 weeks before and is ≥ the 3 after (lows mirrored); known only 3 weeks later (Williams-style fractal, widened for weekly bars) |
| S/R zones | pivot highs *and* lows clustered by price within 0.5 ATR; ≥ 2 pivots to count. Pooled because broken resistance becomes support (role reversal) |
| Trendlines | lines through pairs of the last 6 swing highs (upper) / lows (lower), in log price; dropped if any weekly close since the first anchor is > 0.25 ATR through, or if projected > 3 ATR from the close (no longer in play); most pivot touches wins, ties to the most recent anchors; projected to the coming week |
| Significant levels | prior 52-week and 5-year highs/lows; `blue_sky` = close above the 5-year high, `new_low` = below the 5-year low. These exclude the latest week, so a close above the 52w high is a breakout and that high becomes support |
| Coil | the last 4 weeks: high-to-low span in ATRs (`coil_atr`; ≤ 1.5 = `coiled`), least-squares lines through their highs and lows in log price, projected to the coming week; `apex_weeks` until they meet when converging. `box_atr` = nearest resistance − nearest support in ATRs |
| Breakout | +1 if this week closed above last week's nearest resistance, -1 if below its nearest support |

**Trade plan** (`levels_card`, for `side` +1 long / -1 short):
- stop: 0.5 ATR beyond the nearest level against the position, at least 1 ATR from the close;
  3 ATR (the technical model's chandelier distance) if there is no such level;
- targets: the next two levels in the position's favour — none in blue sky (long) / below the
  5-year low (short);
- `reward_risk` to the first target (`inf` if nothing is in the way), `risk_pct` for sizing.
- **coiled → breakout plan**: entry is a stop order (`trigger`) 0.1 ATR beyond the coil's line
  (upper for a long), or beyond the close if further, and beyond the nearest level ahead when
  that level lies inside the coil's range (break the box, not just the line). The stop rule is
  unchanged (beyond the box's far side), at least 1 ATR from the entry; targets are levels beyond
  the entry; `reward_risk` and `risk_pct` are measured from the entry. Not coiled: `trigger` is
  NaN and the plan enters at the close, as before.

**Scores** in [-1, +1]: `structure` (market view, + bullish) = mean of the two trendline slopes
(full score at 0.5%/week, as the technical trend pillar) and the close's place in the prior
52-week range (−1 at the low, +1 at the high; George & Hwang 2004 on 52-week-high proximity).
`setup` (side-specific) = log(reward:risk) / log 3, clipped: 3:1 → +1, 1:1 → 0, 1:3 → −1;
nothing in the way → +1.

**No look-ahead.** Pivots are only used once confirmed; 52w/5y extremes exclude the
current week; trendline validity uses closes up to the latest only. Tested by comparing pivots
from a truncated series.

**Consequences / open points.**
- We use a 5-year high rather than an all-time high, matching the 5 years of history we fetch
  (CLAUDE.md). If `trim_to_consistent` leaves less than 5 years, the window is shorter;
  `start_5y` says where it starts.
- The nearest level is often close, so `setup` is frequently negative inside a congested range.
  When the range is a tight coil the plan is judged from the breakout trigger instead, so the
  pipeline's `reward_risk` gate asks "is the breakout worth taking?" rather than "is buying
  inside the box worth it?" (it isn't, by construction). A coiled stock still needs the trigger
  to be hit: the card is a plan for a stop order, not a signal to buy at the close.
- Coil and trendline-distance parameters (4 weeks, 1.5 ATR, 3 ATR, 0.1 ATR) are placeholders like
  the rest. No measured-move targets yet: targets stay real levels.
- Parameters are textbook-ish defaults, not fitted; backtest before relying on them.
- Round numbers, volume-at-price and moving-average levels aren't included (the 10w/40w MAs are
  in the technical model).

## Amendment (#194, 2026-10-18): compression and breakout as context, not a gate

ADR 0022: the chart is context. A 5-year replay (every 8th week, 50 stocks) showed the coil test
(4-week span ≤ 1.5 ATR) in 36% of weeks, too often to mean compression, and `reward_risk >= 2`
in 5% of weeks (median uptrend R:R 0.34): the next resistance is not where a +15-30% move
stops. The card gains context fields, measured against the stock's **own past year** so they
stay generic and need no per-stock tuning:

| Field | Rule |
|---|---|
| `range_pctile` | log high/low range of the last 4 weeks; share of the prior 52 weeks' 4-week ranges that were tighter (0 = tightest of the year); NaN under 26 weeks to compare |
| `volume_ratio` | mean volume per session of the last 4 weeks / median of the 52 weeks before them (< 1 = drying up) |
| `compression` | [-1, +1], + = compressed: mean of `1 - 2 range_pctile` and `-log2(volume_ratio)` clipped (half the usual volume = +1) |
| `compressed` | `range_pctile` ≤ 0.2 and `volume_ratio` ≤ 0.8 (`COMPRESS_PCTILE`, `VOLUME_DRY`) |
| `wedge` | both trendlines intact and converging, meeting within 26 weeks (`WEDGE_APEX_WEEKS`); `wedge_apex_weeks`, `wedge_width_atr` |
| `new_high`, `from_high_52w` | close above the prior 52-week high; close / that high - 1 |
| `context` | the above (plus breakout, blue sky, a pending trigger) in one line for the agents |

All use only the frame given (point-in-time). In the same replay `compressed` fired in 6.9% of
stock-weeks, `wedge` 14.4%, `new_high` 2.6%, `new_low` 1.2%.

**Bands** (`toolkits.py`): no clause uses `reward_risk` (it stays on the card, with `setup`,
as information). `act: compressed == true or new_high == true`; `watch: new_low == false`, so
PASS only on a close below the 5-year low. Replayed: ACT 9.4%, WATCH 89.4%, PASS 1.2% (was ACT
2.3%, WATCH 40.6%, PASS 57.2%). `breakout` (+1 fired in 17% of weeks, often through a nearby
minor level) is context, not an ACT condition.

`coiled`, `coil_pending` and `trigger` keep their rule: they are the plan's entry mechanics (a
stop order above a tight coil), not the compression signal. The validator's pack carries the
fields as `chart_context`; `/validate` and `/research` read them as timing context. Thresholds
are placeholders like the rest.
