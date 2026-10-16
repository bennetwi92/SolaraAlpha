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
- Working on **single-stock analysis**; first example is Jet2 (`JET2.L`, AIM-listed).
- Price data is **fetched fresh each run** (no `@parquet_cache`). With `--save` the raw fetch is
  also snapshotted into the results DB (`solaraalpha.store`) — for the record, never as a cache.
- **Results DB**: scripts' `--save` records the run, model cards and input snapshots in
  `data/solara.db` (append-only SQLite) — see docs/decisions/0007. Hosted copy (Turso) not set up yet.
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
  an agent via the `/events` skill (`.claude/skills/events/SKILL.md`). See docs/decisions/0006.
- Model outputs for the scorecard are scores in **[-1, +1]** (+ = bullish / favourable).
- Toolkits run as **one serialised weekly pipeline** (docs/decisions/0009): toolkits are
  registered by id in `solaraalpha/toolkits.py`, each with a fixed pass/fail verdict; the stages
  are `WEEKLY` in `solaraalpha/pipeline.py` — `screen` (technical + levels, independent, both must
  pass) → `fundamentals` → `events`. Each stage runs on every stock still in before the next
  starts. Stocks: `config/watchlist.toml`. GitHub Actions run it Sunday (`scan.yml`) or on
  demand for one stock (`stock.yml`). Thresholds are placeholders. Daily tasks (news,
  announcements, volatility) and the agent stage are not built yet.
- All price data must pass through `check_prices` (review the issues), `clean_prices` and
  `trim_to_consistent` before use.

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
| Event pack (filings, reactions, expected events, news) | `uv run python scripts/events.py JET2.L [--days 7] [--horizon 16]` |
| Read one announcement | `uv run python scripts/announcement.py <NSM url>` |
| Events analyst (agent) | `/events JET2.L` in Claude Code |
| Weekly pipeline over the watchlist | `uv run python scripts/scan.py [--save]` |
| Some stocks through the pipeline / one toolkit | `uv run python scripts/scan.py JET2.L` / `JET2.L --toolkit technical [--param k=v]` |
| List stages and toolkits | `uv run python scripts/scan.py --list` |
| Save a run to the results DB | add `--save` to `stock_prices` / `technical` / `levels` / `fundamentals` / `scan` |
| Notebooks | `make notebook` |

Always use `uv run …` (or `make`) — never bare `python`/`pip`. Never edit `uv.lock` by hand.

## Layout

```
src/solaraalpha/        importable package — all reusable code lives here
  config.py             Settings (env vars prefixed SOLARA_, loaded from .env); data paths
  log.py                get_logger(__name__)
  store.py              results DB (SQLite): runs, model cards, pipeline steps, price/fundamental
                        snapshots; save_run / load_results / load_steps / latest / load_prices
                        — see docs/decisions/0007
  toolkits.py           toolkit register: id -> run fn + pass conditions; Inputs (lazy,
                        shared per-ticker data)
  pipeline.py           WEEKLY stages, conditions, run_pipeline (stage by stage) — see
                        docs/decisions/0009
  data/                 fetching, cleaning, storage of external data
    http.py             shared requests session with retry/backoff — use for all HTTP
    cache.py            @parquet_cache decorator for DataFrame-returning fetchers
    prices.py           daily OHLCV for one stock (Yahoo chart API)
    fundamentals.py     statements, TTM EPS, P/E / forward P/E / market cap for one stock
                        (Yahoo fundamentals-timeseries API), tidy long frame
    announcements.py    regulatory filings for one issuer (FCA NSM, by LEI), ticker → LEI
                        (LSE + GLEIF), announcement full text
    news.py             press headlines (Google News RSS), discovery only
    clean.py            check_prices / clean_prices / trim_to_consistent / to_weekly
  analysis/             research & analytics code
    technical.py        weekly technical model (any stock): trend/momentum/entry scores,
                        open-position assessment — see docs/decisions/0003
    levels.py           weekly levels model (any stock): S/R zones, trendlines, 52w/5y
                        highs/lows, blue sky; stop/targets/reward:risk — see docs/decisions/0004
    fundamentals.py     fundamentals model (one stock): growth/value/quality scores,
                        point-in-time via publication lags — see docs/decisions/0005
    events.py           events model (any stock): market reaction per filing, expected
                        scheduled events from the issuer's own history — see docs/decisions/0006
  portfolio/            portfolio construction / risk (later phase)
scripts/                thin CLI entry points (argparse) that call into the package
config/watchlist.toml   stocks the weekly pipeline runs
.github/workflows/      ci.yml (checks), scan.yml (weekly pipeline, Sunday), stock.yml (one stock)
.claude/skills/events/  agent skill: interprets an event pack into analysis.json / analysis.md
notebooks/              exploration only; promote anything reused into src/
tests/                  pytest; mirrors src/ structure
data/                   gitignored; raw/ processed/ cache/ created on demand
docs/
  data-sources.md       register of every external data source, its terms and limits
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
- **Always raise a PR** against `main` once the work is pushed — no need to ask. Reference the
  issue (`Closes #N`) when there is one. Then **enable auto-merge (squash)** on it so it merges
  as soon as CI passes; keep it open (no auto-merge) only if the owner says to or a decision is
  still open on the issue.
- When adding a data source: add the fetcher in `src/solaraalpha/data/`, a fixture-based test,
  and an entry in `docs/data-sources.md`.
- When adding a toolkit: register it in `toolkits.py` with `@toolkit(id, passes=...)` (its
  verdict card under its own id, with a `date` field), then add it to a stage in `WEEKLY`.
- When making a non-obvious design choice, add a short ADR in `docs/decisions/`.
- Update this file when conventions, layout or commands change.
