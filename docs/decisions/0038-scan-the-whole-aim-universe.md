# 0038 — The weekly scan runs the whole tradeable AIM universe

Date: 2026-10-29. Status: accepted (owner).

## Context

On 2026-10-28 the owner took research down to the whole of AIM (#288), and #290 built
`config/research_universe.toml`: every AIM company Trading212 offers in GBX (576 on the
2026-10-28 snapshot). #290 kept the live watchlist (FTSE AIM 100 + AIM UK 50, 104 stocks)
unchanged "until a signal passes" (research 0010).

## Decision

The owner, 2026-10-29: the weekly scan runs **the watchlist plus every other research-universe
stock** (`pipeline.universe()`, about 578) from the next Sunday run. The watchlist stays as the
FTSE-index list (the AIM 100 control, the stale-snapshot warning); the research universe is
refreshed by hand with `scripts/research_universe.py --write`.

Nothing else in the screen changes: the sanity floor (`min_adv_gbp` £25k), the rank, the
shortlist size (12 + 3 catalysts) and `max_agent_tickers` are as before, so the agents' load and
cost do not grow. The scan's fetches grow about 5.5x (`scan.yml` timeout 120 → 240 minutes);
the daily job fetches prices for more WATCH stocks.

**Unknown sector.** `config/benchmarks.toml` has a `sector` for the original 104 only; the new
stocks are `unknown`. `unknown` is not one sector, so the fit check skips the sector cap for it
and flags the plan `sector unknown: sector cap not checked`, and the portfolio breaches skip it.
Slots, heat, cash and the position cap still apply. Relative strength falls back to the market
index (the `.L` default) with no industry or peers.

**A dry run of the whole universe** from the cloud container (2026-10-29, nothing saved) took 13
minutes and found two things fixed here:

- **Thin history.** 114 of the new stocks trade so rarely that `trim_to_consistent` cuts them to
  a few weeks after their last stale close (> 20 days unchanged); every toolkit raised on them.
  A stock with fewer than `pipeline.MIN_WEEKS` (26) clean weekly bars now skips the toolkits and
  is PASS "too little clean history", not an ERROR in the desk-errors issue every week.
- **The FCA NSM refused us (403)** after about a dozen stocks of unpaced searches, and for some
  minutes after. NSM searches are now at most one a second (`announcements.NSM_GAP`), about 13
  minutes more per scan.

## Consequences

- The rank is no more validated on micro caps than on the AIM 100 (`fit.RANK_NOTE`).
- Most micro caps fail the £25k liquidity floor and PASS; those above it compete for the 12 slots.
- The research universe is a survivor snapshot and has no `sector`; adding sectors by hand is
  a follow-up if the sector cap matters for the new names.
