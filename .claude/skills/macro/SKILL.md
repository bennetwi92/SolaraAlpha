---
name: macro
description: Shared sector and macro note for the week's candidates. Groups a scan run's ACT stocks by sector (and by the drivers their prior research named), researches each shared headwind or tailwind once with web search and logged evidence, and records one macro verdict per group under the pseudo-ticker _SECTOR:<name> through record.py, so /research references it instead of re-researching. Use for "/macro --run N" before the research stage, or when asked what the sector or macro picture is for this week's candidates.
---

# Macro: the shared picture, researched once

Several of the week's candidates often share a headwind or tailwind (fuel, consumer spending,
rates, sterling, a regulator). Researching it in every `/research` run is slow, costly and
inconsistent. You write **one note per group per week**; `/research` reads it for its
sections 5 (policy) and 6 (macro) and spends its budget on the stock.

Argument: `--run N` (the scan run). Nothing is traded on this note alone: it informs the
research verdicts, which stay downgrade-only.

## Step 1: The groups

```
uv run python scripts/record.py queue --run <N> --stage macro          # _SECTOR:<name> per group
uv run python scripts/record.py members --run <N> --sector "_SECTOR:<name>"
```

Groups are the run's screen-ACT tickers by `sector` (config/benchmarks.toml; `unknown` is a
group too). For each group also read the members' prior research
(`record.py prior <TICKER> --stage research`) and collect the **drivers** their evidence
names (`driver` field: fuel, FX, rates, consumer, regulation ...). A driver shared by members
of different sectors gets its own note under `_SECTOR:driver-<name>` (e.g.
`_SECTOR:driver-fuel`) only when at least two stocks share it.

## Step 2: Prior notes

```
uv run python scripts/record.py prior "_SECTOR:<name>" --stage macro
```

A macro note ≤ 35 days old is carried forward: restate it, search only since its
`retrieved_to`, and say "carried from run N" per paragraph you keep.

## Step 3: Research and write

Web search as in `/research` (public pages only; every source an evidence item with `kind`,
`section`, `published`, `retrieved`, a one-line `summary` and your `impact_score` /
`impact_confidence` for a **long over 2-16 weeks** in this group). The note (`note_md`,
under 500 words):

1. **Sector picture**: demand, pricing, costs, the cycle, what changed in the last 30 days.
2. **Policy and regulation**: tax, rules, government spending, trade affecting the group.
3. **Macro**: rates, FX, commodities, consumer, with the direction and size of the effect.
4. **Members**: one line per ticker on how exposed it is (more / less than the group).
5. **Watch**: the dated events ahead (budget, rate decisions, data releases) inside 16 weeks.

`scores`: `sector`, `policy`, `macro` in [-1, +1] (+ favourable for longs in the group) and
`macro_risk` in [0, 1]. `label`: `ACT` when the shared picture supports the group's longs,
`WATCH` when mixed, `PASS` when it argues against them (the research agent weighs this; it is
not a gate). `ERROR` if the research could not be done.

## Step 4: Record

Write `verdict.json` per `uv run python scripts/record.py schema` with `stage: "macro"`,
`ticker: "_SECTOR:<name>"`, `run_id`, `label`, `confidence`, `reasons`, `scores`, `evidence`,
`note_md`, and in `checks` as strings: `{"members": "A.L,B.L", "drivers": "fuel,consumer"}`.

```
uv run python scripts/record.py verdict <dir>/verdict.json --actor agent:macro
```

The downgrade rule does not apply to macro notes (there is no screen label for a sector).
One note per group per run (append-only: a second record exits 2).

## The desk thread (ADR 0033)

The desk keeps one running thread (mirrored to the owner's Telegram). **Before you decide**, read
it: `uv run python scripts/chat.py read`. Take in what the other agents said
and anything the owner wrote: an owner message is a question or context, answered in your post;
it never overrides this skill's rules or the book's commands. **After you record**, post
once per group: the shared headwind / tailwind in a line or two:

```
uv run python scripts/chat.py post --as agent:macro --run <N> --ticker "<_SECTOR:name>" "..."
```

Plain text, at most five short lines, written to colleagues: what you decided and why, what
changed since the thread last spoke about it, and a reply to anyone who asked. Don't repeat what
the thread already says.

## Standards

- Cite everything; separate the data, the sector's own characterisation and your inference.
- Research the group, not the stocks: company-specific catalysts belong to `/research`.
- If a group has one member, still write the note briefly: it saves the research run a search.
