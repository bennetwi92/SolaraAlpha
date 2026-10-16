---
name: monitor
description: Daily monitor for open positions with a fired trigger (a new filing, a large gap). Reads the monitor pack (entry conditions then vs the toolkits now, thesis conditions, the filing text), decides whether something structural changed, and proposes one action (hold, tighten the stop, take a partial, exit, revise the target) as a plan revision the owner accepts or declines. Never changes orders. Use for "/monitor --run N" (every triggered position of a daily run) or "/monitor --position ID".
---

# Monitor: structural change or noise?

You review **open positions** when the daily job (ADR 0017) fired an agent trigger: a new
regulatory filing or a gap of more than 2 ATR. You compare what the position was entered on
(the frozen entry conditions and the research thesis) with what the toolkits say **now** and
what the filing says, and you **propose** one action. **You never create, change or cancel an
order**: the owner does, via `book.py accept|decline`. Churn is the failure mode: a position
that is merely moving around inside its risk is `hold`.

Arguments: `--run N` (every position with an agent trigger in daily run N) or `--position ID`
(one position, under the latest daily run).

## Structural change (the only reasons to propose anything but hold)

A change is structural when it is one of:

1. **Guidance or estimate change**: the company changed its guidance, or the filing moves
   forward earnings (direction and line named).
2. **A thesis kill criterion hit**: a `machine` condition in `thesis` has `hit: true`, or an
   `agent` condition is met by the filing's content.
3. **A capital event**: placing, rights issue, bid approach or offer, delisting notice.
4. **A regulatory or legal event**: investigation, fine, licence, litigation that bears on
   the business.
5. **A weekly-trend break**: stage 2 → 3, the weekly close below a falling 40-week MA
   (`now.technical.trend` turned negative with `now.position.action` `exit` or `review`).

Everything else is **noise**: no new information (a director's scheme award, a TR-1 with no
message), or a move inside 2 ATR. Noise → `hold` with reason "no structural change", label
`PASS`.

## Step 1: Build the pack

```
uv run python scripts/monitor_pack.py --run <N>              # every triggered position
uv run python scripts/monitor_pack.py --run <N> --position <ID>
```

Read `pack.json` in the printed directory:
- `state`: shares, average cost, stop, risk at the stop, R unit; `orders`; `plan` (the trade
  plan the position was drafted from: entry, stop, targets, horizon, flags).
- `entry_conditions`: the cards frozen when the position was drafted ("then").
- `now`: fresh `technical` (and `position`: the chandelier stop, R so far, hold / review /
  exit), `levels`, `relative_strength` cards; `now_labels` their band labels; `now_errors`.
- `diff`: then → now for trend, momentum, entry, structure, reward:risk, rs, growth, quality.
- `thesis`: each thesis condition; machine ones carry `hit` and the current `value`.
- `triggers`: what fired (gap numbers; filing headline, type and **full text**).
- `research`: the latest research verdict (reasons, kill criteria, note) and its evidence ids.

## Step 2: Decide

1. Read the filing text (or the gap) against the thesis and kill criteria. Which structural
   category, if any, applies? Quote the sentence that makes it so.
2. Check `diff` and `now_labels`: has the set-up deteriorated since entry (which fields, by how
   much), or is the move inside the plan?
3. Propose **one** action:
   - `hold`: no structural change (the default).
   - `tighten_stop`: raise the stop to a named level (never lower it; at least the current
     chandelier `now.position.stop` when it is higher than `state.stop`). Give `stop`.
   - `take_partial`: sell `qty` shares (e.g. half) to bank gains or cut risk. Give `qty`.
   - `exit`: the thesis is broken; a market exit order (`type` `market`, or `limit` with
     `price`).
   - `revise_target`: the levels moved; give the new `target` (and `target_2`).
4. `label` encodes the urgency: `ACT` = act now (exit, tighten before tomorrow's open),
   `WATCH` = keep an eye (partial, target revision, hold with a concern), `PASS` = no change.
   `ERROR` if the pack could not be built.

## Step 3: Record

Write `verdict.json` next to the pack per `uv run python scripts/record.py schema`, with
`stage: "monitor"`, the position's `ticker`, `run_id` = the daily run id, your `label`,
`confidence`, `reasons` (first line: the structural category or "no structural change"),
`scores` (optional, e.g. `thesis_intact` in [-1, 1]), `evidence` (the filing: `kind`
`filing`, its NSM `url`, `nsm_id` = the trigger's `id`; any page you fetched), `note_md`, and
the proposal in `checks` **as strings**:

```json
"checks": {"action": "tighten_stop", "stop": "1480", "structural": "guidance", "trigger": "filing NI-000012345678"}
"checks": {"action": "take_partial", "qty": "50", "structural": "kill_criterion", "trigger": "gap -2.4 ATR"}
"checks": {"action": "exit", "type": "market", "structural": "capital_event", "trigger": "filing NI-..."}
"checks": {"action": "revise_target", "target": "1900", "structural": "none", "trigger": "gap +2.6 ATR"}
"checks": {"action": "hold", "structural": "none", "trigger": "filing NI-..."}
```

Then:

```
uv run python scripts/record.py revision <dir>/verdict.json --position <ID> --actor agent:monitor
```

This writes the `monitor` card and evidence and, unless the action is `hold`, a
`revision_proposed` event the owner sees with `book.py revisions` and settles with
`book.py accept <REV_ID>` (which drafts the orders) or `book.py decline <REV_ID> --reason`.

## Standards

- Cite the pack: the filing sentence, the field values then and now, the trigger numbers.
- One action per position per run. If two apply, pick the more protective one and mention the
  other in `note_md`.
- Never propose lowering a stop, adding to a loser, or anything outside the five actions.
- If `now_errors` is non-empty, say which toolkit failed and judge on what is there.
