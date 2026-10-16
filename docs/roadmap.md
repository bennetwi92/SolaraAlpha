# Roadmap: from research toolkits to a managed book

Written 2026-10-16, from the state of `main` at #100. This is the plan for getting from
"scores for 50 stocks" to "Claude proposes, designs and manages positions; the owner places the
orders by hand at a broker with no API". Steps become GitHub issues, so update the doc as they
are done or the plan changes.

## Target product

```
WEEKLY (Sunday)                                   DAILY (each trading day, after the close)
scan: screen → fundamentals → events              for every open position and working order:
        │ shortlist (one run_id)                    prices, stop/target hits, new RNS, news,
        ▼                                           vol shocks, results approaching
agent research per candidate (skill)                        │
        ▼                                                   ▼
trade design: entry / stop / targets / size       agent review (skill): hold / tighten /
  checked by code against portfolio limits          reduce / exit / cancel; research when
        ▼                                           something material happened
PROPOSALS ──► owner approves ──► owner places               ▼
  orders at broker ──► owner reports fills ──►    ACTIONS ──► owner places ──► owner reports
  LEDGER (orders, fills, positions, cash)            fills ──► LEDGER
        └──────────── feedback: every proposal and trade tracked against later returns ──┘
```

Principles carried over from the existing ADRs:
- **Code computes and enforces; the agent judges.** Sizes, limits, stop maths and state changes
  are code. Risk limits are checked in code, never left to a prompt.
- **Append-only state** (ADR 0007). An order changing status is a new row; current state is
  the latest row. Every decision links to the `run_id` and evidence it came from.
- **Point-in-time and no look-ahead** for every decision record, so the feedback loop is honest.
- **The owner is the execution layer.** Nothing is "live" until the owner says it was placed,
  and nothing is "filled" until the owner reports a fill.

## Where we are

Built and working:
- Data: Yahoo prices / fundamentals / futures / FX, FCA NSM filings with full text, GLEIF,
  Google News. Quality checks + trimming.
- Models (cards in [-1, +1]): technical (incl. `assess_position` with a chandelier stop),
  levels (stop, target, reward:risk, breakout trigger), relative strength, fundamentals,
  events pack, Jet2 fair value.
- Weekly pipeline (`scan.py`, `scan.yml`), results DB (`store.py`), two agent skills
  (`/events`, `/jet2-value`).

Gaps that block the product:

| Gap | Why it blocks |
|---|---|
| **No durable DB.** `data/solara.db` lives in an ephemeral container / Actions artifact. | Positions and orders are state that must survive between runs. This is the first blocker. |
| **`portfolio/` is empty.** No capital, risk budget, sizing, or limits. | A trade can't be designed without a size, and size needs the account and risk rules. |
| **No order / fill / position ledger**, and no way for the owner to report fills. | The core loop of the product. |
| **No decision layer.** The pipeline ends at "passed events"; ADR 0010's agent stage isn't built. | Nothing turns a shortlist into a proposal. |
| **No daily pipeline** (ADR 0010 lists it as not built). | Positions can't be managed. |
| **Only Jet2 has a research skill.** `/events` is generic; valuation is Jet2-only. | The other 49 names need a generic research playbook. |
| **Thresholds are placeholders; no evaluation.** | Proposals can't be trusted with real money until the screen is checked against returns. |
| **No owner-facing output** beyond the Actions job summary. | Proposals and daily actions must reach the owner, and quickly on daily exits. |
| `scan.yml` has never run (first schedule: Sun 18 Oct 2026). | Unverified in CI: network, timing, 50-stock runtime. |

Housekeeping: issues #75 and #76 are from the archived codebase (multi-asset mandate,
`config/data_sources.yaml`) and no longer apply. #84 says it is blocked by the DB, which now
exists (local); it is really blocked by the hosted DB (step 1).

## Plan

Each step is roughly one issue (some two). Order matters: 1 → 2 → 3 is the critical path;
4 and 5 need 3; 6 starts as soon as 2 exists and runs alongside.

### 0. Housekeeping (small)
- Close #75, #76 as not planned. Re-point #84 at step 1.
- Run `scan.yml` once by hand on 3-5 tickers, then the full watchlist; record runtime and any
  blocked hosts.

### 1. Durable results DB (hosted SQLite)
ADR 0007 already picks Turso (free tier, HTTPS API). Build the "thin executor swap": `store.py`
talks to either the local file or Turso via `settings` (`SOLARA_DB_URL`, `SOLARA_DB_TOKEN` in
`.env` / repo secrets / cloud environment). Both `scan.yml` and Claude Code cloud sessions must
reach it, so add Turso's host to the environment's network allowlist. Back up with a periodic
dump (a scheduled Action that uploads the SQL dump as an artifact) because the book will be the
only record of positions besides the broker.

### 2. The book: portfolio config + ledger + manual entry
- `config/portfolio.toml`: capital base, currency, risk per trade (% of equity), max open
  positions, max total open risk ("heat"), max position % of equity, max % of average daily
  value traded (AIM liquidity), broker costs (commission, spread estimate, stamp duty; AIM
  shares are generally exempt, to confirm), order types the broker supports.
- New append-only tables (sketch, refine in an ADR):
  - `proposals`: ticker, side, run_id, thesis, entry plan, stop, targets, size, valid-until,
    evidence links, the cards it came from.
  - `orders` + `order_events`: purpose (entry / stop / target / exit), type
    (market / limit / stop / stop-limit), qty, prices, time-in-force; status
    `proposed → approved → placed → part-filled → filled | cancelled | expired | rejected`,
    with who set it (agent / owner) and the broker reference.
  - `fills`: order, time, qty, price, fees, entered-at.
  - `cash`: deposits, withdrawals, dividends, fees.
  - `position_events`: stop / target changes with reason and run_id. Positions themselves
    are derived from fills, never stored as mutable rows.
- `solaraalpha/portfolio/`: `book.py` (ledger reads/writes, derived positions, cash, equity,
  open risk), `limits.py` (check a proposal against `portfolio.toml`).
- `scripts/book.py`: `show` / `approve` / `placed` / `fill` / `cancel` / `cash` / `reconcile`.
- Owner input, simplest first: tell Claude in plain words ("filled 1,200 JET2 at 1,532p, £10
  commission") and a `/book` skill turns it into a `scripts/book.py` call and reads it back for
  confirmation. Weekly `reconcile`: the owner pastes the broker's positions / cash and the book
  flags differences.

### 3. Trade design and sizing (code)
`portfolio/design.py`: from the levels and technical cards, build the order set for a candidate:
- Entry: limit near the close / pullback zone, or a stop order at the levels `trigger` when
  coiled. Valid for one week (weekly cadence).
- Stop: the tighter of levels `stop` and technical `stop`, as long as it isn't inside normal
  noise (≥ ~1 weekly ATR). Choose one rule in the ADR; don't tune per stock.
- Targets / scale-out: levels `target` as the first; trail the rest with the chandelier
  (matches "held longer while it keeps climbing"). Optional add-on rule for winners.
- Size: risk budget ÷ (entry − stop), capped by position %, liquidity cap and remaining heat.
  Round to whole shares; include costs in the risk.
- Output: a `proposal` + its `orders` in status `proposed`, rejected with reasons if any limit
  fails. Fully tested offline.

### 4. Weekly decision stage (agent)
Adds ADR 0010's agent stage after `events`:
- `/research <TICKER>` skill: a generic analyst playbook built from `/events` and
  `/jet2-value`: business and what drives earnings, recent results versus guidance, catalysts,
  valuation versus own history and peers, liquidity, what would invalidate the trade. Ends in a
  scored JSON note validated by a script and saved to the DB (this also closes #84).
- `/weekly-review` skill (orchestrator): reads the saved scan run and the book; researches the
  shortlist; ranks; for the chosen ones calls `portfolio/design.py`; writes proposals; produces
  the owner's weekly pack (proposals with rationale, positions, open risk, upcoming events).
- Owner approves or rejects each proposal (`/book approve <id>`); rejection reasons are kept.

### 5. Daily management stage
- `scripts/daily.py` (code): for every open position and working order, fetch daily bars and
  record: probable fills of working orders (price traded through the limit/stop → ask the owner
  to confirm), stop / target touched, gap and z-move, new NSM filings, results date within N
  days, chandelier stop for the coming week, days since entry. Saves a `daily` card per holding.
  Thin, so it can run in Actions.
- Rules from ADR 0003 stay weekly-close based; daily adds only hard events: an intraday stop hit
  (if the broker holds no resting stop), a material filing, a volatility shock, results ahead.
- `/daily-review` skill: only if something fired, research it (`/events`, `/research`), then
  decide hold / tighten stop / reduce / exit / cancel order, each with a reason. Writes
  `order_events` / `position_events` as `proposed` for the owner.
- Notify the owner (email or push) when there is an action. Silence means nothing to do.

### 6. Feedback loop (start early, runs in parallel)
- Every proposal, taken or not, is tracked forward: return at 2 / 4 / 8 / 16 weeks, max
  adverse / favourable excursion, whether stop or target hit first. This works from the
  saved scans with no extra data.
- Closed trades get a short `/post-mortem` note: thesis versus outcome, rule followed or not.
- Backtest the screen thresholds and stop rules on weekly history (the models are already
  look-ahead safe); remember the watchlist has survivorship bias.
- **Paper-trade first**: run steps 2-5 against a paper book for a few weeks before real money.
  The ledger is the same; only the "placed" step is skipped.

### Infra: where things run
- Deterministic code (scan, daily facts, backups): GitHub Actions, free, as now.
- Agent work (research, weekly review, daily review): Claude Code scheduled tasks in a cloud
  environment with this repo, reading and writing the hosted DB. Or the Claude Code GitHub
  Action with an API key (pay-per-use). This is issue #92's open question; decide it once for
  all agent jobs in one ADR. Chain: Action finishes → agent task starts after it (timed, or
  fired by the Action).
- The cloud environment's network policy must allow Yahoo, FCA, GLEIF, Google News, LSE and
  Turso; secrets (DB token) go in the environment, never in the repo.

### Writing the prompts (skills)
Every agent job is a skill in `.claude/skills/<name>/SKILL.md`, in the same shape as
`/events`:
1. **Role and horizon** (long, 2-16 weeks, weekly cadence).
2. **Exact commands** to gather inputs. The agent never queries the DB with raw SQL or edits
   state by hand; it calls scripts.
3. **Steps**, with what evidence each judgement needs and "unknown" when it's missing.
4. **Output schema** (JSON) checked by a `--validate` script before it is saved, plus a short
   human note.
5. **Hard limits restated as checks the script will enforce**, so a failed check is reported,
   not argued around.
6. **What to do on failure** (data blocked, stale consensus): report it in the output and on
   an issue, never guess.

Planned skills: `/research`, `/weekly-review`, `/daily-review`, `/book`, `/post-mortem`, with
`/events` and `/jet2-value` reused inside them.

## Open questions for the owner

These change the design, so they need answers before steps 2-3:
1. **Account**: starting capital, and the account type (ISA / SIPP / general account / spread
   bet / CFD)? This affects tax, costs, order types and whether fractional sizing works.
2. **Broker**: does it hold resting stop-loss orders (GTC)? Limit orders on AIM names? What
   commission? Partial fills? If stops can't rest at the broker, the daily job has to alert
   on stop hits itself.
3. **Risk rules**: risk per trade (e.g. 0.5-1% of equity), max positions, max total open risk,
   max position size?
4. **Approvals**: approve every order, or pre-approve mechanical ones (e.g. raising a stop)?
5. **Notifications**: email, phone push, a GitHub issue per proposal, or a dashboard page?
6. **Paper first**: how long, and what result is good enough to go live?
7. **Data terms**: Yahoo's terms are personal, non-commercial. That's fine for your own
   account; it needs revisiting if the fund ever manages outside money.
