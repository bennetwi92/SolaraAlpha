# Data Infrastructure

Production-grade multi-source market data pipeline for SolaraAlpha trading operations.

## Architecture

**Pipeline**: `fetch` → `validate` → `store` → `serve`

**Components**:
- `config/` - YAML-based configuration management
- `ingest/` - Multi-source data fetching (YFinance primary, extensible)
- `validate/` - Quality checks (OHLC, spikes, gaps, staleness)
- `storage/` - Parquet-based persistence with metadata tracking
- `pipeline.py` - End-to-end orchestration

**Storage**: `data/{asset_class}/{symbol}/{frequency}/data.parquet`

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
