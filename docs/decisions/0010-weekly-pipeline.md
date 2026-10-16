# 0010 — One serialised weekly pipeline, stage by stage (supersedes 0008)

**Context.** 0008 ran each stock through its own TOML dependency graph, stock by stock. The
owner thinks of it differently: one scheduled run that does the week's tasks in sequence —
check every stock with the first toolkits, move the ones that pass on, and so on — ending,
later, in a long, discretionary agent step over the shortlist. Graph files, universes and a
condition-driven DAG are more machinery than that needs.

**Decision.**
- **One pipeline in code** (`solaraalpha.pipeline.WEEKLY`): an ordered tuple of stages, each a
  set of toolkits. Today: `screen` (technical + levels) → `fundamentals` → `events`.
- **Stage by stage, not stock by stock.** A stage runs on every stock still in before the next
  stage starts, so each stage's results for all stocks exist side by side — what the agent
  stage will work from.
- **Toolkits in one stage are independent.** Technical and levels both run on every stock and
  a stock moves on only if *both* pass; neither gates the other, so one model's view can't
  hide the other's.
- **Fixed thresholds**, the toolkits' own `passes` (no per-run overrides). Still placeholders.
- **Watchlist** is one list, `config/watchlist.toml`. `scripts/scan.py` runs it, or given
  tickers, or one toolkit on one stock (`--toolkit … --param k=v`).
- **Schedule:** `scan.yml`, Sunday 18:17 UTC (after Friday's weekly bar, before Monday's open),
  plus manual. `stock.yml` runs one stock on demand. With `--save` the whole scan is one run
  in the results DB (`start_run`, then `save_ticker` per stock): every stock's cards plus a
  `steps` row per (stock, toolkit) with its stage, so a week's shortlist is one `run_id`.
  Every card is dated by the week it describes (the last completed Friday), events included
  (its build time is the card's `generated`), so a stock's cards join on `date`.

**Consequences / open points.**
- Daily tasks (news / announcements during the week, volatility events) will be a second,
  separate pipeline on its own schedule; not built.
- The agent stage is not built. It will be the last stage, reading the shortlist from the
  saved run. A GitHub-hosted job is capped at 6 hours; if the agent needs longer, it moves to a
  second job in the same workflow. Running it in Actions needs a paid key or subscription token.
- A stage could later rank (e.g. top N) instead of a fixed pass; nothing does yet.
