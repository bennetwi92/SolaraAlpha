# 0022 — What Solara Alpha is looking for

**Context.** The toolkits were designed one by one and are not aligned on one kind of
opportunity. Some clauses worked against the strategy: `risk_pct <= 0.15` screened out volatile
stocks (dropped, ADR 0014 amendment of 2026-10-18), and `reward_risk >= 2` assumes the price
stops at the next resistance. A 5-year replay (every 8th week, 50 stocks) gave 1 ACT in 1,228
stock-weeks. The owner set out the strategy in a Q&A on 2026-10-18. This ADR records it as the
reference that every toolkit, band and agent is checked against.

**The opportunity.**

> We are trading, not investing: we want liquid-enough AIM stocks (built or exited over a few
> days) that can deliver large percentage moves, typically +15–30%, within 1–10 weeks. A big
> move means a high return on very little capital, and capital that isn't moving goes to a
> reserve tracker rather than sitting as dead money. We want volatile but orderly stocks,
> either continuing an uptrend or breaking out to new highs. Volatility is never a reason to
> exclude a stock; position sizing handles the risk. **The edge is a catalyst**, recent or
> upcoming: news or a fundamental driver that points to growth and upside the market has not
> yet priced in, ideally backed by growing earnings and rising forecasts. Whether a catalyst
> qualifies, and whether it's already priced in, is the agents' judgement. The chart is
> context, not a gate. Compression, such as a narrowing range, volume drying up or a wedge,
> shows when a move may be close, but it's one input among many. The systematic screen
> therefore casts a wide net with few hard rules and passes the 5–10 best-ranked names a week
> to the agents. The agents decide whether the set-up is good, with market conditions as part
> of that call, and the trade plan gives a level to enter at or below. We hold 5–10 positions
> and exit when the stop is hit, the thesis breaks, the expected move is done, or momentum
> stalls.

**Decision.**
- This paragraph is the definition of the opportunity. A toolkit, band or agent instruction that
  works against it is changed, not the paragraph. Only the owner changes the paragraph.
- **The screen is a ranked wide net, not a strict gate.** Few hard rules. It sends the top 5–10
  names a week to the agents, ranked by a combined score.
- **A catalyst is essential**, and it is the agents' call: what counts, and whether it is priced
  in. A fresh catalyst should be able to bring a stock to the agents.
- **Technical patterns are context.** Compression, wedges and distance to highs inform the
  agents; they are not gates. Jet2's current wedge (October 2026) is the owner's test of how the
  agents read a forming set-up.
- **Volatility is never a gate**; sizing from the stop handles risk.
- **Fundamentals:** growth and rising forecasts.
- **Relative strength:** its role is open. In the replay, ACT weeks did worse than WATCH or PASS
  weeks. Treat it as context until that is investigated.
- **Entry and adding:** the plan gives an enter-at-or-below level. Adding to a winner happens
  only when an agent recommends it.
- **No outright exclusions** (the agents judge). A weak market is weighed by the agents per trade.

**Consequences.** The ACT/WATCH/PASS bands (ADR 0014) and the stage gating (ADR 0010) are
reviewed against this ADR in follow-up issues: the ranked shortlist, a catalyst route onto the
shortlist, levels as compression context, and the relative-strength investigation. Until those
land, the bands still run as coded.

**Amendment (2026-10-27, owner).** "Recent or upcoming" is applied in research as written: a
stock can be ACT on news from the last 30 days, on a dated catalyst inside the horizon, or on a
company-reported upgrade up to 60 days old that the price is confirming (breakout or near a
52-week high). The last two alone are low conviction (research confidence ≤ 0.6, PM 1% risk).
Before this, research let only the last 30 days argue for ACT and downgraded 12 of 16 validated
names in runs 9 and 14, nearly all for "no fresh catalyst". The shadow book measures whether
the extra small trades pay.

**Amendment (2026-10-28, owner).** Any one of the three alone is low conviction, *recent*
included (research confidence ≤ 0.6, PM 1% risk); 2-3% needs two of them (e.g. good results
the price is still confirming, or recent news with results due inside the horizon). Research
0007: AIM prices a big news day's move about 80% within 15 minutes of the open, with only a
small further drift (good results +2-3% in a week, little else), so by the weekly run recent
news alone is mostly priced. Recent news still counts as a catalyst for ACT and for the
shortlist's catalyst slots; only its sizing changes.
