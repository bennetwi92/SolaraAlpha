# CLAUDE.md

Guidance for Claude Code working in this repo. Read this first, every session.

## Project

Solara Alpha is a mini hedge fund's codebase. Work happens in two phases:

1. **Analysis** (current) — data acquisition, research, exploratory analysis.
2. **Portfolio management** (later) — construction, risk, monitoring.

There is **one developer** (the owner) working with Claude Code. Optimise for speed of
iteration and clarity, not for team process. **Prefer lightweight, stripped-back
implementations over complexity** — the simplest thing that works, fewest dependencies,
no speculative options or layers. No assets, markets, instruments or strategies
have been chosen yet beyond what's listed below — **never assume any**. Ask when a task
depends on that choice.

Current state:
- **Strategy: long trades seeking bursty profits over ~2-16 weeks** (held longer while the stock
  keeps climbing). Models are judged by usefulness over that horizon.
- **Trading frequency is weekly** unless the owner says otherwise. Weekly bars end Friday
  (`to_weekly`), and only completed weeks are used.
- Working on **single-stock analysis**; first example is Jet2 (`JET2.L`, AIM-listed). The
  pipeline universe is the **FTSE AIM UK 50** (current constituents, hand-copied snapshot in
  `config/watchlist.toml` — refresh after each quarterly FTSE review; not point-in-time).
- Price data is **fetched fresh each run** (no `@parquet_cache`). With `--save` the raw fetch is
  also snapshotted into the results DB (`solaraalpha.store`) — for the record, never as a cache.
- **Results DB**: scripts' `--save` records the run, model cards and input snapshots in an
  append-only SQLite DB (the **book** — positions, orders, fills, cash — lives in the same DB,
  `portfolio/book.py`: state changes are new event rows, never updates) — hosted on **Turso** when `SOLARA_TURSO_URL` / `SOLARA_TURSO_TOKEN` are
  set (GitHub secrets, cloud env vars, `.env`), else the local `data/solara.db`. See
  docs/decisions/0007, 0013.
- Price history: fetch the **last 5 years** (default), then cut per stock to the span after
  the last blocking data issue (`trim_to_consistent`). Don't pull long history yet.
- The technical models (`analysis/technical.py`, `analysis/levels.py`) must stay **generic across
  stocks** — no per-stock tuning. Other models (e.g. fundamentals) are stock-specific: the
  scoring in `analysis/fundamentals.py` is generic, and per-stock adjustments (accounting
  quirks) are added only when a stock needs one.
- Fundamentals are fetched fresh each run too (`fetch_fundamentals`). Statement dates are
  **period ends, not publication dates**: anything as-of a past date goes through `known()`
  (statutory reporting lags), never the raw frame.
- Events & catalysts are split: `scripts/events.py` gathers facts programmatically (filings,
  market reactions, expected events, headlines) into an event pack; interpretation is done by
  an agent via the `/events` skill (`.claude/skills/events/SKILL.md`), and the Sunday research
  stage is the `/research` skill (nine-section strategist's note with web search), both
  persisted through `record.py`. See docs/decisions/0006, 0016.
- **Jet2 fair value** (`analysis/jet2_value.py`, `/jet2-value` skill) is deliberately Jet2-only:
  a driver P&L calibrated to consensus EPS (`CONSENSUS`, `REFERENCE` — hand-updated, dated;
  refresh after each results / trading update), updated for the live fuel curve and FX, valued
  at the holding horizon; news goes in as driver shocks. See docs/decisions/0009.
- Model outputs for the scorecard are scores in **[-1, +1]** (+ = bullish / favourable).
- Toolkits run as **one serialised weekly pipeline** (docs/decisions/0010, 0014): toolkits are
  registered by id in `solaraalpha/toolkits.py`, each with `act=` / `watch=` bands that label a
  stock **ACT / WATCH / PASS** (ERROR if it raised); labels combine ERROR > PASS > WATCH > ACT.
  The stages are `WEEKLY` in `solaraalpha/pipeline.py` — `screen` (technical + levels + relative
  strength + liquidity, on every stock; liquidity has no bands and feeds sizing) → `fundamentals`
  (on ACT and WATCH) → `events` (on ACT; it has no bands and is the input to research, not a
  gate). The **screen label** combines the four
  screening toolkits and is saved per stock in the `labels` table (`store.shortlist`), with
  `watch_on` (what a WATCH stock waits for, JSON) and `rank` (ACT order, 1 = best). Stocks:
  `config/watchlist.toml` (hand snapshot with a `snapshot` date; the scan warns once a quarterly
  FTSE review month has passed since it). After the pipeline the scan **reviews every open position** (all
  toolkits with the position's entry → a `review` card; the stop ratchets up and is drafted as
  an approved order with `--save`; an exit is reported, never drafted; docs/decisions/0015).
  GitHub Actions run it Sunday (`scan.yml`) or on demand for one stock (`stock.yml`). Bands are
  placeholders until backtested.
- **Daily job** (docs/decisions/0017): `scripts/daily.py` / `daily.yml` run every weekday after
  the close over open positions, the latest scan's WATCH tickers and pending breakout entries:
  price triggers (stop closed through / touched, target hit, gap > 2 ATR), coil and entry
  breaks, new filings and headlines since the last daily run, saved to the `triggers` table.
  Owner actions and agent reviews are separated in the report; the **monitor agent**
  (`/monitor`, docs/decisions/0018) runs only on agent triggers and *proposes* plan revisions
  (`revision_proposed` events) that the owner accepts or declines in `book.py`; agents never
  change orders. Market-wide volatility is not built yet.
- **Agents run unattended when the owner adds a Claude secret** (docs/decisions/0019):
  `agents.yml` runs after the scan (`/validate` → `/research` → `plan.py` → `weekly_pack.py`)
  and after the daily job (`/monitor`) via `claude-code-action`, capped by
  `max_agent_tickers` and `--max-turns`; without `CLAUDE_CODE_OAUTH_TOKEN` or
  `ANTHROPIC_API_KEY` it skips and nothing is spent. A failed invocation is recorded as an
  ERROR verdict (`record.py error`); the job fails only if every item errored.
- **Agents write through one door** (docs/decisions/0016): a `verdict.json` (schema:
  `record.py schema`) recorded with `scripts/record.py verdict`, which writes the `labels` row,
  a card under `model = stage` and the `evidence` rows on the same run. Agents can only
  **downgrade** a label (ACT > WATCH > PASS; ERROR always allowed). `record.py prior` gives the
  previous verdicts and the filings already interpreted.
- **Relative strength** (`analysis/relative_strength.py`) compares each stock with its market
  index, industry index / ETF and peers, listed per stock in `config/benchmarks.toml` (market
  defaults by listing suffix). Method is generic; the comparison lists are per stock. See 0011.
- All price data must pass through `check_prices` (review the issues), `clean_prices` and
  `trim_to_consistent` before use.
- **Portfolio rules** (capital, risk per trade, position / sector / heat limits, entry validity,
  long-only) live in `config/portfolio.toml`, read through `portfolio.rules.load_rules`. All
  values are **placeholders** until the owner sets them and flips `confirmed = true`; reports
  print a warning while it is false. See epic #104.

## Hard rules

- **Python only.**
- **Free data sources only.** No paid APIs, paid tiers, or trial keys that later charge. A free
  tier with a key is fine (key goes in `.env`). If a source's terms are unclear, flag it before
  building on it. Record every source used in `docs/data-sources.md`.
- **Never commit data or secrets.** `data/` and `.env` are gitignored. Keys are read through
  `solaraalpha.config.settings`, never hard-coded.
- **No look-ahead.** Any analysis over time-series must use only information available at each
  point in time. Be explicit about timestamps, timezones and publication lags.
- **Run `make check` before every commit.** It must pass.

## Commands

| Task | Command |
|---|---|
| Install / sync deps | `make install` (first time) or `uv sync` |
| Add a dependency | `uv add <pkg>` (dev-only: `uv add --dev <pkg>`) |
| Format + autofix | `make fmt` |
| Lint / types / tests | `make lint` / `make type` / `make test` |
| Everything (pre-commit gate) | `make check` |
| Tests incl. live network | `make test-all` |
| Single test | `uv run pytest tests/test_x.py::test_name -q` |
| Run a script | `uv run python scripts/<name>.py` |
| Stock prices + quality report | `uv run python scripts/stock_prices.py JET2.L` |
| Technical scorecard | `uv run python scripts/technical.py JET2.L [--entry-date D]` |
| Levels + trade plan | `uv run python scripts/levels.py JET2.L [--short]` |
| Fundamentals scorecard | `uv run python scripts/fundamentals.py JET2.L [--as-of D]` |
| Relative strength vs market / industry / peers | `uv run python scripts/relative_strength.py JET2.L [--peers EZJ.L IAG.L]` |
| Event pack (filings, reactions, expected events, news) | `uv run python scripts/events.py JET2.L [--days 7] [--horizon 16]` |
| Jet2 fair value + news/scenario shocks | `uv run python scripts/jet2_value.py [--weeks 12] [--scenario fuel_spike] [--shock seats=+2%] [--list]` |
| Jet2 news analyst (agent) | `/jet2-value <news or question>` in Claude Code |
| Read one announcement | `uv run python scripts/announcement.py <NSM url>` |
| Events analyst (agent; persists its note via `record.py`) | `/events JET2.L [--run N]` in Claude Code |
| Research analyst (agent): 9-section pre-trade note with web search, evidence logged | `/research JET2.L --run N` or `/research --run N` in Claude Code |
| Validator pack (DB only, independent of other agents) | `uv run python scripts/validate_pack.py JET2.L --run N` / `--run N --all` |
| Validator (agent) | `/validate JET2.L --run N` or `/validate --run N` in Claude Code |
| Monitor pack for a daily run's triggered positions (then vs now, thesis, filing text) | `uv run python scripts/monitor_pack.py --run N [--position ID]` |
| Monitor (agent): structural change? propose hold / tighten / partial / exit / revise target | `/monitor --run N` or `/monitor --position ID` in Claude Code; then `book.py revisions` / `accept REV_ID` / `decline REV_ID --reason` |
| Validator disagreement log (toolkits not warranted, concerns, rate per run) | `uv run python scripts/validator_stats.py [--since D]` |
| Post-mortem pack for a closed position (record + weekly bars, hindsight labelled) | `uv run python scripts/postmortem_pack.py POSITION_ID` / `--since ISO` |
| Post-mortem (agent): set-up, what worked / failed, signal right or wrong, rule proposals | `/postmortem POSITION_ID` in Claude Code → `record.py verdict ... --position ID` |
| Record an agent verdict / read prior work / print the schema | `uv run python scripts/record.py verdict FILE --actor agent:NAME` / `revision FILE --position ID` / `prior JET2.L [--stage research]` / `schema`; runner: `error TICKER --run N --stage S --reason ...` / `queue --run N --stage S` / `latest-run` / `outcome --run N --stage S` |
| Daily check: prices, triggers (stops, targets, gaps, breaks), new filings and headlines | `uv run python scripts/daily.py [--save] [--report FILE]` |
| Weekly pipeline over the watchlist | `uv run python scripts/scan.py [--save]` |
| Some stocks through the pipeline / one toolkit | `uv run python scripts/scan.py JET2.L` / `JET2.L --toolkit technical [--param k=v]` |
| List stages and toolkits | `uv run python scripts/scan.py --list` |
| Book trades by hand (draft, approve, amend, reject, tickets, placed, fills, exits) | `uv run python scripts/book.py new JET2.L --entry 1520 --stop 1400 --qty 100` / `list` / `show ID` / `approve ID` / `amend ID --qty N --price P --stop P` / `reject ID --reason TEXT` / `tickets` / `placed ORDER_ID --ref REF` / `fill ORDER_ID --qty N --price P [--commission X]` / `unfilled ORDER_ID` / `cancel ORDER_ID` / `expire` / `exit ID --reason stop|target|thesis_broken|...` / `cash dividend TICKER GBP --at EXDATE` / `split ID --ratio 2:1` / `corporate-close ID --price P --reason takeover|delisting` (`--db PATH` for a local file) |
| Draft the week's trade plans from a saved scan run (final ACT labels → plan → fit → draft positions) | `uv run python scripts/plan.py --run N [--dry-run]` |
| Weekly pack for a run (new plans with approve/reject commands, fit rejections, watch list, open positions, money, errors) | `uv run python scripts/weekly_pack.py --run N [--out FILE] [--report FILE]` |
| Portfolio view: holdings, cash, equity, heat, sector exposure, breaches | `uv run python scripts/portfolio.py [--save] [--price JET2.L=1520]` (cash in: `book.py cash deposit 10000`) |
| Equity curve, time-weighted return, drawdown, vs benchmark, correlated holdings | `uv run python scripts/portfolio.py --history` |
| Label scorecard (forward returns per label / toolkit band) and trade journal | `uv run python scripts/learn.py scorecard [--since D]` / `journal` |
| Results DB: which is in use, copy local ↔ Turso | `uv run python scripts/db.py status` / `push [--from PATH] [--append-new-runs]` / `pull [--to PATH]` |
| Save a run to the results DB | add `--save` to `stock_prices` / `technical` / `levels` / `relative_strength` / `fundamentals` / `jet2_value` / `scan` |
| Notebooks | `make notebook` |

Always use `uv run …` (or `make`) — never bare `python`/`pip`. Never edit `uv.lock` by hand.

## Layout

```
src/solaraalpha/        importable package — all reusable code lives here
  config.py             Settings (env vars prefixed SOLARA_, loaded from .env); data paths
  log.py                get_logger(__name__)
  store.py              results DB (SQLite): runs, model cards, pipeline steps, labels,
                        price/fundamental snapshots; start_run + save_ticker (a scan = one run) /
                        save_run / save_label / load_results / load_steps / load_labels /
                        shortlist / latest / load_prices — see docs/decisions/0007
  turso.py              hosted results DB: sqlite3-style connection to Turso over HTTP — 0013
  agents.py             Verdict / Evidence / KillCriterion models, record_verdict (labels row +
                        card + evidence, downgrade-only), prior — see docs/decisions/0016
  validation.py         the validator's pack from the DB: independent cards, steps, label, price
                        snapshot as seen, fundamentals known at the card date, filing headlines
  postmortem.py         the post-mortem pack for a closed position: close summary, plan / set-up,
                        entry conditions, reviews, revisions, triggers, agent verdicts + evidence,
                        weekly bars as seen at the close (post-exit weeks = hindsight) — 0016
  monitoring.py         the monitor's pack: position then (entry conditions) vs now (fresh cards),
                        thesis conditions evaluated, triggers + filing text — docs/decisions/0018
  toolkits.py           toolkit register: id -> run fn + act / watch bands; Inputs (lazy,
                        shared per-ticker data)
  pipeline.py           WEEKLY stages (runs_on), clauses, band / combine / screen_label,
                        run_pipeline (stage by stage) — see docs/decisions/0010, 0014
  data/                 fetching, cleaning, storage of external data
    http.py             shared requests session with retry/backoff — use for all HTTP
    cache.py            @parquet_cache decorator for DataFrame-returning fetchers
    prices.py           daily OHLCV for one stock (Yahoo chart API); dividends and splits
                        (fetch_corporate_actions, same endpoint)
    fundamentals.py     statements, TTM EPS, P/E / forward P/E / market cap for one stock
                        (Yahoo fundamentals-timeseries API), tidy long frame
    announcements.py    regulatory filings for one issuer (FCA NSM, by LEI), ticker → LEI
                        (LSE + GLEIF), announcement full text
    news.py             press headlines (Google News RSS), discovery only
    futures.py          commodity futures curves, one Yahoo ticker per contract month
    clean.py            check_prices / clean_prices / trim_to_consistent / to_weekly
  analysis/             research & analytics code
    technical.py        weekly technical model (any stock): trend/momentum/entry scores,
                        open-position assessment — see docs/decisions/0003
    levels.py           weekly levels model (any stock): S/R zones, trendlines, 52w/5y
                        highs/lows, blue sky, coil (squeeze → breakout trigger);
                        stop/targets/reward:risk — see docs/decisions/0004
    fundamentals.py     fundamentals model (one stock): growth/value/quality scores,
                        point-in-time via publication lags — see docs/decisions/0005
    relative_strength.py  relative strength (any stock) vs market, industry, peer basket:
                        vol-scaled excess returns, peer rank — see docs/decisions/0011
    liquidity.py        liquidity (any stock): median daily traded value (20 / 60 sessions),
                        zero-volume share, Corwin-Schultz spread proxy, max position at max_adv_pct
    charts.py           weekly_chart: candles + 10/40w MAs + levels, coil, trigger, stop/target
                        + volume as a PNG (Agg) for the validator's pack
    scorecard.py        label scorecard: 2/4/8/16-week forward returns (and excess vs market) per
                        saved label, by stage x label and toolkit x band, no look-ahead
    events.py           events model (any stock): market reaction per filing, expected
                        scheduled events from the issuer's own history — see docs/decisions/0006
    jet2_value.py       Jet2-only fair value: consensus-calibrated FY27/FY28 driver P&L, live
                        fuel curve / FX, horizon valuation, news shocks, Monte Carlo — 0009
  portfolio/            portfolio construction, booking and risk
    rules.py            Rules dataclass + load_rules (config/portfolio.toml, validated)
    book.py             the book: positions, orders, fills, cash, entry/thesis conditions as
                        append-only event tables in the results DB; booking operations (draft,
                        approve, amend, fill → protective stop, exit → auto-close);
                        position_state, close_summary, to_account
    risk.py             holdings / equity / heat / exposure / breaches vs the rules; sector_of
                        (optional `sector` per stock in config/benchmarks.toml)
    plan.py             TradePlan + make_plan: entry (breakout trigger or limit), stop, targets,
                        risk-based size with caps, review dates, flags — see docs/decisions/0015
    fit.py              portfolio fit before drafting: slots, sector cap, heat (reject);
                        correlation, event clash (flag); rank_score — 0015
    planner.py          plan_run: a saved scan run's final labels → plans → fit → draft positions
                        with entry conditions (frozen cards) and thesis conditions (kill criteria)
    weekly_pack.py      build_pack (run + book → data) and render_pack (→ markdown), the Sunday page
    triggers.py         daily triggers (pure): stop_close / stop_touched / target_hit / gap /
                        watch_break / entry_break / filing / news — see docs/decisions/0017
    daily.py            run_daily (what to check, fetch, evaluate, save the triggers) + render_daily
    review.py           weekly review of open positions: every toolkit re-run with the entry,
                        ReviewCard (ratcheted stop, deterioration, horizon rule, action), raise_stop
    performance.py      equity_curve (rebuilt from cash + fills + closes), drawdown, time-weighted
                        return vs Rules.benchmark, correlation of holdings' weekly returns
    journal.py          trade journal: closed trades (close summary + set-up type from the plan),
                        hit rate / average R / expectancy by exit reason and set-up
scripts/                thin CLI entry points (argparse) that call into the package
config/watchlist.toml   stocks the weekly pipeline runs (FTSE AIM UK 50 snapshot)
config/benchmarks.toml  per-stock market / industry / peers for relative strength (+ sector)
config/portfolio.toml   portfolio rules: capital, risk per trade, limits (placeholders until confirmed)
.github/workflows/      ci.yml (checks), scan.yml (weekly pipeline + weekly pack, Sunday), daily.yml
                        (weekday daily check), agents.yml (unattended agents after both; inert
                        without a Claude secret), stock.yml (one stock)
.claude/skills/events/  agent skill: interprets an event pack into analysis.json / analysis.md
.claude/skills/jet2-value/  agent skill: maps Jet2 news to driver shocks and reports the impact
.claude/skills/validate/  agent skill: re-checks a run's screen labels against the pack; downgrade only
.claude/skills/research/  agent skill: strategist's 9-section pre-trade note, web search, evidence → record.py
.claude/skills/monitor/   agent skill: structural change or noise on a triggered position; proposes one action
.claude/skills/postmortem/  agent skill: post-mortem of a closed trade from its pack; setup / execution scores
notebooks/              exploration only; promote anything reused into src/
tests/                  pytest; mirrors src/ structure; fakes.py (FakeTurso), conftest.py (db_backend)
data/                   gitignored; raw/ processed/ cache/ created on demand
docs/
  data-sources.md       register of every external data source, its terms and limits
  user-story-map.md     end-to-end portfolio flows (weekly + daily), what's built vs to deliver
  decisions/            short ADRs for non-obvious choices (NNNN-title.md)
```

## Conventions

- **Python ≥3.12**, type hints on all public functions. `from __future__ import annotations`
  at the top of modules.
- **pandas** is the default tabular library; **parquet** is the default storage format.
  Time-indexed frames use a `DatetimeIndex`, timezone-aware (UTC) unless there's a reason not to.
- Fetchers return tidy DataFrames and are wrapped with `@parquet_cache(ttl=...)` so research
  doesn't hammer free APIs. Write raw downloads to `settings.raw_dir`, cleaned data to
  `settings.processed_dir`. `settings.cache_dir` is disposable.
- Use `solaraalpha.data.http.session()` / `get_json()` for HTTP — never a bare `requests.get`.
- Logging via `get_logger(__name__)`, not `print`, in package code. `print` is fine in scripts
  and notebooks.
- Keep modules small and functional; prefer plain functions + dataclasses over class
  hierarchies. Don't build abstractions until there's a second use.
- Ruff (line length 100) formats and lints; mypy type-checks `src/` and `tests/`.

## Testing

- Every new module in `src/` gets tests. Tests must run offline and fast by default.
- Tests that hit a real external source are marked `@pytest.mark.network`; `make test` skips
  them, `make test-all` runs them.
- For fetchers, test parsing against a small saved fixture (in `tests/fixtures/`), not the
  live API.
- Use the `tmp_cache` fixture (or `tmp_path`) — tests must never write into `data/`.
- Every DB table is proven on both backends: DB tests take the `db_backend` fixture (a `tmp_path`
  SQLite file, then `FakeTurso` from `tests/fakes.py`) as the `path` argument to store functions.

## Notebooks

- Exploration only. Name `NN-short-topic.ipynb`. Import from `solaraalpha`; don't define
  reusable logic in notebooks.
- Clear outputs before committing large notebooks.

## Issues

GitHub issues are the backlog and the hand-off between threads/agents.
- One issue = one deliverable. Title is imperative ("Persist events notes to the DB").
- Body: **Context** (why, links to ADRs/PRs), **Scope** (what's in / out), **Acceptance**
  (checkable list), **Open questions** (anything needing the owner's choice — don't assume).
- State dependencies explicitly ("Blocked by #N"). Keep it short; detail goes in the PR/ADR.
- PRs reference their issue (`Closes #N`). Note decisions taken during the work on the issue.

## Workflow for Claude

- Work on the branch you're given; small, focused commits with descriptive messages.
- Before committing: `make fmt && make check`.
- **Every session ends with a PR, conflict-free.** Do this every time, without asking:
  1. Before pushing, `git fetch origin main` and `git merge origin/main` into your branch.
     Resolve any conflicts (keep both sides' intent; regenerate `uv.lock` with `uv lock`, never
     by hand), then `make check` again.
  2. Push, then **raise a PR** against `main` (or update the existing open one for the branch).
     Reference the issue (`Closes #N`) when there is one.
  3. Check the PR's mergeable state. If GitHub reports a conflict (e.g. `main` moved since
     step 1), repeat step 1 and push — never rebase or force-push a branch with an open PR.
  4. **Enable auto-merge (squash)** so it merges as soon as CI passes; keep it open (no
     auto-merge) only if the owner says to or a decision is still open on the issue.
  Ask the owner only when both sides changed the same logic and either choice loses behaviour.
- When adding a data source: add the fetcher in `src/solaraalpha/data/`, a fixture-based test,
  and an entry in `docs/data-sources.md`.
- When adding a toolkit: register it in `toolkits.py` with `@toolkit(id, act=..., watch=...)`
  (its verdict card under its own id, with a `date` field), then add it to a stage in `WEEKLY`.
- When making a non-obvious design choice, add a short ADR in `docs/decisions/`.
- Update this file when conventions, layout or commands change.
