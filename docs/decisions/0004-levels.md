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
| Trendlines | lines through pairs of the last 6 swing highs (upper) / lows (lower), in log price; dropped if any weekly close since the first anchor is > 0.25 ATR through; most pivot touches wins, ties to the most recent anchors; projected to the coming week |
| Significant levels | prior 52-week and 5-year highs/lows; `blue_sky` = close above the 5-year high, `new_low` = below the 5-year low. These exclude the latest week, so a close above the 52w high is a breakout and that high becomes support |
| Breakout | +1 if this week closed above last week's nearest resistance, -1 if below its nearest support |

**Trade plan** (`levels_card`, for `side` +1 long / -1 short):
- stop: 0.5 ATR beyond the nearest level against the position, at least 1 ATR from the close;
  3 ATR (the technical model's chandelier distance) if there is no such level;
- targets: the next two levels in the position's favour — none in blue sky (long) / below the
  5-year low (short);
- `reward_risk` to the first target (`inf` if nothing is in the way), `risk_pct` for sizing.

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
  That is intended: it says "wait for the breakout"; after one, `breakout` flips and the next
  level becomes the target.
- Parameters are textbook-ish defaults, not fitted; backtest before relying on them.
- Round numbers, volume-at-price and moving-average levels aren't included (the 10w/40w MAs are
  in the technical model).
