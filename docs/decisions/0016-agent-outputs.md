# 0016 — Agent outputs and evidence: one schema, one door, downgrade only

**Context.** The validator (#118), the research strategist (#119), the daily monitor (#121)
and the post-mortem (#134) agents all end with the same kind of output: a label with a
confidence and reasons, some scores, kill criteria and the sources they relied on. The owner
wants it written to the DB on the same run so it reads like any other stage, every source
logged with its discretionary impact (story map S2.3, S3.4, S3.5), and the `/events` notes to
stop vanishing with the container (#84). Agents should not write SQL.

**Decision.**
- **Schema** (`solaraalpha.agents`, pydantic, `extra="forbid"`): `Verdict` = `stage`
  (validate / research / events / monitor / postmortem), `ticker`, `run_id`, `label`
  (ACT / WATCH / PASS / ERROR), `confidence` [0, 1], `reasons`, `scores` (each in [-1, 1]
  unless the key ends `_risk`), `kill_criteria` (`condition`, `kind` machine / agent,
  `evidence_index`), `evidence` (`Evidence`: url, title, publisher, published, retrieved, kind
  filing / news / web / data, section, summary, impact_score, impact_confidence, driver,
  horizon_weeks, nsm_id), `note_md` (the full human note) and `checks` (e.g. the validator's
  checklist answers). `record.py schema` prints the JSON Schema for the skills.
- **One door**: the agent writes `verdict.json` and runs `scripts/record.py verdict FILE
  --actor agent:<name>`, which validates it and, in one transaction, writes (1) a `labels` row
  for `events` / `validate` / `research`, (2) a card under `model = stage` in `results` with
  label, confidence, every score, `reasons` (joined), `kill_criteria` (JSON, with
  `evidence_index` resolved to the saved `evidence_id`), `checks` (JSON), `note_md`, actor and
  evidence ids — dated with the run's **screen week** for that ticker so it joins the toolkit
  cards on `date` (else the run's start date) — and (3) one `evidence` row per source.
  Append-only: a stage already recorded for a run and ticker is refused, never replaced.
- **Agents only downgrade** (§6 Q4 default): a `validate` label may be no better than the
  run's `screen` label, a `research` label no better than `validate` (else `screen`), ordered
  ACT > WATCH > PASS. ERROR is always allowed, so a failure is recorded, never silent. `events`
  is informational (ACT unless it errors); `monitor` and `postmortem` write cards, not labels.
- **Reuse**: `record.py prior TICKER [--stage] [--limit]` prints the ticker's latest verdicts
  (label, scores, reasons, date, run), their evidence and every filing already interpreted
  (`nsm_id`), so a run starts from the previous one instead of re-interpreting filings (#84).
  `store.latest("events")` / `store.latest("research")` give the latest `catalyst_score` per
  ticker for the scorecard.

**Consequences.** The toolkits and the agents share one results table and one labels table,
so the planner's final label (#114) is just the latest stage present. Evidence is deduplicated
by the agent (via `prior` and `nsm_id`), not by a constraint: the same URL may appear in two
runs when it was relied on twice. A later `record.py revision` (#121) and `record.py error`
(#123) reuse the same models.

## Validator (#118)

The validator is a second, independent opinion of the **same data** the toolkits saw. It
does no research and never sees `events`, `research` or other agents' notes, so a wrong
toolkit label is caught by a different reader of the same evidence, not by someone with more
evidence. Mechanics:

- `scripts/validate_pack.py TICKER --run N` (`--run N --all` for every screen-ACT ticker)
  writes `pack.json` from the DB alone (`solaraalpha.validation`): the independent cards
  (technical, levels, relative_strength, fundamentals, position), the steps and screen label,
  the price snapshot **as seen at the run** (`check_prices` issues, the span kept by
  `trim_to_consistent`, 104 weekly bars with 10w / 40w MAs, 60 daily bars, the 5 largest
  weekly moves) and the fundamentals **known at the card date** (statutory lags). The one live
  fetch is the recent filings' headlines (13 weeks, material only, published before the run);
  a failure becomes `"unavailable"`.
- The skill `.claude/skills/validate/SKILL.md` answers a fixed eight-item checklist (data
  issues; one-off spikes vs filings; overextension in ATRs; stale or just-broken levels;
  relative strength carried by one comparison; stale, one-off or currency-mismatched
  fundamentals; agreement between toolkits; the picture agrees with the numbers), each `ok` /
  `concern` / `unknown` with a line of pack evidence, and marks each toolkit *warranted* or
  *not warranted*, all in `checks`.
- **Chart pack (#126)**: `validate_pack.py` also writes `chart.png` (`analysis/charts.py`,
  matplotlib Agg, now a main dependency): the last 104 weekly candles, 10w / 40w MAs, the levels
  card's support, resistance, trendlines, coil lines and trigger, the stop and targets, over
  volume. The agent reads the image; the `picture_agrees` item records what it saw.
- **Disagreement log (#126)**: a validate label worse than the screen label is a disagreement.
  `validation.disagreements(labels, cards)` lists them with the toolkits the validator marked
  `not warranted` and the checklist items it flagged `concern` (parsed from the card's
  `checks`, which is why the skill must use those words); `scripts/validator_stats.py` counts
  them by toolkit and by item and gives the rate per run, so a rule that is often overruled
  surfaces (A11).
- It records a `validate` verdict through `record.py` (downgrade only). A pack that cannot
  be built gives an ERROR verdict with the failure as the reason.

Chart images and disagreement statistics are R2 (#126).

## Post-mortem (#134)

Every closed trade is a labelled example. `scripts/postmortem_pack.py POSITION_ID` builds the
pack from the record alone (`solaraalpha.postmortem`): the close summary, the plan and the
set-up type derived from it (`pullback` / `breakout` / `blue_sky`, as in the trade journal),
the frozen entry conditions, every weekly `review` card and plan revision over the trade, the
daily triggers, the `research` and `monitor` verdicts (and their evidence) from runs started
between two weeks before the draft and the close, the fills and events, and weekly bars from
the entry week - 8 to the exit week + 4. Bars come from the price snapshots **as seen at the
close** (`load_prices(as_seen=closed_at)`); a week that completed after the close is labelled
`hindsight` and a pre-exit week with no snapshot at the time `as_seen: false`, so the agent
can be held to what was knowable.

The `/postmortem` skill writes a `postmortem` verdict through the same door: `stage =
"postmortem"`, no `labels` row (it is not a pipeline stage), a card under `model =
"postmortem"` with `setup_quality` and `execution_quality` in [-1, 1], the label meaning
ACT = repeat this set-up, WATCH = mixed, PASS = avoid, and the set-up / exit reason / signals
right and wrong in `checks`. `record.py verdict --position ID` keeps the position id on the
card; it is recorded under the position's `opened_from_run_id` (or `--new-run` for a position
booked by hand). Rule suggestions in the note are proposals only. The unattended runner
(`agents.yml`, daily mode) queues the positions closed since the previous daily run that have
no post-mortem card (`record.py queue --stage postmortem`).

## Macro notes (#140)

The same headwind should be researched once a week, not once per stock. `/macro --run N`
(`.claude/skills/macro/SKILL.md`) groups the run's screen-ACT tickers by `sector`
(`config/benchmarks.toml`; `record.py queue --stage macro` lists the groups, `members` their
tickers) and by a driver shared across sectors, writes one note per group with web search and
logged evidence, and records it through the same door as a `macro` verdict under the
**pseudo-ticker `_SECTOR:<name>`** (`agents.SECTOR_PREFIX`). `macro` is a card-only stage: no
`labels` row, no downgrade rule (there is no screen label for a sector), one note per group
per run. `/research` reads the run's macro note for the stock's sector with `record.py prior
"_SECTOR:<name>" --stage macro` and references it in sections 5 and 6 instead of
re-researching. The unattended runner (ADR 0019) runs the `macro` job after `validate` and
before `research`, with usage and ERROR recording like the other stages.
