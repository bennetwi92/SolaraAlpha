# Data Source: Yahoo Finance via yfinance

## Selected Source
**Yahoo Finance** accessed through the `yfinance` Python library.

## Rationale

### Requirements Met
- **Cost**: Completely free, no API key required, no hidden costs
- **Historical Depth**: 20+ years of data available, validated 3+ years for backtesting
- **Symbol Coverage**: Comprehensive coverage of US equities including SPY, QQQ, and major stocks
- **OHLCV Data**: All required columns (Open, High, Low, Close, Volume) plus Dividends and Stock Splits
- **Data Quality**: Industry-standard data from Yahoo Finance, widely used in trading/research
- **Reliability**: Mature library (ranaroussi/yfinance), actively maintained

### Comparison to Alternatives

**Research Conducted**: 2025-10-23

| Source | Free Tier Limit | Historical Data | API Key Required | Notes |
|--------|----------------|-----------------|------------------|-------|
| **Yahoo Finance (yfinance)** | Rate limited (1s delays recommended) | 20+ years | No | **SELECTED** - Works with v0.2.66+ |
| Alpaca Markets | 200 calls/min (Basic tier) | 7+ years | Yes (free paper account) | **FALLBACK** - 15-min delay for free historical |
| Alpha Vantage | 5 calls/min, 25/day | 20+ years | Yes | Too restrictive for bulk downloads |
| Twelve Data | 8 calls/min, 800/day | Varies, intraday from 2022+ | Yes | Insufficient for rapid data collection |
| Polygon.io | 5 calls/min | 2 years (free tier) | Yes | Historical depth insufficient |

*Yahoo Finance validated 2025-10-23 with yfinance 0.2.66. Previous rate limit issues resolved in recent versions. Use 1-second delays between ticker requests for stability.

**Alpha Vantage 2025 update: Free tier reduced to 25 calls/day (down from 500/day previously).

**Evaluation Criteria**:
- **Cost**: Must be free (no paid subscriptions)
- **Historical Depth**: 3+ years required for backtesting
- **Symbol Coverage**: 5+ liquid symbols (SPY, QQQ, major stocks)
- **Data Quality**: Complete OHLCV with minimal gaps
- **Rate Limits**: Sufficient for initial bulk download
- **Reliability**: Stable API with active maintenance

## API Access Approach

### Installation
```bash
# Required - use version 0.2.66 or later
pip install yfinance>=0.2.66
```

### Basic Usage (yfinance 0.2.66+)
```python
import yfinance as yf
import time

# Standard usage with yfinance 0.2.66+
ticker = yf.Ticker("SPY")
df = ticker.history(start="2020-01-01", end="2023-12-31", auto_adjust=True)

# Returns DataFrame with: Open, High, Low, Close, Volume, Dividends, Stock Splits
# Add 1s delay between ticker requests for stability
time.sleep(1)
```

### Rate Limits and Constraints

**Current Status (2025-10-23 FINAL RE-VALIDATION)**:
- **yfinance 0.2.66+ works reliably** - Rate limit issues resolved in recent versions
- Successfully validated with yfinance 0.2.66 (upgraded from 0.2.54)
- **curl_cffi NOT required** with latest yfinance version
- Successfully validated: 753 days per symbol across 5 symbols (SPY, QQQ, AAPL, MSFT, GOOGL)

**Best Practices**:
- Use yfinance 0.2.66 or later to avoid rate limit issues
- Add 1-second delays between sequential ticker requests for stability
- Cache all historical data locally after initial fetch
- Use `period` parameter for relative time ranges (e.g., "3y" for 3 years)
- Monitor for API changes (Yahoo Finance has history of breaking changes)

**Current Implementation (yfinance 0.2.66+)**:
```python
import yfinance as yf
import time

ticker = yf.Ticker(symbol)
hist = ticker.history(start=start_date, end=end_date, auto_adjust=True)
time.sleep(1)  # Rate limit protection between tickers
```

**Rate Limiting Details**:
- Rate limiting is IP-based and undocumented
- yfinance 0.2.66+ includes improvements to handle rate limits gracefully
- 1-second delay between ticker requests recommended for stability
- Issue #2340 (premium subscription requirement) was resolved - caused by ad-blockers, not paywalls

### Data Validation

**Latest Validation (2025-10-23 FINAL)**: yfinance 0.2.66 works reliably without curl_cffi
- **Version Tested**: yfinance 0.2.66 (upgraded from 0.2.54)
- **Symbols**: SPY, QQQ, AAPL, MSFT, GOOGL
- **Data Points**: 753 days per symbol (3 years)
- **Date Range**: 2022-10-24 to 2025-10-23
- **Columns**: Open, High, Low, Close, Volume (plus Dividends, Stock Splits, Capital Gains for ETFs)
- **Data Quality**: Zero missing values across all symbols
- **Rate Limits**: No errors with 1-second delays between tickers
- **Multi-Symbol Fetch**: Successfully validated 5 symbols sequentially

## Configuration

**No API credentials required** - yfinance accesses Yahoo Finance's public API.

### Environment Variables
None required for basic usage. Optional configuration:

```bash
# Optional: Configure yfinance cache directory
export YFINANCE_CACHE_DIR="/path/to/cache"
```

### Configuration File
No configuration file needed. Rate limiting handled at application level via delays.

## Implementation Notes

### Advantages
- Zero configuration required (no API keys, no registration)
- No daily/monthly call limits to track
- Extensive historical data coverage
- Includes corporate actions (splits, dividends)
- Widely adopted library with strong community support

### Limitations
- Rate limiting requires delays (1s recommended between symbols)
- Yahoo Finance API unofficial, subject to change without notice
- Real-time data limited to 15-minute delay
- No guaranteed SLA or uptime (educational/personal use)

### Known Risks

**Yahoo Finance Stability**: Unofficial API with history of breaking changes (Oct 2024, early 2024). No SLA or stability guarantees.

**Current Status (2025-10-23 FINAL)**:
- yfinance 0.2.66+ works reliably without special workarounds
- Data access confirmed for 5 symbols, 3 years each (753 days per symbol)
- No rate limit errors with 1-second delays between requests
- Suitable for Phase 1 prototype and backtesting

**Residual Risks**:
- Yahoo Finance may change API endpoints or data structure without notice
- No guaranteed uptime or data accuracy (educational/personal use only)
- Rate limiting behavior may change (historically inconsistent)
- Production-scale testing incomplete (only 5 symbols tested)

**Mitigation Strategy**:
- Use yfinance 0.2.66+ with 1-second delays between ticker requests
- Implement local caching to reduce API dependency
- Monitor for API changes or failures
- Consider Alpaca Markets for production (free tier available with API key)

### Risk Mitigation
- Use yfinance 0.2.66+ for improved rate limit handling
- Add 1-second delays between ticker requests
- Implement caching layer to minimize API dependency
- Add retry logic with exponential backoff for network errors
- Monitor for API changes or deprecation
- **Fallback option**: Alpaca Markets (free tier, API key required)

## 2025-10-23 Final Validation Results

**Test Date**: 2025-10-23
**yfinance Version**: 0.2.66 (upgraded from 0.2.54)
**Test Method**: Standard yfinance (no special workarounds)
**Test Code**:
```python
import yfinance as yf
from datetime import datetime, timedelta
import time

symbols = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'GOOGL']
end_date = datetime.now()
start_date = end_date - timedelta(days=365*3)

for symbol in symbols:
    ticker = yf.Ticker(symbol)
    hist = ticker.history(start=start_date, end=end_date, auto_adjust=True)
    print(f'{symbol}: {len(hist)} days')
    time.sleep(1)  # Rate limit protection
```

**Results**:
- **All 5 symbols**: Successfully retrieved 753 days each (2022-10-24 to 2025-10-23)
- **Rate Limits**: No errors with 1-second delays between requests
- **Columns**: Open, High, Low, Close, Volume (plus Dividends, Stock Splits, Capital Gains for ETFs)
- **Data Quality**: Zero missing values

**Finding**: yfinance 0.2.66 works reliably without curl_cffi or browser impersonation. Previous rate limit issues resolved in recent versions.

**Key Insight**: Issue #2340 (reported premium subscription requirement) was caused by ad-blocking software, not Yahoo Finance paywalls. Upgrading to 0.2.66+ resolves rate limit handling.

**Recommendation**: Use yfinance 0.2.66+ with 1-second delays for Phase 1 prototype. Simple, reliable, no legal/TOS concerns.

## References
- yfinance GitHub: https://github.com/ranaroussi/yfinance
- yfinance Documentation: https://pypi.org/project/yfinance/
- Issue #2340 (Premium subscription): https://github.com/ranaroussi/yfinance/issues/2340
- Alpaca Markets (fallback option): https://alpaca.markets/data
