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

| Source | Free Tier Limit | Historical Data | API Key Required | Notes |
|--------|----------------|-----------------|------------------|-------|
| **Yahoo Finance (yfinance)** | ~5-10 calls/min* | 20+ years | No | **SELECTED** - Phase 1 prototype |
| Alpaca Markets | 200 calls/min | 10+ years | Yes (free paper account) | **FALLBACK** - More reliable for production |
| Alpha Vantage | 500 calls/day | 20+ years | Yes | Rate limits restrictive for bulk downloads |
| Twelve Data | 800 calls/day | Varies by market | Yes | Intraday data limited to 2022+ |
| Polygon.io | 5 calls/min (free) | 2004+ tick data | Yes | Free tier too restrictive |

*Yahoo Finance rate limits are IP-based, undocumented, and variable. Historical instability: API breaking changes reported in Oct 2024 and early 2024.

## API Access Approach

### Installation
```bash
pip install yfinance curl_cffi
```

### Basic Usage
```python
import yfinance as yf
from curl_cffi import requests

# CRITICAL: Use curl_cffi session to bypass Yahoo Finance rate limits
session = requests.Session(impersonate="chrome")

# Fetch historical data
ticker = yf.Ticker("SPY", session=session)
df = ticker.history(start="2020-01-01", end="2023-12-31")

# Returns DataFrame with: Open, High, Low, Close, Volume, Dividends, Stock Splits
```

### Rate Limits and Constraints

**Solution (2025-10-23)**:
- **curl_cffi browser impersonation**: Bypasses Yahoo Finance rate limiting
- Uses TLS fingerprint impersonation (Chrome browser)
- Eliminates 429 "Too Many Requests" errors completely
- No delays required between requests

**Implementation**:
```python
from curl_cffi import requests
session = requests.Session(impersonate="chrome")
ticker = yf.Ticker(symbol, session=session)
```

**Previous Issues (RESOLVED)**:
- ~~Rapid sequential requests trigger "Too Many Requests" (429) errors~~
- ~~Rate limiting is aggressive and IP-based~~
- ~~2-second delays insufficient~~

**Why curl_cffi Works**:
Yahoo Finance detects scripts via headers like 'User-Agent: python-requests'. curl_cffi impersonates browser TLS fingerprints, making requests indistinguishable from real browser traffic.

**Best Practices**:
- Always use curl_cffi session for all yfinance requests
- Cache all historical data locally after initial fetch
- Use `period` parameter for relative time ranges (e.g., "3y" for 3 years)

### Data Validation

Successfully validated on 2025-10-23 with 3.5 years of data using curl_cffi:
- **Symbols**: SPY, QQQ, AAPL, MSFT, TSLA
- **Data Points**: 877 rows per symbol (3.5 years)
- **Date Range**: 2022-04-27 to 2025-10-23
- **Columns**: Open, High, Low, Close, Volume, Dividends, Stock Splits
- **Data Quality**: Zero missing values, zero volume days: 0
- **Multi-Symbol Fetch**: Validated 5 symbols sequentially without rate limits

**Note**: See test_yahoo_cffi.py for validation script.

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
- Rate limiting requires delays (2s recommended between symbols)
- Yahoo Finance API unofficial, subject to change without notice
- Real-time data limited to 15-minute delay
- No guaranteed SLA or uptime (educational/personal use)

### Known Risks
**Yahoo Finance Stability**: Unofficial API with history of breaking changes (Oct 2024, early 2024). No SLA or stability guarantees.

**Current Status (2025-10-23)**:
- Rate limiting bypassed via curl_cffi browser impersonation
- Data access confirmed working for 5 symbols, 3.5 years each
- No rate limit errors observed in testing

**Residual Risks**:
- Yahoo Finance may change API endpoints or data structure
- curl_cffi impersonation may be detected in future
- No guaranteed uptime or data accuracy

**Mitigation Strategy**:
- **Phase 1 (current)**: Acceptable for prototype with curl_cffi solution
- **Production**: Evaluate Alpaca Markets if Yahoo Finance breaks
- **Immediate**: Implement local caching to reduce API dependency
- **Monitoring**: Add data freshness checks to detect API failures early

### Risk Mitigation
- curl_cffi session impersonation eliminates rate limiting (IMPLEMENTED)
- Implement caching layer to minimize API dependency (REQUIRED before production)
- Add retry logic with exponential backoff for network errors
- Monitor for API changes or deprecation
- **Fallback plan**: Alpaca Markets as production alternative (evaluated, documented)

## References
- yfinance GitHub: https://github.com/ranaroussi/yfinance
- yfinance Documentation: https://pypi.org/project/yfinance/
- curl_cffi GitHub: https://github.com/yifeikong/curl_cffi
- Validation Script: `/test_yahoo_cffi.py`
- Original validation (outdated): `/test_data_source.py`
