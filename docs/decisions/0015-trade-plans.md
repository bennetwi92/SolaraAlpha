# 0015 — Trade plans, sizing, portfolio fit, weekly review and scaling

**Context.** The levels and technical cards give an entry, a stop, targets and reward:risk
(story map S4.1). An ACT stock needs those turned into a concrete, sized plan the owner can
approve and place (S4.2, S4.3), checked against the rest of the book (S4.4), reviewed weekly
(S8.2–S8.4) and, later, scaled (S4.9). The rules come from `config/portfolio.toml` (ADR-less:
placeholders until the owner confirms them, #105).

## Trade plans and sizing (#112)

`solaraalpha.portfolio.plan.make_plan` is a pure function of the cards, the rules and the
account's equity and cash. Choices:

- **Entry.** A coiled stock with its trigger above the close (`levels.coil_pending`) is a
  breakout entry at `levels.trigger`: a buy-**stop** order while `stop_entry_orders` is true
  (§6 Q2 default: assumed available at the broker), else a **limit** at the trigger with
  `wait_for_break`, which the daily job turns into "place it now" once a close is above the
  trigger. Anything else is a limit at `close × (1 + entry_limit_buffer)` (1%): orders are
  drafted Sunday for Monday and should fill on a flat open, not chase a gap.
- **Validity.** Cards' `date` (the Friday) + `entry_validity_days` (7): one week, §6 Q8.
- **Stop source.** `levels.stop` when finite and below the entry (a real level with a buffer),
  else `technical.stop` (3 ATR chandelier, ADR 0003); nothing below the entry is an error, not a
  plan. `stop_source` records which. A stop more than 15% below the entry is flagged
  (`risk_pct > 15%`), matching the technical ACT band.
- **Targets.** `levels.target` / `target_2`; a target below a raised (breakout) entry is
  skipped for the next one; no target = blue sky, reward:risk ∞.
- **Size.** Risk first: `floor(equity × risk_per_trade / risk_per_share)`, then the caps in
  this order, each named in `capped_by` when it binds: `max_position_pct × equity / entry`;
  `max_adv_pct × adv_gbp / entry` when the median daily traded value is known (`adv_gbp`, 20
  sessions of close × volume); `(cash − cash_buffer × equity) / entry`. Zero shares is a
  plan with the flag `size zero`, so the pack still shows the set-up.
- **Review dates and event flag.** `events.next_event − 2 days` when it falls inside the
  horizon; `event within N days` flagged when it is within `event_flag_days` (14) of the plan
  date (story map §4 A3: an event inside the first ~2 weeks is a sizing / timing flag).
- Prices stay in the quote currency (pence); money is GBP through `book.to_account`.

## Portfolio fit (#113)

`solaraalpha.portfolio.fit.fit` runs before plans become draft positions. Candidates are
taken in **rank order** (highest `rank_score` first, ties by ticker, unscored last) and each
accepted plan is added to the running totals before the next is checked, so when the book is
full the lowest-ranked candidates are the ones left out. Checks:

- **slots**: open + pending (draft / approved, not yet open) + accepted < `max_positions`;
- **sector**: sector value after adding ≤ `max_sector_pct` × equity (sector from
  `risk.sector_of`, "unknown" when not configured — unknown sectors still share one bucket,
  which is conservative);
- **heat**: (open risk + pending risk + accepted risk + plan risk) / equity ≤ `max_heat`;
- a plan sized to **zero shares** is rejected rather than drafted.

A breach marks the plan `rejected_by_fit` with the reason. Two things only **flag**:
26-week correlation of weekly returns with a holding above 0.7 (`correlated with X (0.82)`) and
the plan's own `event within N days` flag. **Rank score** (placeholder): mean of technical
`trend`, levels `setup`, relative_strength `rs` and fundamentals `growth`, ignoring NaN.

## Weekly review (#122)

*Added by #122.*

## Scaling (#128)

*Added by #128.*
