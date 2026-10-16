# 0005 — Fundamentals model: growth / value / quality for 2-16 week longs

**Context.** The strategy looks for long trades with bursty profits over roughly 2-16 weeks
(longer if the stock keeps climbing). Over that horizon fundamentals matter mainly through
*news about earnings*: results that beat, estimates that get revised, growth that speeds up
(post-earnings-announcement drift, estimate-revision momentum). Valuation is a weaker,
slower force, useful as re-rating fuel. Balance-sheet quality is a risk filter that helps avoid a
trade blowing up on a profit warning or a refinancing. The technical (0003) and levels (0004)
models time the trade; this model says whether the business backs it.

**Data.** Yahoo's `fundamentals-timeseries` endpoint (no key, no cookie/crumb): fiscal-year
statements, TTM diluted EPS and share count at each reporting period end (half-years for UK
names), and dated observations of trailing P/E, forward P/E and market cap. Yahoo's
`quoteSummary` (analyst estimates, earnings dates, recommendation trends) needs a
cookie + crumb and was rate-limited (429) from the dev environment, so it isn't used. Analyst
expectations are instead implied: forward EPS growth = trailing P/E ÷ forward P/E − 1, and
forward earnings = market cap ÷ forward P/E, whose change is the estimate revision.

**Decision.** `solaraalpha.analysis.fundamentals` scores three pillars in [-1, +1]
(+ = favourable for a long). Each is the mean of the sub-signals that are *available*, because
fundamental data is patchy; a pillar is NaN only when none are (this differs from the technical
model, which needs every input).

| Pillar | Sub-signals (each clipped to [-1, 1]) |
|---|---|
| Growth | TTM EPS YoY (±20% = ±1); acceleration = that growth minus the previous period's (±20 pts = ±1); fiscal-year revenue YoY (±20%); implied forward EPS growth (±20%); forward-earnings revision over the last 4-26 weeks (±10%) |
| Value | earnings yield and FCF yield (5% = 0, i.e. P/E 20; 10% = +1; ≤0 = −1); today's earnings yield percentile in its own weekly history (cheapest = +1), needs 52 weeks |
| Quality | ROE (15% = +1, 0% = −1); net debt / EBITDA (net cash = +1, 3× = −1); FCF / net income (1 = +1, 0 = −1); diluted share count YoY (5% buyback = +1, 5% dilution = −1) |

Market observations older than 13 weeks are ignored (Yahoo samples them only a few times a
year, so forward growth / revisions are often NaN). Valuation converts the price from its
minor unit (GBp → GBP) and is skipped (NaN) if the listing and reporting currencies differ —
no FX conversion yet. `next_period` is the expected end of the next reporting period, with
results typically 1-3 months later. Treat a results date inside the holding window as both a
catalyst and gap risk.

**No look-ahead.** Statement figures are dated at the period end but published later. For a
past `as_of`, a figure is used only from period end + 6 months for fiscal years (AIM rule 19;
Main Market is 4 months) and + 3 months for interims (AIM rule 18 / DTR 4.2). These are the
statutory deadlines, so most companies report sooner and the lag is conservative. Market
observations are known on their date. The live card (no `as_of`) uses everything fetched,
since it has all been published by now. The own-history earnings-yield percentile always uses
lagged EPS. Tested by changing every not-yet-public value and checking the card is unchanged.

**Consequences / open points.**
- The scoring is generic, but fundamentals are stock-specific in use. Example: Jet2's cash
  includes customer deposits for future holidays, so its net cash (and quality score) is
  flattered. Per-stock adjustments get added when a stock needs one, not speculatively.
- Thresholds are round-number conventions, not fitted. Backtest before relying on the weights.
- Sector-relative valuation, short interest and directors' dealings aren't included (no
  reliable free source yet). Earnings dates and surprise vs consensus would be the most
  valuable additions for this horizon if a free source turns up.
