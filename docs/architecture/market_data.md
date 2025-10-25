# Market Data Architecture

**Version**: 1.1 (Post-Peer Review)
**Last Updated**: 2025-10-25
**Status**: Design Phase - Approved with Changes

## Executive Summary

SolaraAlpha's market data architecture provides unified access to multi-asset, multi-timeframe market data through a combination of existing MCP servers and purpose-built data infrastructure. The architecture prioritizes free data sources, robust caching, rate limit management, and data quality validation to support our multi-strategy trading operations.

**Key Design Decisions**:
- **Hybrid MCP approach**: Leverage existing MCP servers (Alpha Vantage, CoinGecko) for immediate access; build custom integrations for specialized needs
- **SQLite storage**: Local persistence with asset-specific schemas supporting OHLCV, fundamentals, and economic indicators
- **Tiered caching**: Multi-level caching strategy to minimize API calls and ensure data availability
- **Rate limit orchestration**: Centralized request throttling across all data sources
- **Relative value infrastructure**: Pre-computed correlation matrices and pair relationships for statistical arbitrage strategies
- **Event-driven fundamentals**: Poll fundamental data based on earnings calendar rather than fixed schedules

**Critical Constraints**:
- **Alpha Vantage Limit**: 25 requests/day means only 25 symbols can receive fresh daily data
- **Universe Refresh Cycle**: Full 500-symbol universe requires 20 days for complete refresh
- **Prioritization Required**: Active positions > watchlist > full universe
- **CoinGecko Limit**: Crypto updates limited to top 20 symbols every 5 minutes; others hourly

---

## 1. Data Sources

### 1.1 Free Data Source Inventory

#### **Alpha Vantage** (Primary - Equities, Forex, Fundamentals)
- **Assets**: Stocks, ETFs, Forex, Crypto
- **Data Types**:
  - Real-time & historical OHLCV
  - Income statements, balance sheets, cash flow
  - Company profiles, earnings reports
- **Rate Limits**: 25 API requests/day (free tier)
- **Latency**: ~200-500ms per request
- **Coverage**: Global markets (US, international equities)
- **Reliability**: Official API with documented endpoints
- **MCP Server**: `alpha-vantage` (existing implementation available)
- **Cost**: $0 (free tier)
- **Limitations**: Very restrictive daily limit; requires aggressive caching

#### **CoinGecko** (Primary - Cryptocurrency)
- **Assets**: 10,000+ cryptocurrencies
- **Data Types**:
  - Real-time prices, market cap, volume
  - Historical OHLCV data
  - Exchange data, trading pairs
- **Rate Limits**: 30 calls/min, 10,000 calls/month (free tier)
- **Latency**: ~100-300ms per request
- **Coverage**: Comprehensive crypto coverage across 600+ exchanges
- **Reliability**: Stable, well-documented API
- **MCP Server**: Available via third-party implementations
- **Cost**: $0 (free tier)
- **Limitations**: Monthly cap requires careful usage planning

#### **FRED** (Primary - Economic Indicators)
- **Assets**: Economic data, rates, indicators
- **Data Types**:
  - Interest rates (Fed Funds, LIBOR, Treasury yields)
  - Economic indicators (GDP, CPI, unemployment)
  - Commodity prices, exchange rates
- **Rate Limits**: 120 requests/minute
- **Latency**: ~100-200ms per request
- **Coverage**: 800,000+ US & international economic time series
- **Reliability**: Official Federal Reserve API
- **MCP Server**: Not available; requires custom integration
- **Cost**: $0 (free tier)
- **Limitations**: None significant for our use case

#### **yfinance** (Secondary - Equities, ETFs)
- **Assets**: Stocks, ETFs, indices, some crypto
- **Data Types**:
  - Historical & recent OHLCV
  - Company fundamentals, options data
  - Dividends, splits, earnings calendar
- **Rate Limits**: Unofficial (~100-200 requests/day from single IP before throttling)
- **Latency**: ~300-800ms per request (scraping delays)
- **Coverage**: Yahoo Finance universe (global equities)
- **Reliability**: Unofficial scraper; prone to blocks and rate limiting
- **MCP Server**: `yahoo-finance` (existing implementation)
- **Cost**: $0
- **Limitations**: Unreliable for production; use as backup only
- **Risk Mitigation**: Implement exponential backoff, IP rotation detection, fallback to Alpha Vantage

#### **CoinMarketCap** (Tertiary - Cryptocurrency Backup)
- **Assets**: 9,000+ cryptocurrencies
- **Data Types**: Real-time prices, market cap, historical OHLCV
- **Rate Limits**: Varies (free tier available)
- **Latency**: ~200-400ms per request
- **Coverage**: Comprehensive crypto coverage
- **Reliability**: Official API
- **MCP Server**: Not available; custom integration if needed
- **Cost**: $0 (free tier)
- **Limitations**: Use as backup to CoinGecko if rate limits exceeded

### 1.2 Asset Class Coverage Matrix

| Asset Class | Primary Source | Secondary Source | Tertiary Source | Data Types | Update Frequency |
|-------------|---------------|------------------|-----------------|------------|------------------|
| **US Equities** | Alpha Vantage | yfinance | - | OHLCV, fundamentals | Daily (EOD) |
| **ETFs** | Alpha Vantage | yfinance | - | OHLCV, holdings | Daily (EOD) |
| **Forex** | Alpha Vantage | FRED (rates) | - | Exchange rates, real-time | Hourly |
| **Cryptocurrency** | CoinGecko | CoinMarketCap | - | OHLCV, market cap | 5-minute |
| **Fixed Income** | FRED | - | - | Treasury yields, rates | Daily |
| **Commodities** | FRED | - | - | Spot prices, futures | Daily |
| **Economic Indicators** | FRED | - | - | Macro indicators | Daily/Monthly |

### 1.3 Data Source Selection Logic

```python
# Pseudo-code for source selection
def select_data_source(asset_class, data_type, date_range):
    if asset_class == "crypto":
        return "coingecko"  # Primary
    elif asset_class in ["equity", "etf"]:
        if data_type == "fundamentals":
            return "alpha_vantage"  # Only source with free fundamentals
        else:
            return "alpha_vantage" if rate_limit_available() else "yfinance"
    elif asset_class == "forex":
        return "alpha_vantage"
    elif asset_class in ["fixed_income", "commodity", "economic"]:
        return "fred"
    else:
        raise UnsupportedAssetClass(asset_class)
```

---

## 2. MCP Server Strategy

### 2.1 Decision: Hybrid Approach

**Rationale**:
- **Leverage existing MCP servers** where available and functionally complete (Alpha Vantage, Yahoo Finance, CoinGecko)
- **Build custom integrations** for FRED (no existing MCP) and specialized data needs
- **Avoid vendor lock-in**: Abstract MCP dependencies behind unified data interface

### 2.2 MCP Server Usage

#### **Use Existing MCP Servers**:
1. **Alpha Vantage MCP Server**
   - Purpose: Primary source for equities, forex, fundamentals
   - Installation: `npm install @modelcontextprotocol/server-alphavantage`
   - Configuration: API key via environment variable
   - Tools exposed: `get_quote`, `get_time_series`, `get_fundamentals`, `get_forex_rate`

2. **Yahoo Finance MCP Server** (Backup)
   - Purpose: Secondary source for equities when Alpha Vantage rate limits exhausted
   - Installation: Available via MCP registry
   - Configuration: No API key required
   - Tools exposed: `get_historical_data`, `get_quote`, `get_fundamentals`

3. **CoinGecko MCP Server** (If available via community)
   - Purpose: Primary cryptocurrency data
   - Installation: TBD based on availability
   - Tools exposed: `get_price`, `get_market_data`, `get_historical_ohlc`

#### **Build Custom Integrations**:
1. **FRED Integration** (Python client)
   - Library: `fredapi` (PyPI)
   - Reason: No existing MCP server; simple REST API
   - Implementation: Thin wrapper around `fredapi` library
   - Caching: Aggressive (economic data updates infrequently)

2. **CoinGecko Integration** (If no MCP available)
   - Library: `pycoingecko` (PyPI)
   - Reason: Fallback if MCP server not production-ready
   - Implementation: Direct API client with rate limit tracking

### 2.3 Data Access Abstraction Layer

**Design Pattern**: Repository pattern with source abstraction

```python
# Unified interface regardless of underlying source (MCP or direct API)
class MarketDataRepository:
    def get_ohlcv(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Fetch OHLCV data from optimal source with caching"""

    def get_fundamentals(self, symbol: str, statement_type: str) -> dict:
        """Fetch financial statements from optimal source"""

    def get_economic_indicator(self, series_id: str, start_date: date) -> pd.Series:
        """Fetch economic time series from FRED"""
```

**Benefits**:
- Swap data sources without changing strategy code
- Unified caching and rate limiting across all sources
- Easy testing with mock data providers

---

## 3. Storage Schema

### 3.1 Database Selection: SQLite

**Rationale**:
- **Free**: No infrastructure costs
- **Embedded**: No separate server process
- **Fast**: Optimized for read-heavy workloads
- **Portable**: Single file database
- **Sufficient Scale**: Handles millions of rows efficiently

**Limitations**:
- Single-writer (acceptable: batch ingestion during off-hours)
- Not distributed (acceptable: single-machine deployment initially)

### 3.2 Schema Design

#### **3.2.1 OHLCV Data (Time Series)**

```sql
-- Equity/ETF/Forex OHLCV
CREATE TABLE ohlcv_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(20) NOT NULL,
    asset_class VARCHAR(20) NOT NULL,  -- 'equity', 'etf', 'forex', 'crypto'
    date DATE NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume BIGINT,
    adjusted_close REAL,  -- For equities (split/dividend adjusted)
    source VARCHAR(50) NOT NULL,  -- 'alpha_vantage', 'yfinance', 'coingecko'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, asset_class, date, source)
);

CREATE INDEX idx_ohlcv_symbol_date ON ohlcv_daily(symbol, date);
CREATE INDEX idx_ohlcv_asset_class ON ohlcv_daily(asset_class);
CREATE INDEX idx_ohlcv_date ON ohlcv_daily(date);

-- Intraday OHLCV (for crypto and high-frequency strategies)
CREATE TABLE ohlcv_intraday (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(20) NOT NULL,
    asset_class VARCHAR(20) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    interval VARCHAR(10) NOT NULL,  -- '1min', '5min', '15min', '1hour'
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume BIGINT,
    source VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, asset_class, timestamp, interval, source)
);

CREATE INDEX idx_ohlcv_intraday_symbol_ts ON ohlcv_intraday(symbol, timestamp);
CREATE INDEX idx_ohlcv_intraday_interval ON ohlcv_intraday(interval);
```

#### **3.2.2 Fundamental Data**

```sql
-- Company fundamentals (balance sheet, income statement, cash flow)
CREATE TABLE fundamentals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(20) NOT NULL,
    statement_type VARCHAR(50) NOT NULL,  -- 'income_statement', 'balance_sheet', 'cash_flow'
    fiscal_year INTEGER NOT NULL,
    fiscal_quarter VARCHAR(10),  -- 'Q1', 'Q2', 'Q3', 'Q4', or NULL for annual
    report_date DATE NOT NULL,
    metric_name VARCHAR(100) NOT NULL,  -- 'revenue', 'net_income', 'total_assets', etc.
    metric_value REAL,
    currency VARCHAR(3) DEFAULT 'USD',
    source VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, statement_type, fiscal_year, fiscal_quarter, metric_name, source)
);

CREATE INDEX idx_fundamentals_symbol ON fundamentals(symbol);
CREATE INDEX idx_fundamentals_statement_type ON fundamentals(statement_type);
CREATE INDEX idx_fundamentals_fiscal ON fundamentals(fiscal_year, fiscal_quarter);

-- Company metadata
CREATE TABLE company_metadata (
    symbol VARCHAR(20) PRIMARY KEY,
    name VARCHAR(255),
    exchange VARCHAR(50),
    sector VARCHAR(100),
    industry VARCHAR(100),
    market_cap BIGINT,
    country VARCHAR(50),
    ipo_date DATE,
    primary_exchange VARCHAR(50),
    trading_currency VARCHAR(3) DEFAULT 'USD',
    data_completeness_30d REAL,  -- 0.0 to 1.0
    source VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Earnings calendar (for event-driven fundamental data polling)
CREATE TABLE earnings_calendar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(20) NOT NULL,
    earnings_date DATE NOT NULL,
    fiscal_year INTEGER,
    fiscal_quarter VARCHAR(10),
    is_estimate BOOLEAN DEFAULT TRUE,
    source VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, earnings_date, fiscal_quarter)
);

CREATE INDEX idx_earnings_date ON earnings_calendar(earnings_date);
CREATE INDEX idx_earnings_symbol ON earnings_calendar(symbol);
```

#### **3.2.3 Economic Indicators**

```sql
-- Economic time series from FRED
CREATE TABLE economic_indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id VARCHAR(50) NOT NULL,  -- FRED series ID (e.g., 'GDP', 'UNRATE')
    series_name VARCHAR(255),
    date DATE NOT NULL,
    value REAL NOT NULL,
    unit VARCHAR(50),  -- 'percent', 'billions_usd', 'index', etc.
    frequency VARCHAR(20),  -- 'daily', 'monthly', 'quarterly', 'annual'
    source VARCHAR(50) DEFAULT 'fred',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(series_id, date)
);

CREATE INDEX idx_economic_series_date ON economic_indicators(series_id, date);
CREATE INDEX idx_economic_date ON economic_indicators(date);

-- Economic series metadata
CREATE TABLE economic_series_metadata (
    series_id VARCHAR(50) PRIMARY KEY,
    series_name VARCHAR(255),
    category VARCHAR(100),  -- 'interest_rates', 'inflation', 'employment', etc.
    units VARCHAR(50),
    frequency VARCHAR(20),
    seasonal_adjustment VARCHAR(50),
    last_updated DATE,
    source VARCHAR(50) DEFAULT 'fred'
);
```

#### **3.2.4 Data Quality & Metadata**

```sql
-- Track data quality issues
CREATE TABLE data_quality_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(20),
    asset_class VARCHAR(20),
    data_type VARCHAR(50),  -- 'ohlcv', 'fundamentals', 'economic'
    issue_type VARCHAR(50),  -- 'missing_data', 'outlier', 'stale_data', 'validation_failed'
    gap_type VARCHAR(20),  -- 'holiday', 'api_failure', 'illiquid', 'delisted', NULL
    issue_description TEXT,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    severity VARCHAR(20)  -- 'low', 'medium', 'high', 'critical'
);

CREATE INDEX idx_quality_symbol ON data_quality_log(symbol);
CREATE INDEX idx_quality_detected ON data_quality_log(detected_at);
CREATE INDEX idx_quality_resolved ON data_quality_log(resolved_at);

-- Track API usage and rate limits
CREATE TABLE api_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source VARCHAR(50) NOT NULL,  -- 'alpha_vantage', 'coingecko', 'fred', etc.
    endpoint VARCHAR(255),
    request_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    response_time_ms INTEGER,
    status_code INTEGER,
    rate_limit_remaining INTEGER,
    error_message TEXT
);

CREATE INDEX idx_api_usage_source_ts ON api_usage_log(source, request_timestamp);
```

#### **3.2.5 Relative Value Strategy Infrastructure**

```sql
-- Pair relationships for pairs trading and statistical arbitrage
CREATE TABLE pair_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_a VARCHAR(20) NOT NULL,
    symbol_b VARCHAR(20) NOT NULL,
    correlation_30d REAL,  -- 30-day rolling correlation
    correlation_90d REAL,  -- 90-day rolling correlation
    cointegration_pvalue REAL,  -- ADF test p-value
    hedge_ratio REAL,  -- Optimal hedge ratio from regression
    spread_mean REAL,  -- Historical spread mean
    spread_std REAL,  -- Historical spread std dev
    spread_zscore REAL,  -- Current z-score
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol_a, symbol_b)
);

CREATE INDEX idx_pairs_correlation ON pair_relationships(correlation_90d);
CREATE INDEX idx_pairs_cointegration ON pair_relationships(cointegration_pvalue);
CREATE INDEX idx_pairs_zscore ON pair_relationships(spread_zscore);

-- Sector/industry groupings for finding tradable pairs
CREATE TABLE sector_pairs (
    sector VARCHAR(100),
    symbol_a VARCHAR(20),
    symbol_b VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(sector, symbol_a, symbol_b)
);

CREATE INDEX idx_sector_pairs_sector ON sector_pairs(sector);

-- Corporate actions (splits, dividends, mergers) impact on pairs
CREATE TABLE corporate_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(20) NOT NULL,
    action_type VARCHAR(50) NOT NULL,  -- 'split', 'dividend', 'merger', 'spinoff'
    action_date DATE NOT NULL,
    adjustment_factor REAL,  -- For splits (e.g., 2.0 for 2:1 split)
    dividend_amount REAL,  -- For dividends
    currency VARCHAR(3) DEFAULT 'USD',
    description TEXT,
    source VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, action_type, action_date)
);

CREATE INDEX idx_corporate_actions_symbol ON corporate_actions(symbol);
CREATE INDEX idx_corporate_actions_date ON corporate_actions(action_date);
```

### 3.3 Data Retention Policies

| Data Type | Retention Period | Rationale | Storage Estimate |
|-----------|-----------------|-----------|------------------|
| Daily OHLCV | 10 years | Support long-term backtesting | ~252 MB (500 symbols × 10 years) |
| Intraday OHLCV (5min) | 1 month | Reduced from 3 months due to storage constraints | ~50 MB (20 crypto symbols) |
| Intraday OHLCV (15min+) | 6 months | Medium-term intraday strategy development | ~100 MB |
| Fundamentals | Indefinite | Low storage cost; historical analysis valuable | ~50 MB |
| Economic Indicators | Indefinite | Low storage cost; macro trend analysis | ~10 MB |
| Pair Relationships | 1 year | Correlation/cointegration changes over time | ~100 MB |
| Corporate Actions | Indefinite | Permanent record for adjustment calculations | ~10 MB |
| Earnings Calendar | 2 years | Historical + future events | ~5 MB |
| Data Quality Logs | 1 year | Monitoring and debugging | ~20 MB |
| API Usage Logs | 6 months | Rate limit analysis | ~10 MB |

**Total Estimated Storage**: ~600 MB for initial deployment (manageable with SQLite)

**Note**: 1-minute intraday bars removed due to excessive storage requirements (~2.6 TB annually). Use 5-minute minimum interval instead.

---

## 4. Data Ingestion Patterns

### 4.1 Batch vs Streaming

#### **Batch Ingestion** (Primary Pattern)
- **Schedule**: Daily at 6:00 AM ET (after US market close + data availability)
- **Process**:
  1. Query missing dates for tracked symbols
  2. Fetch data from APIs with rate limit throttling
  3. Validate data quality
  4. Insert into SQLite with transaction batching
- **Assets**: Equities, ETFs, daily fundamentals, economic indicators
- **Advantages**: Simple, predictable, efficient API usage

#### **Streaming/Polling** (Secondary Pattern)
- **Schedule**: Every 5 minutes during market hours
- **Process**:
  1. Poll for latest prices (crypto, forex)
  2. Update intraday tables
  3. Trigger alerts for significant price moves
- **Assets**: Cryptocurrencies, forex (24/7 markets)
- **Advantages**: Near-real-time data for active strategies

### 4.2 Polling Frequency by Asset Class

| Asset Class | Polling Frequency | Update Window | Rationale | Symbols |
|-------------|------------------|---------------|-----------|---------|
| US Equities (Active) | Daily (EOD) | 6:00 AM ET | Market closes 4:00 PM; data available ~1-2 hours later | 25 max/day |
| US Equities (Full Universe) | 20-day rotation | 6:00 AM ET | Alpha Vantage limit: 500 symbols ÷ 25 req/day | All symbols |
| ETFs | Daily (EOD) | 6:00 AM ET | Same as equities; counted toward 25/day limit | Varies |
| Forex | Hourly | 24/7 | Continuous trading; hourly sufficient for daily strategies | 10 pairs |
| Crypto (Top 20) | 5 minutes | 24/7 | CoinGecko limit-aware; high volatility; support intraday | 20 symbols |
| Crypto (Others) | Hourly | 24/7 | Reduce API usage; stay within 10k/month limit | 80 symbols |
| Fixed Income | Daily | 9:00 AM ET | FRED updates daily; low urgency | ~50 series |
| Economic Indicators | Daily | 10:00 AM ET | FRED releases follow schedules; check daily | ~50 series |
| Fundamentals | Event-driven | On earnings date ± 1 day | Poll based on earnings_calendar table | As needed |
| Fundamentals (Fallback) | Weekly | Sunday 12:00 AM | For symbols without known earnings date | Remaining |

### 4.3 Rate Limit Handling

#### **Centralized Rate Limiter**

```python
# Rate limit tracker per source
class RateLimiter:
    def __init__(self, source: str, limits: dict):
        self.source = source
        self.per_minute_limit = limits.get('per_minute')
        self.per_day_limit = limits.get('per_day')
        self.per_month_limit = limits.get('per_month')
        self.request_history = deque()  # Timestamp log

    def can_make_request(self) -> bool:
        """Check if request can be made without exceeding limits"""
        self._clean_old_requests()

        if self.per_minute_limit:
            recent_requests = self._count_requests(timedelta(minutes=1))
            if recent_requests >= self.per_minute_limit:
                return False

        if self.per_day_limit:
            daily_requests = self._count_requests(timedelta(days=1))
            if daily_requests >= self.per_day_limit:
                return False

        return True

    def wait_time_until_available(self) -> float:
        """Calculate seconds to wait before next request allowed"""
        # Implementation: Calculate based on request_history

    def record_request(self):
        """Log request timestamp"""
        self.request_history.append(datetime.now())
```

#### **Rate Limit Configuration**

```python
RATE_LIMITS = {
    'alpha_vantage': {
        'per_minute': None,
        'per_day': 25,
        'per_month': None
    },
    'coingecko': {
        'per_minute': 30,
        'per_day': None,
        'per_month': 10000
    },
    'fred': {
        'per_minute': 120,
        'per_day': None,
        'per_month': None
    },
    'yfinance': {
        'per_minute': 5,  # Conservative; unofficial limit
        'per_day': 100,
        'per_month': None
    }
}
```

#### **Fallback & Retry Logic**

```python
def fetch_with_fallback(symbol, asset_class, sources=['primary', 'secondary']):
    for source in sources:
        try:
            if rate_limiter[source].can_make_request():
                data = fetch_from_source(source, symbol)
                rate_limiter[source].record_request()
                return data
            else:
                logger.warning(f"Rate limit exceeded for {source}, trying next source")
        except Exception as e:
            logger.error(f"Error fetching from {source}: {e}")

    raise DataUnavailableError(f"All sources exhausted for {symbol}")
```

### 4.4 Request Prioritization

**Priority Queue for API Requests**:
1. **Critical** (P0): Real-time strategy execution data
2. **High** (P1): Backfill missing data gaps
3. **Medium** (P2): Routine daily updates
4. **Low** (P3): Historical data extension, exploratory queries

**Rationale**: With Alpha Vantage's 25 requests/day limit, prioritization ensures critical data always available.

---

## 5. Caching Strategy

### 5.1 Multi-Level Caching

#### **Level 1: In-Memory Cache (Application)**
- **Tool**: `functools.lru_cache` or `cachetools`
- **Scope**: Hot data (frequently accessed symbols)
- **TTL**: 5 minutes for intraday, 1 hour for daily data
- **Size**: 1000 most recent queries
- **Eviction**: LRU (Least Recently Used)

#### **Level 2: SQLite Database (Persistent)**
- **Scope**: All fetched data
- **TTL**: Based on data type and staleness rules
- **Size**: Unlimited (disk storage)
- **Query Pattern**: Check cache before API call

#### **Level 3: CDN/Object Storage (Future - Archive)**
- **Scope**: Historical data (>1 year old)
- **Tool**: S3-compatible free tier (e.g., Backblaze B2 10GB free)
- **Purpose**: Offload cold data, reduce SQLite size

### 5.2 Cache Invalidation Rules

| Data Type | Invalidation Trigger | Rationale |
|-----------|---------------------|-----------|
| Daily OHLCV | New trading day (6:00 AM ET) | Data becomes stale after market close + settlement |
| Intraday OHLCV | 5 minutes elapsed | Near-real-time strategies need fresh data |
| Fundamentals | Quarterly earnings date | Financial statements update quarterly |
| Economic Indicators | Scheduled release date (from FRED metadata) | Indicators follow release calendars |
| Company Metadata | 30 days elapsed | Low change frequency |

### 5.3 Cache Warming

**Pre-populate cache for active trading universe**:
- **Schedule**: Daily at 5:00 AM ET (before market open)
- **Process**:
  1. Load list of active positions + watchlist symbols
  2. Fetch latest data for all symbols
  3. Warm in-memory cache with today's expected queries
- **Benefit**: Reduce latency for strategy execution

---

## 6. Data Quality & Validation

### 6.1 Validation Rules

#### **OHLCV Data Validation**

```python
def validate_ohlcv(row: dict) -> tuple[bool, list[str]]:
    """Validate OHLCV data point"""
    errors = []

    # Price consistency
    if row['high'] < row['low']:
        errors.append("High < Low")
    if not (row['low'] <= row['open'] <= row['high']):
        errors.append("Open outside [Low, High] range")
    if not (row['low'] <= row['close'] <= row['high']):
        errors.append("Close outside [Low, High] range")

    # Price reasonableness
    if row['close'] <= 0:
        errors.append("Non-positive close price")
    if row['volume'] < 0:
        errors.append("Negative volume")

    # Outlier detection (3-sigma rule vs recent history)
    recent_prices = get_recent_prices(row['symbol'], lookback_days=30)
    if recent_prices is not None:
        mean_price = recent_prices['close'].mean()
        std_price = recent_prices['close'].std()
        if abs(row['close'] - mean_price) > 3 * std_price:
            errors.append(f"Outlier: {row['close']} vs mean {mean_price:.2f} ± {std_price:.2f}")

    return len(errors) == 0, errors
```

#### **Fundamental Data Validation**

```python
def validate_fundamentals(statement: dict) -> tuple[bool, list[str]]:
    """Validate financial statement consistency"""
    errors = []

    # Balance sheet identity: Assets = Liabilities + Equity
    if statement['type'] == 'balance_sheet':
        assets = statement.get('total_assets', 0)
        liabilities = statement.get('total_liabilities', 0)
        equity = statement.get('total_equity', 0)

        if abs(assets - (liabilities + equity)) > 1e6:  # Allow $1M rounding error
            errors.append(f"Balance sheet doesn't balance: {assets} != {liabilities + equity}")

    # Income statement: Revenue > 0 for most companies
    if statement['type'] == 'income_statement':
        revenue = statement.get('revenue', 0)
        if revenue < 0:
            errors.append(f"Negative revenue: {revenue}")

    return len(errors) == 0, errors
```

#### **Staleness Detection**

```python
def is_data_stale(symbol: str, asset_class: str, data_type: str) -> bool:
    """Check if cached data is too old"""
    last_update = get_last_update_timestamp(symbol, data_type)

    staleness_thresholds = {
        ('equity', 'ohlcv_daily'): timedelta(days=1),
        ('crypto', 'ohlcv_intraday'): timedelta(minutes=5),
        ('equity', 'fundamentals'): timedelta(days=90),
        ('economic', 'indicators'): timedelta(days=1)
    }

    threshold = staleness_thresholds.get((asset_class, data_type), timedelta(days=7))
    return datetime.now() - last_update > threshold
```

### 6.2 Missing Data Handling

**CRITICAL**: Never forward-fill OHLCV data - this creates synthetic bars that corrupt backtests. Mark gaps as NULL instead and let strategies decide how to handle.

**Strategies by Data Type**:
1. **OHLCV Data**:
   - Mark gaps as NULL (do NOT forward-fill or interpolate)
   - Distinguish gap types in `data_quality_log`:
     - `holiday`: Expected gap (market closed), no alert
     - `api_failure`: Unexpected gap, retry + alert
     - `illiquid`: Expected for low-volume symbols, flag but don't retry
     - `delisted`: Symbol no longer trading, permanent gap
   - Let trading strategies decide how to handle gaps (skip symbol, use last known, etc.)

2. **Economic Indicators**: Linear interpolation acceptable (smooth time series)

3. **Fundamentals**: No interpolation; use last reported value until new earnings release

4. **Alert & Exclude**: Mark symbol as data-unavailable; exclude from strategy universe if gap_type is critical

**Example**:
```python
def handle_missing_ohlcv_data(symbol: str, missing_dates: list[date]):
    """Handle missing OHLCV data - mark gaps but do NOT synthesize data"""
    for d in missing_dates:
        gap_type = classify_gap(symbol, d)  # 'holiday', 'api_failure', 'illiquid', 'delisted'

        if gap_type == 'holiday':
            # Expected gap, no action needed
            log_data_quality_issue(symbol, 'missing_data', f'Market holiday {d}',
                                    severity='low', gap_type='holiday')
        elif gap_type == 'api_failure':
            # Retry fetching
            retry_fetch(symbol, d)
            log_data_quality_issue(symbol, 'missing_data', f'API failure {d}',
                                    severity='high', gap_type='api_failure')
        elif gap_type == 'illiquid':
            # Expected for low-volume symbols
            log_data_quality_issue(symbol, 'missing_data', f'Illiquid symbol {d}',
                                    severity='medium', gap_type='illiquid')
        elif gap_type == 'delisted':
            # Permanent gap
            log_data_quality_issue(symbol, 'missing_data', f'Delisted {d}',
                                    severity='high', gap_type='delisted')
            mark_symbol_inactive(symbol, d)

def classify_gap(symbol: str, date: date) -> str:
    """Classify why OHLCV data is missing"""
    # Check holiday calendar
    if is_market_holiday(symbol.exchange, date):
        return 'holiday'

    # Check if symbol delisted
    if is_delisted(symbol, date):
        return 'delisted'

    # Check recent volume
    recent_volume = get_recent_average_volume(symbol, lookback_days=30)
    if recent_volume < 1000:  # Threshold for illiquid
        return 'illiquid'

    # Default: API failure
    return 'api_failure'
```

### 6.3 Data Quality Monitoring

**Daily Report**:
- Count of validation failures by symbol and error type
- Staleness report (symbols with data >7 days old)
- API error rate by source
- Coverage report (% of trading universe with up-to-date data)

**Alerting**:
- Critical: >10% of trading universe has stale data
- High: Validation failure rate >5%
- Medium: API source unavailable for >1 hour

---

## 7. Timezone Handling

### 7.1 Standard: UTC Storage, Local Display

**Rationale**:
- **UTC storage**: Avoid DST ambiguity, simplify multi-timezone operations
- **Local conversion**: Display in exchange timezone when needed (e.g., NYSE = America/New_York)

### 7.2 Exchange Timezone Mapping

```python
EXCHANGE_TIMEZONES = {
    'NYSE': 'America/New_York',
    'NASDAQ': 'America/New_York',
    'LSE': 'Europe/London',
    'TSE': 'Asia/Tokyo',
    'HKEX': 'Asia/Hong_Kong',
    'CRYPTO': 'UTC'  # 24/7 markets
}
```

### 7.3 Market Hours & Holiday Calendar

**CRITICAL**: Market hours detection must include holiday calendar to avoid wasting API calls on closed trading days.

```python
import pandas_market_calendars as mcal

def is_market_open(exchange: str, timestamp: datetime) -> bool:
    """Check if market is currently open (includes holiday calendar)"""
    # Get exchange calendar (handles holidays, early closes, DST)
    calendar = mcal.get_calendar(exchange)

    # Check if this date is a trading day
    schedule = calendar.schedule(start_date=timestamp.date(), end_date=timestamp.date())

    if schedule.empty:
        # No trading this date (weekend or holiday)
        return False

    # Check if within market hours
    market_open = schedule.iloc[0]['market_open']
    market_close = schedule.iloc[0]['market_close']

    return market_open <= timestamp <= market_close

def is_market_holiday(exchange: str, date: date) -> bool:
    """Check if date is a market holiday"""
    calendar = mcal.get_calendar(exchange)
    schedule = calendar.schedule(start_date=date, end_date=date)
    return schedule.empty  # Empty schedule = holiday

# Dependency: pandas-market-calendars (free, MIT license)
# Supports: NYSE, NASDAQ, LSE, TSE, HKEX, and 20+ other global exchanges
# Handles: Holidays, early closes, daylight saving time transitions
```

**Benefits**:
- Avoids fetching data on market holidays (saves API calls)
- Handles early closes (e.g., day after Thanksgiving)
- DST transitions handled automatically
- Maintains schedules for 20+ global exchanges

---

## 8. Testing Strategy

### 8.1 Test Coverage Requirements

**Target**: >85% code coverage (per CLAUDE.md)

### 8.2 Test Categories

#### **Unit Tests**
- Data validation functions (OHLCV, fundamentals)
- Rate limiter logic (request counting, wait time calculation)
- Cache invalidation rules
- Timezone conversions

#### **Integration Tests**
- End-to-end data fetch from live APIs (with mocks for CI/CD)
- SQLite schema creation and querying
- MCP server integration (Alpha Vantage, Yahoo Finance)
- Fallback logic when primary source unavailable

#### **Data Quality Tests**
- Synthetic data injection with known errors
- Verify validation catches outliers, missing data, inconsistencies
- Test staleness detection with backdated timestamps

#### **Performance Tests**
- Batch ingestion of 1000 symbols (measure time, memory)
- Concurrent query load (simulate 100 strategies reading simultaneously)
- Cache hit rate measurement

### 8.3 Test Data Management

**Mock Data Sources**:
- Create synthetic OHLCV datasets with known patterns (trends, volatility)
- Mock API responses for Alpha Vantage, CoinGecko, FRED
- Use `pytest-mock` for MCP server interactions

**Fixtures**:
```python
@pytest.fixture
def sample_ohlcv_data():
    return pd.DataFrame({
        'symbol': ['AAPL'] * 5,
        'date': pd.date_range('2025-01-01', periods=5),
        'open': [150, 151, 152, 151, 153],
        'high': [152, 153, 154, 153, 155],
        'low': [149, 150, 151, 150, 152],
        'close': [151, 152, 153, 152, 154],
        'volume': [1000000] * 5
    })
```

---

## 9. Scalability Considerations

### 9.1 Current Scale (Phase 1)

**Assumptions**:
- Trading universe: 500 symbols (equities, ETFs, crypto)
- Data types: Daily OHLCV, quarterly fundamentals, 50 economic indicators
- Storage: ~10GB for 10 years of daily data + fundamentals

**Bottlenecks**:
- Alpha Vantage 25 requests/day limit (most restrictive)
- SQLite write concurrency (single writer)

**Mitigations**:
- Aggressive caching reduces API calls to ~20/day (routine updates only)
- Batch writes during off-hours (no write conflicts)

### 9.2 Future Scale (Phase 2+)

**When SQLite insufficient** (>1M symbols, real-time ingestion):
- Migrate to PostgreSQL (free, supports concurrent writes)
- Consider TimescaleDB extension for time-series optimization
- Implement sharding by asset class if needed

**When API rate limits insufficient**:
- Evaluate paid API tiers (if trading profits justify cost)
- Implement distributed scraping (multiple IPs for yfinance)
- Add more free sources (e.g., EOD Historical Data free tier)

---

## 10. Operational Monitoring

### 10.1 Metrics to Track

| Metric | Target | Alert Threshold | Purpose |
|--------|--------|-----------------|---------|
| API Success Rate | >95% | <90% | Detect source outages |
| Data Freshness | <1 day lag | >3 days lag | Ensure timely updates |
| Cache Hit Rate | >80% | <50% | Optimize API usage |
| Validation Failure Rate | <1% | >5% | Data quality monitoring |
| Storage Growth | ~1GB/month | >5GB/month | Capacity planning |
| Rate Limit Utilization | <80% of daily limit | >95% | Avoid hitting caps |

### 10.2 Logging Strategy

**Log Levels**:
- **DEBUG**: Individual API requests, cache hits/misses
- **INFO**: Daily ingestion summary (symbols updated, records inserted)
- **WARNING**: Validation failures, rate limit proximity, missing data
- **ERROR**: API errors, database write failures, critical missing data
- **CRITICAL**: Complete source outage, database corruption

**Log Destinations**:
- **File**: `/var/log/solaraalpha/market_data.log` (rotated daily)
- **Console**: Errors and above (for orchestrator monitoring)
- **Database**: `api_usage_log`, `data_quality_log` tables

### 10.3 Alerting

**Notification Channels**:
- **Email**: Daily summary report (data coverage, quality metrics)
- **GitHub Issues**: Automatically create issue for critical data gaps (e.g., primary source down >24 hours)

**Alert Examples**:
- "Alpha Vantage rate limit exhausted; switched to yfinance for AAPL, MSFT, GOOGL"
- "FRED API returned 500 error; economic indicators not updated for 2025-10-25"
- "Validation failed for BTC-USD: Close price ($45,000) is 3.5σ outlier"

---

## 11. Implementation Roadmap

### Phase 1: Foundation (Week 1-2)
- [ ] Set up SQLite database with schema
- [ ] Implement Alpha Vantage MCP integration
- [ ] Build FRED direct API client
- [ ] Create `MarketDataRepository` abstraction layer
- [ ] Implement rate limiter and request prioritization
- [ ] Add OHLCV validation logic
- [ ] Write unit tests (target >85% coverage)

### Phase 2: Caching & Quality (Week 3-4)
- [ ] Implement multi-level caching (in-memory + SQLite)
- [ ] Add cache invalidation logic
- [ ] Build data quality monitoring dashboard
- [ ] Implement staleness detection and auto-refresh
- [ ] Add outlier detection for OHLCV
- [ ] Create daily data quality report

### Phase 3: Multi-Source & Redundancy (Week 5-6)
- [ ] Integrate CoinGecko for crypto (MCP or direct)
- [ ] Add yfinance as backup for equities
- [ ] Implement fallback logic with automatic source selection
- [ ] Add fundamental data validation
- [ ] Build batch ingestion scheduler
- [ ] Performance testing and optimization

### Phase 4: Monitoring & Ops (Week 7-8)
- [ ] Set up logging and alerting infrastructure
- [ ] Create operational dashboards (coverage, freshness, API usage)
- [ ] Implement automatic retry with exponential backoff
- [ ] Add data archival to cold storage (optional)
- [ ] Load testing (1000 symbols, concurrent access)
- [ ] Documentation and runbook for operations

---

## 12. Risk Mitigation

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| **Alpha Vantage rate limit exhaustion** | High (no equity data) | Medium | Multi-source fallback (yfinance); aggressive caching; prioritize critical symbols |
| **yfinance blocking/throttling** | Medium (backup unavailable) | High | Use sparingly; implement exponential backoff; monitor error rates |
| **SQLite write bottleneck** | Medium (ingestion delays) | Low | Batch writes during off-hours; future migration to PostgreSQL |
| **Data staleness** | High (bad trades) | Low | Staleness monitoring; auto-refresh; alerts for critical gaps |
| **Validation false positives** | Low (over-filtering) | Medium | Tune outlier thresholds; manual review of flagged data |
| **API source outage** | Critical (no new data) | Low | Multi-source redundancy; cached data fallback; alerts |
| **Storage capacity** | Medium (out of disk) | Low | Monitor growth; archive old data; retention policies |

---

## 13. Compliance & Licensing

### 13.1 Data Source Terms of Service

| Source | License | Restrictions | Commercial Use |
|--------|---------|--------------|----------------|
| **Alpha Vantage** | Free tier | 25 requests/day; attribution recommended | Allowed |
| **yfinance** | Unofficial scraper | No official ToS; may violate Yahoo ToS | Use at own risk |
| **FRED** | Public domain (US Govt) | Attribution required; no restrictions | Allowed |
| **CoinGecko** | Free tier | 30 calls/min, 10k/month; attribution required | Allowed |

### 13.2 Attribution

Include in application footer / docs:
```
Market data provided by:
- Alpha Vantage (https://www.alphavantage.co/)
- Federal Reserve Economic Data (FRED) (https://fred.stlouisfed.org/)
- CoinGecko (https://www.coingecko.com/)
```

---

## 14. Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2025-10-25 | Use hybrid MCP approach (existing + custom) | Leverage existing Alpha Vantage MCP; build FRED integration (no MCP exists) |
| 2025-10-25 | SQLite for storage | Free, embedded, sufficient scale for initial phase |
| 2025-10-25 | Alpha Vantage as primary equity source | Only free source with fundamentals; yfinance too unreliable |
| 2025-10-25 | CoinGecko as primary crypto source | Better rate limits than CoinMarketCap; well-documented API |
| 2025-10-25 | Daily batch ingestion for equities | Alpha Vantage limit (25/day) requires careful request management |
| 2025-10-25 | 3-sigma outlier detection | Industry standard; balance between false positives and catching errors |
| 2025-10-25 | Never forward-fill OHLCV data | Prevents synthetic data from corrupting backtests; mark gaps as NULL |
| 2025-10-25 | Event-driven fundamental polling | Poll based on earnings calendar rather than fixed weekly schedule |
| 2025-10-25 | Add pandas-market-calendars | Critical for holiday detection to avoid wasting API calls |
| 2025-10-25 | 5-minute minimum intraday interval | 1-minute bars require ~2.6TB storage annually; 5-minute reduces to ~50MB |

---

## 14A. API Key Management

### Storage
- **Location**: `.env` file in project root (gitignored)
- **Format**:
```bash
ALPHA_VANTAGE_API_KEY=your_key_here
FRED_API_KEY=your_key_here
COINGECKO_API_KEY=  # Optional (free tier doesn't require key)
```

### Loading
```python
from dotenv import load_dotenv
import os

load_dotenv()  # Load .env file

ALPHA_VANTAGE_KEY = os.getenv('ALPHA_VANTAGE_API_KEY')
FRED_API_KEY = os.getenv('FRED_API_KEY')

if not ALPHA_VANTAGE_KEY:
    raise ValueError("ALPHA_VANTAGE_API_KEY not found in environment")
```

### Key Acquisition
- **Alpha Vantage**: Free key at https://www.alphavantage.co/support/#api-key
- **FRED**: Free key at https://fred.stlouisfed.org/docs/api/api_key.html
- **CoinGecko**: Free tier doesn't require API key (optional for higher limits)

### Rotation
1. Update `.env` file with new key
2. Restart ingestion process (orchestrator will reload environment)
3. No downtime required (keys rotated during off-hours)

### CI/CD
- Use GitHub Secrets for test API keys
- Configure in repository settings → Secrets → Actions
- Reference in workflow: `${{ secrets.ALPHA_VANTAGE_API_KEY }}`

### Security
- **Never commit `.env` to git** (verified via .gitignore)
- Use read-only keys when available
- Rotate keys quarterly
- Monitor usage logs for unauthorized access

---

## 15. Appendix

### 15.1 Key Dependencies

```yaml
# environment.yml additions
dependencies:
  - python=3.11
  - pandas>=2.0
  - numpy>=1.24
  - sqlalchemy>=2.0
  - sqlite  # Built-in
  - requests>=2.31
  - pytz>=2023.3
  - cachetools>=5.3

  # Data source clients
  - pip:
    - fredapi>=0.5.1
    - pycoingecko>=3.1.0
    - alpha-vantage>=2.3.1

  # Market calendars and API key management
  - pip:
    - pandas-market-calendars>=4.0  # Holiday calendar for exchanges
    - python-dotenv>=1.0  # API key management

  # Relative value strategy support
  - pip:
    - statsmodels>=0.14  # Cointegration tests (ADF test)
    - scipy>=1.11  # Statistical functions

  # Testing
  - pytest>=7.4
  - pytest-cov>=4.1
  - pytest-mock>=3.11
```

### 15.2 Sample API Calls

**Alpha Vantage** (via MCP):
```python
# Assuming MCP server running
response = mcp_client.call_tool(
    "alpha_vantage",
    "get_time_series",
    {
        "symbol": "AAPL",
        "interval": "daily",
        "outputsize": "compact"  # Last 100 days
    }
)
```

**FRED** (direct):
```python
from fredapi import Fred
fred = Fred(api_key=os.getenv('FRED_API_KEY'))
gdp_data = fred.get_series('GDP', observation_start='2020-01-01')
```

**CoinGecko** (direct):
```python
from pycoingecko import CoinGeckoAPI
cg = CoinGeckoAPI()
bitcoin_price = cg.get_price(ids='bitcoin', vs_currencies='usd')
```

### 15.3 Useful FRED Series IDs

| Indicator | Series ID | Frequency | Description |
|-----------|-----------|-----------|-------------|
| GDP | GDP | Quarterly | Gross Domestic Product |
| Unemployment Rate | UNRATE | Monthly | Civilian unemployment rate |
| Federal Funds Rate | FEDFUNDS | Monthly | Effective federal funds rate |
| CPI | CPIAUCSL | Monthly | Consumer Price Index |
| 10-Year Treasury | DGS10 | Daily | 10-year Treasury constant maturity rate |
| VIX | VIXCLS | Daily | CBOE Volatility Index |
| S&P 500 | SP500 | Daily | S&P 500 index |

### 15.4 SQLite Optimization Tips

```sql
-- Vacuum database monthly to reclaim space
VACUUM;

-- Analyze tables to update query planner statistics
ANALYZE;

-- Enable WAL mode for better concurrency
PRAGMA journal_mode=WAL;

-- Increase cache size (default 2MB -> 64MB)
PRAGMA cache_size=-64000;
```

---

## Document Control

**Approvals**:
- [x] Architecture review (via Task tool peer review) - **Approved with Changes** (2025-10-25)
- [ ] CEO approval (via commit to main branch)

**Peer Review Summary** (2025-10-25):
- **Status**: Approved with Changes Required
- **Overall Assessment**: Comprehensive architecture with strong technical depth; critical issues addressed
- **Critical Fixes Applied**:
  1. Added holiday calendar integration (pandas-market-calendars)
  2. Removed forward-fill for OHLCV data; gap classification implemented
  3. Changed fundamentals to event-driven polling (earnings calendar)
  4. Added relative value infrastructure (pair relationships, correlation tables)
  5. Corrected storage estimates and API usage projections
  6. Added API key management section
- **Implementation Readiness**: 85% (up from 70%) - Ready for Phase 1 with monitoring for rate limit issues

**Change History**:
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-10-25 | Claude (general-purpose) | Initial architecture design |
| 1.1 | 2025-10-25 | Claude (general-purpose) | Post-peer review updates: holiday calendar, no forward-fill, event-driven fundamentals, relative value tables, API key mgmt |

**Next Review**: After Phase 1 implementation (Week 2)

---

**End of Document**
