"""Unit tests for Parquet storage engine."""
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.data.storage.parquet_storage import ParquetStorage


@pytest.fixture
def temp_storage():
    """Create a temporary storage instance."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a mock config
        class MockConfig:
            def get_storage_config(self):
                return {
                    "base_path": temp_dir,
                    "format": "parquet",
                    "compression": "snappy",
                }

        storage = ParquetStorage(config=MockConfig())
        yield storage


@pytest.fixture
def sample_ohlcv_data():
    """Create sample OHLCV data."""
    dates = pd.date_range(start="2023-01-01", periods=100, freq="D")
    data = pd.DataFrame(
        {
            "Open": 100 + pd.Series(range(100)) * 0.5,
            "High": 102 + pd.Series(range(100)) * 0.5,
            "Low": 99 + pd.Series(range(100)) * 0.5,
            "Close": 101 + pd.Series(range(100)) * 0.5,
            "Volume": 1000000 + pd.Series(range(100)) * 10000,
        },
        index=dates,
    )
    return data


def test_storage_initialization(temp_storage):
    """Test storage initialization."""
    assert temp_storage.base_path.exists()
    assert temp_storage.compression == "snappy"


def test_write_and_read(temp_storage, sample_ohlcv_data):
    """Test writing and reading data."""
    temp_storage.write(
        sample_ohlcv_data,
        "equities",
        "AAPL",
        "1d",
        source="yfinance",
        validation_status="passed",
    )

    # Read back
    data = temp_storage.read("equities", "AAPL", "1d")

    # Verify data integrity
    assert len(data) == len(sample_ohlcv_data)
    assert list(data.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(data.index, pd.DatetimeIndex)


def test_write_empty_data_raises_error(temp_storage):
    """Test that writing empty data raises ValueError."""
    empty_data = pd.DataFrame()

    with pytest.raises(ValueError, match="Cannot write empty data"):
        temp_storage.write(
            empty_data, "equities", "AAPL", "1d"
        )


def test_write_missing_columns_raises_error(temp_storage):
    """Test that writing data with missing columns raises ValueError."""
    dates = pd.date_range(start="2023-01-01", periods=10, freq="D")
    incomplete_data = pd.DataFrame(
        {"Open": [100] * 10, "Close": [101] * 10},
        index=dates,
    )

    with pytest.raises(ValueError, match="Missing required columns"):
        temp_storage.write(
            incomplete_data, "equities", "AAPL", "1d"
        )


def test_write_without_datetime_index_raises_error(temp_storage):
    """Test that writing data without DatetimeIndex raises ValueError."""
    bad_data = pd.DataFrame(
        {
            "Open": [100, 101],
            "High": [102, 103],
            "Low": [99, 100],
            "Close": [101, 102],
            "Volume": [1000000, 1100000],
        }
    )

    with pytest.raises(ValueError, match="must have DatetimeIndex"):
        temp_storage.write(
            bad_data, "equities", "AAPL", "1d"
        )


def test_read_nonexistent_data_raises_error(temp_storage):
    """Test that reading nonexistent data raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        temp_storage.read("equities", "NONEXISTENT", "1d")


def test_read_with_date_filters(temp_storage, sample_ohlcv_data):
    """Test reading data with date range filters."""
    temp_storage.write(
        sample_ohlcv_data, "equities", "AAPL", "1d"
    )

    # Read with date filters
    start = datetime(2023, 1, 15)
    end = datetime(2023, 1, 30)

    filtered_data = temp_storage.read(
        "equities", "AAPL", "1d", start_date=start, end_date=end
    )

    assert filtered_data.index.min() >= start
    assert filtered_data.index.max() <= end
    assert len(filtered_data) < len(sample_ohlcv_data)


def test_get_metadata(temp_storage, sample_ohlcv_data):
    """Test retrieving metadata for stored data."""
    temp_storage.write(
        sample_ohlcv_data,
        "equities",
        "AAPL",
        "1d",
        source="yfinance",
        validation_status="passed",
    )

    metadata = temp_storage.get_metadata("equities", "AAPL", "1d")

    assert metadata["source"] == "yfinance"
    assert metadata["validation_status"] == "passed"
    assert metadata["record_count"] == 100
    assert "last_updated" in metadata
    assert "start_date" in metadata
    assert "end_date" in metadata


def test_get_metadata_nonexistent_raises_error(temp_storage):
    """Test that getting metadata for nonexistent data raises error."""
    with pytest.raises(FileNotFoundError):
        temp_storage.get_metadata("equities", "NONEXISTENT", "1d")


def test_exists(temp_storage, sample_ohlcv_data):
    """Test checking if data exists."""
    # Initially doesn't exist
    assert temp_storage.exists("equities", "AAPL", "1d") is False

    # Write data
    temp_storage.write(
        sample_ohlcv_data, "equities", "AAPL", "1d"
    )

    # Now exists
    assert temp_storage.exists("equities", "AAPL", "1d") is True


def test_list_symbols(temp_storage, sample_ohlcv_data):
    """Test listing symbols with stored data."""
    # Write data for multiple symbols
    temp_storage.write(
        sample_ohlcv_data, "equities", "AAPL", "1d"
    )
    temp_storage.write(
        sample_ohlcv_data, "equities", "GOOGL", "1d"
    )
    temp_storage.write(
        sample_ohlcv_data, "equities", "MSFT", "1d"
    )

    symbols = temp_storage.list_symbols("equities")

    assert len(symbols) == 3
    assert "AAPL" in symbols
    assert "GOOGL" in symbols
    assert "MSFT" in symbols


def test_list_symbols_empty_asset_class(temp_storage):
    """Test listing symbols for empty asset class."""
    symbols = temp_storage.list_symbols("nonexistent")
    assert symbols == []


def test_delete(temp_storage, sample_ohlcv_data):
    """Test deleting stored data."""
    # Write data
    temp_storage.write(
        sample_ohlcv_data, "equities", "AAPL", "1d"
    )

    assert temp_storage.exists("equities", "AAPL", "1d") is True

    # Delete
    temp_storage.delete("equities", "AAPL", "1d")

    assert temp_storage.exists("equities", "AAPL", "1d") is False


def test_overwrite_existing_data(temp_storage, sample_ohlcv_data):
    """Test that writing overwrites existing data."""
    # Write initial data
    temp_storage.write(
        sample_ohlcv_data, "equities", "AAPL", "1d"
    )

    # Write new data with different values
    new_data = sample_ohlcv_data.copy()
    new_data["Close"] = new_data["Close"] * 2

    temp_storage.write(
        new_data, "equities", "AAPL", "1d"
    )

    # Read back
    stored_data = temp_storage.read("equities", "AAPL", "1d")

    # Verify new data was stored (reset index freq for comparison)
    stored_subset = stored_data[["Open", "High", "Low", "Close", "Volume"]].copy()
    stored_subset.index = stored_subset.index.as_unit('ns').to_period('D').to_timestamp()
    new_data.index = new_data.index.as_unit('ns').to_period('D').to_timestamp()

    pd.testing.assert_frame_equal(stored_subset, new_data, check_freq=False)


def test_data_sorted_by_date(temp_storage):
    """Test that data is sorted by date when written."""
    # Create unsorted data
    dates = pd.to_datetime(
        ["2023-01-05", "2023-01-01", "2023-01-03", "2023-01-02", "2023-01-04"]
    )
    unsorted_data = pd.DataFrame(
        {
            "Open": [105, 101, 103, 102, 104],
            "High": [106, 102, 104, 103, 105],
            "Low": [104, 100, 102, 101, 103],
            "Close": [105.5, 101.5, 103.5, 102.5, 104.5],
            "Volume": [1000000] * 5,
        },
        index=dates,
    )

    temp_storage.write(
        unsorted_data, "equities", "AAPL", "1d"
    )

    # Read back
    stored_data = temp_storage.read("equities", "AAPL", "1d")

    # Verify data is sorted
    assert stored_data.index.is_monotonic_increasing
    assert stored_data.index[0] == pd.Timestamp("2023-01-01")
    assert stored_data.index[-1] == pd.Timestamp("2023-01-05")
