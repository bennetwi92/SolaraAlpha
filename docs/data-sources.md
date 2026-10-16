# Data sources

Every external data source used by the project. **Free sources only.**

| Source | What it provides | Access (key? library?) | Rate limits / terms | Module |
|---|---|---|---|---|
| Yahoo Finance chart API (`query1.finance.yahoo.com/v8/finance/chart`) | Daily OHLCV + adjusted close for listed stocks (e.g. `JET2.L`) | No key. Plain HTTP via `requests`; needs a browser-like User-Agent (otherwise 429) | **Unofficial, undocumented endpoint.** Yahoo terms: personal, non-commercial use only — revisit before any commercial use. No published rate limit; keep calls light. LSE history before ~2006 is padded filler (see `check_prices`). Close can sit outside high/low on older LSE bars | `solaraalpha.data.prices` |
