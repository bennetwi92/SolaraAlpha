# 0018 — Monitor agent and plan revisions: agents propose, the owner decides

**Context.** When a daily trigger needs judgement (a new filing, a gap of more than 2 ATR,
ADR 0017), someone has to read it against the position's entry conditions and thesis and say
whether the plan still holds. The owner wants that done by an agent, but "structural change"
has to be defined tightly or the agent churns, and the agent must never touch an order
(story map S7.5–S7.7, §6 Q4).

**Decision.**
- **Structural change** is exactly one of: a guidance or estimate change; a thesis kill
  criterion hit; a capital event (placing, bid, delisting); a regulatory or legal event; a
  weekly-trend break (stage 2 → 3: close below a falling 40w MA). Anything else is noise and
  gets `hold` with the reason "no structural change": no new information, or a move inside
  2 ATR. The definition lives in the skill and here.
- **Pack** (`scripts/monitor_pack.py --run DAILY_RUN_ID [--position ID]`,
  `solaraalpha.monitoring`): for each position with an agent trigger in that daily run,
  `pack.json` holds the position state, orders and plan; the **entry conditions** frozen at
  drafting ("then"); fresh `technical` (with the position's entry date and average cost, so
  the `position` card comes too), `levels` and `relative_strength` cards run through
  `pipeline.run_toolkit` on new `Inputs` ("now"); a per-field then → now diff (trend,
  momentum, entry, structure, reward_risk, rs, growth, quality); the thesis conditions, the
  `machine` ones evaluated now with `pipeline.Condition` (plus `close` from the fresh card);
  the triggers with the filing's full text; the latest research verdict and its evidence ids.
- **Skill** `.claude/skills/monitor/SKILL.md` (`/monitor --run N` or `--position ID`): decide
  whether the change is structural (cite the category and the sentence), propose **one**
  action (`hold`, `tighten_stop`, `take_partial`, `exit`, `revise_target`), label the urgency
  (ACT act now / WATCH keep an eye / PASS no change), write `verdict.json` with
  `stage = "monitor"` and the proposal in `checks` as strings, then
  `record.py revision FILE --position ID`.
- **Write-back** (`agents.record_revision`): the `monitor` card and evidence rows (ADR 0016,
  no `labels` row), and, unless the action is `hold`, a `revision_proposed` position event
  with payload `{action, old: {stop, target, qty}, new: {...}, reason, evidence_ids,
  trigger}`. Its `event_id` is the revision id.
- **Owner side** (`book.py revisions | accept REV_ID | decline REV_ID --reason`):
  `accept` adds `revision_accepted` and drafts the orders, all `approved` — `tighten_stop`
  replaces the live stop at the new price and adds `stop_raised` (**ratchet only**: a lower
  stop is refused); `take_partial` drafts an exit order for the qty (reason `discretionary`),
  and the fill path re-sizes the protective stop to what is left; `exit` drafts a market (or
  limit) exit with reason `thesis_broken`; `revise_target` records a `revised` event that
  the daily job and later revisions read. `decline` records `revision_declined` with the
  reason. A revision is decided once.
- The monitor **never** changes orders; `hold` leaves nothing for the owner to do.

**Consequences.** Every agent review leaves a card, its evidence and (when it proposed
something) an auditable old → new with the owner's decision, which the post-mortem (#134)
and the agent value-added measure (#144) can read. Running it unattended is #123;
notifications are #132; machine thesis conditions checked daily without an agent are #131.


**Amendment (ADR 0024): every weekday, changes applied.** The monitor reviews every open
position and pending entry each weekday, sets stops by judgement (systematic stops are
context), and also acts on pending entries (`amend_entry`, `cancel`). An agent's revision is
accepted by the book as it is recorded; the owner is notified with the broker action and can
reverse it with `book.py undo REV_ID --reason`. "The monitor never changes orders" no longer
holds: it changes them through the book's revision path, never directly.
