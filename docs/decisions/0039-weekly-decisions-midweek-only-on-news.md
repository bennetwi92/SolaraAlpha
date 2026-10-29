# 0039 — Decisions are weekly; midweek the desk acts only on news that can break a thesis

**Status:** accepted (owner, 2026-10-29). Amends ADR 0024 (daily agent review), 0036 (risk
manager and PM daily review); supersedes ADR 0037 (event sessions). Returns the daily job to
the shape of ADR 0017: cheap checks every day, the agent only when one fires.

**Context.** The strategy is weekly: weekly bars, a Sunday scan, long trades held for 1–10
weeks for a +15–30% burst, and held longer while the stock keeps climbing. Since ADR 0024 the
monitor re-judged every position and pending entry every weekday and set the stop each time.
A stop can only be raised, never lowered, so every daily look is a chance to tighten and none
is a chance to loosen. On volatile AIM stocks that means being shaken out of the very trades
the strategy is after. No test shows the daily judgement adds value. ADR 0036's daily risk
agent and ADR 0037's mid-session event reviews add more of the same: a decision driven by a
day's prices. The owner (2026-10-29) does not want to act impulsively on daily signals in a
weekly strategy, wants stops changed weekly only, and wants news that can change a thesis (a
profit warning, a placing, an offer) still acted on midweek.

**Decision.**

- **The weekly review is the Friday daily run.** The daily job gives every open position and
  pending entry a `daily_review` trigger only on Friday (London date; a weekend re-run counts),
  after the week's last close, which is the same information the Sunday scan sees, so the
  review is done before Monday's open. On that run the monitor has its full action set:
  open: hold / tighten_stop / take_partial / exit / revise_target / add; pending: hold /
  amend_entry / cancel. This is the only time the monitor sets a stop by judgement.
- **Midweek (Monday to Thursday) the monitor runs only on an event trigger**: a material filing,
  a gap of more than 2 ATR at the open, or a machine kill criterion hit (`thesis_hit`). It can
  only `hold`, `take_partial` or `exit` an open position, or `hold` or `cancel` a pending
  entry (`monitoring.MIDWEEK_ACTIONS`). No stop changes, adds, target changes or entry
  amendments: those wait for Friday. `record_revision` refuses anything else on a run that is
  not the position's weekly review, and the monitor pack says which kind of review this is.
  Default is `hold`: a price move on its own is not news.
- **The cheap checks stay daily**, as do booking the broker, stop orders at Trading212, order
  expiry, dividends, splits and the triggers report. The broker's stop order is the midweek
  protection.
- **The risk card stays daily; the risk agent is weekly.** `daily.py --save` still saves the
  `book_risk` card every weekday (code, no judgement). The `/risk` agent runs on the weekly
  review run, or on any day the card breaches a limit (`escalate`: VaR over `max_var`, heat,
  the circuit breaker). `record.py queue --stage risk` decides. The PM's daily review
  (`/pm-daily`) runs as before only when called, so midweek only on a breach of a limit the
  owner set. The owner's Telegram risk and P&L report becomes weekly, plus breach days.
- **Event sessions are retired** (ADR 0037): `event-session.yml`, `event_sessions.py`, `daily.py
  --session` and `record.py session` are removed, along with the PM's and the risk manager's
  booking steps. A dated event that matters is handled by the stop at the broker and, if it
  produces news on a held stock, by the midweek monitor. The `event_sessions` table stays in
  the DB as history.

**Consequences.** Far fewer agent runs midweek: none on a quiet day. Stops move once a week, so
a winner gets room to run. A held stock can still be exited or a pending buy cancelled the
same evening a profit warning comes out. A sharp price fall with no news waits for the stop at
the broker or Friday's review. The risk manager no longer reports every weekday. Whether the
monitor's changes add value is still unmeasured; the shadow book (ADR 0028) and the track
record (ADR 0035) are where to test it.
