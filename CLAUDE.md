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
- **Trading frequency is weekly** unless the owner says otherwise. Weekly bars end Friday
  (`to_weekly`), and only completed weeks are used.
- Working on **single-stock analysis**; first example is Jet2 (`JET2.L`, AIM-listed).
- Price data is **not persisted** for now — fetch fresh each run (no `@parquet_cache`).
- Price history: fetch the **last 5 years** (default), then cut per stock to the span after
  the last blocking data issue (`trim_to_consistent`). Don't pull long history yet.
- The technical models (`analysis/technical.py`, `analysis/levels.py`) must stay **generic across
  stocks** — no per-stock tuning. Other models (e.g. fundamentals) are stock-specific.
- Model outputs for the scorecard are scores in **[-1, +1]** (+ = bullish / favourable).
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
| Notebooks | `make notebook` |

Always use `uv run …` (or `make`) — never bare `python`/`pip`. Never edit `uv.lock` by hand.

## Layout

```
src/solaraalpha/        importable package — all reusable code lives here
  config.py             Settings (env vars prefixed SOLARA_, loaded from .env); data paths
  log.py                get_logger(__name__)
  data/                 fetching, cleaning, storage of external data
    http.py             shared requests session with retry/backoff — use for all HTTP
    cache.py            @parquet_cache decorator for DataFrame-returning fetchers
    prices.py           daily OHLCV for one stock (Yahoo chart API)
    clean.py            check_prices / clean_prices / trim_to_consistent / to_weekly
  analysis/             research & analytics code
    technical.py        weekly technical model (any stock): trend/momentum/entry scores,
                        open-position assessment — see docs/decisions/0003
    levels.py           weekly levels model (any stock): S/R zones, trendlines, 52w/history
                        highs/lows, blue sky; stop/targets/reward:risk — see docs/decisions/0004
  portfolio/            portfolio construction / risk (later phase)
scripts/                thin CLI entry points (argparse) that call into the package
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

## Workflow for Claude

- Work on the branch you're given; small, focused commits with descriptive messages.
- Before committing: `make fmt && make check`.
- When adding a data source: add the fetcher in `src/solaraalpha/data/`, a fixture-based test,
  and an entry in `docs/data-sources.md`.
- When making a non-obvious design choice, add a short ADR in `docs/decisions/`.
- Update this file when conventions, layout or commands change.
