# Research 0006: Event-gap sizing as a shadow-book row: pre-registration (issue #223, #214 R3)

*Pre-registered 2026-10-20. Follows ADRs 0028 and 0029. **Not run**, and no code is changed: the
row is specified here for the owner and PM to accept before anything records it. Its result
goes to the owner as a proposed `portfolio.toml` change (owner, #214: "the cap waits for the
shadow-book row").*

## Question

Sizing from the stop treats the stop as the worst case. A results-day gap on AIM goes through
any stop. At 2% risk and an 8% stop the position hits the 25% cap, and a −40% gap then costs 10%
of equity (#214 F5, PM review). Would capping the size by the stock's past results-day moves,
when results fall early in the trade, have cut the tail loss without costing much return?

## The row (a new baseline id, per ADR 0028)

- **id `event_gap`**, written by `plan.py` beside `flat2_rank`:
  - **Names, entry, stop, trail:** the same as `flat2_rank` (every research ACT with trade
    levels, in screen-rank order; the agent's entry and stop; `trail = agent`).
  - **Risk:** 2%, as `flat2_rank`.
  - **Size:** the one thing that differs. It is `min(stop-based size, gap-capped size)` when
    the PM pack's `event_gap.in_first_half` is true (the next scheduled event within 8 weeks,
    half of `horizon_weeks[1]` = 16). Otherwise it is the stop-based size, so the two rows are
    identical.
  - **Gap-capped size:** the position value at which a move of `g` costs 2 × 2% = 4% of equity,
    so value = 0.04 × equity / g.
  - **`g`:** the stock's `event_worst_move` (absolute) when `event_moves ≥ 4` (`MIN_EVENT_MOVES`).
    Otherwise it is the universe p90 of results-day |moves|, frozen when the row is first
    written. The PM asked for the maximum with a minimum count, and a universe prior below it.
  - **Same constraints:** the same caps (25% position, liquidity, cash) and the same `fit`
    against its own shadow holdings (ADR 0028).
- **Fits the existing schema:** the row's `risk_pct` is 2% × `s` (the size ratio), so no new
  column is needed. Its `inputs` JSON stores `g`, its source (stock or prior), `n`,
  `weeks_to_event` and both sizes, so the cap's effect can be read without re-running anything.

## Measures (paired: same names, same weeks, same fills)

The two arms differ only in size on the trades where the cap binds. So the P&L difference is
`Σ (s_i − 1) × PnL_i`, where `s_i` ≤ 1 is the size ratio. What is uncertain is whether the
capped trades' P&L is worse than the rest.

1. **Tail (primary):** the share of trades losing more than 2× planned risk (more than 4% of
   equity), and the 95th-percentile trade loss in % of equity, in each arm.
2. **Cost:** the expectancy per trade in % of equity, `event_gap` minus `flat2_rank`, with a
   paired 90% bootstrap interval (resampling trades by week).
3. **Book:** the maximum drawdown of each arm's 5-slot replay.

## Pass bar (to propose the cap to the owner)

- **Tail:** p95 trade loss in `event_gap` at least 30% smaller than in `flat2_rank`.
- **Cost:** the lower bound of the 90% interval of the expectancy difference above −0.10% of
  equity per trade (non-inferiority).
- **Minimum n before reading it:** 30 trades where the cap binds. Below that, the row is shown
  as "accumulating", never acted on (ADR 0028).

## Power and how long it takes

- **Live only:** the row is paired and the arms differ only on the capped trades. So the
  binding constraint is the count of capped trades, not total trades.
  - At 1–2 research ACTs a week, about half have results within 8 weeks (approximate, from
    3–4 results-category filings per issuer a year). The cap binds on a part of those.
  - So 30 capped trades takes roughly **6–12 months** of live runs.
- **Faster, and recommended:** once `analysis/simulate.py` exists (#210 phase 1), replay the
  same rule on history. Use the simulator's breakout entry and 2-ATR stop as a stand-in for the
  agent levels, and in-sample weeks only. That gives hundreds of capped trades. It is a test of
  the sizing rule, not of the agents, and the live row stays the check.
- **MDE for the cost test (approximate):**
  - The SD of trade P&L is about 1.5R; at 2% risk that is about 3% of equity per trade.
  - The difference on a capped trade is about `(1 − s) × PnL`. With a median `s` of about 0.6,
    its SD is about 1.2% of equity.
  - At n = 30 capped trades, the SE of the mean difference is about 0.22% of equity. The 80%
    power MDE at a 90% interval is then about 2.49 × 0.22 ≈ **0.55% of equity per capped
    trade**. Only a large cost is detectable live.
  - The tail measure is the more useful one at this n.

## What a pass or a fail would mean

- **Pass:** a proposal to the owner: `portfolio.toml` keys for the cap (on / off, the 2× risk
  multiple, the minimum count of 4, the frozen prior), and `make_plan` sizing to match. The
  owner decides.
- **Fail on tail:** the cap does not cut the tail enough to matter. It stays information in the
  PM pack, as now.
- **Fail on cost:** the cap gives away return. The PM keeps applying the pack number through
  conviction, as today.
