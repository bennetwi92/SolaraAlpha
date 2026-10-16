# 0006 — Results database: append-only SQLite, hosted SQLite (Turso) later

**Context.** We need to keep model outputs (scorecards, trade plans, position assessments) and
the input data they were computed from, to track stocks over time, screen many stocks, feed
dashboards and evaluate models against later returns. It must also work from Claude Code cloud
sessions, whose containers are discarded and which reach the internet only over HTTPS through a
proxy, so a local file is not durable there and ordinary Postgres drivers cannot connect.

**Decision.** `solaraalpha.store`: one SQLite file (`settings.db_path`, `data/solara.db`,
stdlib `sqlite3`, no new dependency) with four append-only tables — `runs` (time, script,
args, git commit), `results` (tidy long: run, ticker, model, week, field → value/text),
`prices` and `fundamentals` (stored only when a row is new or differs from the latest version
beyond `RTOL` = 1e-6, which ignores Yahoo's last-bit float jitter). Scripts write only with
`--save`; analysis still uses the fresh fetch, never the stored snapshot as a cache.

Point-in-time: `results.date` is the week the card describes, separate from `runs.started_at`;
`load_prices/load_fundamentals(as_seen=...)` rebuild what we had seen by a date. `as_seen` is
when *we* saw it, not when it was published — fundamentals still go through `known()`.

Hosting (not built yet): a hosted SQLite reachable over HTTPS. Turso (free: 5 GB, 10M rows
written/month, no overage billing; HTTP `/v2/pipeline` API callable with our `requests`
session) is first choice; Cloudflare D1 (SQLite, but 100k rows written/day) is the fallback.
Checked Oct 2026: Supabase announced it is acquiring Turso, and libSQL is in maintenance mode,
so the store sticks to plain SQLite SQL and the remote part will be a thin executor swap.

**Consequences.** Nothing is ever updated or deleted, so a bad run is filtered out by
`run_id`, not removed. Schema changes are additive (`CREATE TABLE IF NOT EXISTS`); a new table
column would need a manual `ALTER TABLE`. `adj_close` is back-adjusted, so each dividend adds a
new version of the whole history (a few thousand rows per stock per year) — acceptable.
