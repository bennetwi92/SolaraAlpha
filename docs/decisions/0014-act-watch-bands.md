# 0014 — What we're looking for: ACT / WATCH / PASS / ERROR bands (amends 0010)

**Context.** 0010's toolkits each gave pass / fail against one set of conditions, and a stock
moved on only while every toolkit passed. The owner wants every stock labelled **ACT, WATCH,
PASS or ERROR**, decided on all four screening toolkits, so near-misses become a watch list the
daily monitor can act on, and the definition of "what we're looking for" written down and coded
(story map §4 A1, S1.3–S1.5, S1.7).

**In one line:** a stage-2 uptrend with positive momentum, priced near support rather than
stretched above it, outperforming its peers, with earnings growing or being revised up, and at
least 2:1 to the next resistance (or nothing overhead).

| Question | Toolkit / fields | ACT | WATCH |
|---|---|---|---|
| In an uptrend? Stage 2: price above a rising 40w MA, 10w above 40w | technical `trend` | ≥ 0.25 | 0 to 0.25 |
| Momentum behind it? | technical `momentum` | > 0 | -0.25 to 0 |
| Not overextended? Near the 10w MA, RSI with room, histogram turning up | technical `entry` | `entry` ≥ 0 (`risk_pct` dropped as a gate: see the 2026-10-18 amendment) | `entry` < 0 (extended: wait for a pullback) |
| Structure and room to run? | levels `structure`, `reward_risk`, `blue_sky`, `coil_pending` | `structure` > 0 and R:R ≥ 2 (blue sky = ∞ counts) | R:R ≥ 1.5, or coiled with the trigger above the close (wait for the break) |
| Leading its market, industry and peers? | relative_strength `rs`, `rs_high`, `peer_top_half` | `rs` > 0 | `rs` ≥ -0.25 with `rs_high` or a top-half `peer_rank` |
| Potential for growth, at a sane price, not broken? | fundamentals `growth`, `quality`, `eps_revision` | `growth` > 0, `quality` > -0.5, `eps_revision` ≥ 0 or unavailable | `quality` > -0.5 and revisions rising |

**Decision.**
- **Bands per toolkit** (`solaraalpha.toolkits`, `act=` / `watch=` tuples of clauses). A
  toolkit's label is ACT if every `act` clause holds; else WATCH if every `watch` clause holds;
  else PASS; ERROR if it raised. A toolkit with no clauses (`events`) is ACT whenever it runs:
  it is the input to research, not a gate. As coded:
  ```
  technical          act:   trend >= 0.25 | momentum > 0 | entry >= 0
                     watch: trend >= 0 | momentum >= -0.25
  levels             act:   structure > 0 | reward_risk >= 2
                     watch: reward_risk >= 1.5 or coil_pending == true
  relative_strength  act:   rs > 0
                     watch: rs >= -0.25 | rs_high == true or peer_top_half == true
  fundamentals       act:   growth > 0 | quality > -0.5 | eps_revision >= 0 or eps_revision is nan
                     watch: quality > -0.5 | eps_revision > 0
  events             (none: always ACT)
  ```
  `|` separates clauses; within a clause `or` joins conditions. Derived card fields:
  `LevelsCard.coil_pending` = coiled and trigger above the close;
  `RelativeStrength.peer_top_half` = `peer_rank <= ceil((peer_count + 1) / 2)`.
- **Clause grammar** (`pipeline.Clause`, `Condition`): `field op value` as before, plus
  `field is nan`; conditions joined by ` or `. NaN or missing values still fail ordinary
  conditions, so a toolkit with a missing input lands on PASS, never ACT.
- **Combining rule** (`pipeline.combine`): ERROR if any toolkit errored; else PASS if any is
  PASS; else WATCH if any is WATCH; else ACT. A stock is only as good as its weakest toolkit, so
  one model can't hide another (as in 0010). Fundamentals is one of the four and combines like
  the others (§6 Q3 default: it can veto and it can hold a stock at WATCH, but ACT needs all).
- **Stage flow** (`Stage.runs_on`): `screen` (technical + levels + relative_strength) runs on
  every ticker; `fundamentals` on running label ACT or WATCH, so a WATCH stock still gets a full
  four-toolkit label; `events` on ACT only. Tickers outside `runs_on` get `skip` steps. The
  **screen label** is `combine` over the four screening toolkits (`pipeline.screen_label`).
- **Storage**: `steps.status` holds each toolkit's label in lowercase (act / watch / pass /
  error / skip). New table `labels(run_id, ticker, stage, label, confidence, reason, actor)`,
  one row per run, ticker and stage: the scan writes `screen` rows whose reason lists each
  toolkit's label and failing clauses (e.g. `technical WATCH: failed entry >= 0 (entry=-0.41)`);
  the agents write `validate` / `research` rows (ADR 0016). `store.load_labels` and
  `store.shortlist(run_id, stage, labels)` read them. Steps from runs before this change hold
  pass / fail, where pass meant "moved on".
- `scan.py` reports per-toolkit labels and the screen label, with ACT / WATCH (why) / ERROR
  sections; its exit code is still 1 when any toolkit errored.

**Placeholders.** Every threshold above is a proposal until the screen is backtested
point-in-time (#138) and the labels scored against forward returns (#137). Change them in
`toolkits.py`, not in the pipeline.

**Consequences.** WATCH stocks cost one more toolkit run (fundamentals) each week. A toolkit
that cannot compute a field it is banded on (e.g. `eps_revision` without revisions data) is
handled by explicit `is nan` clauses, so the bands say which gaps are tolerated. Ranking the
ACT list and structured WATCH reasons come in #125.

## Amendment (#125): structured WATCH reasons and the ACT rank

`labels` gains two nullable columns, added by `store.connect()` with `ALTER TABLE ... ADD
COLUMN` when missing (idempotent, both backends): **`watch_on`** (TEXT, JSON) on WATCH rows and
**`rank`** (INTEGER) on ACT rows. Older rows stay NULL.

- `watch_on` is a **list**, one object per WATCH toolkit (`pipeline.watch_on`), each with
  `toolkit` and a `kind`: technical extended (`entry` < 0) → `{"kind": "pullback", "level":
  <10w MA>, "entry": <entry score>}` (the technical card now carries `ma10` and `ma40`);
  levels coiled → `{"kind": "break", "trigger": <trigger>}`; levels thin → `{"kind":
  "reward_risk", "reward_risk": x}`; relative strength → `{"kind": "rs", "rs": x}`;
  fundamentals → `{"kind": "revisions", "eps_revision": x}`; any other WATCH toolkit keeps its
  reason text. A list because several toolkits can be WATCH at once and the daily job acts on
  each.
- The daily job (ADR 0017) reads `watch_on` for the latest scan's WATCH tickers: a `break`
  item uses its `trigger` (the levels card's pending coil when there is no item);
  a `pullback` item fires **`watch_pullback`** when the close is within `PULLBACK_ATR` = 1 daily
  ATR of `level` while the close is above the technical card's `ma40` (trend intact). Both are
  report triggers: re-run the screen for the stock.
- `rank` = position by `portfolio.fit.rank_score` over the ticker's cards among the run's ACT
  tickers, 1 = best, NaN last, ties by ticker (`pipeline.rank_act`), so it is deterministic.
  `store.shortlist`, `planner.final_labels` (and so the weekly pack's "ACT by rank" and the plan
  order) and the agents' `queue` use the stored rank; unranked ACT rows fall back to a fresh
  `rank_score`.

## Amendment (#138): backtesting the bands

`analysis/backtest.py` replays the screen **point-in-time**: for each Friday the technical,
levels and relative-strength cards are computed from the weekly bars up to that week (benchmark
bars sliced the same way) and the fundamentals card with `as_of` = that week (`known()` lags),
then labelled with the current bands. `forward` gives the 4 / 8 / 16-week forward return and
the excess over the market; `sweep` changes one ACT threshold at a time (`trend` in {0, 0.1,
0.25, 0.4}; `reward_risk` in {1.5, 2, 3}; `rs` in {-0.1, 0, 0.1}) and reports the ACT count,
mean return, excess and hit rate; `report` adds the sample sizes and a **suggested** band
table (best mean 8-week return with at least 30 ACT weeks). The universe is today's watchlist,
so the report carries the survivorship caveat and the bands in code are never changed by it:
the owner reads the report and edits `toolkits.py` if convinced. `scripts/backtest.py` fetches
fresh and caches nothing but the report.

## Amendment (2026-10-18): `risk_pct` is not a gate

`risk_pct` is `3 x weekly ATR / close`, so `risk_pct <= 0.15` was a low-volatility filter
(weekly ATR <= 5% of price). The strategy is trading for capital efficiency and wants volatile
stocks; risk is handled by sizing (`plan.py` sizes from the stop distance), not by excluding
them. A 5-year replay (every 8th week, 50 stocks) passed it in 1.7% of weeks and 3.5% of uptrend
weeks (median uptrend `risk_pct` 0.22). Dropped from the technical ACT band; the field stays on
the card for sizing and reports.
