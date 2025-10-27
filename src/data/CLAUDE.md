# Data Infrastructure

Production-grade multi-source, multi-asset market data pipeline for SolaraAlpha trading operations.

## Architecture

**Pipeline**: `fetch` → `validate` → `store` → `serve`

**Components**:
- `config/` - YAML-based configuration management
- `ingest/` - Multi-source data fetching (YFinance for equities/ETFs, CCXT for crypto)
- `validate/` - Asset-specific quality checks (OHLC, spikes, gaps, staleness)
- `storage/` - Parquet-based persistence with metadata tracking
- `pipeline.py` - End-to-end orchestration

**Storage**: `data/{asset_class}/{symbol}/{frequency}/data.parquet`

## Supported Asset Classes

**Equities/ETFs**: YFinance ingestor (free, no API key)
**Crypto**: CCXT Binance ingestor (24/7 markets, 1m/5m/1h/1d intervals, 2+ years historical)
- Supports: BTC, ETH, USDT, BNB, SOL, XRP, USDC, ADA, AVAX, DOGE
- Real-time streaming via `fetch_realtime_ohlcv()`

**Crypto Validation**: Higher volatility tolerance (20%), no gaps allowed, 2-hour staleness threshold

## Usage

```bash
# Backfill all configured symbols
python scripts/backfill_data.py --all

# Backfill specific asset class
python scripts/backfill_data.py --asset-class equities

# Backfill single symbol
python scripts/backfill_data.py --symbol AAPL --asset-class equities --force
```

## Configuration

Edit `config/data_sources.yaml` to add symbols, modify validation rules, or enable additional data sources.

## Testing

Comprehensive test suite: `pytest tests/test_data_*.py --cov=src/data` (>85% coverage target)
