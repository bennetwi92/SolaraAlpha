# 0040 — The desk moves from weekly to daily frequency

**Status:** accepted (owner, 2026-11-23); built in slices (below). Supersedes ADR 0039 (weekly
decisions, midweek only on news). Amends ADR 0010 / 0014 (the weekly pipeline), 0017 (the daily
job), 0024 (the monitor), 0029 (the research protocol), 0036 (the risk manager), 0019 (agent
caps).

**Context.** Since ADR 0010 the desk has run at weekly frequency: weekly bars end Friday, the
scan runs on Sunday, trades are held 1–10 weeks for a +15–30% burst, and since ADR 0039 the
monitor and risk agents decide only on Fridays (midweek on news only). On 2026-11-23 the owner
moved the desk to daily frequency. Everything moves to daily:

- **decisions**: the monitor (and the PM when called) acts on any weekday with the full action
  set, the stop included;
- **the scan and entries**: the screen, shortlist, research and PM allocation run every weekday
  after the close;
- **the models**: technical, levels, relative strength and liquidity are rebuilt on daily bars;
- **the risk report**: the risk agent and the owner's Telegram report run every weekday;
- **the horizon shortens to days** (swing trades), so the research target, the holdout and the
  shadow rules are redefined;
- **agent caps are raised** so coverage keeps up with a daily cadence (more Claude spend
  accepted).

**Decision.** Build it in slices, each a PR:

1. **Decision cadence (built with this ADR).** Every daily run gives every open position and
   pending entry a `daily_review` (`triggers.review_day` is gone); the monitor has its full
   action set on every run that put the position up (`monitoring.full_review`); only a
   `--position` review on a run that did not put it up is limited to risk-cutting actions
   (`monitoring.UNREVIEWED_ACTIONS`). The risk agent reports on every daily run with a risk
   card (`agents.risk_due`); the PM's daily review still runs only when called.
2. **Daily-bar models** (senior-quant): technical, levels, relative strength and liquidity on
   daily bars, generic across stocks; the minimum history in sessions instead of 26 weekly bars;
   cards dated by session; bands back to placeholders.
3. **Daily scan**: the pipeline (screen → fundamentals → events → shortlist), the agent chain
   (validate → macro → research → PM → plan) and the pack every weekday after the close, with
   the raised agent caps; entry validity and PM renewals (ADR 0034) in sessions.
4. **Horizon and research** (senior-quant, owner sign-off): a new target in place of
   `burst_10w`, a pass bar fixed in advance, a new locked holdout; scorecard and backtest
   horizons, the shadow book's rules and the plan's horizon rule in sessions.

**Consequences.** Until slice 2 the monitor decides daily on weekly cards (as of last Friday)
and judges today's price from `latest`. ADR 0039's concern comes back: a stop that can be
raised every day, and never lowered, is one more chance a day to shake a volatile winner out.
The monitor skill keeps the rule that the stop moves only when invalidation moves, and the
shadow book (ADR 0028) and the track record (ADR 0035) are where to measure it. The research
done so far (docs/research/0001–0016) is at weekly frequency; its conclusions do not carry over
to daily signals without a new test. Agent spend rises several-fold.

**Open (owner):** the profit target per trade at the shorter horizon (still +15–30%?), the
holding range in sessions, the new agent caps (`max_agent_tickers`, `--max-turns`, job
timeouts), whether macro and retro stay weekly, and whether the scan and the daily job become
one workflow.
