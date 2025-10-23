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
| **Yahoo Finance (yfinance)** | No hard limit* | 20+ years | No | **SELECTED** |
| Alpha Vantage | 500 calls/day | 20+ years | Yes | Rate limits restrictive for bulk downloads |
| Twelve Data | 800 calls/day | Varies by market | Yes | Intraday data limited to 2022+ |
| Polygon.io | 5 calls/min (free) | 2004+ tick data | Yes | Free tier too restrictive |

*Yahoo Finance enforces rate limiting but manageable with 2-second delays between requests.

## API Access Approach

### Installation
```bash
pip install yfinance
```

### Basic Usage
```python
import yfinance as yf
from datetime import datetime, timedelta

# Fetch historical data
ticker = yf.Ticker("SPY")
df = ticker.history(start="2020-01-01", end="2023-12-31")

# Returns DataFrame with: Open, High, Low, Close, Volume, Dividends, Stock Splits
```

### Rate Limits and Constraints

**Observed Behavior**:
- No documented rate limits from Yahoo Finance
- Rate limiting is aggressive and IP-based
- **CRITICAL**: 2-second delays sometimes insufficient (rate limit window varies)
- Initial data ingestion requires patience or distributed approach

**Known Issues**:
- Rapid sequential requests trigger "Too Many Requests" (429) errors
- Rate limit recovery time: variable (minutes to hours)
- Affects initial historical data download phase

**Mitigation Strategies**:
1. **Delays**: Start with 2s between requests, increase to 5-10s if rate limited
2. **Batch Processing**: Download 5-10 symbols, pause 10-15 minutes, continue
3. **Caching**: Once fetched, store locally to eliminate future API dependency
4. **Retry Logic**: Implement exponential backoff for 429 errors
5. **Off-Peak Hours**: Consider running bulk downloads during market off-hours

**Best Practices**:
- Cache all historical data locally after initial fetch
- For production: consider pre-fetching data in advance of backtests
- Use `period` parameter for relative time ranges (e.g., "3y" for 3 years)
- Monitor for 429 errors and implement automatic rate limit handling

### Data Validation

Successfully validated on 2025-10-23 with 3+ years of data:
- **Symbols**: SPY, QQQ, AAPL, MSFT, GOOGL
- **Data Points**: 773 rows per symbol (~3.07 years)
- **Date Range**: 2022-09-26 to 2025-10-23
- **Columns**: Open, High, Low, Close, Volume, Dividends, Stock Splits

**Note**: Validation requires waiting between test runs due to rate limiting. See test_data_source.py for validation script.

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

### Risk Mitigation
- Implement caching layer to minimize API dependency
- Add retry logic with exponential backoff
- Monitor for API changes or deprecation
- Consider fallback sources for critical production deployment

## References
- yfinance GitHub: https://github.com/ranaroussi/yfinance
- yfinance Documentation: https://pypi.org/project/yfinance/
- Validation Script: `/test_data_source.py`
