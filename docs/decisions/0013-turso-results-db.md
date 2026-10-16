# 0013 — Hosted results DB on Turso, over HTTP with our own thin client

**Context.** 0007 kept the results DB as a local SQLite file and planned a hosted SQLite for
GitHub Actions and Claude Code cloud sessions, whose containers are discarded. The Turso DB
(`solaradb`, AWS eu-west-1, free tier) now exists, with `SOLARA_TURSO_URL` / `SOLARA_TURSO_TOKEN`
set as GitHub secrets and cloud-session env vars.

**Decision.** `solaraalpha.turso`: a ~150-line sqlite3-style `Connection` speaking Hrana over
HTTP (`POST /v2/pipeline`) through our `requests` session — no driver dependency (libSQL is in
maintenance mode, 0007) and it works through the cloud HTTPS proxy. It covers only what the
store uses: `execute` / `executemany` (batched, 500 statements per request) / `executescript`,
`with conn:` transactions (`BEGIN` … `COMMIT`/`ROLLBACK` on one stream, held by its baton) and
`close`. `store.connect()` picks Turso when `settings.turso_url` is set, else
`data/solara.db`; an explicit `path` always means that local file (tests use this). Reads go
through `store._query` instead of `pd.read_sql_query`, which only accepts sqlite3/SQLAlchemy.

JSON has no infinity and Turso returns ±inf as `null`, so ±inf (e.g. a blue-sky target) is
sent and stored as ±`sys.float_info.max` and read back as ±inf. Raw SQL against Turso sees
1.8e308 for those cells. Settings strip surrounding quotes from both values, since a pasted
`"libsql://…"` in an environment's settings otherwise breaks the URL.

**Consequences.** The `--save` from Actions, cloud sessions and the local machine (with `.env`)
all land in one DB. There is no automatic copy between the local file and Turso; a local file
built before this stays local. Each `connect()` costs ~3 round trips (schema + migration check)
and each `save_ticker` a few more — fine for a weekly scan. Scans still upload `data/` as an
artifact for event packs.
