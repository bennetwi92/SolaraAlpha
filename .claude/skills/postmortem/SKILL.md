---
name: postmortem
description: Post-mortem of one closed trade from the record. Reads the post-mortem pack (entry conditions, plan, reviews, revisions, triggers, research and monitor verdicts, close summary, weekly bars with the post-exit weeks labelled hindsight), names the set-up type, what worked, what failed and which signal was right or wrong, and records a postmortem verdict (ACT = repeat this set-up, WATCH = mixed, PASS = avoid) with setup_quality and execution_quality scores through record.py. Rule changes are proposals only. Use for "/postmortem POSITION_ID".
---

# Post-mortem: what did this trade teach?

You review **one closed position**. The fund runs long trades over ~2-16 weeks (ADR 0015);
the trade was entered on a set of frozen conditions and a plan, reviewed weekly, and closed
with a tagged exit reason. Your job is a short, honest note: what the entry said, what then
happened, and which signal deserved the credit or the blame. The verdict feeds the scorecard
(`scripts/learn.py`), so it must be comparable across trades. **You change nothing**: no
orders, no rules; rule suggestions are proposals the owner may adopt.

Argument: `POSITION_ID` (a closed position). Optional `--run N` (the results run to record
under; default: the position's `opened_from_run_id`).

## Step 1: Build the pack

```
uv run python scripts/postmortem_pack.py <POSITION_ID>
```

Read `pack.json` in the printed directory:
- `close_summary`: exit reason, gross / costs / dividends / net P&L, R-multiple, holding
  weeks, entry and exit averages, MFE / MAE when known.
- `plan` and `setup`: the trade plan the position was drafted from; `setup.type` is derived
  from it (`pullback`: limit near the close; `breakout`: stop entry at a coil trigger
  (`coil_pending`, `wait_for_break`); `blue_sky`: no target overhead). Correct it if the plan
  says otherwise and explain.
- `entry_conditions`: the cards frozen at the draft (technical, levels, relative strength,
  fundamentals, liquidity, events).
- `reviews`: every weekly review card (R so far, stop ratchet, deterioration, action).
- `revisions`: what the monitor proposed and the owner accepted or declined.
- `triggers`: daily triggers on the position (stops touched, targets, gaps, filings,
  dividends).
- `research`, `monitor`, `evidence`: the agents' verdicts over the trade and their sources.
- `events`, `orders`, `fills`: the full booking trail.
- `bars`: weekly bars from the entry week - 8 to the exit week + 4. `hindsight: true` bars
  completed **after** the close: use them only to say what happened next, never to judge a
  decision taken before them. `as_seen: false` marks pre-exit weeks with no snapshot at the
  time (levels may differ slightly from what was seen).

## Step 2: Judge

Answer, each in a few lines, with the pack's numbers:

1. **Set-up**: type, and whether the entry conditions were as good as the plan assumed
   (which fields were strong, which were marginal).
2. **What worked**: the signals and rules that were right (the stop that held, the review that
   tightened in time, the research kill criterion that fired).
3. **What failed**: the signals and rules that were wrong or late (entry too far from the
   stop, a target never reachable, a thesis condition that should have been a kill criterion,
   an exit on noise). Separate bad outcome from bad decision: a stop-out on a sound set-up is
   not a failed rule.
4. **Which signal was right or wrong**: for technical, levels, relative strength,
   fundamentals, events / research: right, wrong or silent, with the field and value.
5. **Rule suggestions**: concrete, testable changes (a threshold, a filter, a review rule),
   each marked as a proposal. Do not change anything.

Scores in `scores`, both in [-1, 1]:
- `setup_quality`: how good the set-up was **at entry** given what was knowable then.
- `execution_quality`: how well the plan was run (fills near the plan, stop ratchet, exit
  discipline), independent of the P&L.

`label`: `ACT` = repeat this set-up as it is; `WATCH` = mixed, repeat with the changes you
propose; `PASS` = avoid this set-up. `ERROR` only if the pack could not be built.

## Step 3: Record

Write `verdict.json` next to the pack per `uv run python scripts/record.py schema`, with
`stage: "postmortem"`, the `ticker`, `run_id` = `opened_from_run_id` from the pack (any
integer if it is null: `--new-run` replaces it), `label`, `confidence`, `reasons` (first line:
the set-up type and the exit reason), `scores`, `evidence` (anything you fetched; the pack's
own sources need no new rows), `note_md` with the five sections above, and in `checks` as
strings:

```json
"checks": {"position_id": "12", "setup": "breakout", "exit_reason": "stop", "r_multiple": "-1.05", "signal_right": "levels,relative_strength", "signal_wrong": "technical"}
```

Then record it (`--position` ties the card to the position so the runner does not repeat it):

```
uv run python scripts/record.py verdict <dir>/verdict.json --position <POSITION_ID> --actor agent:postmortem
uv run python scripts/record.py verdict <dir>/verdict.json --position <POSITION_ID> --actor agent:postmortem --new-run   # booked by hand (no run)
```

## Standards

- Every claim cites a pack field and its value; quote the review or filing line when you lean
  on it.
- Hindsight bars never justify a verdict on a decision taken before them.
- One trade, one note, under 400 words of `note_md`. Proposals are proposals.
