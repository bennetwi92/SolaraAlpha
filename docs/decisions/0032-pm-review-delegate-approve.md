# 0032 — The PM reviews the desk's cards, delegates follow-ups, then approves

**Status:** accepted (owner, 2026-10-21, issue #239). Built in slices; slices 1 (desk errors)
and 2 (the PM pack's "Must" cards) are built, 3 and 4 are to come. Numbered 0032 because
0031 (order execution, #240) merged first. Amends 0027 (the PM's phases and its fallback) and 0021 (a second
notification issue). Leaves 0024 unchanged.

**Context.** The PM (ADR 0027) runs once on Sunday, reads one pack and records one verdict,
and that verdict is the approval. It can't ask anyone anything: if it wants more on a name, all
it can do is leave the name unfunded and say why in the memo. On weekdays only the monitor runs.
The owner wants the desk to work like a real one. Over the weekend the specialists prepare
cards. The PM reviews them, delegates a follow-up when it needs clarity (more research on a
stock, another check) and approves the plan; only then is it executed. On each weekday a
cut-down version runs for the stocks we hold or want to hold. Some cards are per stock; others
are desk-wide (market, infrastructure, development, portfolio risk). The PM says what it needs;
nobody assumes it. The PM's requirements are on #239 and the owner signed them off as written.

**Decision.**

- **The loop.** The PM reviews, delegates (or not), re-reviews the answers and approves. Weekly:
  an explicit `approve` verdict after the last round, naming the final allocation and rotations
  and confirming it has read every delegated answer. `plan.py` runs only after that. Daily: one
  verdict per daily run that confirms the day's revisions, reverses or exits only when that cuts
  risk, or queries by delegating. It never funds and never loosens risk.
- **Allocations on Sunday only.** A daily review can approve, query or delegate, but it doesn't
  fund a new idea mid-week.
- **Monitor revisions keep applying as recorded** (ADR 0024 unchanged). They execute a plan the
  PM already passed, so its approval is implicit.
- **Delegation.** The PM may delegate to any skill or subagent, on any watchlist stock. A
  request names the task, the target, one answerable question, the decision it gates and a
  decision rule. The answer is a verdict through `record.py verdict` (downgrade-only, as now)
  that answers the question in its first line, or says it couldn't and why.
- **Cap.** At most 3 delegated tasks in 2 rounds on Sunday (at most 1 per stock); 1 task in 1
  round on a weekday, only after a material event; at most 20 a month. A toolkit re-run
  (`scan.py`) is free and outside the cap. The cap lives in `config/portfolio.toml`, next to
  `max_agent_tickers`, and the code enforces it. Over the cap, the PM decides without the extra
  work and says so in its memo.
- **Deadlines.** The Sunday approval is recorded by Monday 06:00 UK, leaving two hours before
  the open to place orders. A weekday delegated answer is due before the next open (06:00 UK).
- **No fallback.** A plan the PM hasn't approved is not drafted, and the idea waits for next
  Sunday. This replaces ADR 0027's fallback (`risk_per_trade` in rank order, approved by the
  pipeline). Open positions and approved pending entries carry on under the monitor. The PM
  refuses the whole plan when the book and Trading212 disagree on cash or holdings, or when the
  breaker has tripped.
- **Days with nothing held or pending.** The daily review is skipped.
- **Desk errors.** Every failure goes to a GitHub issue labelled `desk-errors` ("Solara desk
  errors"), which the owner subscribes to. Each one is a plain-words line: what failed, what it
  means, what happened automatically, and the command to re-run it. The serious ones (the PM
  didn't decide, a position went unreviewed, a whole workflow failed) also go at the top of the
  inbox. The PM's pack lists failures by item, so its memo can say what wasn't looked at.
- **The packs.** The PM's signed-off list (#239) is the spec: the "Must" cards come before the
  loop. The development card is one line per change (merged PR, `portfolio.toml` change, new
  ADR).

**Slices.** Each one is useful on its own.

1. **Desk errors.** The `desk-errors` issue, the inbox headline, and a workflow that reports a
   failed run. Built: see "As built".
2. **The PM pack's "Must" cards** (weekly): the validator verdict, the research note's text, the
   last close and its distance from the entry, scan coverage, the reconcile result, failures by
   item, the results calendar, the development card, and the kill criteria evaluated now.
3. **The loop on Sunday**: requests, the cap, the runner for delegated tasks, the `approve`
   verdict, and no fallback.
4. **The daily PM pack and review.**

**As built (slice 1).**

- `notify.desk_errors(conn, run_id, part)` lists the run's failures. `part="pipeline"` covers the
  scan's ERROR labels (actor `pipeline`). `part="agents"` covers the agents' ERROR labels and
  cards, plus the positions due a daily review that got no monitor card when monitors ran for
  that run. A failure whose stage also failed in the previous run of the same script is marked
  as a repeat. `scripts/notify.py --mode errors --part pipeline|agents --run N --channel
  github-errors` posts them to the `desk-errors` issue, and exits 3 when there are none.
- `scan.yml` and `daily.yml` post the pipeline part, the agents' plan job (weekly) and
  monitor-outcome job (daily) post the agents part, so nothing is posted twice.
- `desk-errors.yml` runs when the weekly pipeline, the daily check, the agents or the book
  workflow ends in failure. `notify.py --mode job-failed` posts the workflow, what that means and
  the run link to the `desk-errors` issue and to the inbox, without reading the DB, so a Turso
  outage is still reported.
- Inbox headline: when the PM recorded an ERROR (weekly), or a position went unreviewed or its
  monitor errored (daily), the inbox message opens with one line pointing to the `desk-errors`
  issue.

**As built (slice 2).** `pm.build_pack` carries the "Must" cards, all from the DB:

- **Per candidate:** `validator` (label, reasons, checks), `research.note_md`, and `last_close`
  (`date`, `close`, `vs_entry`).
- **Per open position:** `kill_criteria_now`. Each thesis condition, with `hit` from the latest
  daily run's `thesis_hit` triggers for machine ones, and None for agent ones.
- **Per pending entry:** `valid_until` / `days_left`, `placed`, `waits_for`, and `last_close`
  against the entry.
- **Desk-wide:**
  - `failures`: every ERROR of the run by item, with turns and minutes.
  - `coverage`: tickers scanned, toolkit errors by item, and `stale_prices`, a latest bar more
    than 5 days before the run.
  - `broker`: the latest daily run's Trading212 differences, with their commands.
  - `results_calendar`: events in the next 4 weeks across candidates, holdings and pending
    entries.
  - `changes`: one line per commit since the previous scan, tagged `rules` / `ADR` / `skill` /
    `bands` / `rank`. The PM job checks out the full history for it.

  A failed reconcile isn't recorded in the DB yet, so `broker` shows only the differences
  found. Its failure reaches the owner through the daily report.

**Consequences.** The owner gets a second issue, which stays silent while nothing fails. Until
slice 3 the PM still runs once on Sunday, and ADR 0027's fallback still applies.
