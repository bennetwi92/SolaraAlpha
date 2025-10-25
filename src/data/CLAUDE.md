# Data Module

## Purpose
Market data acquisition, storage, and retrieval layer for SolaraAlpha trading systems.

## Components
- **market_client.py**: Unified interface to multiple free market data sources (yfinance, Alpha Vantage, FRED)
- **Caching**: SQLite-based local cache with configurable TTL to respect rate limits

## Data Sources
- **yfinance**: OHLCV for equities/ETFs/crypto (unlimited)
- **Alpha Vantage**: Forex quotes, fundamentals (25 requests/day free tier)
- **FRED**: Economic indicators (unlimited)

## Usage
```python
from src.data.market_client import MarketDataClient

client = MarketDataClient()
df = client.get_ohlcv("AAPL", start="2024-01-01", end="2024-12-31")
```

## Standards
- All methods include type hints and docstrings with examples
- Error handling with exponential backoff for API failures
- Local caching to minimize API calls and respect rate limits
