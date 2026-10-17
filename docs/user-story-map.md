# User story map — managing the portfolio end to end

This is the whole job, from a stock entering the universe to a closed trade feeding back into
the models. Use it to see what's built and what still has to be delivered. Each story that
isn't built should become one GitHub issue (see CLAUDE.md, *Issues*), and its row here links
to that issue. The build order and shared conventions are in epic #104.

- **Backbone**: the activities, left to right, in the order they happen in a week (§3).
- **Stories**: what each activity needs, grouped by release (§4). R1 is the smallest set that
  trades end to end.
- **Status**: ✅ built · 🟡 partly built · ⬜ not built. "Partly" says what's missing.
- **Proposal** marks a number or rule I've suggested and the owner hasn't confirmed. Treat
  these as placeholders, like the toolkit thresholds. Open questions are collected in §6.

---

## 1. The owner's notes (starting point)

Kept as written (lightly tidied); everything below expands them.

> **On a Sunday — to generate new positions.** The technical, levels, relative strength and
> fundamentals toolkits are run for each stock. The results are posted to the DB. Each stock
> has an **act, watch or pass (or error)** outcome, decided on all four toolkits. These are
> specifically designed to pick a perfect entry. *What we are looking for: stocks in an
> uptrend but not overextended, with potential for growth.*
>
> This narrows the list to a handful labelled **act**. We then run a **validation** process: a
> Claude agent, following a carefully worded prompt, double-checks that the toolkit outcomes
> are warranted given the underlying data. It's a second, independent check, and outputs
> act / watch / pass / error. It may remove more stocks.
>
> For the remaining **act** stocks we check upcoming events, recent news catalysts,
> commercial, policy and environment headwinds, etc. This must be well structured, as though
> a hedge fund strategist were doing it. The articles etc. are logged in the DB with their
> impact recorded (the impacts are entirely discretionary). The research must search the web
> for up-to-date information: we can't trade on months-old information, though that sets the
> scene. The output is the same as the other stages.
>
> For the stocks still labelled **act** we generate a **trade plan**: draft orders, and
> positions to house them. The toolkit outputs are all linked to the position as **entry
> conditions**.
>
> **Daily — to check existing positions.** I manually enter fills against open orders, or mark
> them as not filled. An agent checks all the toolkits for each stock with an open position,
> to see whether the situation has changed enough that the trade plan needs revising. That
> means something structural in the position has changed, most likely a news catalyst or an
> announcement.

---

## 2. Actors, objects and states

### Actors

| Actor | Who / what | Runs |
|---|---|---|
| **Owner** | the one human: approves, places orders at the broker, enters fills, overrides | ad hoc |
| **Pipeline** | deterministic Python (`scripts/scan.py`), GitHub Actions | Sunday + daily |
| **Validator agent** | Claude, a fixed prompt, re-checks toolkit verdicts against the data | Sunday, shortlist only |
| **Strategist agent** | Claude with web search: catalysts, news, headwinds, scored and logged | Sunday, shortlist only; daily on triggers |
| **Monitor agent** | Claude: daily review of open positions, proposes plan revisions | daily, open positions only |

Agents **propose**; only the owner changes orders or positions. Every agent output is
written to the DB with its evidence.

### Outcome labels (every stage, every stock)

| Label | Meaning | What happens next |
|---|---|---|
| **ACT** | the set-up is there now | moves to the next stage |
| **WATCH** | close (e.g. right trend, but extended, or waiting for a coil to break) | kept on a watch list; re-run next week (and daily triggers for a break) |
| **PASS** | not a candidate | drops out this week |
| **ERROR** | couldn't judge (data, source, agent failure) | reported and never treated as a pass; ticker drops out this week |

Today the pipeline gives each toolkit `pass` / `fail` / `skip` / `error`. **ACT/WATCH needs a
third band per toolkit, and a rule that combines them** (§4, S1.3).

**Combining labels within a stage** (proposal): ERROR if any toolkit errored; otherwise
PASS if any toolkit is PASS; otherwise WATCH if any is WATCH; otherwise ACT. A stock is only
as good as its weakest toolkit, so one model can't hide another (as in ADR 0010).

### Objects and lifecycle

```
Watchlist ticker
  └─ weekly Candidate (run_id, ticker, stage labels)            ACT / WATCH / PASS / ERROR
       └─ Trade plan (entry, stop, targets, size, horizon, entry conditions = linked cards)
            └─ Position  draft → approved → open → (scaled) → closed | cancelled
                 ├─ Orders  draft → placed → filled | part-filled | not filled | cancelled | expired
                 │    └─ Fills (date, qty, price, costs) — entered by the owner
                 ├─ Plan revisions (who, why, evidence, old → new)
                 ├─ Daily / weekly checks (cards with entry_date)
                 └─ Close: exit reason, P&L, R-multiple, post-mortem
Evidence (articles, filings, events) ──linked to── candidates, plans, revisions
```

---

## 3. Backbone

| # | Activity | Cadence | Actor | Today |
|---|---|---|---|---|
| A0 | Set up universe, reference data and portfolio rules | quarterly / ad hoc | Owner | 🟡 |
| A1 | **Screen**: technical, levels, relative strength, fundamentals → label | Sunday | Pipeline | 🟡 |
| A2 | **Validate**: independent agent check of the labels | Sunday | Validator | ⬜ |
| A3 | **Research**: events, news, headwinds; log evidence and impact | Sunday | Strategist | 🟡 |
| A4 | **Plan and size**: trade plan, size, portfolio fit; draft orders and positions | Sunday | Pipeline | 🟡 |
| A5 | **Approve and place**: owner reviews the week's pack, places orders at the broker | Sun eve / Mon | Owner | ⬜ |
| A6 | **Record fills**: fill, part-fill, not filled; open positions | daily | Owner | ⬜ |
| A7 | **Monitor daily**: prices vs stops/triggers, announcements, news, volatility | daily | Pipeline + Monitor | ⬜ |
| A8 | **Review weekly**: re-run toolkits on open positions; hold / trail / add / exit | Sunday | Pipeline + Monitor | 🟡 |
| A9 | **Exit and close**: exit orders, final fill, P&L, post-mortem | when triggered | Owner | ⬜ |
| A10 | **Portfolio view and risk**: holdings, exposure, cash, heat, drawdown | daily / weekly | Pipeline | ⬜ |
| A11 | **Learn**: track record, calibrate thresholds and labels against returns | monthly / quarterly | Owner + agent | ⬜ |
| A12 | **Operate**: schedules, hosted DB, notifications, costs, failures | always | Pipeline | 🟡 |

A typical week:

```
Fri close ─▶ Sun 18:17 A1 screen ─▶ A2 validate ─▶ A3 research ─▶ A4 plan ─▶ "weekly pack"
                                     (also A8 weekly review of open positions)        │
Sun eve / Mon pre-open ◀──────────────────────────────── A5 owner approves, places ◀──┘
Mon–Fri, each evening:  A6 owner enters fills ─▶ A7 daily monitor ─▶ revision proposals
                        └─ stop hit / exit signal ─▶ A9 exit
```

---

## 4. Stories by activity

Story format: *as <actor>, I want <capability> so that <outcome>*, then acceptance criteria.
**R1** = first release, the walking skeleton that can place, track and close one trade.
**R2** makes it robust and automatic. **R3** is learning and refinement.

### A0 — Universe, reference data and portfolio rules

| ID | Story | Rel | Status |
|---|---|---|---|
| S0.1 | As the owner I want a watchlist universe so the scan knows which stocks to run. | R1 | ✅ `config/watchlist.toml` (FTSE AIM UK 50, hand snapshot) |
| S0.2 | As the owner I want per-stock benchmarks and peers so relative strength is meaningful. | R1 | ✅ `config/benchmarks.toml` |
| S0.3 | As the owner I want **portfolio rules** in one config file (capital, risk per trade, max open positions, max per stock / sector, max portfolio heat, cash buffer, min liquidity) so sizing and checks are consistent and versioned. | R1 | ✅ `config/portfolio.toml`, `portfolio.rules` (placeholders, `confirmed = false`) · #105 |
| S0.4 | As the owner I want a reminder when the watchlist snapshot is older than the last quarterly FTSE review, so the universe doesn't drift. | R2 | ✅ `snapshot` in `watchlist.toml`; `scan.py` warns after the next FTSE review month · #124 |
| S0.5 | As the owner I want each stock's sector / industry recorded so concentration can be checked. | R2 | 🟡 `sector` key per stock in `benchmarks.toml`; only JET2.L confirmed (the LSE pages need JavaScript; fill by hand, #124) |
| S0.6 | As the owner I want each stock's liquidity (median daily £ volume, spread proxy) so size is capped at what AIM names can absorb. | R2 | ✅ `liquidity` toolkit (`analysis/liquidity.py`); `plan.py` sizes on its `adv_gbp_20` · #124 |

S0.3 acceptance (proposal for the values; owner to set them):
- `config/portfolio.toml` holding `capital`, `risk_per_trade` (e.g. 0.5–1% of capital),
  `max_positions`, `max_position_pct`, `max_sector_pct`, `max_heat` (sum of open risk),
  `max_adv_pct` (position ≤ N% of median daily traded value), `cash_buffer`.
- Read by A4 sizing and A10 risk; covered by tests.

### A1 — Screen (Sunday)

**What we're looking for.** Defined below; this is the long entry the four toolkits are
meant to find. It's written in terms of fields that already exist on the cards. All numbers
are proposals and need backtesting (A11).

| Question | Toolkit / fields | ACT (proposal) | WATCH (proposal) |
|---|---|---|---|
| **In an uptrend?** Stage 2: price above a rising 40w MA, 10w above 40w | technical `trend` | ≥ 0.25 | 0 to 0.25 |
| **Momentum behind it?** | technical `momentum` | > 0 | -0.25 to 0 |
| **Not overextended?** Near the 10w MA, RSI with room, histogram turning up | technical `entry`, `risk_pct` | `entry` ≥ 0; risk to the stop ≤ 15% | `entry` < 0 (extended: wait for a pullback) |
| **Structure and room to run?** Rising trendlines, high in the 52w range, a target far enough away (or blue sky) | levels `structure`, `reward_risk`, `blue_sky` | `structure` > 0 and R:R ≥ 2 (blue sky counts) | R:R 1.5–2, or `coiled` with a `trigger` above the close (wait for the break) |
| **Leading its market, industry and peers?** | relative_strength `rs`, `rs_high`, `peer_rank` | `rs` > 0 | `rs` −0.25 to 0 with `rs_high` or a top-half `peer_rank` |
| **Potential for growth, at a sane price, and not broken?** | fundamentals `growth`, `quality`, `value`, `eps_revision` | `growth` > 0 and `quality` > −0.5, and `eps_revision` not negative | `growth` ≤ 0 but revisions rising |

In one line: **a stage-2 uptrend with positive momentum, priced near support rather than
stretched above it, outperforming its peers, with earnings growing or being revised up, and
at least 2:1 to the next resistance (or nothing overhead).**

| ID | Story | Rel | Status |
|---|---|---|---|
| S1.1 | As the pipeline I want technical, levels and relative strength run on every watchlist stock, side by side. | R1 | ✅ stage `screen` |
| S1.2 | As the pipeline I want fundamentals run on the survivors. | R1 | ✅ stage `fundamentals` |
| S1.3 | As the owner I want each toolkit to give **ACT / WATCH / PASS / ERROR** from its own bands (`act=` and `watch=` conditions) rather than pass/fail, so near-misses become a watch list. | R1 | ✅ `act=` / `watch=` bands per toolkit (ADR 0014) · #111 |
| S1.4 | As the owner I want one **screen label per stock** from all four toolkits (the combining rule in §2), saved as a `steps` row, so the shortlist is one query. | R1 | ✅ `labels` table, one `screen` row per stock; `store.shortlist` · #111 |
| S1.5 | As the owner I want the **"what we're looking for"** definition above recorded in an ADR and coded as the bands, so the screen is explicit and testable. | R1 | ✅ ADR 0014; bands coded in `toolkits.py` (placeholders until backtested) · #111 |
| S1.6 | As the owner I want WATCH stocks to keep their reason (e.g. "extended: entry −0.4", "coiled: trigger 1,520p") so the daily monitor knows what would turn them into ACT. | R2 | ✅ `labels.watch_on` (pullback level / break trigger / R:R / rs / revisions); daily `watch_pullback` + `watch_break` · #125 |
| S1.7 | As the owner I want the event pack (`events` toolkit) to stop being a pass/fail screen and become the **input to A3** (it has no pass conditions today). | R1 | ✅ no bands, always ACT; runs on ACT only · #111 |
| S1.8 | As the owner I want the screen to rank ACT stocks (e.g. by setup × RS) so the agents work the best first if the list is long. | R2 | ✅ `labels.rank` by `rank_score`; shortlist, pack and agent queue in rank order · #125 |

### A2 — Validate (agent, Sunday)

The validator is a second, independent opinion of the **same data**. It does no new
research. Its job is to catch model artefacts: bad data, a score driven by one odd bar, a
"trend" that's really a takeover spike, a target that's a stale level.

| ID | Story | Rel | Status |
|---|---|---|---|
| S2.1 | As the owner I want a `/validate <TICKER> [--run N]` skill that reads the saved cards, steps and price snapshot for that run, and judges each toolkit's label as *warranted / not warranted* with a reason. | R1 | ✅ `/validate`, `scripts/validate_pack.py` · #118 |
| S2.2 | As the validator I want a fixed checklist: data issues (`check_prices`, trim span, gaps, suspensions); one-off spikes (bid, placing, results gap) inflating trend or momentum; overextension vs ATR; levels that are stale or that the price just broke; RS driven by one peer; fundamentals stale, a one-off, or currency-mismatched; agreement between the toolkits. | R1 | ✅ the seven-item checklist in the skill, answered in `checks` · #118 |
| S2.3 | As the owner I want its output to be ACT / WATCH / PASS / ERROR + confidence + reasons, written to the DB as a `validate` card and step on the **same run**. | R1 | ✅ `record.py verdict` (#117), `/validate` records `stage = validate` (#118) |
| S2.4 | As the owner I want it to be **independent**: it sees the raw data and the cards, never the other agents' notes, and can only *downgrade* (ACT → WATCH / PASS), never upgrade. | R1 | ✅ pack excludes other agents' cards; `record.py` enforces downgrade only · #118 |
| S2.5 | As the owner I want it rendered as a short chart pack (weekly bars, MAs, levels, stop, target) the agent can read, so it judges the picture as well as the numbers. | R2 | ⬜ · #126 |
| S2.6 | As the owner I want the validator's disagreements logged and counted, so bad toolkit rules surface (feeds A11). | R2 | ⬜ · #126 |

### A3 — Research: events, catalysts and headwinds (agent, Sunday)

**Structure** (proposal), the way a strategist writes a pre-trade note. Each section has a
score in [−1, +1], a confidence and cited evidence. Unknowns stay "unknown".

1. **Scene setting (background)**: business model, what drives earnings, the last results
   and guidance, the consensus path, and the current narrative. Older sources are allowed
   here and labelled as background.
2. **Recent catalysts (≤ 4–6 weeks)**: filings and news since the last results. For each:
   what changed vs expectations, estimate impact, durability, market reaction (z, volume,
   drift). Extends `/events`.
3. **Upcoming events (to the end of the horizon, ~16 weeks)**: results, trading updates,
   AGM, ex-dividend dates, index reviews, lock-up expiries, regulatory decisions, macro data
   that matters for this stock. Date status (confirmed / estimated), the set-up, scenarios,
   typical move. **An event inside the first ~2 weeks is a sizing / timing flag.**
4. **Commercial**: demand, pricing, competitors, customers, contracts, supply chain, input
   costs.
5. **Policy and regulatory**: tax, regulation, government spending, trade, sector rules.
6. **Macro and environment**: rates, FX, commodities, consumer, weather or climate where
   relevant, sector rotation.
7. **Ownership and flow**: director dealings, major holders, buybacks, placings and
   dilution, short interest where free data exists.
8. **Thesis and kill criteria**: why this goes up over 2–16 weeks, what would prove it wrong,
   and the bear case. The kill criteria become the plan's **thesis conditions** (A4) and the
   monitor's triggers (A7).
9. **Verdict**: ACT / WATCH / PASS / ERROR, net `catalyst_score`, `event_risk`, confidence,
   and a one-paragraph summary.

**Freshness rule** (proposal): a section can only argue for ACT on evidence ≤ 30 days old
(filings: since the last results). Older items are "background". Every item records
`published` and `retrieved` times; anything published after the scan's `date` is
labelled as such (no look-ahead in the record, and the live decision uses what's known now).

| ID | Story | Rel | Status |
|---|---|---|---|
| S3.1 | As the strategist I want the event pack (filings with reactions, expected events, headlines) as the starting facts. | R1 | ✅ `scripts/events.py`, `events` toolkit |
| S3.2 | As the strategist I want an interpretation skill for catalysts and upcoming events. | R1 | ✅ `/events` skill (persists via `record.py`, #119) |
| S3.3 | As the owner I want a `/research <TICKER>` skill (extending `/events`) that follows the 9-section structure above and **searches the web** for fresh information. | R1 | ✅ `/research` skill · #119 |
| S3.4 | As the owner I want every source the agent relies on logged in the DB as an **evidence** row: url, title, publisher, published, retrieved, section, summary, discretionary impact (score, confidence, horizon, which driver), and a link to the run and ticker. | R1 | ✅ `evidence` table, `record.py verdict` · #117 |
| S3.5 | As the owner I want the research verdict saved as a `research` card and step on the same run, so it reads like any other stage. | R1 | ✅ `record.py verdict` (#117); `/research` records `stage = research` (#119) |
| S3.6 | As the owner I want the agent to separate facts (quoted, cited) from interpretation, and never invent consensus or dates. | R1 | ✅ rule in `/events` and `/research` · #119 |
| S3.7 | As the owner I want the free web/news sources it uses listed in `docs/data-sources.md`, with terms checked. | R1 | ✅ web search row in `docs/data-sources.md` (owner to confirm source types, §6 Q6) · #119 |
| S3.8 | As the owner I want stock-specific value models (e.g. `/jet2-value`) called from research when they exist, so news is turned into earnings impact. | R2 | 🟡 Jet2 only, not wired in · #127 |
| S3.9 | As the owner I want the prior week's research for a stock reused as background and only the delta researched, so repeat candidates are cheap. | R2 | ⬜ · #127 |
| S3.10 | As the owner I want a sector / macro note shared across the week's candidates (the same headwind researched once). | R3 | ⬜ · #140 |

### A4 — Plan and size (Sunday)

| ID | Story | Rel | Status |
|---|---|---|---|
| S4.1 | As the pipeline I want an entry, stop and targets for each stock. | R1 | ✅ levels card: `trigger` / close, `stop`, `target`, `reward_risk`; technical `stop` |
| S4.2 | As the owner I want a **trade plan** per ACT stock: entry type (buy-stop at `trigger` if coiled, else limit near the close / Monday open), entry validity (e.g. good for 1 week), initial stop, targets, holding horizon (2–16 weeks), trailing rule (chandelier 3 ATR, ADR 0003), review dates (before known events). | R1 | ✅ `portfolio.plan.make_plan` → `TradePlan` (ADR 0015) · #112 |
| S4.3 | As the owner I want **position size** from risk: shares = (capital × `risk_per_trade`) ÷ (entry − stop), capped by `max_position_pct`, liquidity (`max_adv_pct`) and cash. | R1 | ✅ risk-based size capped by position %, liquidity and cash · #112 |
| S4.4 | As the owner I want a **portfolio fit check** before a plan is drafted: max positions, sector cap, total heat, correlation with open positions, event clash (results within N days of entry). It reports breaches and ranks candidates if the slots run out. | R1 | ✅ `portfolio.fit.fit` → `FitReport` · #113 |
| S4.5 | As the owner I want **draft positions and draft orders** written to the DB (positions, orders tables) with a status, so there's one record from plan to close. | R1 | ✅ `portfolio/book.py` (#106); `scripts/plan.py` drafts positions and orders from a run (#114) |
| S4.6 | As the owner I want every card that justified the trade (technical, levels, RS, fundamentals, validate, research) **linked to the position as its entry conditions**: run_id + ticker + model, plus a frozen copy of the key fields, so the monitor can compare "then" with "now". | R1 | ✅ `entry_conditions`: every card of the run frozen on the position · #114 |
| S4.7 | As the owner I want the research's kill criteria stored as **thesis conditions** on the position (e.g. "FY guidance cut", "close < 40w MA", "fuel > $X"), each either machine-checkable (field op value) or agent-checked text. | R1 | ✅ `thesis_conditions` from the research card's `kill_criteria` (machine if it parses, else agent) · #114 |
| S4.8 | As the owner I want a **weekly pack** (markdown / artifact): new ACT plans with size and orders, the WATCH list with what would trigger it, open-position actions, portfolio heat and cash. | R1 | ✅ `scripts/weekly_pack.py`, appended to the scan job summary and saved under `data/processed/packs/` · #115 |
| S4.9 | As the owner I want scaling rules (add on a confirmed breakout or a pullback to a new support; never add to a loser) in the plan, so it can be "held longer while it keeps climbing". | R2 | ⬜ · #128 |

### A5 — Approve and place (owner)

| ID | Story | Rel | Status |
|---|---|---|---|
| S5.1 | As the owner I want to approve, amend (size, price) or reject each draft position with a reason, recorded in the DB. | R1 | ✅ `book.py approve / amend / reject` · #107 |
| S5.2 | As the owner I want approved orders listed as broker-ready tickets (ticker, side, type, qty, limit/stop price, validity) to place by hand. | R1 | ✅ `book.py tickets` · #107 |
| S5.3 | As the owner I want to mark orders as **placed**, with the broker reference. | R1 | ✅ `book.py placed ORDER_ID --ref` · #107 |
| S5.4 | As the owner I want rejections and amendments counted, so I can see where the system and I disagree (A11). | R2 | ⬜ · #130 |
| S5.5 | As the owner I want a simple CLI (`scripts/book.py approve|reject|placed ...`) and later a page, rather than raw SQL. | R1 / R2 | ✅ CLI `scripts/book.py` (#107); a page is R2 |

### A6 — Record fills (owner, daily)

| ID | Story | Rel | Status |
|---|---|---|---|
| S6.1 | As the owner I want to enter a fill against an order: date-time, qty, price, commission, stamp duty / fees (AIM shares are currently exempt from UK stamp duty; record any fee anyway). | R1 | ✅ `book.py fill` (commission and fees in GBP) · #108 |
| S6.2 | As the owner I want to mark an order **not filled / expired / cancelled**, and a part fill to leave the rest open. | R1 | ✅ `book.py unfilled / cancel / expire` · #108 |
| S6.3 | As the owner I want a first fill to open the position, and it to show average cost, shares, risk at the stop and R. | R1 | ✅ first fill opens the position; `show [--price P]` gives cost, shares, risk, R · #106, #108 |
| S6.4 | As the owner I want each newly opened position to get a **protective stop order** drafted for me to place. | R1 | ✅ protective stop drafted (approved) on the first fill, re-sized on later fills · #108 |
| S6.5 | As the owner I want a reminder of orders past their validity with no fill entered. | R2 | ⬜ · #130 |
| S6.6 | As the owner I want fills reconciled against a broker statement / CSV export, if the broker offers one, so manual entry errors show up. | R3 | ⬜ (depends on the broker; owner to choose) · #141 |

### A7 — Monitor daily

Two layers, as in ADR 0006: **cheap programmatic checks every day for every open position
and WATCH stock**, and **the agent only when a check fires**.

| ID | Story | Rel | Status |
|---|---|---|---|
| S7.1 | As the pipeline I want a **daily job** (weekday evenings after the 16:35 close) that fetches daily prices for open positions and WATCH stocks. | R1 | ✅ `scripts/daily.py`, `daily.yml` (ADR 0017) · #120 |
| S7.2 | As the owner I want **price triggers**: close below the stop (exit), intraday through the stop (check the fill), hit a target (take profit / trail), gap over N ATR, WATCH coil `trigger` broken (promote to ACT mid-week). | R1 | ✅ `portfolio/triggers.py`: stop_close, stop_touched, target_hit, gap, watch_break, entry_break · #120 |
| S7.3 | As the owner I want **new regulatory announcements** for open positions checked daily (NSM by LEI) and new headlines from the news feed. | R1 | ✅ `filing` (agent) and `news` (listed) triggers since the last daily run · #120 |
| S7.4 | As the owner I want **thesis conditions** (S4.7) that are machine-checkable evaluated daily. | R2 | ⬜ · #131 |
| S7.5 | As the owner I want the **monitor agent** run only for positions with a fired trigger: read the filing / news, compare it with the entry conditions and the thesis, and propose **hold / tighten stop / take partial / exit / revise target**, with evidence logged (as S3.4). | R1 | ✅ `/monitor`, `scripts/monitor_pack.py`, `record.py revision` · #121 |
| S7.6 | As the owner I want "structural change" defined so the agent doesn't churn: guidance or estimate change; a thesis kill criterion hit; a capital event (placing, bid, delisting); a regulatory or legal event; a weekly-trend break (stage 2 → 3). Noise (no new information, a move inside 2 ATR) is ignored. | R1 | ✅ defined in the skill and ADR 0018 · #121 |
| S7.7 | As the owner I want each proposal stored as a **plan revision** (old → new, reason, evidence, status proposed / accepted / rejected), and new draft orders if accepted. | R1 | ✅ `revision_proposed` / `accepted` / `declined` events; `book.py accept` drafts the orders · #121 |
| S7.8 | As the owner I want a daily notification only when something needs me (a revision, a stop hit, a fill to enter, an error); silence otherwise. | R2 | ⬜ · #132 |
| S7.9 | As the owner I want market-wide volatility checks (index drawdown, VIX / VFTSE proxy) to flag "tighten everything" days. | R3 | ⬜ · #142 |

### A8 — Review weekly (open positions, Sunday)

| ID | Story | Rel | Status |
|---|---|---|---|
| S8.1 | As the pipeline I want the technical position assessment (chandelier stop, R, hold / review / exit) for each open position. | R1 | ✅ `assess_position` via `technical --param entry_date=...` |
| S8.2 | As the pipeline I want **every** toolkit re-run for open positions (not only screen survivors) and compared with the entry conditions: what has deteriorated since entry. | R1 | ✅ review pass in `scan.py`, `portfolio/review.py` · #122 |
| S8.3 | As the owner I want the new stop each week (ratchet only), with a drafted amend order when it moves. | R1 | ✅ `new_stop` ratchets; the stop order is replaced (approved) with `--save` · #122 |
| S8.4 | As the owner I want the "hold while it keeps climbing" rule explicit: beyond 16 weeks, hold only while trend ≥ 0 and the trailing stop holds; flag positions past the horizon with stalled momentum. | R1 | ✅ `ReviewCard.action` exit past the horizon with trend < 0 or momentum ≤ 0 (ADR 0015) · #122 |
| S8.5 | As the owner I want an upcoming-event check per position (results inside the next 2 weeks: hold through, trim or exit, decided in advance). | R2 | 🟡 expected events exist in the pack; no rule · #129 |

### A9 — Exit and close

| ID | Story | Rel | Status |
|---|---|---|---|
| S9.1 | As the owner I want exit reasons tagged (stop, trailing stop, target, thesis broken, time, discretionary, corporate action) when I close. | R1 | ✅ `book.py exit --reason ...`; stop fills tag `stop` / `trailing_stop` · #109 |
| S9.2 | As the owner I want exit fills to close the position and compute realised P&L (gross, costs, net), R-multiple, holding weeks, max favourable and adverse excursion. | R1 | ✅ auto-close on the emptying fill; `CloseSummary` (gross / costs / net, R, weeks, MFE / MAE) · #109 |
| S9.3 | As the owner I want corporate actions handled (dividends received, splits, consolidations, takeovers, delisting) so P&L is right. | R2 | ✅ dividends (daily job proposes, `book.py cash dividend`), `book.py split`, `book.py corporate-close` · #133 |
| S9.4 | As the owner I want a short post-mortem per closed trade (agent draft from the record: entry conditions vs what happened) kept in the DB. | R2 | ✅ `postmortem_pack.py` + `/postmortem` → `postmortem` card via `record.py verdict --position`; daily runner · #134 |

### A10 — Portfolio view and risk

| ID | Story | Rel | Status |
|---|---|---|---|
| S10.1 | As the owner I want a **holdings view**: positions, shares, cost, last price, P&L, R, stop, risk at the stop, weeks held, next event. | R1 | ✅ `scripts/portfolio.py`, `risk.holdings` · #110 |
| S10.2 | As the owner I want **cash and equity** tracked (capital, deposits / withdrawals, realised and unrealised P&L), so sizing uses current equity. | R1 | ✅ `book.py cash`, `risk.equity` · #110 |
| S10.3 | As the owner I want **portfolio heat** (sum of open risk to the stops) and exposure by sector vs the limits in S0.3. | R1 | ✅ `risk.heat / exposure / breaches` · #110 |
| S10.4 | As the owner I want an equity curve, drawdown, and a benchmark comparison (e.g. FTSE AIM All-Share). | R2 | ✅ `portfolio.py --history`, `performance.py`; benchmark `Rules.benchmark` (default `^FTAS`) · #135 |
| S10.5 | As the owner I want a correlation / overlap check across holdings (same driver: fuel, consumer, rates). | R2 | ✅ `performance.correlation`, in `--history` and the weekly pack · #135 |
| S10.6 | As the owner I want a dashboard over the DB (holdings, weekly pack, history). | R2 | ⬜ · #136 |
| S10.7 | As the owner I want a drawdown circuit breaker (e.g. stop new entries if equity is X% below its high). | R3 | ⬜ (proposal; owner to set) · #143 |

### A11 — Learn and calibrate

| ID | Story | Rel | Status |
|---|---|---|---|
| S11.1 | As the owner I want every week's labels stored point-in-time, so they can be scored against later returns. | R1 | ✅ results DB, `date` per card (ADR 0007) |
| S11.2 | As the owner I want a **label scorecard**: forward 2/4/8/16-week returns (and excess vs market) for ACT vs WATCH vs PASS, per stage and per toolkit band. | R2 | ✅ · #137 |
| S11.3 | As the owner I want a backtest of the screen rules over history (point-in-time, survivorship caveat noted) to set the bands in S1.5. | R2 | ⬜ · #138 |
| S11.4 | As the owner I want agent value-added measured: did the validator's and strategist's downgrades avoid losers, did upgrades or retentions pick winners. | R3 | ⬜ · #144 |
| S11.5 | As the owner I want a trade journal: hit rate, average R, expectancy, by exit reason and by set-up type (pullback vs breakout vs blue sky). | R2 | ✅ · #137 |

### A12 — Operate

| ID | Story | Rel | Status |
|---|---|---|---|
| S12.1 | As the pipeline I want the Sunday scan scheduled. | R1 | ✅ `scan.yml` |
| S12.2 | As the owner I want one stock run on demand. | R1 | ✅ `stock.yml` |
| S12.3 | As the owner I want a **durable DB** that Actions, cloud sessions and my machine all reach (today the DB is an Actions artifact for 90 days). | R1 | ✅ Turso client (#103, ADR 0013); `scripts/db.py` push/pull and the dual-backend `db_backend` fixture (#116) |
| S12.4 | As the owner I want the agent stages (A2, A3, A7) runnable unattended (Actions or a scheduled Claude session) after the scan, reading the run from the DB. | R1 | ✅ `agents.yml` + `claude-code-action` (ADR 0019); inert until the owner adds a Claude secret · #123 |
| S12.5 | As the owner I want a daily workflow (A7) alongside the weekly one. | R1 | ✅ `daily.yml`, 17:47 UTC weekdays, `concurrency: book` shared with the scan · #120 |
| S12.6 | As the owner I want failures reported (ERROR labels, source outages, agent errors) without stopping the rest of the run. | R1 | ✅ toolkits (ERROR steps) and agents (`record.py error`, `outcome`) · #123 |
| S12.7 | As the owner I want notifications (email / push) for the weekly pack and the daily actions. | R2 | ⬜ · #132 |
| S12.8 | As the owner I want agent cost and runtime per run logged, so the free / subscription limits aren't hit by surprise. | R2 | ⬜ · #139 |

---

## 5. Data model to add (sketch)

Additive tables in the same append-only store (ADR 0007). Mutable states (an order getting
filled) are new **event rows**, never updates, so the current state is the latest event.

| Table | Key fields |
|---|---|
| `labels` | run_id, ticker, stage (`screen`/`validate`/`research`), label, confidence, reason. Or extend `steps.status` to act/watch/pass/error |
| `evidence` | evidence_id, ticker, url, title, publisher, published, retrieved, section, summary, impact (score, confidence, driver, horizon), run_id, agent |
| `positions` | position_id, ticker, side, opened_from_run_id, horizon, thesis, created_at |
| `position_events` | position_id, at, kind (drafted/approved/rejected/opened/revised/closed), payload JSON, actor, reason |
| `entry_conditions` | position_id, run_id, model, field, value / text (frozen copy) |
| `thesis_conditions` | position_id, condition (`field op value` or text), source evidence_id |
| `orders` | order_id, position_id, side, type (limit/stop/market), qty, price, valid_until, purpose (entry/stop/target/add/exit) |
| `order_events` | order_id, at, status (draft/approved/placed/filled/part/not_filled/cancelled/expired), broker_ref, actor |
| `fills` | fill_id, order_id, at, qty, price, commission, fees |
| `cash` | at, kind (deposit/withdrawal/dividend/fee), amount, currency |

---

## 6. Open questions for the owner

Nothing in this list has been assumed in the design above.

1. **Capital and risk**: starting capital, risk per trade, max open positions, max per
   position / sector, max portfolio heat.
2. **Broker and account**: which broker, account type (ISA / SIPP / GIA: affects tax and
   whether shorts or CFDs are possible), can it export fills, which order types does it
   support on AIM (stop-entry orders often aren't available for AIM names)?
3. **Label bands**: are the ACT / WATCH bands in A1 the right definition of "uptrend, not
   overextended, growth potential"? Should fundamentals be able to give ACT on its own, or
   only veto?
4. **Agents' authority**: may the validator only downgrade (proposed), or also upgrade? Should
   the monitor ever act without approval (e.g. cancel an unfilled entry after a profit
   warning)? Proposed: never.
5. **Where agents run**: Actions with an API key (cost), a scheduled Claude Code session
   (subscription), or by hand on Sunday? This decides S12.4.
6. **Web search sources** for research: which free sources are acceptable (search engine
   results, company sites, trade press, government sites) given the "terms unclear → flag"
   rule?
7. **Weekly pack delivery**: email, artifact page, GitHub issue / PR comment, or Notion?
8. **Entry timing**: place orders Sunday evening for Monday, or wait for Monday's open and
   use limits? Validity of an unfilled entry order: 1 week?
9. **Event rule**: hold positions through results, or trim or exit before them?
10. **Shorts**: the toolkits support `side=-1`; is the book long-only (as the strategy says)?

---

## 7. Release slices

| Release | Goal | Stories |
|---|---|---|
| **R1: walking skeleton** | Sunday → orders → fills → daily check → close, for one trade, by hand where needed | S0.3, S1.3–S1.5, S1.7, S2.1–S2.4, S3.3–S3.7, S4.2–S4.8, S5.1–S5.3, S5.5, S6.1–S6.4, S7.1–S7.3, S7.5–S7.7, S8.2–S8.4, S9.1–S9.2, S10.1–S10.3, S12.3–S12.5 |
| **R2: robust and automatic** | runs unattended, notifies, handles the edge cases | S0.4–S0.6, S1.6, S1.8, S2.5–S2.6, S3.8–S3.9, S4.9, S5.4, S6.5, S7.4, S7.8, S8.5, S9.3–S9.4, S10.4–S10.6, S11.2–S11.3, S11.5, S12.7–S12.8 |
| **R3: learn and refine** | measure and improve the models and agents | S3.10, S6.6, S7.9, S10.7, S11.4 |

Suggested build order for R1, each step usable on its own:

1. Portfolio config and the positions / orders / fills tables with a booking CLI (S0.3, S4.5,
   S5, S6, S9.1–S9.2, S10.1–S10.3). Trades can then be recorded by hand straight away.
2. ACT / WATCH / PASS labels and the "what we're looking for" ADR (S1.3–S1.5, S1.7).
3. Trade plan, sizing and portfolio fit → draft orders, with entry conditions linked (S4.2–S4.8).
4. Hosted DB (S12.3), so agents and Actions share state.
5. Validator skill (S2.1–S2.4).
6. Research skill with web search and the evidence table (S3.3–S3.7, closes #84).
7. Daily job, triggers and the monitor agent, with plan revisions (S7.1–S7.3, S7.5–S7.7, S12.5).
8. Open positions in the weekly review (S8.2–S8.4).
