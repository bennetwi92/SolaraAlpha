# 0006 — Events & catalysts: programmatic facts, agent interpretation

**Context.** Over a 2-16 week long, the big moves cluster around events. These include
results and trading updates (beats, misses, guidance changes, post-announcement drift),
capital returns, M&A, director dealings and changes in major holdings. Two jobs follow:
*anticipate* scheduled events (when, what matters, how big a gap), and *interpret* catalysts
once they have happened (what changed against expectations, and does it move estimates?).
Collecting the facts is mechanical. Interpreting them needs reading and judgement, which a
fixed-rule scorer would do badly.

**Decision.** Split the work into two layers.

1. **Programmatic (runs anywhere, e.g. GitHub Actions).** `scripts/events.py` writes an
   *event pack* (`pack.json` plus each announcement's full text) from:
   - **FCA National Storage Mechanism**, the official UK archive of regulated filings. It is
     the primary source for company events, keyed by the issuer's **LEI** so renames don't
     break history. The LEI comes from GLEIF, via the ISIN from the LSE instrument endpoint;
     `--lei` skips that lookup.
   - **Market reaction** (`analysis.events.reactions`), measured in the first session that
     could trade the filing. A filing at or after 16:30 London counts from the next
     session, so there is no look-ahead. The pack records the gap, the session return, the
     z-score against the prior 60-session volatility, the volume ratio, the 5-session
     run-up and the drift since.
   - **Expected events** (`analysis.events.expected_events`), projected from the issuer's
     *own* history. Results/trading-update/AGM filings are projected forward by whole
     years. Projections within 21 days of each other form one event, which needs at least
     2 supporting years. An event is dropped once this cycle's filing has appeared, and
     stays while overdue. The typical session move on past occurrences gives the expected
     gap size. No per-stock calendar or taxonomy is used: generic across stocks.
   - **Press headlines** (Google News RSS), used for discovery only.
2. **Agent (Claude Code skill `/events`, `.claude/skills/events/SKILL.md`).** The agent
   reads the pack and the filing texts and writes `analysis.json` and `analysis.md`:
   - per catalyst: facts, the baseline used (company-quoted consensus, then prior guidance,
     then the prior year), estimate impact, durability, a read of the reaction, and a score
     in [-1, +1] with a confidence;
   - per event: date status (confirmed / estimated / overdue), the KPIs to watch, the
     set-up, scenarios and the typical move;
   - a net `catalyst_score` in [-1, +1] and an `event_risk` level.
   Every claim must cite a source. Unknowns stay unknown, and consensus is never invented.

**Why not score catalysts programmatically.** Headline categories don't carry the
direction: a "Trading Statement" can be an upgrade or a profit warning. Even the type code
for the same event varies (Jet2's finals were filed as `FR`, then `PRE`). Sentiment models
add a dependency without reading the numbers against guidance, which is the actual work.

**Assumptions kept minimal.**
- Routine filings dropped as administrative: daily buyback executions, total voting rights
  and block-listing returns (`ROUTINE`). Everything else reaches the agent.
- "Scheduled" means the NSM category groups Financial Results and General Meetings.
- No benchmark is subtracted from reactions: choosing an index is an assumption, so the
  z-score uses the stock's own volatility.
- The 16:30 cutoff ignores the closing auction (to 16:35) and half-days. A filing in
  those minutes is attributed to the next session, which is conservative.

**Consequences / open points.**
- Estimated windows are estimates: Jet2's Feb 2026 trading update came 6 days after the
  projected window. The agent confirms dates from company text before relying on them.
- LSE tickers only (the NSM is UK-only). Other markets would need their own filing source.
- The NSM search API is the backend of the NSM website, not a documented API, so it can
  change. The LSE instrument endpoint is likewise undocumented, with unclear terms; `--lei`
  avoids it.
- No analyst consensus, estimate revisions or broker actions from a free source yet; the
  agent uses company-quoted consensus where a filing gives it.
- Where agent output persists between scheduled runs (e.g. committed research notes, an
  artifact, Notion) is still to be decided. Each run is stateless: it reviews the last
  `--days` and re-derives expected events.

**Amendment (#119, ADR 0016).** The agent's output now persists: `/events` records its note
as a verdict (`stage = "events"`, label ACT unless it errored, `catalyst_score` and
`event_risk` as scores, one evidence row per catalyst and dated event with the filing's
`nsm_id`) through `scripts/record.py`, and starts each run from `record.py prior` so filings
already interpreted are not re-read and estimated dates can move to confirmed. **Research
supersedes events as the Sunday agent stage**: `/research` (`.claude/skills/research/SKILL.md`)
extends the events note into the nine-section strategist's note with web search and logged
evidence, and its verdict is the stage the planner reads; `events` stays the fact layer (the
pack and the toolkit card) that research starts from.
