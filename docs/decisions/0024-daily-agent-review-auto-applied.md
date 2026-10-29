# 0024: Daily agent review of every position and pending entry, changes applied

**Status:** accepted (owner, 2026-10-20); amended by ADR 0039 (the full review is weekly, on
Friday's run; midweek only on news, risk-cutting only). Amends 0015 (weekly review), 0017 (daily job) and
0018 (monitor agent).

**Context.** Stops came from rules: the levels map or an ATR stop at planning, and the weekly
review ratcheted a chandelier stop and drafted it. The monitor agent ran only on event triggers
(a filing, a gap, a thesis hit) on open positions and proposed changes the owner accepted. An
unfilled entry order was never re-evaluated: a profit warning on Wednesday left Sunday's
buy-stop in place. The owner wants stops set and re-evaluated by agents, every weekday, on
everything still at risk, and the agents' changes to take effect without waiting for an accept.

**Decision.**

- **Every weekday, every open position and every pending entry** (a draft / approved position
  with an unfilled entry order: draft, approved, placed or part filled) gets a `daily_review`
  trigger (agent kind) carrying the day's other triggers on it as context (`detail.fired`).
  Pending entries now get the same filing, headline, gap and thesis checks as open positions.
  The monitor queue is every reviewed position, event-triggered first, then open, then pending,
  capped by `max_agent_tickers`.
- **The monitor sets the stop by judgement**: where the thesis is invalidated, never lower for
  an open position. The pack carries the systematic stops (levels, technical ATR, chandelier)
  and the research verdict's trade levels (ADR 0023) as context only. The weekly review keeps
  the chandelier on the `review` card but no longer drafts it.
- **Actions.** Open: `hold` / `tighten_stop` / `take_partial` / `exit` / `revise_target`.
  Pending: `hold` / `amend_entry` (new entry `price` and / or `stop`, optionally `qty`; without a
  qty the size is re-computed to the drafted plan's risk budget) / `cancel`. Default is `hold`:
  churn is the failure mode.
- **Applied as recorded.** A revision whose actor is an agent (`agent:...`) is accepted by the
  book in the same transaction (`propose_revision` → `accept_revision`), so the event trail is
  unchanged: `revision_proposed` → the orders → `revision_accepted` (with `done`: order ids, old
  → new). One the book refuses (a lower stop, an open-position action on a pending entry, a stop
  above the entry) is recorded as `revision_declined` by the pipeline with the reason; nothing
  else changes. Pipeline proposals (pre-results trims) still wait for the owner. Since ADR 0025 the monitor
  can also `add` to an open position, within the portfolio limits.
- **The owner is told what to do at the broker** (Trading212): the daily inbox lists each
  applied change with the broker action ("stop raised 743.3p → 760p — amend the stop order in
  Trading212, then `book.py placed N`") and the undo command. A replaced order starts
  `approved`: it has to be placed again.
- **Undo**: `book.py undo REV_ID --reason` (`revision_undone`): the stop order back at the old
  stop, the old target, the entry order and stop as they were, or the exit / add order cancelled
  if unfilled. A cancelled pending entry cannot be reopened: re-plan it with `plan.py --replan`.

**Consequences.** More agent invocations: one per open position and pending entry per weekday,
up to the cap. The owner's role on stops moves from approving to supervising, with every change
auditable (card, evidence, old → new, undo). The rule that a stop is never lowered by an agent
stays; only the owner's undo restores a lower stop.
