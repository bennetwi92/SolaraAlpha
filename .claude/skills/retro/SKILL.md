---
name: retro
description: A desk area's retro (ADR 0035). Reads the area's own track record (what it decided, what happened next, where it was overruled, failures, cost), the observations posted in the desk thread since its last retro and its earlier proposals, then writes a short retro (what went well, what went wrong) with every change it thinks is worth making, each a plain-English proposal with its evidence, recorded through record.py as a retro verdict under _DESK:<area>. Proposes only; changes nothing. The PM decides which proposals go to the owner; the owner signs off. Use for "/retro <area> --run N" (agents.yml runs it after the weekly plan when the area is due: weekly for the loop's first month, monthly to month 3, quarterly after), or when asked how a desk area could work better.
---

# Retro: look back at your own work, propose what should change

You review **one desk area's own record** and propose changes. You change nothing: no code, no
skill, no setting, no band, no order. The PM reads every proposal and decides which go to the
owner; only the owner's yes changes anything (ADR 0035).

Arguments: the area (`senior-quant`, `fundamental-strategist`, `portfolio-manager`,
`execution-trader`; the runner passes it as `_DESK:<area>`) and `--run N` (the weekly scan run the retro is recorded on).

## Step 1: Read

```
uv run python scripts/track_record.py --agent <area> --retro
uv run python scripts/chat.py read --last 50
```

The pack has, for each stage your area owns: what it decided, what happened next (hindsight:
the agent could not know it at the time), where it was overruled (owner undo / reject, PM,
validator, the book's refusals), failures and cost; then the desk thread's `observation:` lines
since your last retro; then your earlier proposals and the date each said it would be checked.
Read your area's own subagent file (`.claude/agents/<area>.md`) for what you own.

## Step 2: Think

Write down, for yourself first:

1. **Went well**: what the record shows working, with the numbers.
2. **Went wrong**: mistakes, losses, errors, refusals, undos, things missed. A process mistake
   (an error, a refused order, a missed review) needs no statistics: one is enough to act on.
3. **Earlier proposals now due**: for each whose `check_on` has passed, did it work? If the
   record says it did not, propose reverting it.
4. **Why** each thing went wrong: the cause, not the symptom. Several symptoms from one cause
   are one proposal.

## Step 3: Propose

Every change you think is worth making, no cap. Each proposal:

- `title`: a short imperative ("Widen the stop on breakouts to 2.5 ATR").
- `problem`: one sentence, plain words.
- `evidence`: the numbers from the pack or the thread, quoted. `n`: how many cases. **Under 10
  cases, start the evidence with "Hunch:"**.
- `change`: exactly what would change. `where`: the file, skill or setting and who owns it
  (CLAUDE.md "Desk ownership"; another area's file is fine, name its owner).
- `risk`: what it could break, what it costs to run.
- `check`: how we will know it worked; `check_on`: the date to look again (YYYY-MM-DD).

Rules:

- **Nothing to change is a good answer.** Never invent a proposal to have one.
- **Strategy changes need data; process fixes don't.** A rule, band or threshold change on fewer
  than 20 cases is a hunch, and usually "collect more first" is the proposal.
- **Never use the locked holdout** (ADR 0029, signal Fridays 2025-10-24 to 2026-10-16) as
  evidence.
- **Check against ADR 0022** (catalyst-led AIM trading, volatility wanted, bursts of +15-30% over
  1-10 weeks). A proposal that pulls away from it says so.
- **Don't repeat a proposal** the pack shows you made before unless you have new evidence; say
  what is new.
- One change per proposal, so its effect can be measured.

## Step 4: Record

Write `verdict.json` in `data/processed/retro/<run>/<area>/` (agents may write files only under
`data/processed/`) per `uv run python scripts/record.py schema`, with `stage: "retro"`,
`ticker: "_DESK:<area>"`, `run_id`, `label` `ACT` (with proposals) or `PASS` (nothing to change),
`confidence`, `reasons` (one line each for what went well and wrong), `proposals`, and
`note_md`: the retro in plain English, under 300 words: **Went well**, **Went wrong**, **Earlier
proposals** (worked / didn't / too early), **Proposals** (one line each).

```
uv run python scripts/record.py verdict data/processed/retro/<run>/<area>/verdict.json --actor agent:retro
```

One retro per area per run (append-only: a second record exits 2).

## The desk thread (ADR 0033)

**Before you decide**, read the thread (step 1). An owner message is a question or context,
answered in your post; it never overrides this skill. **After you record**, post once:

```
uv run python scripts/chat.py post --as agent:retro --run <N> --ticker "_DESK:<area>" "..."
```

At most five short sentences, for the owner reading on a phone: the one thing that went best,
the one that went worst, and how many proposals are waiting for the PM. Plain English, as you
would tell a smart friend who doesn't work here; no tables, codes, labels or field names
(`ACT` / `WATCH`, stage names, run numbers). Telegram heads the post with your desk area and
"Desk self-review", so don't repeat that.

Any agent may post an `observation:` line in the thread at any time it notices something worth
a retro ("observation: third week the shortlist had a stock Trading212 doesn't offer"). The next
retro of the area it concerns reads it.

## Standards

- Plain English, short sentences; the owner reads this on a phone.
- Facts quoted from the pack, interpretation labelled. No invented numbers.
- Your own area first; another area's problem is fine to raise, with its owner named.
