# 0017 — Daily monitoring: cheap checks every day, the agent only when one fires

**Context.** Open positions and WATCH stocks need a look every weekday evening, but an agent
reading every stock every day is slow, costly and churns (story map A7). ADR 0006 already
split events into a programmatic layer and an agent layer; the daily job follows the same
shape. The fetchers exist (`fetch_daily`, `fetch_announcements` by LEI, `fetch_news`); 0010
left the daily pipeline unbuilt.

**Decision.**
- **Two layers.** `scripts/daily.py` (`solaraalpha.portfolio.daily`) runs the cheap checks on
  every open position, every WATCH ticker of the latest scan and every entry order waiting for
  a break. The monitor agent (#121) runs only for positions with an **agent** trigger.
- **Triggers** (`solaraalpha.portfolio.triggers`, pure functions; thresholds are placeholders):

  | kind | fires when | who |
  |---|---|---|
  | `stop_close` | the session closed through the active stop | owner: exit |
  | `stop_touched` | the session traded through the stop but closed on the right side | owner: check whether the broker stop filled |
  | `target_hit` | the session reached the plan's target | owner / weekly review: take profit or trail |
  | `gap` | \|open − previous close\| > 2 × ATR(14, daily; the ATR known before the session) | agent |
  | `watch_break` | a WATCH ticker whose latest levels card had `coil_pending` closed above its `trigger` | report: promote candidate; `scan.py TICKER --save` |
  | `entry_break` | a draft / approved entry with `wait_for_break` whose ticker closed above the trigger | owner: place the limit now |
  | `filing` | a new non-routine NSM filing (the event pack's `material` filter) since the last daily run, for an open position | agent |
  | `news` | new Google News headlines since the last run | informational, listed only (R1) |

  Boundaries: a close *equal* to the stop is "touched", not "through"; a close equal to a
  trigger is not a break; a gap exactly at 2 ATR does not fire.
- **"Since the last daily run"** is the `started_at` of the latest `daily` run in the DB,
  defaulting to 3 days back, so the Monday run covers the weekend.
- **Storage.** With `--save`: one `daily` run, a price snapshot per ticker, and a `triggers`
  table (`run_id, ticker, kind, position_id, order_id, detail JSON, at`). The monitor pack
  (#121) selects positions by the agent triggers of a daily run.
- **Report** (job summary): Owner actions, Agent review needed (with the `/monitor` command),
  Watch breaks, Headlines, Errors. Exit 1 only if a fetch errored, after reporting; one
  ticker's failure never stops the others.
- **Schedule.** `daily.yml`, cron `47 17 * * 1-5` (after the 16:35 London close in GMT and
  BST), plus manual dispatch; `concurrency: book` shared with `scan.yml` so the two never
  write the book at once; the Turso secrets passed as in `scan.yml`.

**Consequences.** Each weekday costs one price fetch per monitored ticker plus one NSM query
per open issuer; the news feed is best-effort. Machine-checkable thesis conditions (#131),
stale-order reminders (#130), the event-due trigger (#129), WATCH pullbacks (#125),
notifications (#132) and market-wide volatility (#142) extend this job later. Amends 0010's
"daily tasks not built".

**Amendments (R2).** Later issues added owner triggers on the same path: `dividend` and
`split` (#133, corporate actions the book has not recorded), `watch_pullback` (#125, a WATCH
stock back at its 10w MA, from `labels.watch_on`), `event_due` (#129, an open position's
scheduled event two sessions away under a non-`hold` policy) and `order_stale` (#130, an
approved or placed order past `valid_until` with no fill entered: enter the fill or run
`book.py unfilled` / `expire`). Each is a pure function in `portfolio.triggers`, reported under
"Owner actions" (or "Watch breaks and pullbacks") and saved with the others.
