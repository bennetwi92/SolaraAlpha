# Research 0016: Trend entries with a take-profit and a stop: pre-registration and result

*Pre-registered 2026-11-11, before any outcome of these rules was computed (script sha256 `5089376e191236c1001c9bb8fdeed1db9644dfddcbac27a6c52ffa653761668f` at freezing). Follows ADR 0029
(economics tier) and research 0015. Script: `scripts/research/r14_trend_target.py`. Nothing in
the pipeline is changed.*

## The owner's hypothesis

Research 0015 tested a weekly reshuffle. The owner meant something else (2026-11-11): buy a stock
in a strong trend, **hold it, and cash in once it is up by a set amount, however long that
takes; sell at a stop below entry if it falls instead.** The amount (5% in the chat) was an
example, not a choice, so several targets are tested against a no-target control.

Priors against it: costs on trend names are about 2–4% a round trip (research 0015's spread
proxy, which overstates on volatile stocks). A fixed target also cuts off the few long runners
that carried research 0015's only positive (pre-cost) number. And selling winners early is the
disposition effect (research 0010, B2). Priors for it: a high hit rate on small targets, with
losses capped by the stop.

## Pre-registration

*Written before any outcome of these rules was computed. Data as in research 0015 (the same
panel and floor). The holdout is not computed.*

- **Universe and floor:** research 0015's panel (`r13.parquet` and the daily bars): the AIM
  research universe, past the live £25k ADV floor on the signal Friday.
- **Signal:** `s26` from research 0015, the 26-week log return over σ₂₆ × √26. It is fixed in
  advance as the one lookback. Reason: 3–12-month momentum is the documented effect, 26 weeks
  sits inside it, and 4 and 13 weeks are short-term reversal horizons (Jegadeesh 1990; research
  0001). *Disclosure:* research 0015's results were seen first, and `s26` had the only positive
  IC there (not significant).
- **Entry:** each Friday from 2022-12-16 to 2025-08-08, fill the book's empty slots (8 in all)
  with the highest `s26 > 0` names not already held. Buy at the next session's open (adjusted),
  with 1/8 of the book's value at that Friday's close (capped by cash).
- **Stop:** entry − 3 × daily ATR(14) as of the signal Friday, on adjusted bars. It works as a
  stop-market order: a gap below it fills at the open.
- **Variants (4):** target T = +5%, +10%, +15% above entry, and **none** (the control). A target
  works as a limit sell: a gap above it fills at the open. If the stop and the target are both
  touched in one session, the stop is assumed first.
- **Time limit:** a position still open after 50 sessions (10 weeks, the strategy horizon) is sold
  at that close.
- **Costs:** each side costs half the max(60-session median, 60-session mean) Corwin-Schultz
  spread as of the signal Friday. The bar uses **2×**. 1× and ½× are reported (the proxy
  overstates on volatile names).
- **R:** the trade's return over its stop distance, (exit − entry) ÷ (entry − stop), net of
  costs in the same units.
- **Pass bar (ADR 0029, economics tier):** all four of the following:
  - expectancy above **+0.2R** after 2× costs;
  - a 97.5% moving-block bootstrap interval (16-week blocks of entry weeks; Bonferroni 4) above
    zero;
  - positive in at least 5 of the 6 half-years (by entry date);
  - still above +0.2R with the 3 names that added most R removed from the universe and the book
    re-run.
  The detection tier does not apply: these are exit rules on an entry signal research 0015
  already tested.
- **Reported, not the bar:** the book's annual return (2×, 1×, ½× costs), max drawdown and weekly
  excess over the equal-weight eligible universe; trades, the share ending at the target / stop /
  time limit, the average win and loss, and the days held; the break-even cost per side.
- **Biases stated in advance:** survivors only (research 0012). Fills at printed prices. The
  stop is assumed first in a session that touches both. Positions are sized at 1/8 of the book
  with no ADV or heat cap (a £625 slot is under 5% of £25k ADV).

## Result (2026-11-11): no variant passes; the trend entries have no edge even before costs

Run with `r14_trend_target.py run` on research 0015's panel. `count` (no outcomes) gave 13,103
candidate stock-weeks (`s26 > 0`, past £25k), a median of 95 a Friday. The names bought had an
estimated cost of 0.69% per side (1×): much cheaper than research 0015's 4-week picks.

### The answer in one paragraph

**Taking profit at +5%, +10% or +15% does not rescue trend entries on AIM, because the entries
themselves have no edge.** Before any costs, the average trade made +0.12% with a +5% target,
−0.14% and −0.26% with +10% and +15%, and +0.30% with no target. That is close to nothing. A
+5% target hits often (72% of trades), but the losers are four times the size of the winners
(+2.4% against −9.8% after costs): AIM gaps through stops, and a 3-ATR stop on a volatile stock
is about 10% away. After costs every variant loses money. Even at half the estimated cost, all
four lose 5–14% a year, while the equal-weight universe made +8.9% a year.

### The test: expectancy after 2× costs

| variant | trades | expectancy (R) | 97.5% interval | positive half-years | halves (R) | without the best 3 | pass |
|---|---|---|---|---|---|---|---|
| +5% target | 394 | −0.23 | −0.35 to −0.15 | 0 of 6 | −0.33 / −0.13 / −0.16 / −0.39 / −0.16 / −0.22 | −0.26 | no |
| +10% target | 269 | −0.25 | −0.45 to −0.14 | 1 of 6 | −0.38 / −0.06 / −0.09 / −0.51 / −0.19 / +0.04 | −0.28 | no |
| +15% target | 220 | −0.23 | −0.49 to −0.12 | 2 of 6 | −0.24 / +0.06 / −0.20 / −0.52 / −0.27 / +0.14 | −0.27 | no |
| no target | 138 | −0.23 | −0.60 to −0.10 | 1 of 6 | −0.31 / −0.20 / −0.06 / −0.58 / +0.06 / −0.23 | −0.32 | no |

### In money terms (reported, not the bar)

| variant | a year: 2× / 1× / ½× costs | max drawdown: 2× / ½× | exit at target / stop / 10 weeks | win rate (2×) | avg win / avg loss (2×) | days held (median) | gross return a trade |
|---|---|---|---|---|---|---|---|
| +5% | −47% / −27% / −14% | −84% / −41% | 72% / 25% / 3% | 51% | +2.4% / −9.8% | 6 | +0.12% |
| +10% | −37% / −22% / −13% | −77% / −49% | 53% / 36% / 10% | 52% | +6.1% / −14.3% | 12 | −0.14% |
| +15% | −33% / −20% / −13% | −74% / −52% | 41% / 41% / 18% | 47% | +9.5% / −15.9% | 17 | −0.26% |
| none | −19% / −10% / −5% | −62% / −44% | 0% / 41% / 59% | 37% | +14.4% / −13.3% | 50 | +0.30% |

The equal-weight eligible universe made +8.9% a year (max drawdown −19.8%) over the same span.

### Reading

- **The entry is the problem.** Buying AIM stocks with the steadiest 26-week climb gave about
  zero return a trade before costs, over 2023 to 2025. That agrees with research 0001 (`trend` IC
  −0.008) and 0015. Exits rearrange a zero; they cannot create an edge.
- **Small targets make the arithmetic worse.** They need a win rate far above 72% to cover
  losses of about 10%, which arrive as gaps through the stop.
- **The no-target control did least badly,** in line with "let winners run". It still lost.
- **Together with research 0015, this closes pure price trend on AIM for now**, as an entry on
  its own, in a weekly rotation or with a take-profit, on these years. The holdout was not
  touched. Not tested: trend combined with a situation (research 0010's route), e.g. good news
  near the high (research 0014 V3, now recorded forward only).
