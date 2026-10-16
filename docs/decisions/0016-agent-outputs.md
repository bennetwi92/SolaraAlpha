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

*Added by #118.*
