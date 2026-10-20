# 0025: Adds to a winner are the monitor agent's call, within the portfolio limits

**Status:** accepted (owner, 2026-10-20). Supersedes the rule-based add of 0015 "Scaling"
(#128); amends 0024 (monitor actions).

**Context.** Adds were rule-based: the weekly review proposed one when a position was ≥ 1R in
profit with a confirmed breakout or a pullback to support, sized at `add_fraction` of the
initial shares, at most `max_adds`, and the owner accepted it. Setting the portfolio limits,
the owner chose to leave scaling to the agents: "they are supposed to be hedge fund
professionals so they know when to scale in and by how much".

**Decision.**

- The **monitor agent** (`/monitor`, daily) may record `add` on an open position: `qty`,
  `entry` (`type` limit or stop), the `stop` for the whole position after the add (ratchet
  only) and optionally `valid_until`. When and how much is its judgement; the skill asks that
  the whole position's risk at the new stop be one it would take on a fresh trade, and never
  to add to a loser.
- It is **applied as recorded** like the other monitor actions (ADR 0024): the add order is
  drafted approved, the stop raised, the inbox tells the owner, `book.py undo` cancels it.
- **Hard limits stay in the book.** `book.add_breaches` refuses an add that would break
  `max_position_pct`, `max_sector_pct`, `max_heat` or the `cash_buffer` (equity = cash + open
  positions at cost; the position valued at the add's entry); the refusal is recorded as
  declined with the reason. `max_adv_pct` is the agent's to check against the liquidity card,
  now in the monitor pack with the `limits`.
- **Removed:** `plan.add_plan`, the weekly review's add proposal, and the rules
  `add_fraction`, `max_adds`, `add_min_r`.

**Consequences.** Adds can happen any weekday, not only on Sunday's review, and are sized by
the set-up rather than a fixed fraction. The portfolio limits are the only mechanical guard.
