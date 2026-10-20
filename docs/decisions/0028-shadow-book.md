# 0028: A shadow book of systematic baselines beside every agent decision

**Status:** accepted (owner and PM, 2026-10-20, issue #210). Frozen on 2026-10-20, before any
shadow row or outcome existed. Changing a baseline means a new baseline id, never an edit.

**Context.** Since ADRs 0023, 0024 and 0027 the agents choose the names, set every entry and
stop, manage the trailing exit and size each trade. They are our main trading model, and
nothing measures them against a plain rule. `learn.py agents-value` only asks whether
downgrades avoided losers. Live decisions are the only true out-of-sample data we have, so the
PM asked for recording to start now and simulation to follow (#210, Q1). Writing what each
rule would have done *at decision time* removes any later temptation to tune a baseline with
hindsight.

**Decision.** A `shadow` table in the results DB (`solaraalpha.portfolio.shadow`), append-only
like the book. Each row is one baseline's decision for one ticker in one weekly scan run.
Nothing in it is ever traded.

*Inputs*, all from the saved scan run, as seen at its start:

- card date `D`: the run's last completed Friday (the technical card's `date`);
- daily bars: the run's price snapshot, sessions up to `D`;
- `ATR`: daily ATR(14), Wilder (`technical.atr` on daily bars, as `triggers.py`);
- `high_4w`: the highest daily high of the 4 completed weeks ending `D`;
- levels stop: the levels card's `stop` (nearest support − 0.5 weekly ATR, at least 1 weekly ATR
  under the close);
- the screen rank and shortlist (`labels`), the research ACTs with their trade levels (ADR
  0023) and the PM's allocation (ADR 0027).

*Systematic levels* ("breakout"): a buy-stop at `high_4w + 0.1 × ATR`; stop `entry − 2 × ATR`.

*Baselines* (risk 2% = the rules' `risk_per_trade` today, frozen here as 2%):

| id | names | entry | stop | risk | exit management | written by |
|---|---|---|---|---|---|---|
| `top5_rank` | screen rank 1–5, no agent | breakout | 2 ATR | 2% | chandelier | `scan.py --save` |
| `random2_shortlist` | 2 of the shortlist: `random.Random(run_id).sample(sorted(tickers), 2)` | breakout | 2 ATR | 2% | chandelier | `scan.py --save` |
| `agent` | the PM's allocation, in its order; without a PM verdict the planner's fallback (research ACTs in rank order) | agent | agent | PM's `risk_pct` (fallback 2%) | agent (monitor) | `plan.py` |
| `flat2_rank` | every research ACT with trade levels, in screen-rank order | agent | agent | 2% | agent | `plan.py` |
| `entry_breakout` | same names | breakout | agent | 2% | agent | `plan.py` |
| `stop_2atr` | same names | agent | entry − 2 ATR | 2% | agent | `plan.py` |
| `stop_levels` | same names | agent | levels stop | 2% | agent | `plan.py` |
| `trail_chandelier` | same names | agent | agent | 2% | chandelier | `plan.py` |

*Chandelier* (`trail = chandelier_3atr`): after the fill, the stop is the highest of the
initial stop and (highest daily close since entry − 3 × ATR(14)), ratcheted up only; the trade
exits on a daily close below it. *Agent* (`trail = agent`): the monitor's revisions as they
happened (ADR 0024), or the simulator's stand-in for them; the same for every arm marked
`agent`, so the paired rows differ in one thing only.

The paired rows (`entry_breakout`, `stop_2atr`, `stop_levels`, `trail_chandelier`) share the
agent's names and week with `flat2_rank`, so each isolates one decision: entry, initial stop,
or trailing exit. `flat2_rank` against `agent` measures the PM's selection and conviction
sizing. `top5_rank` and `random2_shortlist` against `agent` measure name selection. Monitor
revisions need no daily rows: the chandelier rule is fixed at entry, and the agent side of it is
already in the book's `revisions`.

*Row*: run, baseline, ticker, priority (the order the baseline fills slots in), entry type
(`stop` = buy the break, `limit` = at or below), entry, stop, `risk_pct`, `valid_days` (the
agent's, else `entry_validity_days`), `trail`, card date, the inputs used (JSON), a note,
`spec = "0028"`, git SHA, `recorded_at`. A name a rule cannot price (no snapshot, NaN ATR, stop
at or above entry) is still written, with entry and stop NULL and the reason in the note, so a
gap is visible rather than silent. Only full weekly scans are recorded (no ticker subset, no
single toolkit). A baseline already recorded for a run is never written again (re-plans,
reruns), so the first decision stands.

*Simulation* comes later (#210 phase 1–2) and applies to every arm alike: fills on daily bars
from Monday's open (buy-stops at the trigger, or the open when it gaps above), gaps through
stops at the open, half the Corwin-Schultz spread per side (stressed at 2×), the entry
validity, and a 50-session horizon. **Fit runs at simulation time** against each baseline's own
shadow holdings (5 slots, 10% heat, sector and position caps, one order per stock), never
against the live book, so a full live book cannot block a baseline's names.

**Consequences.**

- From the next weekly run every decision has its baselines beside it, at a few rows a run.
- Each comparison is reported with its 90% interval, n so far, n needed and, for paired rows,
  the paired correlation ρ (#210, Q11). Nobody changes process on a row before it reaches its
  stated n: about 300 trades per arm for an unpaired 0.3R difference, 31–155 paired.
- `learn.py` and the PM's phase C read the table directly (`shadow.load`).
- Late writes (a `plan.py` re-run from the Book workflow on an old run) are possible and carry
  their own `recorded_at` and SHA; analysis can exclude rows written after the decision's run.
