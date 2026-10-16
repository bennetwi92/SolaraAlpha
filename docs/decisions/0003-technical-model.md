# 0003 — Weekly technical model: trend / momentum / entry scores + position assessment

**Context.** We trade weekly and need a technical view of any single stock that slots into a
wider scorecard alongside other (stock-specific) factors, for both new entries and open
positions. Unlike the other models, this one must be reusable across stocks, so nothing in it
is tuned to one name; volatility-dependent pieces are scaled by the stock's own ATR / vol.

**Decision.** `solaraalpha.analysis.technical` computes standard indicators on weekly bars with
the conventional weekly settings, and scores three pillars in [-1, +1] (equal-weighted
sub-signals; NaN until every input has warmed up, ~52 weeks):

| Pillar | Sub-signals | Source of convention |
|---|---|---|
| Trend (+ = up) | close vs 40w SMA; 10w vs 40w SMA; 40w SMA slope over 4w (full score at 0.5%/wk); ×0.5 if ADX(14) < 20 | 10w/40w ≈ 50/200-day MAs; Weinstein stage analysis (MA slope + price vs MA); Wilder ADX 20 threshold |
| Momentum (+ = up) | RSI(14) scaled 30→-1, 50→0, 70→+1; sign of MACD(12,26,9) histogram; 12-1 momentum ÷ its vol (Sharpe 1 = full) | Wilder; Appel; Jegadeesh & Titman (1993) 12-1 momentum |
| Entry (+ = good timing for `side`) | within 1 ATR of 10w SMA = +1, falling to -1 at 3 ATR; RSI room before 70 (overbought, mirrored for shorts); MACD histogram turning toward `side` | pullback-in-trend entry; Elder's impulse (histogram direction) |

Trend and momentum are market views (positive = bullish). Entry is side-specific, because a
good entry is directional; the combined decision (direction, size) is made at scorecard level.

**Positions.** `assess_position` uses a chandelier stop (LeBeau: 3 × ATR) anchored at entry —
the best high since entry minus 3 ATR(14), ratcheting only in the position's favour, never
looser than the initial stop (entry − 3 × ATR as of the week before entry). Breaches are judged
on weekly closes against the previous week's stop. Action: `exit` if breached, `review` if trend
or momentum is against the position, else `hold`.

**No look-ahead.** All windows are trailing; the row for Friday *t* is known at that close and is
actionable next week. Tested by recomputing on a truncated series. Levels (MAs, ATR, stops)
use raw close/high/low; momentum returns use `adj_close`.

**Consequences / open points.**
- Parameters are textbook defaults, not fitted. Backtesting them (and the equal weights) is a
  later step; don't tune per stock.
- 3 × weekly ATR is wide (~20% for Jet2). It's the stop for a weekly-horizon trade, and the
  portfolio phase should size positions off `risk_pct` rather than tighten the stop.
- Stops are only checked on weekly closes; a resting stop order would trigger intra-week.
- Volume isn't used yet (weekly volume on AIM names is noisy); candidates later: OBV, volume vs
  10w average on breakouts.
