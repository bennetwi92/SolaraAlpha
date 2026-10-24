# 0035 — Continuous improvement: every agent reviews its own record, the PM ranks, the owner decides

**Status:** proposed (owner's request, 2026-10-24). Nothing is built until the owner answers the
decisions at the end. Widens ADR 0027's phase C (a monthly feedback loop from the post-mortems)
from the PM alone to every agent.

**Context.** The owner wants the process to get better week by week: fewer repeated mistakes,
a better strategy. The raw material mostly exists, but it is scattered and nobody is asked to
read it:

| What we already record | Where |
|---|---|
| Every verdict, card and its evidence | `labels`, `results`, `evidence` (ADR 0016) |
| Forward returns per label and toolkit band; did downgrades avoid losers | `learn.py scorecard`, `agents-value` |
| Closed trades: result in R, set-up, one lesson | `/postmortem`, `learn.py journal` |
| What fixed rules would have done | the shadow book (ADR 0028) |
| Validator disagreements | `validator_stats.py` |
| Monitor revisions, undos, declines | `book.py revisions` |
| Orders sent, refused, rejected | `executions` (ADR 0031) |
| Failures and repeats | the `desk-errors` issue (ADR 0032) |
| Agent cost, tokens, minutes | `agent_runs`, `learn.py agents` |
| What the owner and agents said | the desk thread (ADR 0033) |

What is missing: (1) one view per agent of *its own* track record, (2) a regular moment to
think about it, (3) one place proposals go, (4) a rule for how they reach the owner and what
happens after a yes.

**Decision (proposed).** Four steps, light enough to run every month without anyone noticing
the overhead.

1. **Each agent gets its own record** — `scripts/review_pack.py --agent NAME [--since D]`, built
   from the tables above, no new data. Per agent: what it decided, what happened next, where it
   was overruled (owner undo / reject, PM override, validator downgrade), its errors, its cost.
   For example: research sees its ACTs' 2/4/8-week returns against the shadow book's picks;
   monitor sees each stop it set against where price went next; execution sees fill price
   against plan; the PM sees its allocation against "top 5 by rank". Hindsight is labelled
   hindsight, as in the post-mortem pack.
2. **Observations any time, a retro once a month.** Any agent may post an `observation:` line in
   the desk thread the moment it notices something ("third week running the screen shortlisted a
   stock Trading212 doesn't offer"). On the first weekly run of each month, after `/pm`, each
   desk area (senior-quant, fundamental-strategist, portfolio-manager, execution-trader) reads
   its review pack and its month's observations and writes a short retro: what went well, what
   went wrong, and **at most two proposals**. "Nothing to change" is a good answer, not a
   failure. A single costly mistake (a loss beyond 1.5R, a stop not at the broker, a missed
   review) gets an incident note at once, not a month later.
3. **One proposal format, through the one door.** A proposal is a `proposal` verdict recorded
   with `record.py` (so it is in the DB with its evidence) and mirrored as a GitHub issue
   labelled `proposal` + `needs-owner`. It fits on a phone screen:
   - **Problem** — one sentence, plain words.
   - **Evidence** — the numbers and how many cases (n). Under 10 cases it is labelled a hunch.
   - **Change** — what exactly would change, and in which file.
   - **Cost / risk** — what it could break, what it costs to run.
   - **How we'll know** — the check, and the date we look again.
4. **The PM ranks, the owner decides.** `/pm` reads every open proposal once a month and writes
   one **improvement memo**: at most five items, best first, each three lines (what, why, PM's
   recommendation: approve / reject / wait for more data). It merges duplicates and flags
   proposals that clash. The memo goes to the inbox and the desk thread. The owner answers in
   Telegram or on the issue: `approve 2`, `reject 3: reason`, `more data 1`. Only the owner's
   yes changes anything; the PM never approves a process change.

**After a yes.** The area's owner builds it as a normal PR referencing the issue. One change per
area at a time, so its effect can be measured. On the "how we'll know" date the next retro
reports whether it worked; if not, it proposes the revert. Every decision (yes, no, wait) and
the reason is kept on the issue, and the retro pack lists past rejections so the same idea is not
raised again without new evidence.

**Guardrails.** The locked holdout (ADR 0029) is never used to justify a proposal. Agents never
edit their own skills, bands, `portfolio.toml` or ADR 0022; they propose. Hard limits stay in
code. Lessons that are rules go into the skill they belong to (decision rules live only there);
lessons that are context go in the agent's review pack, so it reads them before deciding.

**Consequences.** Four extra agent invocations a month plus one PM memo; at most five items a
month reach the owner, in plain English. Early on there will be few closed trades, so most
proposals will be "wait for more data"; the loop still catches process mistakes (errors,
refusals, missed reviews), which need no statistics.

**Decisions for the owner** (recommendation first):

1. Cadence: **monthly retro + immediate incident notes** / weekly / quarterly.
2. Cap on what reaches you: **five a month** / three / no cap.
3. Where you answer: **Telegram and the issue** / issue only.
4. Build order: **review packs first** (useful on their own), then the retro, then the memo.
