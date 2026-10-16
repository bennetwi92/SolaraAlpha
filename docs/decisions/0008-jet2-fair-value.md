# 0008 — Jet2 fair-value model: consensus-calibrated drivers, valued at the trade horizon

**Context.** We want a Jet2-only fair value that answers two questions for 2-16 week longs:
where should the price be at the end of the hold, and *so what* when news lands ("a deal with
X", "fuel +20%", "H1 yields up")? A long-term DCF answers neither: over weeks the price moves on
changes to next year's earnings, and the multiple barely moves. So the model is a driver P&L
that can translate news into earnings, anchored to what the market already expects.

**What the market prices (research, 16 Oct 2026).** Price 1,413p, 186m shares (≈179m net of
the EBT), ~£2.5bn market cap against £3.3bn cash (£2.1bn of it customer deposits) and £1.3bn
debt + leases. Consensus EPS (Yahoo, 14 analysts) is FY27 134p (FY26: 208p, −36%) and FY28 186p.
Brokers: Kepler FY27 EBIT £252m / FY28 £430m, Peel Hunt £240m / £482m. Targets 1,200-2,100p
(mean ~1,665p). The FY27 trough comes from price investment to fill aircraft in a late-booking
market, the Gatwick start-up (incl. three wet-leased aircraft), a 4% pay award, SAF / ETS
costs and lower interest. Fuel is 93% hedged at $753/t against a market of ~$1,600/t after the
Middle East conflict escalated in late February 2026. FY28 is the year the market is
debating: its fuel hedging is undisclosed, and the heating-oil curve puts Summer 2027 jet at
~$1,300/t. Peers on forward P/E: Ryanair 13.6x, easyJet 18x (depressed EPS), TUI 5.1x. Jet2's
own weekly trailing P/E has a 7.2x median over 2023-26 (Yahoo).

**Decision.** `solaraalpha.analysis.jet2_value` (Jet2-specific, not generic):

1. **Driver P&L** per fiscal year reproducing FY26 exactly. Volume drivers: seats, load factor,
   package mix. Revenue: package price per customer, flight-only yield, non-ticket per sector.
   Costs:
   - per customer: hotels, transfers;
   - per sector: airports, in-flight;
   - per seat: maintenance, staff, marketing, depreciation, other;
   - per tonne of fuel: SAF, carbon;
   - fuel itself: tonnes (seats × burn) × hedged and market $ prices.

   Interest is earned on average cash at Bank Rate. Fuel tonnes are undisclosed; ~1.15m t is
   derived from the FY26 fuel cost.
2. **Calibrated to consensus**: one selling-price factor per year (`yield_adj`) is solved so
   EPS = consensus at the reference date's fuel curve, FX and Bank Rate. The output reads as
   *what's priced in*. On 16 Oct 2026, consensus implies FY27 package prices −1% and FY28 +9%
   at the then fuel curve and our cost assumptions. FY28 has to win back more than its fuel
   step-up.
3. **Live update**: each run moves the base to live inputs:
   - the NYMEX heating-oil curve (Yahoo, one ticker per contract), converted to jet at 330
     gal/t + 4% premium and seat-weighted over the rest of FY27 and over FY28;
   - GBP/USD and GBP/EUR;
   - Bank Rate (CLI input).

   Market fuel and euro moves are passed through to prices at 10% (rest of FY27, largely
   sold) and 30% (FY28). Every operator faces them, while Jet2's own costs move only on
   unhedged volumes.
4. **Value at the horizon** = P/E × FY28 EPS + (FY27 EPS − consensus). FY27 is mostly flown and
   hedged, so its surprises are one-off cash, not a multiple. The P/E starts at today's price ÷
   consensus FY28 (7.6x) and reverts toward `FAIR_PE` (7.5x, Jet2's own recent history, not
   peers or older 12-14x history that brokers cite) with a 52-week half-life. A 12-week hold
   therefore expects ~15% of any re-rating. A stress floor of half of book value excluding the
   hedging reserve (~476p) stops loss years producing nonsense.
5. **News** is shock strings on drivers. A going-forward change hits FY28 in full and the rest
   of FY27 pro rata to seats still to fly. `fy27.` hits all of FY27 (H1 results reveal the
   summer that has already flown). `pe=` shifts the multiple. Named scenarios give typical
   sizes. The script also reports the *market reaction at today's multiple*, to compare with
   the actual move.
6. **Monte Carlo** of the fair value *as it will be known at the horizon*: one-year
   uncertainties are scaled by √(weeks/52), covering fuel, FX, FY28 yields, load factor,
   hotels, Bank Rate, pass-through and the P/E. The 12-week p10-p90 spans roughly ±33%,
   consistent with Jet2's ~40% realised volatility. P(fair value > price) and a score in
   [-1, 1] (±25% upside = ±1) go on the card.

**Consequences / limits.**
- Operating leverage is the story. Package and flight-only revenue is ~£8bn against ~£250m of
  EBIT, so 1% on FY28 yields is worth ~£77m of EBIT, ~34p of EPS and ~18% of fair value; 1pt
  of load factor ~£44m; hotels +1% ~−£39m. With 50% hedged and 30% pass-through, fuel is
  weaker: $100/t ~−£19m (+25% fuel ~−15% of value). A Gatwick expansion is roughly
  EPS-neutral in FY28 (start-up costs, lower early load factor).
- Assumptions to replace when disclosed:
  - FY28 hedge cover (placeholder 50% at $950/t; due at the H1 results on 18 Nov 2026);
  - Summer 2027 capacity (+5%);
  - unit-cost inflation (+3-6%);
  - the hedged FX rate.

  The consensus calibration absorbs level errors, but sensitivities depend on these.
- `CONSENSUS`/`REFERENCE` are hand-updated constants: Yahoo's estimates endpoint needs a crumb
  and is rate-limited (ADR 0005). Refresh both together after each results / trading update;
  a stale consensus makes the "live" moves double-count news analysts have already absorbed.
- Built for FY27/FY28 as of Oct 2026. After the FY27 results (Jul 2027) roll the years forward
  (FY28/FY29) and re-anchor on reported FY27.
- The jet-fuel proxy (heating oil + fixed premium) ignores crack-spread changes between jet and
  diesel. Pass-through rates, `FAIR_PE` and the Monte Carlo sigmas are judgement, not fitted.
  Backtest them against reactions to past trading updates before trusting the score.
