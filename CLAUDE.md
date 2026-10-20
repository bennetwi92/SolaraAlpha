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
- **What we are looking for is defined in docs/decisions/0022** (the owner's paragraph: catalyst-led
  AIM trading, volatility wanted, screen = ranked wide net to the agents). Check toolkits, bands
  and agent prompts against it.
- **Strategy: long trades seeking bursty profits (+15–30%) over ~1-10 weeks** (held longer while the stock
  keeps climbing). Models are judged by usefulness over that horizon.
- **Trading frequency is weekly** unless the owner says otherwise. Weekly bars end Friday
  (`to_weekly`), and only completed weeks are used.
- Working on **single-stock analysis**; first example is Jet2 (`JET2.L`, AIM-listed). The
  pipeline universe is the **FTSE AIM 100 plus the FTSE AIM UK 50** (104 stocks; snapshot in
  `config/watchlist.toml`, refreshed after each quarterly FTSE review with
  `scripts/universe.py --write`, run by hand, never in CI; not point-in-time).
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
  (the real results filing date via `with_publication`, else statutory reporting lags), never the
  raw frame.
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
  stock **ACT / WATCH / PASS** (ERROR if it raised); since ADR 0022 the bands are information,
  not the gate. The stages are `WEEKLY` in `solaraalpha/pipeline.py` — `screen` (technical +
  levels + relative strength + liquidity, on every stock; liquidity has no bands and feeds
  sizing, but its ERROR is the stock's ERROR) → `fundamentals` (every stock without an ERROR) →
  `events` (the shortlist). **The screen is a ranked shortlist** (`pipeline.screen`, ADR 0014
  amendment #192): past a sanity floor (no ERROR, `min_adv_gbp`), stocks are ranked by
  `portfolio.fit.rank_score` (mean of technical `trend`, levels `compression`, fundamentals
  `growth`); the top `shortlist_size` (7) plus up to `shortlist_catalysts` (3) with a fresh
  catalyst (`solaraalpha.catalysts.fresh_catalysts`: new filings, headlines, scheduled events)
  are ACT, the rest keep their band label capped at WATCH. Saved per stock in the `labels`
  table (`store.shortlist`) with `rank`, `shortlist` (why) and `watch_on` (JSON). **The rank is
  unvalidated**: every pack the agents read says so (`fit.RANK_NOTE`, #210). Levels is
  compression / breakout context (`compression`, `compressed`, `wedge`, `new_high`, ADR 0004
  amendment #194; the breakout `trigger` only when `compressed`, amendment #210); relative
  strength is agent context with no bands (per-horizon z, `leadership` and `rs_spike` flags; ADR
  0011 amendment #210, docs/research/0001). Stocks:
  `config/watchlist.toml` (hand snapshot with a `snapshot` date; the scan warns once a quarterly
  FTSE review month has passed since it). After the pipeline the scan **reviews every open position** (all
  toolkits with the position's entry → a `review` card; the trailing stop is context for the
  daily monitor, never drafted (ADR 0024); an exit is reported, never drafted; docs/decisions/0015).
  GitHub Actions run it Sunday (`scan.yml`) or on demand for one stock (`stock.yml`). Bands are
  placeholders until backtested.
- **Daily job** (docs/decisions/0017): `scripts/daily.py` / `daily.yml` run every weekday after
  the close over open positions, the latest scan's WATCH tickers and pending entries (draft /
  approved positions with an unfilled entry order): price triggers (stop closed through /
  touched, target hit, gap > 2 ATR), coil breaks and pullbacks, entry breaks, machine thesis
  conditions re-evaluated on fresh cards (`thesis_hit`), dividends and splits not yet booked, a
  scheduled event two sessions out, orders past their validity, new filings and headlines since
  the last daily run, saved to the `triggers` table. **Every open position and pending entry
  gets a `daily_review` every weekday** (docs/decisions/0024): the **monitor agent**
  (`/monitor`, docs/decisions/0018) re-evaluates each one (queue capped by `max_agent_tickers`,
  event-triggered first) and sets the stop by judgement (the levels / ATR / chandelier stops
  are context). Its revision is **applied as it is recorded** (open: tighten_stop /
  take_partial / exit / revise_target / add; pending: amend_entry / cancel; `hold` changes
  nothing; adds are the agent's call, refused by the book over the portfolio limits, ADR 0025);
  the book refuses an impossible one (recorded as declined). The owner is told in the inbox
  exactly what to change in Trading212 and can reverse it with `book.py undo REV_ID --reason`. The **market regime** (`analysis/regime.py` on `Rules.benchmark`, `^FTAI`: drawdown, 1-day z, realised
  vol percentile → calm / stressed / risk_off, placeholders) heads the daily report, is saved as a
  `regime` card, shows in the weekly pack and flags every plan `market risk_off` (advisory).
- **Shadow book** (docs/decisions/0028, recording only): beside every decision, the `shadow`
  table (`portfolio/shadow.py`) records what fixed rules would have done (top 5 by rank, random 2
  from the shortlist, breakout entry, 2-ATR / levels stops, chandelier trail, flat 2%), written
  once per full weekly scan by `scan.py --save` (screen baselines) and `plan.py` (agent-paired).
  Never traded; a later simulator replays it. **Research protocol** (docs/decisions/0029): target
  `burst_10w`, a pass bar fixed in advance, and a **locked holdout** (signal Fridays 2025-10-24
  to 2026-10-16): never look at it until the owner signs off a candidate.
- **Agents run unattended when the owner adds a Claude secret** (docs/decisions/0019):
  `agents.yml` runs after the scan (`/validate` → `/macro` → `/research` → `/pm` → `plan.py` → `weekly_pack.py`)
  and after the daily job (`/monitor`) via `claude-code-action`, capped by
  `max_agent_tickers` and `--max-turns`; without `CLAUDE_CODE_OAUTH_TOKEN` or
  `ANTHROPIC_API_KEY` it skips and nothing is spent. A failed invocation is recorded as an
  ERROR verdict (`record.py error`); the job fails only if every item errored.
- **Notifications** (docs/decisions/0021, 0026): the workflows post to the GitHub issue labelled
  `inbox` only when `scripts/notify.py` has something for the owner; silence otherwise. Plain
  words: per stock, numbered Trading212 steps (`portfolio/broker.py`: every approved order not
  yet placed, the order it replaces cancelled first, what to place once it fills, price alerts
  for the targets, why, what to tell back), then checks to report back (stop should have sold,
  order past its date, dividend), alert changes, information, errors, undo commands.
- **No owner approval** (docs/decisions/0026): plans are approved as they are drafted and the
  pipeline's revisions (pre-results trims / exits) are applied like the agents'; the owner only
  places orders, sets alerts and reports placements and fills (`book.py reject` / `undo` override).
- **Agents write through one door** (docs/decisions/0016): a `verdict.json` (schema:
  `record.py schema`) recorded with `scripts/record.py verdict`, which writes the `labels` row,
  a card under `model = stage` and the `evidence` rows on the same run. Agents can only
  **downgrade** a label (ACT > WATCH > PASS; ERROR always allowed). `record.py prior` gives the
  previous verdicts and the filings already interpreted.
- **The portfolio manager agent owns the allocation** (docs/decisions/0027): `/pm` runs once per
  weekly run after research, reads the PM pack (`scripts/pm_pack.py`: candidates, the whole book,
  money, regime, macro, correlations) and records one `pm` verdict (ticker `_BOOK`) with the
  `allocation` (research-ACT tickers, `risk_pct` inside `risk_per_trade_range` 1-3% by
  conviction, priority order) and `rotations` (exit an open position / cancel a pending entry,
  applied as recorded). The planner follows it and the PM approves its plans (`agent:pm`);
  without a PM verdict (or an ERROR) plans fall back to `risk_per_trade` in screen-rank order,
  approved by the pipeline. The PM memo heads the weekly pack and follows the inbox steps.
- **The research agent owns the entry and the stop** (docs/decisions/0023): a research ACT must
  carry `trade` (`entry_type` limit = at or below / stop = buy the break, `entry`, `stop` where
  the set-up is wrong, `stop_reason`, optional `valid_days`), saved as `trade_*` on its card
  (`agents.trade_levels`). The planner only sizes, fit-checks and approves it; an ACT without agent
  levels is reported, never drafted. The levels / technical stops are context, not rules.
- **Relative strength** (`analysis/relative_strength.py`) compares each stock with its market
  index, industry index / ETF and peers, listed per stock in `config/benchmarks.toml` (market
  defaults by listing suffix). Method is generic; the comparison lists are per stock. See 0011.
- All price data must pass through `check_prices` (review the issues), `clean_prices` and
  `trim_to_consistent` before use.
- **Broker: Trading212, Stocks & Shares ISA** — long only, no margin, no commission, AIM has no
  stamp duty; not every AIM stock is offered there. Market, limit, stop (market) and stop-limit
  orders and price alerts; **one open order per stock**, so the book never holds two live orders
  for a stock: an exit / partial / add takes the stop order off until it fills or ends, a part
  fill on a buy cancels the rest, no target orders (targets are price alerts). Breakout entries
  are stop market orders (ADR 0026).
- **Portfolio rules** (capital, risk per trade, position / sector / heat limits, entry validity,
  long-only, the pre-results event policy, the drawdown circuit breaker) live in `config/portfolio.toml`, read through `portfolio.rules.load_rules`. The owner
  set them on 2026-10-20 (`confirmed = true`: £5,000, 2% risk (1-3% by the PM's conviction, ADR 0027), 5 positions, 25% / 40% position
  / sector caps, 10% heat, breaker on at 15%); reports warn whenever `confirmed` is false. See
  epic #104.

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
| Macro analyst (agent): one shared sector / macro note per group of the run's ACT stocks | `/macro --run N` in Claude Code (`record.py queue --stage macro` lists the groups) |
| Research analyst (agent): 9-section pre-trade note with web search, evidence logged | `/research JET2.L --run N` or `/research --run N` in Claude Code |
| PM pack for a scan run (candidates, whole book, money, regime, macro, correlations) | `uv run python scripts/pm_pack.py --run N` |
| Portfolio manager (agent): the week's allocation (1-3% risk by conviction), rotations, the PM memo; no owner approval | `/pm --run N` in Claude Code → `record.py verdict pm.json --actor agent:pm` |
| Validator pack (DB only, independent of other agents) | `uv run python scripts/validate_pack.py JET2.L --run N` / `--run N --all` |
| Validator (agent) | `/validate JET2.L --run N` or `/validate --run N` in Claude Code |
| Monitor pack for a daily run's open positions and pending entries (then vs now, thesis, filing text, research levels, systematic stops) | `uv run python scripts/monitor_pack.py --run N [--position ID]` |
| Monitor (agent): daily re-evaluation; open: hold / tighten / partial / exit / revise target / add; pending: hold / amend_entry / cancel — applied as recorded (ADR 0024) | `/monitor --run N` or `/monitor --position ID` in Claude Code; then `book.py revisions --status all` / `undo REV_ID --reason` (pipeline proposals: `accept REV_ID` / `decline REV_ID --reason`) |
| Validator disagreement log (toolkits not warranted, concerns, rate per run) | `uv run python scripts/validator_stats.py [--since D]` |
| Post-mortem pack for a closed position (record + weekly bars, hindsight labelled) | `uv run python scripts/postmortem_pack.py POSITION_ID` / `--since ISO` |
| Post-mortem (agent): set-up, what worked / failed, signal right or wrong, rule proposals | `/postmortem POSITION_ID` in Claude Code → `record.py verdict ... --position ID` |
| Record an agent verdict / read prior work / print the schema | `uv run python scripts/record.py verdict FILE --actor agent:NAME` / `revision FILE --position ID` / `prior JET2.L [--stage research]` / `schema`; runner: `error TICKER --run N --stage S --reason ...` / `queue --run N --stage S` / `latest-run` / `outcome --run N --stage S` / `usage --run N --ticker T --stage S --file execution.json` |
| Daily check: prices, triggers (stops, targets, gaps, breaks), new filings and headlines | `uv run python scripts/daily.py [--save] [--report FILE]` |
| Notify the owner only when something needs them (exit 3 = nothing) | `uv run python scripts/notify.py --mode daily\|weekly --run N [--channel stdout\|github-issue]` |
| Weekly pipeline over the watchlist | `uv run python scripts/scan.py [--save]` |
| Some stocks through the pipeline / one toolkit | `uv run python scripts/scan.py JET2.L` / `JET2.L --toolkit technical [--param k=v]` |
| List stages and toolkits | `uv run python scripts/scan.py --list` |
| Fresh catalysts (filings, headlines, scheduled events) per stock | `uv run python scripts/catalysts.py [TICKERS...] [--days 7] [--horizon 10] [--no-news]` |
| Refresh the watchlist (FTSE AIM 100 + AIM UK 50; by hand, never CI) | `uv run python scripts/universe.py [--write]` |
| Book trades by hand (draft, approve, amend, reject, tickets, placed, fills, exits) | `uv run python scripts/book.py new JET2.L --entry 1520 --stop 1400 --qty 100` / `list` / `show ID` / `approve ID` / `amend ID --qty N --price P --stop P` / `reject ID --reason TEXT` / `tickets` / `placed ORDER_ID [ORDER_ID ...] [--ref REF]` / `fill ORDER_ID --qty N --price P [--commission X]` / `unfilled ORDER_ID` / `cancel ORDER_ID` / `expire` / `exit ID --reason stop|target|thesis_broken|...` / `cash dividend TICKER GBP --at EXDATE` / `split ID --ratio 2:1` / `corporate-close ID --price P --reason takeover|delisting` / `stats [--since D]` (`--db PATH` for a local file; from GitHub: the **Book** workflow, `gh workflow run book.yml -f command='cash deposit 5000'`; `-f script=plan.py -f command='--run 8'` re-plans a run) |
| Plan the week's trades from a saved scan run (final ACT labels → plan → fit → approved positions) | `uv run python scripts/plan.py --run N [--dry-run] [--replan]` (`--replan`: again for tickers whose draft was rejected; also records the shadow book's agent-paired baselines, ADR 0028) |
| Weekly pack for a run (new plans with approve/reject commands, fit rejections, watch list, open positions, money, errors) | `uv run python scripts/weekly_pack.py --run N [--out FILE] [--report FILE]` |
| Portfolio view: holdings, cash, equity, heat, sector exposure, breaches | `uv run python scripts/portfolio.py [--save] [--price JET2.L=1520]` (cash in: `book.py cash deposit 10000`) |
| Equity curve, time-weighted return, drawdown, vs benchmark, correlated holdings | `uv run python scripts/portfolio.py --history` |
| Static HTML dashboard over the DB (holdings, portfolio, equity curve, this week's pack, history) | `uv run python scripts/dashboard.py [--out FILE] [--run N]` |
| Backtest the screen point-in-time and sweep the band thresholds (report only; bands unchanged) | `uv run python scripts/backtest.py [TICKERS...] [--years 5] [--step N] [--out FILE]` |
| Label scorecard (forward returns per label / toolkit band) and trade journal | `uv run python scripts/learn.py scorecard [--since D]` / `journal` / `agents` (cost, tokens, minutes) / `agents-value` (did downgrades avoid losers; calibration) |
| Results DB: which is in use, copy local ↔ Turso | `uv run python scripts/db.py status` / `push [--from PATH] [--append-new-runs]` / `pull [--to PATH]` |
| Save a run to the results DB | add `--save` to `stock_prices` / `technical` / `levels` / `relative_strength` / `fundamentals` / `jet2_value` / `scan` |
| Notebooks | `make notebook` |

Always use `uv run …` (or `make`) — never bare `python`/`pip`. Never edit `uv.lock` by hand.

## Layout

```
src/solaraalpha/        importable package — all reusable code lives here
  config.py             Settings (env vars prefixed SOLARA_, loaded from .env); data paths
  log.py                get_logger(__name__)
  store.py              results DB (SQLite): runs, model cards, pipeline steps, labels, agent_runs,
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
  catalysts.py          fresh_catalysts: ticker -> one-line reason (new material filings, headlines,
                        a scheduled event in the window); a stock with one can join the shortlist
  pm.py                 the portfolio manager's pack: research-ACT candidates (verdict, levels, sizing
                        across the risk band, financing, results-day gap cost), open / pending positions, money, regime, macro,
                        correlations, limits — docs/decisions/0027
  value_models.py       register of per-stock value models (config/value_models.toml) for research
  dashboard.py          render_dashboard: one self-contained HTML page from the DB (inline CSS,
                        base64 PNG charts, md_to_html for the pack) — #136
  notify.py             owner notifications: daily and weekly inbox messages in plain words (broker
                        steps, checks, alerts, info, errors, undo); channels stdout / GitHub inbox
                        issue — 0021, 0026
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
    financing.py        financing card (any stock): cash runway, dilution, equity raises and
                        warrants / convertibles from NSM headlines; context only — 0005, #218
    relative_strength.py  relative strength (any stock) vs market, industry, peer basket:
                        vol-scaled excess returns, peer rank — see docs/decisions/0011
    liquidity.py        liquidity (any stock): median daily traded value (20 / 60 sessions),
                        zero-volume share, Corwin-Schultz spread proxy, max position at max_adv_pct
    charts.py           weekly_chart: candles + 10/40w MAs + levels, coil, trigger, stop/target
                        + volume as a PNG (Agg) for the validator's pack
    backtest.py         point-in-time replay of the screen (cards per Friday from the bars up to
                        it), forward returns, one-threshold-at-a-time sweep, suggested bands — #138
    regime.py           market regime from the index (drawdown_20d, ret_1d_z, realised vol and its
                        1y percentile → calm / stressed / risk_off; realised vol only) — #142
    scorecard.py        label scorecard: 2/4/8/16-week forward returns (and excess vs market) per
                        saved label, by stage x label and toolkit x band, no look-ahead; agent_value
                        (kept vs downgraded, bootstrap interval) and confidence calibration — #144
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
    plan.py             TradePlan + make_plan: the agent's entry + stop (0023), targets,
                        risk-based size with caps, review dates, flags — see docs/decisions/0015
    fit.py              portfolio fit before drafting: slots, sector cap, heat (reject);
                        correlation, event clash (flag); rank_score — 0015
    planner.py          plan_run: a saved scan run's final labels → plans (research trade levels
                        required) → fit → approved positions (ADR 0026)
                        with entry conditions (frozen cards) and thesis conditions (kill criteria)
    weekly_pack.py      build_pack (run + book → data) and render_pack (→ markdown), the Sunday page
    broker.py           Trading212 instructions: approved orders not yet placed → per-stock numbered
                        steps (cancel the replaced order, place, then the stop), price alerts — 0026
    triggers.py         daily triggers (pure): stop_close / stop_touched / target_hit / gap /
                        watch_break / entry_break / filing / news — see docs/decisions/0017
    daily.py            run_daily (what to check, fetch, evaluate, save the triggers) + render_daily
    review.py           weekly review of open positions: every toolkit re-run with the entry,
                        ReviewCard (ratcheted stop, deterioration, horizon rule, action), raise_stop
    performance.py      equity_curve (rebuilt from cash + fills + closes), drawdown, time-weighted
                        return vs Rules.benchmark, correlation of holdings' weekly returns
    journal.py          trade journal: closed trades (close summary + set-up type from the plan),
                        hit rate / average R / expectancy by exit reason and set-up
    shadow.py           shadow book: systematic baselines beside every decision, append-only
                        `shadow` table, record (scan / plan time) and load — 0028
scripts/                thin CLI entry points (argparse) that call into the package
config/watchlist.toml   stocks the weekly pipeline runs (FTSE AIM 100 + AIM UK 50 snapshot)
config/benchmarks.toml  per-stock market / industry / peers for relative strength (+ sector)
config/value_models.toml  per-stock value models (skill + script) the research agent calls — #127
config/portfolio.toml   portfolio rules: capital, risk per trade, limits (placeholders until confirmed)
.github/workflows/      ci.yml (checks), scan.yml (weekly pipeline + weekly pack, Sunday), daily.yml
                        (weekday daily check), agents.yml (unattended agents after both; inert
                        without a Claude secret), stock.yml (one stock), book.yml (book.py, plan.py,
                        weekly_pack.py against Turso, from GitHub)
.claude/skills/events/  agent skill: interprets an event pack into analysis.json / analysis.md
.claude/skills/jet2-value/  agent skill: maps Jet2 news to driver shocks and reports the impact
.claude/skills/validate/  agent skill: re-checks a run's screen labels against the pack; downgrade only
.claude/skills/research/  agent skill: strategist's 9-section pre-trade note, web search, evidence → record.py;
                        delta mode on a prior note ≤ 35 days; calls the stock's value model when registered
.claude/skills/macro/     agent skill: one shared sector / macro note per group of the week's ACT stocks
                        (stage macro, pseudo-ticker _SECTOR:<name>); research reads it — #140
.claude/skills/pm/        agent skill: the portfolio manager: allocation (1-3% risk by conviction), rotations,
                        the PM memo; the buck stops here — 0027
.claude/skills/monitor/   agent skill: structural change or noise on a triggered position; proposes one action
.claude/skills/postmortem/  agent skill: post-mortem of a closed trade from its pack; setup / execution scores
.claude/agents/senior-quant.md  subagent: senior quant persona (technical / levels / RS toolkits, signal research,
                        trade simulation, memos to owner + PM); `claude --agent senior-quant` or delegate
.claude/agents/portfolio-manager.md  subagent: PM persona (the book as a whole, allocation and rotations,
                        oversight of the agents, phases B / C, memos to owner); `claude --agent portfolio-manager`
.claude/agents/fundamental-strategist.md  subagent: fundamentals / balance sheet / valuation / macro / policy
                        specialist (fundamentals toolkit, expectations, financing risk, event gaps, #214);
                        `claude --agent fundamental-strategist`
.claude/hooks/guard_live_writes.py  PreToolUse guard the three subagents load: blocks live book / DB
                        writes, workflow runs and inbox posts unless `SOLARA_ALLOW_WRITES=1`
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

### Desk ownership

The three subagents (`.claude/agents/`) each own an area; one owner per file. Others propose in
the owner's issue rather than edit. Status lives in each agent's issue, never in its file.
Decision rules live in the skills (`.claude/skills/`), never duplicated in a subagent file.

| Area | Owner | Live state |
|---|---|---|
| `analysis/technical.py`, `levels.py`, `relative_strength.py`, `regime.py` (method), `backtest.py`, `scorecard.py`; the `fit.rank_score` formula and its test; `portfolio/shadow.py`; `docs/research/`; ADR 0029 | `senior-quant` | #210 |
| `analysis/fundamentals.py`, `financing.py`, `events.py`, `jet2_value.py`; `data/fundamentals.py`, `announcements.py`; `catalysts.py`; `value_models`; the `growth` input to the rank; the fundamental content of `/events`, `/research`, `/macro` | `fundamental-strategist` | #214 |
| `pm.py`, `portfolio/planner.py`, `plan.py`, `fit.py` (slots, sector, heat), `review.py`; the `/pm`, `/validate`, `/monitor`, `/postmortem` skills and the agent chain (`agents.yml`, failures, cost) | `portfolio-manager` | ADR 0027 phases; its current issue |
| ADR 0022, bands, rank weights, `config/portfolio.toml`, the book and broker mechanics, data sources, anything unlisted; every live write (`SOLARA_ALLOW_WRITES=1`) | owner | — |
- One issue = one deliverable. Title is imperative ("Persist events notes to the DB").
- Body: **Context** (why, links to ADRs/PRs), **Scope** (what's in / out), **Acceptance**
  (checkable list), **Open questions** (anything needing the owner's choice — don't assume).
- State dependencies explicitly ("Blocked by #N"). Keep it short; detail goes in the PR/ADR.
- PRs reference their issue (`Closes #N`). Note decisions taken during the work on the issue.
- Labels (an unlabelled issue is ready to work):
  - `needs-owner` — waiting on the owner's decision (proposals, open questions). Don't build
    until the owner has answered on the issue; then remove the label.
  - `epic` — umbrella issue listing its deliverables; closed when they are done, not by a PR.
  - `R1` / `R2` / `R3` — release slice from `docs/user-story-map.md`.
  - `inbox` — reserved for the "Solara inbox" notification issue (docs/decisions/0021); the
    workflows comment on the open one. Never put backlog work there.
  - `type:task` is legacy; don't use it on new issues.

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
