# 0002 — Daily stock prices from Yahoo's chart endpoint, resampled to weekly locally

**Context.** We need free historical prices for single stocks, including AIM names like Jet2.
Stooq was unreachable from the dev environment; most other free APIs are keyed with tight
daily limits or poor LSE/AIM coverage.

**Decision.** Call Yahoo's `v8/finance/chart` endpoint directly with `requests` (no `yfinance`
dependency), fetch *daily* bars and build weekly bars ourselves with `to_weekly` (W-FRI).
Building weekly bars ourselves makes the period boundary and the "only complete weeks" rule
explicit, and keeps daily data available for quality checks. No caching for now.

History: fetch the last 5 years, then `trim_to_consistent` drops everything up to the last
*blocking* issue (missing/non-positive/stale/gaps/duplicates; relaxed in 0012), so the start date is chosen per stock
from its own data. Large moves and close-outside-high/low are reported but don't cut.

**Consequences.** The endpoint is unofficial and may change or block us; terms are personal
use only. Parsing is isolated in `parse_chart` so swapping source is a small change.
