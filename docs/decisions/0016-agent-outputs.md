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
- The skill `.claude/skills/validate/SKILL.md` answers a fixed seven-item checklist (data
  issues; one-off spikes vs filings; overextension in ATRs; stale or just-broken levels;
  relative strength carried by one comparison; stale, one-off or currency-mismatched
  fundamentals; agreement between toolkits), each `ok` / `concern` / `unknown` with a line of
  pack evidence, and marks each toolkit *warranted* or *not warranted*, all in `checks`.
- It records a `validate` verdict through `record.py` (downgrade only). A pack that cannot
  be built gives an ERROR verdict with the failure as the reason.

Chart images and disagreement statistics are R2 (#126).
