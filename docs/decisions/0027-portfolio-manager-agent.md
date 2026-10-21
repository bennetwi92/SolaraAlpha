# 0027: A portfolio manager agent owns the book's allocation

**Status:** accepted (owner, 2026-10-20). Amends 0015 (sizing, fit order), 0019 (the weekly
agent chain), 0023 (the planner sizes the research levels) and 0026 (who approves the plans).

**Context.** Every agent saw one stock: the validator and research one ticker each, the
monitor one position each. The only cross-book step was `fit`, arithmetic in screen-rank order
(technical trend, compression, growth), so the research conviction never reached the
allocation: every trade took the same 2% risk, the last slot went to the better chart, not
the better catalyst, and when the book was full a new idea was rejected without being compared
with the weakest holding. Nobody owned idle cash, concentration in a shared driver, results
clustering, the regime, or the agents' failure rate (17 of 70 invocations in the first week).
The owner asked for a portfolio manager to whom the buck stops, aiming at maximal returns, and
decided (2026-10-20): the PM decides allocations with no owner approval; risk per trade is a
1-3% band by conviction; the PM may exit a position to fund a better idea; 5 positions stays
(concentrated, confirmed against 0022's "5-10"); deliver Sunday construction first, then a
daily book review, then a feedback loop.

**Decision (Sunday book construction).**

- A **PM agent** (`/pm`, `.claude/skills/pm/SKILL.md`) runs once per weekly run after
  `/research` and before `plan.py` (`agents.yml` job `pm`). It reads the PM pack
  (`scripts/pm_pack.py`, `solaraalpha.pm.build_pack`, DB only): the research-ACT candidates
  with their verdicts, trade levels and what each risk in the band buys after the caps, the
  research WATCH names, every open position (P&L, R, stop, next event, latest monitor and review
  view) and pending entry, money, heat, exposure, drawdown and breaker, the regime, the macro
  notes, 26-week correlations, the limits and what the rules alone would fund.
- It records **one `pm` verdict per run** (pseudo-ticker `_BOOK`, card under `model = pm`)
  through `record.py verdict` (`agents.record_book`), carrying `allocation` (ticker,
  `risk_pct`, reason, in priority order; empty = fund nothing) and `rotations` (position id,
  reason, the ticker it funds). `record_book` refuses an allocated ticker that is not final-ACT
  with research trade levels (the PM cannot upgrade or set levels), a `risk_pct` outside
  `risk_per_trade_range`, and a rotation of a position that is not open or pending. The memo to
  the owner is the verdict's `note_md`.
- **Rotations are applied as recorded** (ADR 0024 path, actor `agent:pm`): an open position
  gets an exit at market, a pending entry is cancelled; the owner can `book.py undo`.
- **The planner follows the PM** (`plan_run`): only the allocated tickers are planned, each
  sized at the PM's `risk_pct` (`risk_per_trade` stays the size without a PM), fit-checked in
  the PM's order; rotated-out open positions no longer take a slot, heat or sector room and
  their value counts as cash. An ACT the PM left out is reported "not funded by the PM".
- **No owner approval** (as ADR 0026): the PM's plans are approved by `agent:pm`, and a draft
  from the same run still waiting (from before ADR 0026) is approved as drafted when the PM
  funds it. The owner places the orders in Trading212 from the inbox steps (no broker API) and
  supervises through the memo, `reject` and `undo`.
- **Fallback**: no PM verdict, or an ERROR (`record.py error _BOOK --stage pm`), and the
  planner sizes at `risk_per_trade` in screen-rank order and the pipeline approves (ADR 0026);
  the weekly pack and the inbox say the PM did not decide.
- **Hard limits stay in code**: slots, sector cap, heat, cash buffer, liquidity cap and the
  circuit breaker are enforced by `fit` / `make_plan` whatever the PM writes.
- The weekly pack opens with the **PM memo**; the inbox opens with the PM's stance and gives the
  memo right after the Trading212 steps.

**Later (not in this change).** B: a daily book review after the monitors (all the day's
revisions together, competing adds, regime de-risking, results clustering, agent failures; it
may reverse a monitor's change but never loosen risk). C: a monthly feedback loop from the
post-mortems and `learn.py agents-value` proposing rule changes to the owner.

**Consequences.** One more agent invocation a week. Sizing now varies 1-3% by conviction;
heat (10%) still caps the total. The owner's role on entries moves from approving to placing
and supervising, as 0024 did for stops.

## Amendment: results-day gap risk in the pack (#219, #214 F5)

The stop is not the worst case when results fall inside the trade: a profit warning gaps through
it. Each candidate in the PM pack carries `event_gap`: the next scheduled event, whether it falls
in the first half of the horizon (`horizon_weeks` max / 2), the stock's past results-day session
returns from the events card (`event_moves`, `event_worst_move`, `event_p90_move`, Financial
Results filings over the 5-year filing history, notices of results dropped, one per session) and
what the worst and p90 move would cost at each risk of the band. Information only: `make_plan`,
`fit` and `portfolio.toml` are unchanged. A hard cap is a rule change that waits for the
shadow-book test (#223) and the owner's yes. Under 4 past events is flagged `small_sample`.
