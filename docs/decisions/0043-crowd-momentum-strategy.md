# 0043 — What Solara Alpha is looking for: crowd momentum in the Nasdaq 100

**Status:** accepted (owner, 2026-11-23). Replaces ADR 0022 as the reference every toolkit, band
and agent is checked against. Answers ADR 0041's open question 1 (the strategy) and question 3
(USD cash), and changes its universe from the S&P 100 to the Nasdaq 100. Nothing is built yet;
`new_entries` stays false until the US pipeline is.

**Context.** The AIM strategy (ADR 0022) produced no successful trades. Its edge was the agents
judging a catalyst the market had not priced in yet, in thinly traded stocks where spreads cost
2–5% a week (research 0015). The owner wants the opposite stance: go with the crowd, in liquid
stocks that have a story people are excited about, managed every day. The owner set it out on
2026-11-23.

**The opportunity.**

> We trade momentum with the crowd. We buy Nasdaq 100 stocks with a live narrative the market
> is excited about — a product cycle, results that beat and raise guidance, a theme, an analyst
> or index event, a wave of attention — and only when strong technicals confirm the money is
> flowing in: an uptrend, new highs or a breakout on heavy volume, leading the market and its
> sector. The crowd is our friend: confirmation is the signal, not a reason to stay away. We
> hold for days to a few weeks (about 3–15 sessions) and manage every position daily. There is
> no fixed profit target: each trade's stop, partial and targets come from that stock's own
> structure — support and resistance, ATR, the move in R. We take a partial into strength and
> let the rest run on a trailing stop while the story and the price both keep going. We exit
> fast when either breaks: the stop, a failed breakout, the narrative fading or the volume
> drying up. Up to 10 positions, about 2% of equity at risk each.

**Decision.**

- This paragraph is the definition of the opportunity. A toolkit, band or agent instruction that
  works against it is changed, not the paragraph. Only the owner changes the paragraph.
- **Universe: the Nasdaq 100** (hand snapshot with a `snapshot` date, refreshed after each index
  change, never in CI; not point-in-time, so research bounds the survivorship bias).
  Benchmarks: `^NDX` / QQQ for the market, sector ETFs for the industry.
- **Narrative and technicals both required.** The narrative is the agents' call: whether it is
  live, whether the crowd is still arriving or already leaving. The technicals are measured:
  trend, breakout / new high, volume against its norm, relative strength against QQQ and the
  sector. Unlike ADR 0022, the chart is not just context. A stock with a story and no
  confirmation is WATCH, not ACT.
- **Crowd measures (free data only):** price and volume (gaps, relative volume, highs), the
  earnings reaction, headline counts, SEC filings (8-K, Form 4), and page-view / search
  attention once their terms are checked. The price and volume part is tested on daily bars
  before it trades (ADR 0029 protocol, a new holdout); the narrative part stays agent judgement.
- **Holding period: about 3–15 sessions**, held longer only while price and story keep going.
- **No fixed target.** Per trade, research sets the entry, the stop where the set-up is wrong,
  the level for the partial and any targets, from levels, ATR and R. `target_moves` (+15–30%)
  goes when the US config is written.
- **Partials, then let it run:** a partial into strength at the trade's own level, the rest on a
  trailing stop. The monitor decides daily.
- **Book: at most 10 positions, about 2% of equity at risk per trade** (stop-sized).
- **USD cash:** the ISA holds USD so the 0.15% FX fee is paid once on conversion, not on every
  trade (a round trip each fortnight would cost about 8% a year).
- **Daily management** as in ADR 0040: every position reviewed every weekday.

**Answered (owner, 2026-11-23).**

- **Risk band:** the PM keeps its 1–3% band by conviction around the 2% target (ADR 0027).
- **Partial:** half the position by default, when research gives no other size.
- **Caps raised** for 10 positions in one correlated index. Proposed for the US config, to confirm
  when it is written: `max_heat` 10% → 20% (10 × 2%), `max_sector_pct` 40% → 60% (the Nasdaq 100
  is mostly technology); `max_position_pct` stays 25%.
- **Benchmark in GBP:** the Nasdaq 100 (`^NDX`) converted to GBP, so the book is judged in the
  currency it is owned in.
- **EDGAR contact:** supplied by the owner. It is kept out of the repo (the repo is public): it
  goes in `.env` and a GitHub secret, read through `settings`, never committed.
- **Fresh book, no AIM data whatsoever:** the US desk starts on a new, empty results DB. Nothing
  is migrated from the AIM book, runs, cards or chat; the AIM-only code, config and research
  data are removed (ADR 0041's list), not kept beside the US pipeline.

**The agents' role** (ADR 0041 question 2) is ADR 0044: technicals lead, the agents check what
we buy into and set the trade levels, and an agent can bring in a stock on its story.

**Consequences.** Fixed rules in `config/portfolio.toml` (`max_positions`, `target_moves`,
`horizon_weeks`, `currency`, heat and sector caps) change with the US config, not now: the file
still holds the AIM rules (buys off, every workflow paused). The weekly-frequency research (0001–0016) does not
carry over. Attention-driven moves are known to give part of the gain back once the attention
fades, so quick exits and daily review are part of the strategy, not an extra. Momentum wins
roughly half the time and makes its money on the size of the winners, so stopped-out losers
are expected even when it works. The shadow book (ADR 0028) gets new baselines for this trade.
