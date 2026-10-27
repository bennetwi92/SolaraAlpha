# 0037 — Event sessions: the agents book a mid-session review of the book for a known event

**Status:** accepted (owner, 2026-10-27). Extends ADR 0017 / 0024 (the daily job and monitor)
and ADR 0036 (risk manager, PM daily review).

**Context.** The book is reviewed once a weekday, after the close, so a decision about a
midweek event (the UK Budget, a rate decision, a held stock's results) reaches Trading212 the
next morning at the earliest. The agents already know the dated events (the macro notes'
Watch, the results calendar, the desk thread). The owner expects them to organise a review
straight after such an event and act on it the same day, booked at the start of the week from
the open positions and the known midweek events.

**Decision.**

- **Who books** (owner): the **PM** in the weekly run books the week's sessions (`/pm` Step 5);
  the **risk manager** books one in its daily report for an event before the next daily close
  that has none (the midweek catch-up). The owner can too. `record.py session add --at
  "YYYY-MM-DD HH:MM" --event ... --reason ... [--tickers ...] --actor agent:pm|agent:risk|owner`
  (London time); `session list`, `session cancel`. Stored in the append-only `event_sessions`
  table (`solaraalpha/event_sessions.py`): `scheduled`, then `started` (with its daily run) or
  `cancelled`.
- **When**: inside a London session, from the open to 14:30 on a full day (two hours before the
  close), so the review and its orders finish before the 15:45 exit deadline (ADR 0031). The
  agents time it about an hour after the announcement.
- **How it runs**: `event-session.yml` asks every 15 minutes in the session whether one is due
  (`record.py session due`; offset from the execution job's minutes). When one is: `daily.py
  --save --session ID` (the daily check on the session's prices so far; the run is marked as the
  session's and the desk thread is told), then `agents.yml` dispatched in daily mode on that run:
  monitor on every position and pending entry → risk → pm-daily when called. The execution job's
  next turn sends what they decided. A session missed on its day is not run late.
- **Whole book, risk-cutting only** (owner): the monitor in an event session may record `hold`,
  `tighten_stop`, `take_partial`, `exit`, or for a pending entry `hold` / `cancel`; `record.py
  revision` refuses `add`, `revise_target` and `amend_entry` (they wait for the evening review).
  pm-daily only cuts risk already. The monitor and risk packs open with the session's note.
- **Concurrency**: an event session's Agents run sits in its own concurrency group
  (`event-session`) instead of `book`, so the execution job's 15-minute turns, which queue in
  `book`, never cancel it while it waits; the book's writes wait out each other's lock (#274).

**Consequences.**

- Prices in a session are the day so far: a "close" through a stop is not yet a close (the stop
  at the broker is working), and the risk card's day P&L is intraday. The skills say so; the
  evening daily run then reviews the book again on the close.
- A session costs about one evening's monitor and risk spend; no extra cap beyond
  `max_agent_tickers`. Results days get a session only when the PM or risk manager books one.
- The poller costs a short runner job every 15 minutes in the session; it reads the DB and stops
  when nothing is due.
