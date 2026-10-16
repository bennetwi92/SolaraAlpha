# Data sources

Every external data source used by the project. **Free sources only.**

| Source | What it provides | Access (key? library?) | Rate limits / terms | Module |
|---|---|---|---|---|
| Yahoo Finance chart API (`query1.finance.yahoo.com/v8/finance/chart`) | Daily OHLCV + adjusted close for listed stocks (e.g. `JET2.L`) | No key. Plain HTTP via `requests`; needs a browser-like User-Agent (otherwise 429) | **Unofficial, undocumented endpoint.** Yahoo terms: personal, non-commercial use only — revisit before any commercial use. No published rate limit; keep calls light. LSE history before ~2006 is padded filler (see `check_prices`). Close can sit outside high/low on older LSE bars | `solaraalpha.data.prices` |
| Yahoo Finance fundamentals-timeseries API (`query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries`) | Fiscal-year statements (revenue, EBITDA, net income, EPS, FCF, debt, cash, equity, shares), TTM EPS / share count per reporting period, dated trailing P/E, forward P/E and market cap | No key, no cookie/crumb. Plain HTTP via `requests` with a browser-like User-Agent | **Unofficial, undocumented endpoint**; same Yahoo terms (personal, non-commercial). ~4 fiscal years of statements; market ratios sampled only a few times a year. Dates are period ends, not publication dates (see ADR 0005). `quoteSummary` (estimates, earnings dates) needs a crumb and was rate-limited, so not used | `solaraalpha.data.fundamentals` |
