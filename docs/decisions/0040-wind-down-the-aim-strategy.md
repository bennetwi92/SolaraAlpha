# 0040 — Wind down the AIM strategy

**Status:** accepted (owner, 2026-11-23). Supersedes ADR 0022 (what we are looking for) and
ADR 0038 (scan the whole AIM universe); retires ADR 0009 (Jet2 fair value). The successor is
ADR 0041 (pivot to US mega caps).

**Context.** The owner (2026-11-23) ends the AIM catalyst strategy and pivots the fund to
major US stocks (ADR 0041). None of the AIM signal tests passed (research 0005, 0013–0016), and
the AIM machinery costs a lot to run: about 35 minutes of FCA NSM pacing a scan, thin and stale
histories, survivor-only universes and GBX quirks. On the day of the decision:

- The Trading212 practice account held five AIM positions (RWS, EYE, FRAN, PGH, LIKE: £2,858
  invested, −£130 unrealised, £1,960 free cash). FRAN and LIKE had **no stop at the broker**.
- The hosted results DB (Turso) refused every read ("SQL read operations are forbidden", a plan
  limit), so every job that reads the book was failing.

**Decision.**

- **Every AIM position is exited now.** On 2026-11-23 at about 12:10 London, the three working
  stops were cancelled (RWS, EYE, PGH) and a market sell sent for each of the five holdings on the
  practice account, outside the book (the owner: "exit them all now; no need to track it in
  Turso"). RWS filled at once; the four SETSqx names fill at the next auction.
- **No new entries.** `config/portfolio.toml` gets `new_entries = false`. The planner writes no
  positions (it reports what it would have planned), and the execution guard refuses every buy
  (`execution.check`). Sells, stops and cancels still go out. The switch stays off until the US
  strategy is defined and the owner turns it on (ADR 0041).
- **The jobs keep running for now** (owner). `scan.yml`, `daily.yml`, `agents.yml`,
  `execute.yml` and `desk-reply.yml` keep their schedules until the US pipeline replaces them.
  With the switch off they cannot open a trade. They still cost GitHub minutes, and while a Claude
  secret is set the agents still spend tokens researching AIM names every Sunday. Turning the
  schedules off, and the cron-job.org dispatches, is the owner's call.
- **The book is not updated for the exits.** The DB book still shows the five positions as open.
  When Turso reads come back, the daily job and `book.py reconcile` will show the differences:
  exits the book doesn't know about, and stops it thinks are working. The execution job may try
  to put a stop on shares the account no longer holds; Trading212 refuses it and the refusal goes
  to the desk-errors issue. The US desk starts from a fresh book (ADR 0041), so the AIM book is
  left as the record and is not cleaned up.
- **The AIM code stays until the US build replaces it.** Nothing is deleted here. The AIM-only
  parts are listed in ADR 0041 and are removed as their US replacements land:
  `config/watchlist.toml`, `research_universe.toml`, `issuers.toml`, `sectors.toml`,
  `aim_leavers.csv`, `data/announcements.py` (NSM), `official_list.py`, `leavers.py`,
  `analysis/universe.py`, `jet2_value.py`, and the research 0005–0016 scripts.

**Consequences.**

- ADR 0022's paragraph no longer defines the opportunity. Until ADR 0041's strategy is written,
  the toolkits, bands and agent prompts have no reference to be checked against, and that is a
  further reason the switch stays off.
- ADR 0029's locked holdout was AIM-only. It is spent on nothing and simply lapses; the US
  research needs its own holdout.
- The AIM research notes (`docs/research/`) stay as the record of what failed.
