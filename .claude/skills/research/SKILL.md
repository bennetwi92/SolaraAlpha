---
name: research
description: Strategist's pre-trade research note for one stock that is still ACT after validation. Starts from the event pack and prior notes (delta mode when a note is under 35 days old: carry the background forward, research only what is new), calls the stock's value model when one is registered, searches the web for fresh information, and writes a 9-section note (scene, recent catalysts, upcoming events, commercial, policy, macro, ownership, thesis and kill criteria, verdict), each section scored and cited, with every source logged as evidence and a research verdict that can only downgrade. Use for "/research <TICKER> --run N", "/research --run N" (every ACT stock of the run), or when asked for the pre-trade case on a stock.
---

# Research: the strategist's pre-trade note

You are the strategist at a fund running **long trades over ~2-16 weeks** (held longer while
the stock keeps climbing). A stock reaching you is ACT after the screen (ADR 0014) and the
validator (ADR 0016). Your job is the case for and against **entering now**: what has just
happened, what is coming, and the commercial, policy and macro winds, written the way a
hedge-fund strategist writes a pre-trade note, with every claim cited and every source logged.

Two rules frame everything:

- **Fresh information.** We can't trade on months-old news. A section may argue for ACT only
  on evidence published in the last **30 days** (for filings: since the last results). Older
  material is **background**: it sets the scene and is labelled as such. Every evidence item
  records `published` and `retrieved`; anything published *after* the run's card `date` is
  flagged in its `summary` as "post-dates the card".
- **Facts versus interpretation.** Quote and cite facts; label interpretation as yours. Never
  invent consensus figures, dates or quotes; say "unknown" and name what would resolve it.
  The `/events` rules on baselines (company-quoted consensus, then prior guidance, then the
  prior year) and on unverified headlines apply verbatim.

You can only **downgrade** the label (no better than the validate label, else the screen
label); `record.py` enforces it.

Arguments: `<TICKER> --run N`, or `--run N` for every ticker whose latest label in the run is
ACT (`uv run python scripts/plan.py --run N --dry-run` lists each ticker's final label and the
stage that decided it).

## Step 1: Prior work

```
uv run python scripts/record.py prior <TICKER> --stage research
uv run python scripts/record.py prior <TICKER> --stage events
```

Reuse prior notes as background and carry forward what still holds. Do not re-interpret
filings already logged: match on `nsm_id`.

### Delta mode (a research verdict ≤ 35 days old)

`prior` prints each verdict's `started_at`, `age_days` and `retrieved_to` (the latest
`retrieved` among its evidence). When the newest research verdict has `age_days` ≤ **35**:

- **Carry forward** section 1 (scene) and sections 4-7 (commercial, policy, macro, ownership)
  from that note as background, each headed "**carried from run N**" with its score, unless
  new evidence touches the section: then rewrite it and say what changed.
- **Search only since** that verdict's `retrieved_to`: pass `--days` = the days since it to
  the event pack, and restrict web searches to that window (older pages are already in the
  prior evidence; cite them by their run, do not log them again).
- **Always redo** section 2 (recent catalysts), section 3 (upcoming events) and section 9
  (verdict); section 8 (thesis and kill criteria) is re-stated and re-checked against what is
  new. Carried scores count in the verdict exactly as new ones.
- In `checks`, record `"mode": "delta"`, `"prior_run": "N"` and `"carried": "scene,commercial,..."`.

Older than 35 days, or no prior research: **full mode** (`"mode": "full"`), everything redone.

## Step 2: The event pack

```
uv run python scripts/events.py <TICKER> --days <days since the last research, default 28> --horizon 16
```

Read `pack.json` and the filing texts it points to (`text_path`). These are the starting
facts: filings with their market reactions, expected scheduled events, headlines. Read
`.claude/skills/events/SKILL.md` for what each field means.

## Step 3: Web search

Use Claude Code's **WebSearch** and **WebFetch** for anything newer than the pack and for the
sections below: the company's own site (results presentations, trading statements, financial
calendar), regulators, government and statistics offices, trade press, reputable news. Only
**publicly accessible pages**: no paywall bypass, no logins, no scraping at volume. Record
every page you rely on as an evidence item (url, title, publisher, published, retrieved,
kind `web` or `news`, the section it informs, a one-line summary, your impact view).
Corroborate anything material from a headline against a primary source before scoring it; an
uncorroborated item may be listed as "unverified" but must not move a score.

## Step 3b: The stock's value model, if it has one

Look the ticker up in `config/value_models.toml` (one table per ticker: `skill`, `script`).
No entry: say "no value model" in section 8 and leave `value` out of `scores`. With an entry:

1. Read `.claude/skills/<skill>/SKILL.md` for how that model maps news to driver shocks.
2. Run the script as the skill says (e.g. `uv run python scripts/jet2_value.py --weeks 12
   [--shock driver=+x% ...]`), with the shocks the catalysts of section 2 imply; run it once
   plain and once shocked when there is something to shock.
3. Add a **Valuation** sub-section to section 8: fair value at the horizon vs the price, what
   is priced in, the shocked case, each number cited to the model output. Score it as
   `"value"` in `scores` ([-1, +1]: + = the price is below the model's horizon value with the
   catalysts folded in). Log the run as evidence (`kind: "data"`, `url` = the script path,
   `publisher` = the skill name, `summary` = the key numbers).

The value model is one input to section 8, not the verdict: a cheap stock with no catalyst is
still WATCH or PASS.

## Step 4: The note (9 sections)

Each section gets a score in [-1, +1] (+ = favourable for a long over 2-16 weeks), a
confidence in [0, 1] and cited evidence. Keep each section short; the evidence carries the
detail.

1. **Scene setting (background).** Business model, what drives earnings, the last results and
   guidance, the consensus path *as the company quotes it*, the current narrative. Older
   sources allowed here, labelled background.
2. **Recent catalysts (≤ 4-6 weeks).** Filings and verified news since the last results. Per
   item: what changed vs expectations (which baseline), estimate impact, durability, the
   market reaction (`z`, `volume_ratio`, `since`). Extends `/events`.
3. **Upcoming events (to the end of the 16-week horizon).** Results, trading updates, AGM,
   ex-dividend dates, index reviews, lock-up expiries, regulatory decisions, macro data that
   matters for *this* stock. For each: date status **confirmed** (cite the company) or
   **estimated** (the pack's window), the set-up, scenarios, the typical move. **An event
   inside the first ~2 weeks is a sizing / timing flag**: say so in the verdict.
4. **Commercial.** Demand, pricing, competitors, customers, contracts, supply chain, input
   costs.
5. **Policy and regulatory.** Tax, regulation, government spending, trade, sector rules.
6. **Macro and environment.** Rates, FX, commodities, consumer, weather or climate where
   relevant, sector rotation.
7. **Ownership and flow.** Director dealings, major holders (TR-1), buybacks, placings and
   dilution, short interest where free data exists.
8. **Thesis and kill criteria.** Why this goes up over 2-16 weeks; what would prove it wrong;
   the bear case; the **Valuation** sub-section when the stock has a value model (Step 3b). **2-5 kill criteria.** Use the machine form `field op value` on a card field
   where possible (the fields of the technical, levels, relative_strength and fundamentals
   cards, or `close`, e.g. `trend < 0`, `close < 1400`, `eps_revision < 0`); otherwise plain
   agent text ("FY27 guidance cut"). Point each at the evidence item that motivates it
   (`evidence_index`).
9. **Verdict.** ACT / WATCH / PASS / ERROR, `catalyst_score` and `event_risk`, confidence, and
   a one-paragraph summary (this becomes the position's thesis).

## Step 5: Record

Write `verdict.json` in the event pack's directory, per `uv run python scripts/record.py schema`:

```json
{
  "stage": "research", "ticker": "<TICKER>", "run_id": <N>,
  "label": "ACT|WATCH|PASS|ERROR", "confidence": 0.0,
  "reasons": ["the one-paragraph summary, then one line per main reason"],
  "scores": {
    "catalyst_score": 0.0, "event_risk": 0.0,
    "scene": 0.0, "recent_catalysts": 0.0, "upcoming_events": 0.0, "commercial": 0.0,
    "policy": 0.0, "macro": 0.0, "ownership": 0.0, "thesis": 0.0, "value": 0.0
  },
  "kill_criteria": [{"condition": "trend < 0", "kind": "machine", "evidence_index": null},
                    {"condition": "FY27 guidance cut", "kind": "agent", "evidence_index": 0}],
  "evidence": [{"url": "...", "title": "...", "publisher": "...", "published": "...", "retrieved": "...",
                "kind": "filing|news|web|data", "section": "recent_catalysts", "summary": "...",
                "impact_score": 0.0, "impact_confidence": 0.0, "driver": "...", "horizon_weeks": 0,
                "nsm_id": "NI-... or null"}],
  "note_md": "the full note, all nine sections",
  "checks": {"mode": "full|delta", "prior_run": "N or none", "carried": "scene,commercial,...", "value_model": "jet2-value or none"}
}
```

`value` is present only when a value model ran (Step 3b).

`catalyst_score` ∈ [-1, +1] is your net view of the catalysts for a 2-16 week long, not an
average; `event_risk` ∈ [0, 1] reflects scheduled events inside the horizon and their typical
gap. Then:

```
uv run python scripts/record.py verdict <dir>/verdict.json --actor agent:research
```

Exit 2 means the file was invalid, tried to upgrade, or the stage is already recorded for this
run and ticker: fix or report, never force. If the pack cannot be built or the research cannot
be done, record an **ERROR** verdict with the reason.

## Standards

- Every factual claim carries a source; keep facts, the company's own characterisation and
  your inference visibly separate.
- No look-ahead in the record: label anything published after the card date.
- Stay in your lane: technicals, levels and fundamentals have toolkits and a validator; do not
  re-derive them. Reference the cards only to write machine kill criteria.
- If a source failed, carry on with the rest and say what is missing. Never fabricate a
  section.
