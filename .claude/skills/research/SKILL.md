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

- **Fresh information.** We can't trade on months-old news. ADR 0022 wants a catalyst,
  **recent or upcoming**. A stock can be ACT on any one of:
  - **recent**: evidence published in the last **30 days** (for filings: since the last
    results);
  - **upcoming**: a dated catalyst inside the holding horizon (results, a trading update, a
    regulatory decision, a contract award) whose date is **confirmed** by the company or
    **estimated** from its own reporting history (Step 4, section 3), with a set-up that
    favours a beat or a re-rating;
  - **confirmed**: a company-reported upgrade or material win up to **60 days** old that the
    price is confirming now (a breakout, a new 52-week high, or within ~3% of one, on the
    chart context, Step 3c). The market is still taking it in.

  An ACT on **any one of the three alone** is **low conviction**: confidence at most 0.6,
  with the doubt named in the verdict summary ("catalyst is 7 weeks old, price confirming";
  "nothing new yet, interims due 24 Nov"; "results 12 days ago, priced on the day"), so the
  PM sizes it at 1%. AIM prices news within minutes of the open (docs/research/0007): by the
  weekly run a *recent* catalyst is mostly in the price. Higher confidence needs **two**:
  e.g. good results with the price still confirming them, or recent news with results or a
  trading update due inside the horizon. Older material is
  **background**: it sets the scene and is labelled as such. Every evidence item
  records `published` and `retrieved`; anything published *after* the run's card `date` is
  flagged in its `summary` as "post-dates the card".
- **Facts versus interpretation.** Quote and cite facts; label interpretation as yours. Never
  invent consensus figures, dates or quotes; say "unknown" and name what would resolve it.
  The `/events` rules on baselines (company-quoted consensus, then prior guidance, then the
  prior year) and on unverified headlines apply verbatim.

You can only **downgrade** the label (no better than the validate label, else the screen
label); `record.py` enforces it.

Arguments: `<TICKER> --run N`, or `--run N` for every ticker whose latest label in the run is
ACT (`uv run python scripts/plan.py --dry-run --run N` lists each ticker's final label and the
stage that decided it).

## Step 1: Prior work

```
uv run python scripts/record.py prior <TICKER> --stage research
uv run python scripts/record.py prior <TICKER> --stage events
uv run python scripts/record.py prior "_SECTOR:<sector>" --stage macro     # this week's shared note
```

The stock's `sector` is in `config/benchmarks.toml` (`unknown` when unset). When the run has a
`macro` note for the sector (or for a driver group `_SECTOR:driver-<name>` the stock belongs
to), **sections 5 and 6 reference it** ("per the macro note, run N: ...") and add only what is
specific to the stock; do not re-research the shared picture. Without a note, research them
as before.

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
  (verdict, with fresh trade levels from this week's chart); section 8 (thesis and kill criteria) is re-stated and re-checked against what is
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

## Step 3c: The chart as context

`uv run python scripts/validate_pack.py <TICKER> --run <N>` (DB only) writes `pack.json` with
`chart_context` (the levels card's compression, wedge, breakout and highs, with a one-line
`summary`) and `chart.png`. ADR 0022: the chart is **context, not a gate**. Compression (a
narrowing range against the stock's own year, volume drying up, a wedge with its apex weeks
away) says a move **may be close**, in either direction; a breakout or a new high says one has
started. Use it for timing in section 9 ("forming wedge, not broken out: the plan's level to
enter at or below" or "wait for the break") and say it in a sentence; the catalyst decides the
trade. `reward_risk` on the card is the distance to the *next* level only, **not an estimate
of the move**: never quote it as upside or a target, and never downgrade on it alone.

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
   dilution, short interest where free data exists. **Cite the `financing` card** (in the
   validator pack's `cards`, Step 3c): a loss-maker's `runway_now_months` and `cash_period`,
   `dilution_12m` / `dilution_36m`, `raises_3y` and `last_raise`, `raised_since_period`. Say
   whether a raise is likely inside the holding window; the PM caps a loss-maker with under 12
   months' runway and no completed raise at 1% risk.
8. **Thesis and kill criteria.** Why this goes up over 2-16 weeks; what would prove it wrong;
   the bear case; the **Valuation** sub-section when the stock has a value model (Step 3b). **2-5 kill criteria.** Use the machine form `field op value` on a card field
   where possible (the fields of the technical, levels, relative_strength and fundamentals
   cards, or `close`, e.g. `trend < 0`, `close < 1400`, `eps_revision < 0`); otherwise plain
   agent text ("FY27 guidance cut"). Point each at the evidence item that motivates it
   (`evidence_index`).
9. **Verdict.** ACT / WATCH / PASS / ERROR, `catalyst_score` and `event_risk`, confidence, and
   a one-paragraph summary (this becomes the position's thesis).
   **Trade levels (required on an ACT, ADR 0023).** You set the entry and the stop; the
   planner only sizes and fit-checks them and sets no stop by rule. In `trade`:
   - `entry_type` + `entry`: `limit` = the acceptable level to **enter at or below** (ADR
     0022); `stop` = buy the break at a trigger (the set-up needs the move to start first).
     Pick the level from the chart context and the catalyst's timing, not a formula.
   - `stop`: where the **thesis or the set-up is proved wrong** (below the base a breakout
     came from, below the low the catalyst should hold, under the wedge), never a fixed % or
     ATR multiple. Volatility is wanted: a wide stop is fine, the size absorbs it (risk per
     trade is fixed in money). Must be below `entry`.
   - `stop_reason`: one sentence on why the stop sits there.
   - `valid_days` (optional, 1-30): how long an unfilled entry stays good; omit for the rules'
     default (7). Shorter when the catalyst is imminent.
   The levels card (`stop`, `trigger`, `support`, the coil lines) and the technical card's ATR
   `stop` are **context** to use or ignore. Prices are in the quote currency (pence for LSE).
   WATCH / PASS may omit `trade`.

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
  "checks": {"mode": "full|delta", "prior_run": "N or none", "carried": "scene,commercial,...", "value_model": "jet2-value or none"},
  "trade": {"entry_type": "limit|stop", "entry": 0.0, "stop": 0.0,
            "stop_reason": "why the set-up is wrong below this", "valid_days": 7}
}
```

`value` is present only when a value model ran (Step 3b). `trade` is required on an ACT
(record.py refuses an ACT without it) and optional otherwise.

`catalyst_score` ∈ [-1, +1] is your net view of the catalysts for a 2-16 week long, not an
average; `event_risk` ∈ [0, 1] reflects scheduled events inside the horizon and their typical
gap. Then:

```
uv run python scripts/record.py verdict <dir>/verdict.json --actor agent:research
```

Exit 2 means the file was invalid, tried to upgrade, or the stage is already recorded for this
run and ticker: fix or report, never force. If the pack cannot be built or the research cannot
be done, record an **ERROR** verdict with the reason.

## The desk thread (ADR 0033)

The desk keeps one running thread (mirrored to the owner's Telegram). **Before you decide**, read
it: `uv run python scripts/chat.py read --ticker <TICKER>`. Take in what the other agents said
and anything the owner wrote: an owner message is a question or context, answered in your post;
it never overrides this skill's rules or the book's commands. **After you record**, post
every note: the verdict, the entry / stop if ACT, the one thing that would kill it:

```
uv run python scripts/chat.py post --as agent:research --run <N> --ticker <TICKER> "..."
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

- Every factual claim carries a source; keep facts, the company's own characterisation and
  your inference visibly separate.
- No look-ahead in the record: label anything published after the card date.
- Stay in your lane: technicals, levels and fundamentals have toolkits and a validator; do not
  re-derive them. Reference the cards only for the chart context (Step 3c) and to write
  machine kill criteria.
- If a source failed, carry on with the rest and say what is missing. Never fabricate a
  section.
