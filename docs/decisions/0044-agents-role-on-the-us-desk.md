# 0044 — The agents' role on the US desk

**Status:** accepted in part (owner, 2026-11-23). Answers ADR 0041's question 2 and ADR 0043's
open item. Nothing is built yet; the agent chain is redesigned when the US pipeline is.

**Context.** On AIM the agents' edge was judging a catalyst before the market priced it in (ADR
0022). That edge does not apply to the Nasdaq 100: these stocks react to news within minutes.
ADR 0043 asks for a live narrative confirmed by strong technicals. This ADR records what the
agents do in that trade.

**Decided (owner, 2026-11-23).**

- **Technicals lead.** In stocks this heavily traded, news is in the price within minutes, so
  the chart is the main evidence and the first thing checked. A story the chart has not
  confirmed is not a trade. A story rarely breaks without the price reacting, so the daily review
  of open positions reads the chart first: the stop, a failed breakout, momentum and volume.
- **Be careful what we buy into.** The agents' main job before entry is to make sure the
  set-up is worth buying: the story is sound and still building, not exhausted or reversing.
  The move is not already overextended, and no event inside the holding period (results above
  all) makes the trade a gamble. The agents can only downgrade a screen pick (ADR 0016).
- **The agents set the trade.** Research sets the entry, the stop where the set-up is wrong,
  the partial level and any targets, from support and resistance, ATR and R (ADR 0023 carries
  over). The PM sizes it inside its 1–3% band.
- **An agent can bring in a stock on its story alone**, even if the screen did not rank it. It
  still needs the technical confirmation of ADR 0043 to be ACT. Without that it is WATCH, and
  the daily run watches it for confirmation.
- **Claude spend is not a constraint** (the owner has a Max subscription). The agent caps
  (`max_agent_tickers`, `--max-turns`) are set for coverage, not cost.
- **Telegram is ended.** The owner does not need it. The Telegram leg of the desk thread and
  the PM's replies to the owner there (`desk-reply.yml`, `/desk`) go when the US desk is built.

**Proposed (to confirm with the owner).** A lean chain in place of the AIM one, which had
about ten agents (validate → macro → research → PM; monitor → risk → PM-daily; execute, retro,
desk):

- weekday after the US close: screen (code) → **research** (story, the "careful what we buy"
  check, the trade levels; stories it nominates go here too) → **PM** (allocation) → plan (code);
- daily: **monitor** for every open position and pending entry (chart first);
- risk as a code report, with the PM's daily review only on a breach;
- validate, macro, retro and the risk agent dropped; execution stays code plus the `/execute`
  agent only if the US session needs it.

The shadow book (ADR 0028) keeps recording what plain rules would have done, so the agents are
measured against them. On AIM that was never found out.

**Open (owner).** With Telegram gone and the inbox unread, where should the owner see the
day's trades, P&L and risk? The options are the dashboard, the GitHub inbox issue, or email.
