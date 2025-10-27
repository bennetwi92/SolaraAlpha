# SolaraAlpha Data Inventory

**Generated:** 2025-10-27 20:59:04 UTC

## Overview

Comprehensive inventory of historical market data available for backtesting and live trading.

## Equities (Daily)

**Total Symbols:** 11

| Symbol | Records | Date Range | Source | Validation |
|--------|---------|------------|--------|------------|
| AAPL | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| GOOGL | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| MSFT | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| AMZN | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| TSLA | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| JPM | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| BAC | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| XOM | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| CVX | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| KO | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| PEP | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |

## ETFs (Daily)

**Total Symbols:** 4

| Symbol | Records | Date Range | Source | Validation |
|--------|---------|------------|--------|------------|
| SPY | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| IVV | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| GLD | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |
| IAU | 500 | 2023-10-30 to 2025-10-27 | yfinance | passed |

## Cryptocurrency (Hourly)

**Total Symbols:** 8

| Symbol | Records | Date Range | Source | Validation |
|--------|---------|------------|--------|------------|
| BTC-USD | 17,520 | 2023-10-28 to 2025-10-27 | yfinance | passed |
| ETH-USD | 17,519 | 2023-10-28 to 2025-10-27 | yfinance | passed |
| USDT-USD | 17,511 | 2023-10-28 to 2025-10-27 | yfinance | passed |
| BNB-USD | 17,520 | 2023-10-28 to 2025-10-27 | yfinance | passed |
| SOL-USD | 17,519 | 2023-10-28 to 2025-10-27 | yfinance | passed |
| ADA-USD | 17,520 | 2023-10-28 to 2025-10-27 | yfinance | passed |
| AVAX-USD | 17,512 | 2023-10-28 to 2025-10-27 | yfinance | passed |
| DOGE-USD | 17,513 | 2023-10-28 to 2025-10-27 | yfinance | passed |

## Forex (Daily)

**Total Symbols:** 0

*No forex data currently stored.*

## Data Quality Summary

- All stored data has passed OHLC validation (High >= Low, etc.)
- Price and volume spike warnings are informational only (crypto volatility is expected)
- Data freshness validated daily by health checker
- Storage format: Parquet with Snappy compression
- Automatic incremental updates configured

## Known Limitations

- **XRP-USD**: 2+ year hourly data unavailable from free sources (YFinance limit: 730 days)
- **USDC-USD**: Not available on Binance, limited YFinance data
- **Forex**: Currently only daily data (hourly planned)

## Storage Structure

```
data/
├── {asset_class}/
│   ├── {symbol}/
│   │   ├── {frequency}/
│   │   │   ├── data.parquet        # OHLCV time series
│   │   │   └── metadata.parquet    # Ingestion metadata
```