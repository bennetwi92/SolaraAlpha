# 0011 — Relative strength vs market, industry and peers

**Context.** The strategy is long, bursty 2-16 week trades. Stocks that are already beating
their market and their sector tend to keep doing so over months (Jegadeesh & Titman 1993;
industry momentum, Moskowitz & Grinblatt 1999), and a stock leading its own peers points to a
stock-specific reason rather than a sector tide. The technical model (0003) only looks at the
stock's own price, so it can't tell "everything rose" from "this rose".

**Decision.** `solaraalpha.analysis.relative_strength`, toolkit `relative_strength` in the
`screen` stage (passes `rs > 0`, placeholder).

*What to compare against* — `config/benchmarks.toml`, hand-curated, three kinds:

| Kind | Rule |
|---|---|
| market | Broad index of the home market, by listing suffix: `.L` → FTSE All-Share (`^FTAS`). Overridable per stock. AIM stocks use it too: it is the UK opportunity set the fund chooses from, and Jet2 is a large weight in the AIM index, which would make it partly a comparison with itself |
| industry | The sector index if Yahoo has its history; else the closest sector ETF. Optional |
| peers | Listed companies in the same business, any exchange, 4-8. Optional |

Curated rather than discovered: Yahoo's "similar symbols" endpoint is unofficial and noisy,
sector classification needs the crumb-protected `quoteSummary`, and a peer list is a judgement
worth seeing in a diff. A stock with no entry gets the market comparison only.

Jet2: FTSE 350 Travel & Leisure (`UB6010.FGI`) has only the latest quote on Yahoo, so industry
is the iShares STOXX Europe 600 Travel & Leisure ETF (`EXV9.DE`). Peers: easyJet, Ryanair,
Wizz Air, IAG, TUI, On the Beach — short-haul airlines and package-holiday sellers.

*How to compare* — the same way for every kind:
- Weekly total returns (`adj_close`), each comparison converted to the stock's currency at
  weekly Yahoo FX closes (`EURGBP=X`); pence vs pounds needs nothing (returns).
- Peers as an equal-weight basket, rebalanced weekly; a member joins when its data starts.
  Also the stock's 13-week return rank among its peers (reported, not scored).
- Excess log return over 4, 13 and 26 weeks, divided by the tracking error expected over that
  span (52-week std of weekly excess × √h): an information-ratio t-stat, clipped to [-1, +1].
  Vol-scaling makes it comparable across stocks with very different volatility (generic, no
  per-stock tuning), as the technical model's 12-1 momentum. Score = mean over horizons;
  `rs` = mean over available kinds, equal weights.
- `rs_high`: RS line vs the market at its 52-week high — a classic leadership flag (O'Neil),
  reported, not scored.

*Data quality* — comparison series go through `check_prices` / `clean_prices` /
`trim_to_consistent`, but with `CLOSE_ONLY_BLOCKING` (non-positive price, gap, stale): indices
and FX carry zero or missing volume and Yahoo returns occasional all-empty days, which are
irrelevant to close-to-close returns but would otherwise trim the whole history. The stock
itself still uses the full `BLOCKING` set.

**Consequences / open points.**
- Horizons, weights and the `rs > 0` threshold are placeholders until checked against
  forward 2-16 week returns. The 4-week leg may want dropping: single-stock 1-month returns
  tend to reverse (Jegadeesh 1990), though industry 1-month returns don't.
- Yahoo indices are price indices, the stock is total return: a small bias in the stock's
  favour (~1% a quarter at UK yields, ~0.1-0.2 of a t-stat).
- Peer lists are today's, so a backtest has survivorship in the peers (delisted rivals
  missing). Fine for the weekly scorecard; note it before backtesting.
- One big week entering or leaving a window moves the score sharply (Jet2 in Oct 2026: a +20%
  week left the 13-week window); the 26-week leg damps it.
- Comparison prices are fetched once per process and shared across stocks; `--save`
  snapshots them alongside the stock's.
