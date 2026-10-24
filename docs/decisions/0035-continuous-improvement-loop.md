# 0035 — Continuous improvement: every agent reviews its own record, the PM ranks, the owner decides

**Status:** accepted (owner, 2026-10-24; answers in "Owner's decisions" below). Not built yet.
Widens ADR 0027's phase C (a monthly feedback loop from the post-mortems)
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

**Decision.** Four steps.

1. **Each agent gets its own record** — `scripts/review_pack.py --agent NAME [--since D]`, built
   from the tables above, no new data. Per agent: what it decided, what happened next, where it
   was overruled (owner undo / reject, PM override, validator downgrade), its errors, its cost.
   For example: research sees its ACTs' 2/4/8-week returns against the shadow book's picks;
   monitor sees each stop it set against where price went next; execution sees fill price
   against plan; the PM sees its allocation against "top 5 by rank". Hindsight is labelled
   hindsight, as in the post-mortem pack.
2. **Observations any time, a retro on a cadence.** Any agent may post an `observation:` line in
   the desk thread the moment it notices something ("third week running the screen shortlisted a
   stock Trading212 doesn't offer"). At each retro (cadence below), after `/pm`, each
   desk area (senior-quant, fundamental-strategist, portfolio-manager, execution-trader) reads
   its review pack and the observations since its last retro and writes a short retro: what went
   well, what went wrong, and **every proposal it thinks is worth making** (no cap). "Nothing to change" is a good answer, not a
   failure. A single costly mistake (a loss beyond 1.5R, a stop not at the broker, a missed
   review) gets an incident note at once, not at the next retro.
3. **One proposal format, through the one door.** A proposal is a `proposal` verdict recorded
   with `record.py` (so it is in the DB with its evidence) and mirrored as a GitHub issue
   labelled `proposal`. It fits on a phone screen:
   - **Problem** — one sentence, plain words.
   - **Evidence** — the numbers and how many cases (n). Under 10 cases it is labelled a hunch.
   - **Change** — what exactly would change, and in which file.
   - **Cost / risk** — what it could break, what it costs to run.
   - **How we'll know** — the check, and the date we look again.
4. **The PM decides what goes forward, the owner signs off.** At each retro `/pm` reads every
   open proposal and judges it against the strategy as a whole (ADR 0022): a change one
   specialist wants may not serve the book. For each it decides **forward**, **reject** (with a
   one-line reason) or **wait for more data**. It merges duplicates and settles clashes. It writes
   one **improvement memo**: the forwarded items, best first, each three lines (what, why, what
   it changes), then one line per rejected or parked item so the owner sees everything. The memo
   goes to the owner on Telegram (the desk thread). The owner replies there: `approve 2`, `no 3`.
   The PM's reject is final unless the owner asks to revive it; nothing changes without the
   owner's yes on a forwarded item.

**After a yes.** The area's owner builds it as a normal PR referencing the issue. One change per
area at a time, so its effect can be measured. On the "how we'll know" date the next retro
reports whether it worked; if not, it proposes the revert. Every decision (yes, no, wait) and
the reason is kept on the issue, and the retro pack lists past rejections so the same idea is not
raised again without new evidence.

**Guardrails.** The locked holdout (ADR 0029) is never used to justify a proposal. Agents never
edit their own skills, bands, `portfolio.toml` or ADR 0022; they propose. Hard limits stay in
code. Lessons that are rules go into the skill they belong to (decision rules live only there);
lessons that are context go in the agent's review pack, so it reads them before deciding.

**Consequences.** Four retros plus one PM memo per cadence step; weekly at first. Every item
that clears the PM reaches the owner, in plain English; cost is not a constraint (owner). Early on there will be few closed trades, so most
proposals will be "wait for more data"; the loop still catches process mistakes (errors,
refusals, missed reviews), which need no statistics.

**Owner's decisions** (2026-10-24):

1. **Cadence tapers.** Weekly retros for the first month after the loop goes live, monthly for
   months 2-3, quarterly after that. Incident notes are immediate throughout.
2. **No cap** on proposals per agent or items reaching the owner; improve everything that needs
   it. Cost is immaterial.
3. **Answers on Telegram** (the desk thread).
4. **The PM has the say on what goes forward**: it can reject a specialist's proposal as not in
   the strategy's interest; the owner signs off what the PM forwards.

Build order (proposed, not yet answered): review packs first, then the retro, then the memo.
