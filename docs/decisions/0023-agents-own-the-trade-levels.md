# 0023 — The research agent owns the entry and the stop

**Context.** Plans took their entry and stop from rules: the levels map's breakout trigger or
the close plus a buffer, and the levels stop or an ATR stop (ADR 0015). The owner (2026-10-20):
stops (and entries) are defined by the agents, not by a systematic rule. That follows ADR 0022,
where the edge is the agents reading catalysts against the chart and the trade plan gives
"acceptable levels to enter at or below".

**Decision.**

- A research verdict carries `trade` (`agents.TradeLevels`): `entry_type` (`limit` = enter at
  or below `entry`; `stop` = a buy-stop at the breakout trigger `entry`), `stop` (below the
  entry: where the thesis or set-up is proved wrong, never a fixed % or ATR multiple),
  `stop_reason` (one sentence) and optional `valid_days` (1-30; default
  `rules.entry_validity_days`). It is **required on a research ACT**: `record.py` refuses an
  ACT without it. `record_verdict` saves it on the research card as `trade_*` fields;
  `agents.trade_levels(card)` reads it back.
- The planner sizes and fit-checks the agent's levels (`stop_source = "agent"`; the reason is
  kept on the plan as `stop_reason`). An ACT with no research trade levels (decided by the
  screen or the validator) is **not drafted**: it is reported as "no agent levels". Targets stay
  `entry x (1 + target_moves)` (ADR 0015 amendment).
- The levels and technical stops stay on the cards and in the packs as context for the agent.
  `make_plan` keeps the rule-based path for callers without agent levels; the planner no longer
  uses it. Scaling (`add_plan`) is unchanged.

**Consequences.** No research, no plan: a run without the agents (no Claude secret) drafts
nothing. Size absorbs a wide stop (risk per trade is fixed in money), so volatile set-ups are
not penalised. Re-evaluating the stop and unfilled entries after drafting is the monitor's job
(a separate change).
