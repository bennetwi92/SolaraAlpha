# Research 0015: Pure trend following with weekly rotation: pre-registration and result

*Pre-registered 2026-11-11, before any forward return was computed (script sha256 `345f305234c18426f2b9e17200eb0430c79b93891c34dfc7d30c9a42bd9afde3` at freezing). Follows ADR 0029, with the
target changed at the owner's request (below). Script: `scripts/research/r13_trend_rotation.py`.
Nothing in the pipeline is changed.*

## The owner's hypothesis

"If a stock has been going up with a good trend for the last n weeks, I'd expect it to continue
for the next week at least. So I can rotate quickly, capturing small gains regularly." (owner,
2026-11-11)

This differs from the screen's set-up (bursts of +15–30% over 1–10 weeks). It is a **one-week
continuation** claim, traded as a **weekly rotation**: hold the strongest trends, sell them as
soon as others are stronger. So the target here is the next week's return, not `burst_10w`
(ADR 0029's target). `burst_10w` is not tested.

The prior evidence is mixed. Over 3–12 months, momentum is one of the most robust effects in
equities, the UK included (Jegadeesh & Titman 1993; Asness, Moskowitz & Pedersen 2013). At one
week to one month, single stocks tend to **reverse** (Jegadeesh 1990; Lehmann 1990), most of all
small, illiquid ones. Research 0001 found the same on AIM: the stock's own past 4-week return
had an IC of −0.044 against the next 8 weeks. Weekly rotation also pays the spread often, and AIM
spreads are wide.

## Pre-registration

*Written before any forward return was computed. The holdout (signal Fridays 2025-10-24 to
2026-10-16) is not computed.*

- **Universe:** `config/research_universe.toml` (every AIM company Trading212 offers). These are
  today's survivors. Daily Yahoo bars from 2021-10-20 go through `check_prices`, `clean_prices`
  and `trim_to_consistent`, then weekly bars (`to_weekly`). A stock-week counts only if that
  Friday's 20-session median traded value is at least the live `min_adv_gbp` (£25k): what the
  book could trade.
- **Signal Fridays:** 2022-12-16 to 2025-10-03. The last week's exit, Monday 2025-10-13, falls
  before the in-sample end (2025-10-17).
- **Timing:** the signal uses closes up to Friday *t*. You buy at Monday's open after *t* and
  sell (or keep) at the next Monday's open. **Target `y`** = adjusted open of the first session
  after Friday *t+1* ÷ adjusted open of the first session after Friday *t* − 1.
- **Signal, three variants (n = 4, 13, 26 weeks):** `s_n = ln(C_t / C_{t−n}) / (σ₂₆ × √n)`.
  `C` is the Friday adjusted close. `σ₂₆` is the standard deviation of the last 26 weekly log
  returns. This scores a *good* trend: a steady climb ranks above one with the same gain on wild
  swings. Bonferroni over 3 variants, so all intervals are 96.7%.
- **Test 1 (detection):** the mean weekly Spearman IC of `s_n` against `y`, with a 96.7%
  moving-block bootstrap (16-week blocks). **Pass:** lower bound > 0; positive in at least 5 of
  the 6 half-years; and with the 3 names that contribute most removed, the IC is still positive
  with its interval above zero.
- **Test 2 (economics: the strategy itself):** each Friday, take the top 8 names by `s_n` (the
  book's 8 positions), only those with `s_n > 0`, at 1/8 of the book each. Empty slots hold cash
  at 0%. Names still in the top 8 are kept with no trade. A name is bought or sold at Monday's
  open, and each side costs its spread: **2× costs** in ADR 0029's terms, i.e. the full
  max(60-session median, 60-session mean) Corwin-Schultz spread per side, as of Friday *t*.
  1× is reported too. Weights reset to equal each week. **Excess** = the book's weekly return
  after costs minus the equal-weight return of every eligible stock that week, gross (holding
  everything trades almost never). **Pass:** mean weekly excess > 0 with its 96.7% block
  bootstrap interval above zero; positive in at least 5 of the 6 half-years; and still above zero
  with the 3 names that added most removed from the universe and the rotation re-run.
- **A variant passes only if both tests pass.** Reported alongside, not part of the bar:
  - the annualised return after costs, maximum drawdown, weeks held per position, names traded
    a week and the hit rate (share of held weeks that rose);
  - the FTSE AIM All-Share (`^FTAI`) over the same weeks;
  - the owner's literal question: how often the next week is up for the top 10% by `s_n`, for
    stocks up in each of their last 4 weeks, and for everyone.
- **Half-years:** H1 2023 (from 2022-12-16), H2 2023, H1 2024, H2 2024, H1 2025, and
  H2 2025 to 2025-10-03.
- **Biases stated in advance:** survivors only (research 0012: about one company in three that
  was on AIM in 2022 has since left, mostly takeovers and failures). Monday-open fills at the
  printed open. One trade a week. A £625 slot is under 5% of £25k ADV, so the 5% ADV cap never
  binds.
- **If it passes:** the holdout gate (the same sign, at least half the size) runs only after the
  owner signs off. If it fails, the note says so and nothing changes.
