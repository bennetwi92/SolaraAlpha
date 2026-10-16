# 0008 — Toolkits run as a dependency graph, per universe, from TOML config

**Superseded by [0010](0010-weekly-pipeline.md)** — graphs and universes replaced by one
stage-by-stage weekly pipeline. The toolkit register and conditions carried over.

**Context.** We will run many instruments, each through several toolkits (technical, levels,
fundamentals, events, more later). Most stocks are not worth the later, slower or costlier
steps in a given week, so a cheap screen should gate the rest: a weekly scan runs the
technical toolkit everywhere and moves on only where it passes. Different instruments may need
different chains. It must run unattended (GitHub Actions) for a whole universe, and on demand
for one stock or one toolkit.

**Decision.**
- **Toolkit register** (`solaraalpha.toolkits`): each toolkit is a function
  `(Inputs, **params) -> {model: card}` registered by id with `@toolkit(id, passes=...)`. Its
  *verdict card* (the one under its own id) plus `passes` conditions give the overall pass /
  fail. With no conditions it passes whenever it runs. `Inputs` fetches each ticker's data
  lazily, once, and shares it across the toolkits in a run.
- **Graphs** (`config/graphs/<id>.toml`): nodes keyed by id, each running one toolkit (default:
  same id) with `params`, `passes` (overriding the toolkit default) and `needs`. A node runs
  only if every node it needs **passed**. Statuses: `pass`, `fail`, `skip` (a need did not
  pass), `error` (raised; logged, treated as not passed, never stops the scan). Validated at
  load: unknown toolkits / needs / keys and cycles are errors (stdlib `graphlib`).
- **Universes** (`config/universes/<id>.toml`): tickers, a default graph, optional per-ticker
  graph overrides. Everything is referred to by id; `scripts/scan.py --list` shows them all.
- **Conditions** are `"<field> <op> <value>"` strings, not Python expressions: readable in
  config, no `eval`. NaN / None fails, so an unwarmed model never passes a gate.
- **One CLI**, `scripts/scan.py`: `--universe <id|all>`, `TICKER --graph <id>`, or
  `TICKER --toolkit <id> [--param k=v]`. `--save` records each ticker as one run: its cards
  (as `model`, or `node.model` when a node renames a toolkit) and a `steps` row per node
  (status, reason) in the results DB. Exits 1 if any node errored.
- **GitHub Actions**: `scan.yml` (weekly, Saturday 06:17 UTC so Friday's bar is complete, plus
  manual for one universe) and `stock.yml` (manual: one ticker, one toolkit or a graph). The
  report goes to the job summary; `data/` (results DB, event packs) is uploaded as an artifact.

TOML because `tomllib` is stdlib (no YAML dependency). Plain functions + a dict registry
rather than classes or a workflow engine (Prefect, Dagster…): one developer, four toolkits.

**Consequences / open points.**
- Default gates (`trend >= 0.25 and momentum > 0`, `reward_risk >= 2`, `quality > -0.5`) are
  placeholders to make the graph run, not tested thresholds. They should be set by the owner
  and later checked against forward returns (steps + cards are saved for exactly that).
- The `events` node builds the pack only; the `/events` agent interpretation is not in Actions
  (it would need an Anthropic API key, which is paid). Run `/events` on stocks that reach it.
- Until the hosted DB exists, each Actions run starts with an empty DB and its results live in
  a 90-day artifact, not a cumulative history.
- Tickers run sequentially in one job; if universes grow, shard with a job matrix.
- Yahoo / NSM may throttle GitHub runner IPs; errors are per node and show in the summary.
