"""Integration tests for the complete data pipeline.

Tests the full end-to-end flow: fetch -> validate -> store -> retrieve.
"""
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.pipeline import DataPipeline


@pytest.fixture
def temp_pipeline():
    """Create a pipeline with temporary storage."""
    with tempfile.TemporaryDirectory() as temp_dir:

        class MockConfig:
            def is_source_enabled(self, source):
                return source == "yfinance"

            def get_data_source_config(self, source):
                return {
                    "rate_limit_per_second": 100,
                    "timeout": 30,
                }

            def get_lookback_years(self):
                return 2

            def get_ingestion_config(self):
                return {
                    "update_buffer_days": 5,
                }

            def get_validation_config(self):
                return {
                    "price_spike_threshold": 0.10,
                    "volume_spike_multiplier": 5.0,
                    "max_missing_days": 5,
                    "max_staleness_days": 7,
                }

            def get_storage_config(self):
                return {
                    "base_path": temp_dir,
                    "format": "parquet",
                    "compression": "snappy",
                }

            def get_all_symbols(self):
                return {
                    "equities": ["AAPL", "GOOGL"],
                    "crypto": ["BTC-USD"],
                }

            def get_symbols(self, asset_class):
                all_symbols = self.get_all_symbols()
                return all_symbols.get(asset_class, [])

        config = MockConfig()
        pipeline = DataPipeline(config=config)
        yield pipeline


@pytest.fixture
def sample_market_data():
    """Create realistic sample market data."""
    dates = pd.date_range(
        start=datetime.now() - pd.Timedelta(days=100), periods=100, freq="D"
    )
    data = pd.DataFrame(
        {
            "Open": [100.0 + i * 0.5 for i in range(100)],
            "High": [105.0 + i * 0.5 for i in range(100)],
            "Low": [95.0 + i * 0.5 for i in range(100)],
            "Close": [102.0 + i * 0.5 for i in range(100)],
            "Volume": [1000000 + i * 10000 for i in range(100)],
        },
        index=dates,
    )
    return data


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_full_pipeline_single_symbol(
    mock_ticker, temp_pipeline, sample_market_data
):
    """Test complete pipeline for a single symbol: fetch -> validate -> store -> retrieve."""
    # Mock YFinance
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.return_value = sample_market_data
    mock_ticker.return_value = mock_ticker_instance

    # Run pipeline
    result = temp_pipeline.fetch_and_store("AAPL", "equities", "1d")

    # Verify validation passed
    assert result.passed is True
    assert result.record_count == 100

    # Verify data was stored
    assert temp_pipeline.storage.exists("equities", "AAPL", "1d")

    # Retrieve and verify data
    stored_data = temp_pipeline.storage.read("equities", "AAPL", "1d")
    assert len(stored_data) == 100
    assert list(stored_data.columns) == ["Open", "High", "Low", "Close", "Volume"]

    # Verify metadata
    metadata = temp_pipeline.storage.get_metadata("equities", "AAPL", "1d")
    assert metadata["source"] == "yfinance"
    assert metadata["validation_status"] == "passed"
    assert metadata["record_count"] == 100


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_pipeline_incremental_update(
    mock_ticker, temp_pipeline, sample_market_data
):
    """Test incremental update functionality."""
    # Initial fetch
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.return_value = sample_market_data
    mock_ticker.return_value = mock_ticker_instance

    temp_pipeline.fetch_and_store("AAPL", "equities", "1d")

    # Create new data for incremental update
    new_dates = pd.date_range(
        start=datetime.now() - pd.Timedelta(days=10), periods=20, freq="D"
    )
    new_data = pd.DataFrame(
        {
            "Open": [200.0 + i for i in range(20)],
            "High": [205.0 + i for i in range(20)],
            "Low": [195.0 + i for i in range(20)],
            "Close": [202.0 + i for i in range(20)],
            "Volume": [2000000 + i * 10000 for i in range(20)],
        },
        index=new_dates,
    )

    mock_ticker_instance.history.return_value = new_data

    # Run incremental update (force_refetch=False triggers incremental logic)
    result = temp_pipeline.fetch_and_store(
        "AAPL", "equities", "1d", force_refetch=False
    )

    assert result.passed is True

    # Verify data was merged
    stored_data = temp_pipeline.storage.read("equities", "AAPL", "1d")
    assert len(stored_data) > 100  # Should have combined data


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_pipeline_validation_failure(mock_ticker, temp_pipeline):
    """Test pipeline behavior when validation fails."""
    # Create data with OHLC violations
    dates = pd.date_range(start="2023-01-01", periods=10, freq="D")
    bad_data = pd.DataFrame(
        {
            "Open": [100] * 10,
            "High": [95] * 10,  # High < Low (violation)
            "Low": [100] * 10,
            "Close": [102] * 10,
            "Volume": [1000000] * 10,
        },
        index=dates,
    )

    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.return_value = bad_data
    mock_ticker.return_value = mock_ticker_instance

    result = temp_pipeline.fetch_and_store("BAD", "equities", "1d")

    # Validation should fail
    assert result.passed is False
    assert len(result.get_errors()) > 0

    # Data should still be stored (with failed validation status)
    assert temp_pipeline.storage.exists("equities", "BAD", "1d")
    metadata = temp_pipeline.storage.get_metadata("equities", "BAD", "1d")
    assert metadata["validation_status"] == "failed"


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_backfill_multiple_symbols(
    mock_ticker, temp_pipeline, sample_market_data
):
    """Test backfilling multiple symbols."""
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.return_value = sample_market_data
    mock_ticker.return_value = mock_ticker_instance

    symbols = ["AAPL", "GOOGL", "MSFT"]
    results = temp_pipeline.backfill_symbols(
        symbols, "equities", frequency="1d"
    )

    # Verify all symbols were processed
    assert len(results) == 3
    assert all(symbol in results for symbol in symbols)

    # Verify all passed validation
    assert all(result.passed for result in results.values())

    # Verify all were stored
    for symbol in symbols:
        assert temp_pipeline.storage.exists("equities", symbol, "1d")


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_backfill_all(mock_ticker, temp_pipeline, sample_market_data):
    """Test backfilling all configured symbols across asset classes."""
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.return_value = sample_market_data
    mock_ticker.return_value = mock_ticker_instance

    results = temp_pipeline.backfill_all(frequency="1d")

    # Verify both asset classes were processed
    assert "equities" in results
    assert "crypto" in results

    # Verify equities symbols
    assert "AAPL" in results["equities"]
    assert "GOOGL" in results["equities"]

    # Verify crypto symbols
    assert "BTC-USD" in results["crypto"]


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_force_refetch(mock_ticker, temp_pipeline, sample_market_data):
    """Test force refetch overwrites existing data."""
    # Initial data
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.return_value = sample_market_data
    mock_ticker.return_value = mock_ticker_instance

    temp_pipeline.fetch_and_store("AAPL", "equities", "1d")

    initial_metadata = temp_pipeline.storage.get_metadata(
        "equities", "AAPL", "1d"
    )

    # Wait a moment to ensure timestamp changes
    import time

    time.sleep(0.1)

    # Force refetch
    temp_pipeline.fetch_and_store(
        "AAPL", "equities", "1d", force_refetch=True
    )

    new_metadata = temp_pipeline.storage.get_metadata("equities", "AAPL", "1d")

    # Verify metadata was updated
    assert new_metadata["last_updated"] > initial_metadata["last_updated"]


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_pipeline_with_warnings(mock_ticker, temp_pipeline):
    """Test pipeline handling of data with warnings (but no errors)."""
    # Create data with price spike (warning, not error)
    dates = pd.date_range(
        start=datetime.now() - pd.Timedelta(days=10), periods=10, freq="D"
    )
    data = pd.DataFrame(
        {
            "Open": [100] * 10,
            "High": [100, 105, 105, 125, 105, 105, 105, 105, 105, 105],  # High must be >= Close
            "Low": [95] * 10,
            "Close": [100, 100, 100, 120, 100, 100, 100, 100, 100, 100],  # 20% spike
            "Volume": [1000000] * 10,
        },
        index=dates,
    )

    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.return_value = data
    mock_ticker.return_value = mock_ticker_instance

    result = temp_pipeline.fetch_and_store("VOLATILE", "equities", "1d")

    # Should pass validation despite warnings
    assert result.passed is True
    assert len(result.get_warnings()) > 0
    assert len(result.get_errors()) == 0

    # Should be stored with "passed" status
    metadata = temp_pipeline.storage.get_metadata("equities", "VOLATILE", "1d")
    assert metadata["validation_status"] == "passed"
