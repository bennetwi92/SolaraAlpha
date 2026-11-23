# 0040 — The results DB is an encrypted SQLite file on the `db` branch

**Status:** proposed (2026-11-23, #321). Supersedes ADR 0013 (hosted results DB on Turso) once built;
amends ADR 0007 (results DB) and the "never commit data" hard rule.

**Context.** Turso's free plan blocked every read on 11 Nov when the month's read allowance ran
out (ADR 0013 amendment). The index fix (#318) is merged, but the allowance stays used up until
Turso resets it, and every job opens the DB first. Since then the weekly scan (last good run
8 Nov), the daily check, the execution job and the desk reply have all failed, and the Agents runs
are empty. The book is out of step with Trading212: two positions (FRAN, LIKE) filled with no
stop sent. A billed-per-row hosted DB is a standing risk for a desk that reads the whole book
every 15 minutes.

The constraints:

- **Free only, Python only, simplest thing that works** (CLAUDE.md).
- **One repository.** Sessions and workflows are scoped to `bennetwi92/SolaraAlpha`, and there is
  no second repo to hold the data.
- **The repository is public.** Anything committed, on any branch, is readable by anyone.
- **The owner queries from a Claude Code cloud chat.** There, GitHub is reachable through the
  GitHub connector and plain `git` (clone, fetch, push), not release-asset downloads or the REST
  API. So the DB has to be something `git fetch` can get.
- Making the repo private would close the exposure but move Actions onto the free private-repo
  allowance (about 2,000 minutes a month). The current schedule (execution every 15 minutes in the
  session, the desk reply every 30, multi-hour scans) is well over that.

**Decision.**

- **One SQLite file, encrypted, on an orphan branch `db`**: `solara.db.gpg`, symmetric AES-256
  with `gpg --batch --symmetric --cipher-algo AES256`, passphrase from `SOLARA_DB_KEY` (a GitHub
  secret and a cloud-environment variable; never committed). `gpg` is preinstalled on GitHub
  runners and in the cloud container, so there is no new dependency. The public branch holds only
  ciphertext.
- **The branch holds one commit.** Every save replaces it (`git push --force` to `db`, and only to
  `db`), so history never grows by a copy of the DB per run. This is the only branch anything
  force-pushes to.
- **Open → work → save.** A small module (`solaraalpha/dbsync.py`, used by `scripts/db.py`):
  - `pull`: `git fetch --depth 1 origin db`, decrypt to `data/solara.db`.
  - `push`: run `PRAGMA integrity_check`, encrypt, commit as a fresh orphan, force-push.
  
  `store.connect()` goes back to the local file only, and `turso.py` is deleted.
- **One writer at a time.** Every workflow that writes uses the existing `book` concurrency group.
  The desk reply joins it, since it writes to the `chat` table. A job pulls at the start and pushes
  at the end **even when a step failed** (`if: always()`, as long as the file passes the integrity
  check). The execution job writes `sending` before it calls the broker, and losing that row would
  risk a double send.
- **Sessions read, workflows write.** A cloud chat runs `db.py pull` and queries the local copy,
  which is as fresh as the last finished job (at most about 15 minutes old in market hours).
  Writes from a chat go through the Book workflow (`book.yml`), as they do now, so they stay
  inside the lock. `db.py push` from a session refuses unless the branch head is still the commit
  it pulled.
- **Price and fundamentals snapshots shrink to what is read back.** GitHub refuses files over
  100 MB, and the full-universe snapshot (557 stocks × 5 years of daily bars) is most of the DB.
  The `prices` / `fundamentals` tables keep only the stocks their readers use: the shortlist,
  open and pending positions, and the benchmark. Those readers are validation, the PM pack,
  postmortem, risk, the weekly pack, the dashboard, shadow and the track record. The full fetch
  goes into the run's artifact as parquet. The pivot away from AIM (under discussion) shrinks the
  universe further.
- **Backups:** the Sunday scan uploads the encrypted file it pushed as an artifact (90 days).

**Consequences.**

- No quotas and no external service. The DB's size and save times are public, but its contents
  are not.
- Losing `SOLARA_DB_KEY` loses the DB. The owner keeps a copy of it outside GitHub.
- Every job adds a fetch, a decrypt, an encrypt and a push: seconds at tens of MB.
- The scan already holds the `book` lock for its whole run (1–3.5 hours on AIM), and GitHub keeps
  only one pending run per group, so execution turns are skipped meanwhile. That is unchanged by
  this ADR. A shorter large-cap scan is the fix.
- Live SQL from anywhere is gone. Every reader pulls a copy.
- The hard rule becomes "never commit data or secrets, except the encrypted results DB on the
  `db` branch (ADR 0040)".
- **Migration:** `db.py pull` from Turso needs reads. Either wait for Turso's monthly reset, or
  start a fresh DB and rebuild the book from Trading212 (`book.py reconcile --book`), losing the
  run, card and agent history. The owner chooses.
- **Not solved by this ADR:** the public `inbox` and `desk-errors` issues already publish order
  steps, positions and errors.
