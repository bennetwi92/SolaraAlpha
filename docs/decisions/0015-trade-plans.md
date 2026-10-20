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

Every Sunday the scan (`scripts/scan.py`, after the candidate pipeline) reviews **every open
position**, in the watchlist or not (`solaraalpha.portfolio.review`):

- technical runs with `entry_date` = the first fill date and `entry_price` = the average cost
  (giving the `position` card: chandelier stop, R so far, hold / review / exit), plus levels,
  relative strength and fundamentals, on the same `Inputs` as the screen when the ticker is in
  the watchlist. Steps are saved with stage `review`; no `labels` row comes from this pass.
- `ReviewCard` (saved under model `review`): `weeks_held`, `r_multiple`, `old_stop`,
  `new_stop` = max(old stop, chandelier) — **ratchet only, never lower** — `stop_moved`,
  `deteriorated` (fields among trend, momentum, structure, rs, growth, quality whose score fell
  by ≥ 0.25 since entry, a placeholder), `past_horizon` (weeks held > `horizon_weeks[1]`),
  `action` and `reason`.
- **Action**: `exit` if the position card says exit (a weekly close breached the trailing
  stop) or past the horizon with trend < 0 or momentum ≤ 0 — the S8.4 rule "beyond 16 weeks,
  hold only while trend ≥ 0, momentum > 0 and the trailing stop holds"; `review` if anything
  deteriorated or the position card says review; else `hold`.
- With `--save`, a moved stop is drafted: the live stop order is replaced at `new_stop`
  (approved, actor `pipeline`) and a `stop_raised` event recorded, so the owner sees it in
  `tickets`. An `exit` action is **reported, never drafted** (pipeline and agents propose; the
  owner decides); the pack row carries the `book.py exit` command. The weekly pack's "Open
  positions" section shows the review card.

## Scaling (#128)

The strategy holds "while the stock keeps climbing", so adding to a working position is
planned and rule-based; **never add to a loser**. `portfolio.plan.add_plan(state, review_card,
levels, technical, rules, adds)` proposes an add only when **all** hold:

- the position is at least `add_min_r` R in profit (the review card's `r_multiple`);
- `adds` so far (filled or live `add` orders) < `max_adds`;
- a **confirmed breakout** (`levels.breakout == 1`, closed above last week's nearest
  resistance) **or** a **pullback to new support** (close within 1 ATR above `levels.support`
  with `technical.trend` ≥ 0.25).

The add is a limit at `close x (1 + entry_limit_buffer)`, valid `entry_validity_days`. Its
stop is the position's current stop **raised to at least breakeven on the original entry**
(`state.entry_avg`), and the whole position moves to that stop. Size = `add_fraction x` the
initial shares, capped so that the open risk of the whole position at the new stop stays ≤ the
original risk (`state.r_unit`); with an equity figure the `max_position_pct` cap applies too
(the weekly review passes none: the owner's accept, and the portfolio view, are the fit check).

The weekly review runs `add_plan` for every `hold` position and, with `--save`, records the
add as a `revision_proposed` event (action `add`, actor `pipeline`), once per position while
one is pending; the report and the weekly pack show it. The owner decides: `book.py accept`
drafts the `add` order (approved) and raises the stop for the whole position (`stop_raised`,
ratchet only); `decline` drops it. A fill on the add increases the shares and re-sizes the
protective stop (`book.fill` → `protect`, as for entry fills).

`add_fraction = 0.5`, `max_adds = 2`, `add_min_r = 1.0` are placeholders in
`config/portfolio.toml`.

## Events before results (#129)

Results inside the next two weeks are the biggest scheduled risk to an open position, so what
to do is **decided in advance** in `config/portfolio.toml`: `event_policy` = `flag` (default,
placeholder: report only), `hold` (record the decision), `trim` (sell `event_trim_fraction` of
the shares) or `exit`. The weekly review runs the `events` toolkit for open positions too; when
its `next_event` falls within `event_flag_days` of the review date the `review` card carries
`event_due`, `event_status` (`confirmed` when every past year's filing landed within two days
of the same date, else `estimated`: the pack projects from the issuer's own history, ADR 0006)
and `event_action` (the policy). `trim` and `exit` are proposed as `revision_proposed` events
(`take_partial` / `exit`, actor `pipeline`, trigger `event_due <date>`) the owner accepts or
declines; nothing is ever drafted without the owner. The daily job fires `event_due` once the
event is two sessions away and the policy is not `hold` (owner action). The weekly pack shows
the event and the policy under each open position. §6 Q9 stays open: `flag` takes no action
until the owner sets a policy.

## Drawdown circuit breaker (#143)

After a run of losses the safest move is to stop adding risk until the book recovers or the
owner reviews it. `config/portfolio.toml` carries `circuit_breaker = false` (off until the
owner turns it on, §6 Q1) and `max_drawdown_new_entries = 0.10` (placeholder). `fit` takes the
book's current drawdown (equity vs its running high, from the stored price snapshots:
`weekly_pack.drawdown_from_store` → `performance.current_drawdown`); with the breaker on and
the drawdown beyond the limit it rejects **every** new plan with `circuit breaker: drawdown X% >
Y%`. Open positions, their stops and reviews are untouched. The weekly pack's portfolio section
and the daily report show the drawdown vs the limit and whether the breaker is off, on or
active, so the owner sees it coming before it trips.

## Amendment (2026-10-20): targets are the move sought; no wide-stop flag

Owner decision, following ADR 0022. Plan targets are `entry x (1 + target_moves)` (`[0.15, 0.30]`
in `config/portfolio.toml`), the +15-30% move the strategy seeks, not the levels map's next
resistance (which sat a fraction above the entry and made R:R meaningless). The next level is
kept on the plan as `next_level` for context (NaN = blue sky; the journal's set-up type reads
it). A target hit is a review trigger, not a sell order: the trailing stop exits. The
`risk_pct > 15%` flag is gone: volatility is wanted. Risk per trade is 2% (owner).
