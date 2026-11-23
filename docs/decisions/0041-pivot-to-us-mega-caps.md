# 0041 — Pivot to US mega caps (S&P 100)

**Status:** proposed (owner, 2026-11-23: universe chosen, strategy to be redefined). Follows
ADR 0040 (AIM wind-down). **Amended 2026-11-23 (owner, ADR 0043): the universe is the Nasdaq
100, not the S&P 100; the strategy is ADR 0043 (open question 1 answered); the ISA holds USD
cash (question 3 answered). Questions 4–6 answered the same day (ADR 0043): a GBP benchmark, the
owner's EDGAR contact kept out of the repo, a fresh book with no AIM data. Question 2 (the
agents' role) is still open.** The rest of this ADR applies to the Nasdaq 100 as written.

**Context.** The owner (2026-11-23) moves the fund from AIM small caps to "standard US major
stocks": the **S&P 100**. The owner also said the strategy "will change significantly", so ADR
0022's bursty catalyst trade does not carry over. A large cap rarely makes +15–30% in ten
weeks anyway. This ADR fixes what is decided, what carries over and what has to change. The new
strategy paragraph, which replaces ADR 0022, is the owner's to write.

**Decided.**

- **Universe: the S&P 100**, about 100 names, 101 lines with GOOGL and GOOG. It is a hand
  snapshot like the old watchlist, with a `snapshot` date, refreshed by hand after each S&P index
  change and never in CI. Not point-in-time: the research has to bound the survivorship bias.
- **Broker: Trading212, the same Stocks & Shares ISA.** Long only, no margin. It offers every S&P
  100 name, in USD, with fractional shares. There is no commission and no US stamp duty, but
  every GBP↔USD conversion costs a 0.15% FX fee each way. US dividends are taxed 15% at source
  with a W-8BEN, which is not reclaimable in an ISA.
- **Price data: Yahoo, as now** (`data/prices.py` works for US tickers). The check, clean and
  trim steps, the weekly bars and the no-look-ahead rules all stay.
- **New entries stay off** (`new_entries = false`, ADR 0040) until the strategy below is written,
  the US pipeline is built and the owner turns the switch on.

**Open: the owner's choices** (nothing below is built until they are answered).

1. **The strategy paragraph**, which replaces ADR 0022: what is the trade, over what horizon,
   and what target? Is it long-only trend following, mean reversion, earnings drift, or factor
   tilts? Is it still weekly? Are positions still stop-sized at 1–3% risk?
2. **The role of the agents.** With 100 heavily covered names, is the catalyst judgement still
   the edge, or do the agents become a check on a systematic signal?
3. **Account currency and FX:** does the book stay in GBP with USD positions converted, or does
   it hold USD cash so the 0.15% fee is paid once, not on every trade?
4. **Benchmark:** `^OEX` (S&P 100), `^GSPC` (S&P 500), or in GBP terms?
5. **SEC EDGAR contact string:** EDGAR needs a User-Agent with a contact. Which name and email
   should go in `.env`? We never put the owner's details in a request on our own.
6. **Fresh book:** a new results DB (Turso is blocking reads on its current plan), or the same DB
   with the AIM book closed off?

**What carries over.** The weekly pipeline shape (ADR 0010, 0014), the toolkit register and
bands-as-information, the results DB and the append-only book (0007, 0013), one order per unit
of stock and the execution door with its guards (0031), the shadow book (0028), the research
protocol with a new holdout (0029), the risk card and its limits (0036), weekly decisions (0039),
the desk thread, the inbox and desk errors (0021, 0032, 0033). The generic technical, levels and
relative-strength models carry over too, but must be re-tested on US data.

**What has to change** (one issue each once the strategy is set):

- **Universe and config:** `config/watchlist.toml` becomes the S&P 100. Drop the research
  universe, `issuers.toml` (LEIs), `sectors.toml` (ICB) and `aim_leavers.csv`. Use GICS sectors
  in `benchmarks.toml` and sector SPDR ETFs (XLK, XLF, …) as industry benchmarks.
- **Filings and events:** SEC EDGAR (free; 8-K, 10-Q / 10-K, Form 4) replaces the FCA NSM. EDGAR
  gives the real filing date, so `known()` can use it instead of reporting lags, and EDGAR's
  XBRL company facts give point-in-time fundamentals. EDGAR has its own fair-access limit of 10
  requests a second. Register it in `docs/data-sources.md`.
- **Currency:** the book, sizing, risk and P&L are GBP/GBX today (`Rules.currency` is GBP only).
  USD prices need an FX rate in sizing, heat, VaR and the equity curve, and the FX fee in costs.
- **Market hours:** NYSE runs 09:30–16:00 New York, which is 14:30–21:00 London (an hour earlier
  in the weeks when US and UK clocks differ). `execute.yml`, `daily.yml` and the London calendar
  in `execution.py`, including the 15:45 exit deadline, move to the NYSE session and holidays.
- **Rules:** `min_adv_gbp`, `max_adv_pct` and the liquidity floor hardly matter for mega caps.
  `benchmark` and the regime index change from `^FTAI`. The position, sector and heat caps are
  re-set for 100 correlated large caps.
- **AIM-only code removed:** NSM pacing, `official_list.py`, `leavers.py`, `analysis/universe.py`,
  `jet2_value.py` and its skill, and the research 0005–0016 scripts.
- **Agents:** the personas and skills describe a "small-cap high-vol desk" and AIM policy, and
  every skill and subagent has to be rewritten for the new strategy.
- **CI:** retire `nsm-test.yml`.

**Consequences.** Until the open questions are answered, the fund holds cash and the AIM jobs
run with buys off (ADR 0040). The first US deliverable is the strategy paragraph, then the
universe snapshot and the price-only pipeline, then EDGAR, then currency.
