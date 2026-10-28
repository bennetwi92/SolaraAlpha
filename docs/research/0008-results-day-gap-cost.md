# Research 0008: What a results day costs a stop-sized position, and whether a gap cap helps

*2026-10-28. Proposal B1 approved by the owner after research 0007. This is a historical check
of the cap specified in research 0006 (#214 F5), in-sample only (ADR 0029). It is not the live
shadow-book row (B2). Script: `scripts/research/r8_results_gap.py` (data: the `collect` step of
`r7_reaction_speed.py`).*

## Question

A gap at the open goes through any stop, and a results day is when AIM gaps (research 0007).
Holding into results is where the burst is, so how often has a position sized the way we size
today lost more than twice its planned risk on one? Would the research 0006 gap cap have cut
that loss, and at what cost?

## Method

- **Events.** Every AIM watchlist stock is treated as held into each of its NSM news filings in
  the Financial Results category, split into two kinds:
  - **results**: `FR` / `IR` / `QRT` / `QRF`, 557 events;
  - **trading statements**: `TST` (pre-close updates and profit warnings), 383 events.

  The sample runs from 2022 to 2025-10-10 (in-sample: every 5-session window ends by
  2025-10-17). The first 200 sessions of each stock are a warm-up.
- **The position, as today.**
  - **Risk:** 2% of equity.
  - **Stop:** k × ATR(14) below the previous close, with k = 2 (median stop 8.1%) and k = 3
    (median 12.1%). It works as a stop-market order: a gap below it fills at the open.
  - **Size:** capped at 25% of equity.
  - **Holding:** from the previous close to 5 sessions after the event, or until stopped.
- **Gap cap (research 0006).**
  - **Size:** capped so that a move of `g` costs 4% of equity.
  - **`g`:** the stock's worst past move on these events when it has at least 4, else the
    universe p90 of past |moves|. Both are point-in-time.
- **Measures.**
  - **Tail:** the share of trades losing more than 4% of equity (2× risk), the p95 loss and the
    worst loss.
  - **Return:** mean P&L per trade in % of equity, and the difference between the cap and today
    with a 90% bootstrap interval clustered by date.

## Results (2 × ATR stop; 3 × ATR in the script output)

| | n | cap binds | loss > 2× risk | p95 loss | worst | mean P&L | cap − today |
|---|---|---|---|---|---|---|---|
| Results, as today | 557 | | **0.0%** | 2.0% | 3.5% | +0.40% | |
| Results, gap cap | | 9% | 0.0% | 2.0% | 3.5% | +0.38% | −0.02% [−0.04, −0.01] |
| Trading statements, as today | 383 | | **3.1%** | 3.1% | **7.5%** | +0.19% | |
| Trading statements, gap cap | | 13% | 2.9% | 3.0% | 7.5% | +0.14% | −0.04% [−0.07, −0.02] |

The table's figures are in % of equity. Across all 940 events, the median |move| on the day is
3.4% and the p90 is 11.9%. 1.5% of events moved below −20% and 1.2% above +20%.

1. **Results days were not the danger.** No position lost more than twice its planned risk on
   finals or interims, at either stop width, and the average trade made money.
2. **Trading statements are where the tail is: profit warnings.** 3.1% of trading statements
   held with a 2-ATR stop cost more than 4% of equity. The worst was 7.5%: Frontier
   Developments, −43% on 9 Jan 2023. Every one of the twelve worst trades was a trading
   statement, nine of them falls of 15 to 46%.
3. **The research 0006 gap cap does not help.** The worst warnings came from stocks whose past
   moves were mild (`g` of 8 to 15%). The cap did not bind on them, and the worst loss is
   unchanged. Where it did bind, it cost return. The difference is small but reliably negative
   (90% intervals below zero for both kinds).
4. **What bounds the tail today is the 25% position cap.** With the 2-ATR stop it binds on
   about half of the positions, and it is the reason the worst loss is 7.5% rather than 10%
   or more. A flat lower cap trades return for tail in proportion. At 15%, trading statements
   go from 3.1% to 0.8% of trades losing more than 4% of equity, and the worst loss from 7.5%
   to 5.9%. Mean P&L falls about 30% on both kinds. There is no free lunch.
5. **A wider stop gives a smaller tail, through size.** At 3 × ATR the median position is 17%
   of equity rather than 25%, and only 0.8% of trading statements cost more than 4% of equity
   (worst 5.0%). That is a research-agent choice per trade, not a rule.

## Caveats

- **Survivorship understates the tail.** The universe is today's AIM 100 and AIM UK 50. Stocks
  that collapsed on a warning and fell out of the index, or were delisted, are missing. The
  real frequency and depth of warnings is worse than measured here.
- **Every stock is treated as held.** The shortlist is not a random stock. For a stock in an
  uptrend (close above its 50-day average, which is above its 200-day), trading statements
  showed fewer losses beyond 2× risk (2.0%, worst 4.1%, n = 102). That sample is too small to
  lean on.
- **Not the agents' levels.** The stop is k × ATR, a stand-in for the research agent's stop.
- **The 5-session window** includes a few post-event moves: SRT Marine Systems' loss came after
  the announcement day, not on it.

## Recommendation

1. **Don't build B2 (the research 0006 shadow-book row) as specified.** The cap keys on the
   stock's past moves, and those don't predict the warning that hurts. It costs a little return
   for no measurable tail reduction. Research 0006's live test would need 6 to 12 months to show
   the same.
2. **The risk to manage is the trading statement, not the results day.** Two small changes,
   for the PM's and the owner's decision:
   - The monitor agent's pre-event decision (`event_policy = "flag"`) should weigh a trading
     statement as the higher-risk event, especially for a stock whose last update was cautious
     or whose forecasts are under pressure. The tail comes from these, not from finals or
     interims. A rule change belongs in the `/monitor` skill.
   - The PM pack's `event_gap` line uses the stock's own worst past move. This study shows that
     understates warnings. Showing the universe figures beside it (p90 |move| about 12%; the
     worst warning fall about 46%) would give the PM a fairer worst case.
3. **No change to `max_position` (25%) is recommended.** At 2% risk it bounds a 40% warning
   gap at about 10% of equity, and 7.5% in this sample. A lower cap cuts the tail only by
   cutting return in proportion. If a single warning costing 7 to 10% of equity is more than
   you want to carry, the lever is `max_position`, not a gap cap, and it's your call.
