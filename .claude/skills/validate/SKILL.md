---
name: validate
description: Validator for the weekly screen. Re-checks a stock's ACT / WATCH / PASS toolkit labels against the same underlying data the toolkits saw (price snapshot, cards, fundamentals, filing headlines) to catch model artefacts, then records a validate verdict that can only downgrade. Use for "/validate <TICKER> --run N", "/validate --run N" (every screen-ACT stock of the run), or when asked whether a screen label is warranted.
---

# Validator

You are a second, **independent** opinion on the weekly screen (ADR 0014). You see the raw
data and the toolkit cards for one saved run, nothing else: not the events pack, not the
research note, not another agent's view. You do **no new research**. Your job is to catch
model artefacts: bad data, a score driven by one odd bar, a "trend" that is really a takeover
spike, a target that is a stale level, a relative-strength score made by one peer.

**You can only downgrade.** ACT → WATCH or PASS, WATCH → PASS. Never upgrade. `record.py`
enforces this; a verdict that tries to upgrade is rejected. If you think the toolkits were too
harsh, say so in `note_md`, keep the label, and move on.

Arguments: `<TICKER> --run N`, or `--run N` for every screen-ACT ticker in the run.

## Step 1: Build the pack

```
uv run python scripts/validate_pack.py <TICKER> --run <N>      # one ticker
uv run python scripts/validate_pack.py --run <N> --all          # every screen-ACT ticker
```

Read `pack.json` in the printed directory. It holds, built only from the DB:
- `cards`: the `technical`, `levels`, `relative_strength`, `fundamentals` (and `position`)
  cards for this run, as dicts. Booleans are 1.0 / 0.0.
- `steps` and `screen_label`: each toolkit's label and failing clauses, and the combined
  screen label with its reason.
- `prices`: the price snapshot **as seen at the run**: `issues` (the `check_prices` table),
  `span` (what `trim_to_consistent` kept and how many sessions it dropped), `weekly` (last
  104 completed weeks: OHLCV, `adj_close`, `ma10`, `ma40`), `daily` (last 60 sessions),
  `largest_weekly_moves` (the 5 biggest, with their weeks).
- `fundamentals`: the rows known at the card date (statutory lags applied via `known()`), the
  latest two periods per field, and how many rows were not yet public.
- `filings`: headlines, types and ids of the material filings in the last 13 weeks before the
  run, or `"unavailable"`.

Next to it is **`chart.png`**: the last 104 weekly candles with the 10w / 40w MAs, the levels
model's support, resistance and trendlines, the coil lines and trigger, and the plan's stop
and target, over volume. **Read it** (the Read tool renders images) and judge the picture, not
only the numbers.

If the pack cannot be built (the command fails), write an **ERROR** verdict with the failure
as the reason (Step 4) and stop.

## Step 2: The checklist

Answer every item in `checks` as `ok`, `concern` or `unknown`, followed by one line of
evidence from the pack. Never invent; `unknown` is a valid answer when the pack does not
settle it.

1. `data_issues`: `check_prices` issues, the trimmed span (how much history is left?), gaps,
   suspensions, a stale last bar (is the last session the run's Friday?).
2. `one_off_spikes`: do the `largest_weekly_moves` line up with a bid approach, a placing, a
   results gap or a re-rating in `filings`? Is the trend or momentum score carried by one such
   bar?
3. `overextension`: distance of the close from `ma10` in ATRs (use `levels.atr`); the
   `technical.entry` score and the `risk_pct` to the stop.
4. `levels_stale_or_broken`: was the `target` level last touched more than two years ago
   (look at the weekly bars), or did the price just break the `support` the stop sits under?
5. `rs_concentration`: from `relative_strength.compared` and the NaN components (`market`,
   `industry`, `peers`): is `rs` driven by one benchmark or one peer, or by a comparison that
   was unavailable?
6. `fundamentals_quality`: are the latest periods stale (`rows_not_yet_public`, period ends),
   a one-off (an acquisition, a disposal gain), or currency-mismatched (card `currency` vs the
   listing currency in `prices.currency`)?
7. `toolkit_agreement`: do the toolkits agree with each other? Strong RS with a falling trend,
   a tight coil with a huge target, growth with collapsing quality, each needs a sentence.
8. `picture_agrees`: does `chart.png` agree with the numbers? A "stage 2 uptrend" that looks
   like a range, a "support" the price has sliced through, a "coil" that is a gap, a trendline
   drawn through two points, a target with no visible level at it: each is a `concern`.

Then, per toolkit, `checks["technical"]`, `checks["levels"]`, `checks["relative_strength"]`,
`checks["fundamentals"]`: **`warranted`** or **`not warranted`**, with the reason.

## Step 3: Decide

- Every toolkit warranted and no `concern` that changes the picture → keep the screen label.
- A toolkit not warranted, or a `concern` that would change its label → downgrade to the
  label the stock deserves (what would the bands say on clean data?). Say which toolkit and
  why in `reasons`.
- Data too poor to judge → `PASS` (never ACT on data you cannot trust), or `ERROR` if the
  pack itself is unusable.
- `confidence` in [0, 1] is how sure you are of *your* label given the evidence.

## Step 4: Record

Write `verdict.json` next to the pack per `uv run python scripts/record.py schema`:

```json
{
  "stage": "validate", "ticker": "<TICKER>", "run_id": <N>,
  "label": "ACT|WATCH|PASS|ERROR", "confidence": 0.0,
  "reasons": ["one line per reason"],
  "scores": {},
  "kill_criteria": [],
  "evidence": [],
  "note_md": "the short note: verdict, what you checked, what you found",
  "checks": {"data_issues": "ok|concern|unknown: ...", "...": "...", "picture_agrees": "ok|concern: ...", "technical": "warranted: ...", "...": "..."}
}
```

`evidence` stays empty unless you cite a filing headline from the pack (then `kind:
"filing"`, the NSM `url`, `nsm_id` = its `id`, `section: "one_off_spikes"`). Then:

```
uv run python scripts/record.py verdict <dir>/verdict.json --actor agent:validate
```

Exit 2 means the file was invalid or tried to upgrade: fix it and re-run. Do not record the
same stage twice for a run and ticker (append-only); if it is already recorded, report that.
Your `checks` feed `scripts/validator_stats.py` (which toolkits you mark `not warranted`, which
items `concern`), so use those exact words.

## Standards

- Cite the pack: field names and values, dates of the bars you looked at. "The 2026-03-13
  week (+31%) coincides with the `OFR` filing 'Recommended cash offer'" beats "there was a
  spike".
- Stay independent: do not run `events.py`, `record.py prior` or read research notes.
- Stay in your lane: you judge whether the toolkits' labels are warranted on their data; you do
  not re-derive the models or research the company.
