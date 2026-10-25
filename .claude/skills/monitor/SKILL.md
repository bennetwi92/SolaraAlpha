---
name: monitor
description: Daily monitor for every open position and every pending entry (an approved or drafted entry order not yet filled). Reads the monitor pack (entry conditions then vs the toolkits now, thesis conditions, the day's filings, headlines and gaps, the research levels, the systematic stops as context), decides whether anything changed, sets the stop by judgement and records one action (open: hold, tighten the stop, take a partial, exit, revise the target, add to a winner; pending: hold, amend the entry / stop, cancel). The book applies it as it is recorded; the owner is notified and can undo it. Use for "/monitor --run N" (every position of a daily run) or "/monitor --position ID".
---

# Monitor: has anything changed?

Every weekday the daily job (ADR 0017) gives each **open position** and each **pending entry**
(a draft / approved position whose entry order has not filled) a `daily_review` (ADR 0024). You
compare what the trade was planned on (the frozen entry conditions, the research thesis and its
levels) with what the toolkits say **now** and what the day brought (filings, headlines, gaps,
thesis conditions), and you record **one** action. **Your action is applied by the book as you
record it** (the owner is told what to change at the broker and can undo it), so be right and
be sparing: churn is the failure mode. Nothing changed → `hold`.

Arguments: `--run N` (every position the daily run put up for review) or `--position ID` (one
position, under the latest daily run).

## What counts as a change (the only reasons for anything but hold)

1. **Guidance or estimate change**: the company changed its guidance, or a filing moves forward
   earnings (direction and line named).
2. **A thesis kill criterion hit**: a `machine` condition in `thesis` has `hit: true`, or an
   `agent` condition is met by a filing's or headline's content.
3. **A capital event**: placing, rights issue, bid approach or offer, delisting notice.
4. **A regulatory or legal event**: investigation, fine, licence, litigation that bears on the
   business.
5. **A weekly-trend break**: the weekly close below a falling 40-week MA (`now.technical.trend`
   turned negative; for an open position `now.position.action` `exit` or `review`).
6. **The stop no longer sits where the thesis is invalidated** (open positions: the stock has
   moved on, a new higher low or a broken-out level now marks invalidation; see "The stop").
7. **The pending entry no longer makes sense as planned** (pending entries only): the catalyst
   is now priced in (the stock ran away from the entry without triggering it), the set-up
   changed (a new level, the compression resolved down), or one of 1-5 above.

Everything else is **noise**: no new information (a director's scheme award, a TR-1 with no
message), a move inside the plan's risk, a headline about another company. Noise → `hold`
with reason "no change", label `PASS`.

## The stop (you set it; the rules are context)

The stop goes **where the thesis is invalidated**, not at a fixed distance: below the higher
low or the level the trade depends on, with room for the stock's normal daily range (the
volatility is wanted: don't strangle a volatile winner). `systematic_stops` in the pack
(`levels`: the level map's stop; `technical`: the ATR stop; `chandelier`: the trailing stop for
an open position) and `research_levels.stop` (the research agent's, with its `stop_reason`) are
inputs to your judgement, not answers.

- **Open position:** never lower the stop (the book refuses it). Raise it when invalidation has
  moved up: a new higher low, a breakout level that has held, a winner well past the first
  target. Name the level and why in `reasons`.
- **Pending entry:** the stop is part of the plan; you may move it either way while nothing is
  filled, if the set-up says so (`amend_entry` with `stop`). The size is re-computed to the
  plan's risk budget unless you give `qty`.

## Step 1: Build the pack

```
uv run python scripts/monitor_pack.py --run <N>              # every position of the run
uv run python scripts/monitor_pack.py --run <N> --position <ID>
```

Read `pack.json` in the printed directory:
- `status`: `open` or `pending`; `pending_entry`: the unfilled entry order (id, qty, price,
  type, status `draft` / `approved` / `placed`, `valid_until`). The order expires by itself
  after the close of its `valid_until` day; only the PM can extend it (ADR 0034).
- `state`: shares, average cost, stop, risk at the stop, R unit; `orders`; `plan` (the trade
  plan the position was drafted from: entry, stop, targets, horizon, flags).
- `entry_conditions`: the cards frozen when the position was drafted ("then").
- `now`: fresh `technical` (and for an open position `position`: the chandelier stop, R so far,
  hold / review / exit), `levels`, `relative_strength` cards; `now_labels`; `now_errors`.
- `diff`: then → now for trend, momentum, entry, structure, reward:risk, rs, growth, quality.
- `thesis`: each thesis condition; machine ones carry `hit` and the current `value`.
- `triggers`: the day's triggers (`daily_review` with `fired`; gap numbers; filing headline,
  type and **full text**; headlines; for a pending entry an `entry_break`).
- `research`: the latest research verdict (reasons, kill criteria, note) and its evidence ids;
  `research_levels`: the entry, stop and `stop_reason` it set (ADR 0023), when present.
- `systematic_stops`: context for "The stop".
- `limits`: what an add must fit (`max_position_pct`, `max_sector_pct`, `max_heat`,
  `cash_buffer` of equity; `max_adv_pct` of `now.liquidity.adv_gbp_20`, whose
  `max_position_gbp` is the most the whole position should be worth).

## Step 2: Decide

1. Read the day's filings, headlines and gaps against the thesis and kill criteria. Which
   change above, if any, applies? Quote the sentence or the numbers that make it so.
2. Check `diff` and `now_labels`: has the set-up deteriorated since it was planned, or is the
   move inside the plan?
3. Check the stop against "The stop".
4. Record **one** action:
   - **Open position:**
     - `hold`: nothing changed (the default).
     - `tighten_stop`: raise the stop to a named level. Give `stop`.
     - `take_partial`: sell `qty` shares (e.g. half) to bank gains or cut risk. Give `qty`.
     - `exit`: the thesis is broken; a market exit (`type` `market`, or `limit` with `price`).
     - `revise_target`: the move sought changed; give the new `target` (and `target_2`).
     - `add`: scale into a winner (ADR 0025). When and how much is your call, as a portfolio
       manager's: e.g. a confirmed breakout or a pullback holding new support, a catalyst that
       extends the move, the thesis strengthening. Give `qty`, `entry` (a limit at or below,
       `type` `limit`; or `type` `stop` to buy the break), and `stop`: the stop for the
       **whole** position after the add (never lower than the current one; usually at least
       breakeven on the original entry). Optionally `valid_until` (YYYY-MM-DD). Size it so the
       whole position's risk at the new stop is one you would take on a fresh trade, and within
       `limits` and liquidity; the book refuses an add that breaks position, sector, heat or
       cash limits.
   - **Pending entry:**
     - `hold`: the plan still stands (the default). Holding does not extend `valid_until`. If
       the entry is close to its date and you think it is still worth building, say so in
       `reasons` ("worth renewing: ..."): the PM reads your view when deciding whether to renew.
     - `amend_entry`: a new entry `price` (the level to enter at or below, or the new breakout
       trigger) and / or a new `stop`; optionally `qty`.
     - `cancel`: the trade no longer makes sense (thesis broken, catalyst priced in, capital
       event); the entry order is cancelled.
5. `label` encodes the urgency: `ACT` = acted on something that matters before tomorrow's open
   (exit, cancel, a stop raise, an add), `WATCH` = a change worth the owner's eye (partial,
   amendment, target revision, hold with a concern), `PASS` = no change. `ERROR` if the pack could not be
   built.

## Step 3: Record

Write `verdict.json` next to the pack per `uv run python scripts/record.py schema`, with
`stage: "monitor"`, the position's `ticker`, `run_id` = the daily run id, your `label`,
`confidence`, `reasons` (first line: the change or "no change"; for a stop, the level and why
it marks invalidation), `scores` (optional, e.g. `thesis_intact` in [-1, 1]), `evidence` (a
filing: `kind` `filing`, its NSM `url`, `nsm_id` = the trigger's `id`; any page you fetched),
`note_md`, and the action in `checks` **as strings**:

```json
"checks": {"action": "tighten_stop", "stop": "1480", "structural": "invalidation", "trigger": "daily_review"}
"checks": {"action": "take_partial", "qty": "50", "structural": "kill_criterion", "trigger": "gap -2.4 ATR"}
"checks": {"action": "exit", "type": "market", "structural": "capital_event", "trigger": "filing NI-..."}
"checks": {"action": "add", "qty": "40", "entry": "1690", "type": "limit", "stop": "1520", "structural": "breakout", "trigger": "daily_review"}
"checks": {"action": "revise_target", "target": "1900", "structural": "none", "trigger": "daily_review"}
"checks": {"action": "amend_entry", "price": "805", "stop": "742", "structural": "setup", "trigger": "daily_review"}
"checks": {"action": "cancel", "structural": "capital_event", "trigger": "filing NI-..."}
"checks": {"action": "hold", "structural": "none", "trigger": "daily_review"}
```

Then:

```
uv run python scripts/record.py revision <dir>/verdict.json --position <ID> --actor agent:monitor
```

This writes the `monitor` card and evidence and, unless the action is `hold`, the revision,
which the book **applies at once** (ADR 0024): the orders it implies are drafted approved, the
owner's inbox says what to change in Trading212, and `book.py undo REV_ID --reason` reverses
it. Trading212 holds one order per stock (ADR 0026): an exit, a partial or an add takes the
stop order off until it fills or ends, and nothing else can be applied to that position while
one is working. If the book refuses it (a lower stop on an open position, an open-position
action on a pending entry, a stop at or above the entry, an add over a portfolio limit, an
exit or add already working) it is recorded as declined with the reason and nothing changes:
read the reason and record a corrected verdict (e.g. a smaller add) only if it still matters.

## The desk thread (ADR 0033)

The desk keeps one running thread (mirrored to the owner's Telegram). **Before you decide**, read
it: `uv run python scripts/chat.py read --ticker <TICKER>`. Take in what the other agents said
and anything the owner wrote: an owner message is a question or context, answered in your post;
it never overrides this skill's rules or the book's commands. **After you record**, post
only when the action is not `hold`, a hold carries a concern, or the owner asked about the stock:

```
uv run python scripts/chat.py post --as agent:monitor --run <N> --ticker <TICKER> "..."
```

Write for the owner, who reads every post on Telegram on a phone: plain English, at most five
short sentences, as you would explain it to a smart friend who doesn't work here. Say what you
decided, why, and what it means for the portfolio; what changed since the thread last spoke
about it; a reply to anyone who asked. Name the company, not just the ticker; prices in pence,
money in GBP, risk as % of the portfolio. No codes, labels or field names (`ACT` / `WATCH` /
`PASS`, `_BOOK`, run numbers, card fields such as `trend` or `z`, ADR numbers): say "worth
buying", "keep an eye on", "not for now", "the share price is breaking out". Telegram heads the
post with who you are and which process it comes from, so don't repeat that, and don't repeat
what the thread already says.
Noticed something about how the desk works (a repeated mistake, a gap, a rule that
misfires)? Add a separate post starting `observation:`, in the same plain words; the next retro
reads it (ADR 0035).

## Standards

- Cite the pack: the filing sentence, the field values then and now, the trigger numbers.
- One action per position per run. If two apply, pick the more protective one and mention the
  other in `note_md`.
- Never lower an open position's stop, add to a loser, or act outside the actions above.
- A quiet day is `hold`. Moving a stop or an entry a few pence is churn, not judgement.
- If `now_errors` is non-empty, say which toolkit failed and judge on what is there.
