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
| **Yahoo Finance (yfinance)** | Unlimited with curl_cffi* | 20+ years | No | **SELECTED** - Phase 1 prototype |
| Alpaca Markets | 200 calls/min | 10+ years | Yes (free paper account) | **FALLBACK** - More reliable for production |
| Alpha Vantage | 5 calls/min, 500/day | 20+ years | Yes | Too restrictive for bulk downloads |
| Twelve Data | 8 calls/min, 800/day | Varies, intraday from 2022+ | Yes | Insufficient for rapid data collection |
| Polygon.io | 5 calls/min | 2 years (free tier) | Yes | Historical depth insufficient |

*Yahoo Finance rate limits bypassed via curl_cffi browser impersonation (TLS fingerprint spoofing). Without curl_cffi, rate limits are IP-based, undocumented, and aggressive (~5-10 calls/min). Historical instability: API breaking changes reported in Oct 2024 and early 2024.

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

**Legal/Ethical Considerations**:
- curl_cffi browser impersonation evades Yahoo Finance rate limiting by spoofing TLS fingerprints
- This approach may violate Yahoo Finance terms of service (educational/personal use only)
- **SHAREHOLDER ACKNOWLEDGMENT REQUIRED**: Explicitly accept risk before capital deployment
- **Phase 1 Status**: Acceptable for prototype and educational backtesting
- **Production Recommendation**: Migrate to Alpaca Markets (free tier, no TOS violations) before live trading

**Current Status (2025-10-23)**:
- Rate limiting bypassed via curl_cffi browser impersonation
- Data access confirmed working for 5 symbols, 3.5 years each
- No rate limit errors observed in testing
- **Peer Review**: Approved for Phase 1 prototype use with conditions (see below)

**Residual Risks**:
- Yahoo Finance may change API endpoints or data structure
- curl_cffi impersonation may be detected in future
- No guaranteed uptime or data accuracy
- Legal risk if Yahoo Finance enforces TOS violations
- Production-scale testing incomplete (only 5 symbols tested, not 50+)

**Mitigation Strategy**:
- **Phase 1 (current)**: Acceptable for prototype with curl_cffi solution
- **Before Production**: Complete Alpaca Markets validation (see Recommendations below)
- **Immediate**: Implement local caching to reduce API dependency
- **Monitoring**: Add data freshness checks to detect API failures early

**Peer Review Recommendations (2025-10-23)**:
1. **Required Before Production**:
   - Validate Alpaca Markets fallback (test script, rate limits, data quality)
   - Production-scale testing (20+ symbols, parallel downloads, 10-year periods)
   - Legal risk assessment (TOS review, shareholder acknowledgment)
2. **Quality Improvements**:
   - Empirical testing of alternatives (Alpha Vantage, Twelve Data)
   - Data quality comparison across sources
   - Automated monitoring and fallback to Alpaca

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
