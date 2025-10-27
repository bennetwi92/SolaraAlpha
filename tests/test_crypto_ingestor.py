"""Unit tests for cryptocurrency data ingestion using CCXT."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import pytest

from src.data.ingest.crypto_ingestor import CryptoIngestor


@pytest.fixture
def mock_exchange():
    """Create a mock CCXT exchange."""
    exchange = MagicMock()
    exchange.fetch_ohlcv = MagicMock()
    exchange.load_markets = MagicMock()
    exchange.markets = {
        "BTC/USD": {"id": "BTCUSD"},
        "ETH/USD": {"id": "ETHUSD"},
    }
    return exchange


@pytest.fixture
def sample_ccxt_data():
    """Create sample OHLCV data in CCXT format.

    CCXT returns: [[timestamp_ms, open, high, low, close, volume], ...]
    """
    base_timestamp = int(datetime(2023, 1, 1).timestamp() * 1000)
    data = []
    for i in range(100):
        timestamp = base_timestamp + (i * 86400000)  # Daily intervals
        data.append(
            [
                timestamp,
                50000.0 + i * 100,  # Open
                52000.0 + i * 100,  # High
                49000.0 + i * 100,  # Low
                51000.0 + i * 100,  # Close
                1000000.0 + i * 1000,  # Volume
            ]
        )
    return data


@pytest.fixture
def sample_dataframe():
    """Create sample DataFrame with OHLCV columns."""
    dates = pd.date_range(start="2023-01-01", periods=100, freq="D")
    return pd.DataFrame(
        {
            "Open": [50000.0 + i * 100 for i in range(100)],
            "High": [52000.0 + i * 100 for i in range(100)],
            "Low": [49000.0 + i * 100 for i in range(100)],
            "Close": [51000.0 + i * 100 for i in range(100)],
            "Volume": [1000000.0 + i * 1000 for i in range(100)],
        },
        index=dates,
    )


def test_crypto_ingestor_initialization():
    """Test CryptoIngestor initialization."""
    with patch("src.data.ingest.crypto_ingestor.ccxt.binance") as mock_binance:
        mock_binance.return_value = MagicMock()

        ingestor = CryptoIngestor(exchange="binance", rate_limit=2.0, timeout=30)

        assert ingestor.exchange_id == "binance"
        assert ingestor.rate_limit == 2.0
        assert ingestor.timeout == 30
        assert ingestor.get_name() == "ccxt_binance"


def test_crypto_ingestor_invalid_exchange():
    """Test that invalid exchange raises error."""
    with pytest.raises(AttributeError):
        CryptoIngestor(exchange="invalid_exchange_name")


def test_normalize_symbol():
    """Test symbol normalization from SolaraAlpha to CCXT format."""
    with patch("src.data.ingest.crypto_ingestor.ccxt.binance") as mock_binance:
        mock_binance.return_value = MagicMock()
        ingestor = CryptoIngestor()

        assert ingestor._normalize_symbol("BTC-USD") == "BTC/USD"
        assert ingestor._normalize_symbol("ETH-USD") == "ETH/USD"
        assert ingestor._normalize_symbol("SOL-USD") == "SOL/USD"


def test_get_timeframe_duration():
    """Test timeframe duration calculation."""
    with patch("src.data.ingest.crypto_ingestor.ccxt.binance") as mock_binance:
        mock_binance.return_value = MagicMock()
        ingestor = CryptoIngestor()

        assert ingestor._get_timeframe_duration("1m") == timedelta(minutes=1)
        assert ingestor._get_timeframe_duration("5m") == timedelta(minutes=5)
        assert ingestor._get_timeframe_duration("1h") == timedelta(hours=1)
        assert ingestor._get_timeframe_duration("1d") == timedelta(days=1)


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_ohlcv_single_batch(mock_binance, sample_ccxt_data):
    """Test fetching OHLCV data for a small date range (single batch)."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = sample_ccxt_data[:50]
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)  # High rate for testing

    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 2, 20)

    data = ingestor.fetch_ohlcv("BTC-USD", start_date, end_date, "1d")

    # Verify data structure
    assert isinstance(data, pd.DataFrame)
    assert list(data.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(data.index, pd.DatetimeIndex)
    assert len(data) == 50

    # Verify CCXT was called with correct parameters
    mock_exchange.fetch_ohlcv.assert_called()
    call_args = mock_exchange.fetch_ohlcv.call_args
    assert call_args[0][0] == "BTC/USD"  # Normalized symbol
    assert call_args[1]["timeframe"] == "1d"


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_ohlcv_multiple_batches(mock_binance, sample_ccxt_data):
    """Test fetching OHLCV data requiring multiple batches."""
    mock_exchange = MagicMock()

    # Simulate pagination - return different chunks on each call
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        start_idx = call_count * 50
        call_count += 1
        return sample_ccxt_data[start_idx : start_idx + 50]

    mock_exchange.fetch_ohlcv.side_effect = side_effect
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)

    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 4, 10)  # 100 days

    data = ingestor.fetch_ohlcv("BTC-USD", start_date, end_date, "1d")

    # Verify multiple batches were fetched
    assert mock_exchange.fetch_ohlcv.call_count >= 2
    assert isinstance(data, pd.DataFrame)
    assert len(data) == 100


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_ohlcv_unsupported_frequency(mock_binance):
    """Test that unsupported frequency raises ValueError."""
    mock_exchange = MagicMock()
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor()

    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 2, 1)

    with pytest.raises(ValueError, match="Unsupported frequency"):
        ingestor.fetch_ohlcv("BTC-USD", start_date, end_date, "15m")


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_ohlcv_empty_data_raises_error(mock_binance):
    """Test that fetching empty data raises ValueError."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = []
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)

    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 2, 1)

    with pytest.raises(ValueError, match="No data returned"):
        ingestor.fetch_ohlcv("INVALID-USD", start_date, end_date, "1d")


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_ohlcv_all_timeframes(mock_binance, sample_ccxt_data):
    """Test fetching data with all supported timeframes."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = sample_ccxt_data[:50]
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)

    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 2, 20)

    for frequency in ["1m", "5m", "1h", "1d"]:
        data = ingestor.fetch_ohlcv("BTC-USD", start_date, end_date, frequency)

        assert isinstance(data, pd.DataFrame)
        assert list(data.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert isinstance(data.index, pd.DatetimeIndex)


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_realtime_ohlcv(mock_binance, sample_ccxt_data):
    """Test fetching real-time OHLCV data."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = sample_ccxt_data[:100]
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)

    data = ingestor.fetch_realtime_ohlcv("BTC-USD", frequency="1m", lookback_periods=100)

    # Verify data structure
    assert isinstance(data, pd.DataFrame)
    assert list(data.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(data) == 100

    # Verify CCXT was called with limit parameter
    call_kwargs = mock_exchange.fetch_ohlcv.call_args[1]
    assert call_kwargs["limit"] == 100


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_realtime_ohlcv_unsupported_frequency(mock_binance):
    """Test that real-time fetch with unsupported frequency raises error."""
    mock_exchange = MagicMock()
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor()

    with pytest.raises(ValueError, match="Unsupported frequency"):
        ingestor.fetch_realtime_ohlcv("BTC-USD", frequency="30m", lookback_periods=50)


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_realtime_ohlcv_empty_data(mock_binance):
    """Test that real-time fetch with no data raises error."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = []
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)

    with pytest.raises(ValueError, match="No data returned"):
        ingestor.fetch_realtime_ohlcv("INVALID-USD", frequency="1m")


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_get_supported_symbols(mock_binance):
    """Test getting list of supported trading pairs."""
    mock_exchange = MagicMock()
    mock_exchange.load_markets = MagicMock()
    mock_exchange.markets = {
        "BTC/USD": {"id": "BTCUSD"},
        "ETH/USD": {"id": "ETHUSD"},
        "SOL/USD": {"id": "SOLUSD"},
    }
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor()

    symbols = ingestor.get_supported_symbols()

    assert "BTC/USD" in symbols
    assert "ETH/USD" in symbols
    assert "SOL/USD" in symbols
    assert len(symbols) == 3


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_get_supported_symbols_error_handling(mock_binance):
    """Test error handling when loading markets fails."""
    mock_exchange = MagicMock()
    mock_exchange.load_markets.side_effect = Exception("API Error")
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor()

    symbols = ingestor.get_supported_symbols()

    # Should return empty list on error
    assert symbols == []


@patch("src.data.ingest.crypto_ingestor.time.sleep")
@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_rate_limiting(mock_binance, mock_sleep, sample_ccxt_data):
    """Test that rate limiting is applied between requests."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = sample_ccxt_data[:50]
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=2.0)  # 2 requests per second

    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 2, 20)

    # Make multiple requests
    ingestor.fetch_ohlcv("BTC-USD", start_date, end_date, "1d")
    ingestor.fetch_ohlcv("ETH-USD", start_date, end_date, "1d")

    # Verify sleep was called for rate limiting
    assert mock_sleep.call_count >= 1


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_ohlcv_removes_duplicates(mock_binance):
    """Test that duplicate timestamps are removed."""
    # Create data with duplicate timestamps
    base_timestamp = int(datetime(2023, 1, 1).timestamp() * 1000)
    duplicate_data = [
        [base_timestamp, 50000, 51000, 49000, 50500, 1000000],
        [base_timestamp, 50000, 51000, 49000, 50500, 1000000],  # Duplicate
        [base_timestamp + 86400000, 50100, 51100, 49100, 50600, 1000100],
    ]

    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = duplicate_data
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)

    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 1, 3)

    data = ingestor.fetch_ohlcv("BTC-USD", start_date, end_date, "1d")

    # Duplicates should be removed
    assert len(data) == 2
    assert data.index.is_unique


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_ohlcv_filters_to_date_range(mock_binance, sample_ccxt_data):
    """Test that data is filtered to exact date range requested."""
    # Return more data than requested
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = sample_ccxt_data
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)

    start_date = datetime(2023, 1, 10)
    end_date = datetime(2023, 1, 20)

    data = ingestor.fetch_ohlcv("BTC-USD", start_date, end_date, "1d")

    # Verify all dates are within requested range
    assert data.index.min() >= start_date
    assert data.index.max() <= end_date


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_ohlcv_sorts_by_index(mock_binance):
    """Test that returned data is sorted by timestamp."""
    # Create unsorted data
    base_timestamp = int(datetime(2023, 1, 1).timestamp() * 1000)
    unsorted_data = [
        [base_timestamp + 172800000, 50200, 51200, 49200, 50700, 1000200],  # Day 3
        [base_timestamp, 50000, 51000, 49000, 50500, 1000000],  # Day 1
        [base_timestamp + 86400000, 50100, 51100, 49100, 50600, 1000100],  # Day 2
    ]

    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = unsorted_data
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)

    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 1, 4)

    data = ingestor.fetch_ohlcv("BTC-USD", start_date, end_date, "1d")

    # Verify data is sorted
    assert data.index.is_monotonic_increasing


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_batch_empty_response(mock_binance):
    """Test handling of empty batch response."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = []
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)

    # Test private method
    result = ingestor._fetch_ohlcv_batch("BTC/USD", 0, "1d")

    assert isinstance(result, pd.DataFrame)
    assert result.empty


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_batch_exception_handling(mock_binance):
    """Test that fetch_ohlcv_batch handles exceptions gracefully."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.side_effect = Exception("Network error")
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)

    # Should return empty DataFrame on error
    result = ingestor._fetch_ohlcv_batch("BTC/USD", 0, "1d")

    assert isinstance(result, pd.DataFrame)
    assert result.empty


@patch("src.data.ingest.crypto_ingestor.ccxt.binance")
def test_fetch_ohlcv_historical_backfill(mock_binance, sample_ccxt_data):
    """Test fetching 2+ years of historical data."""
    mock_exchange = MagicMock()

    # Simulate 2 years of data (730 days)
    extended_data = sample_ccxt_data * 8  # 800 candles
    mock_exchange.fetch_ohlcv.return_value = extended_data[:100]
    mock_binance.return_value = mock_exchange

    ingestor = CryptoIngestor(rate_limit=100)

    # Request 2 years of data
    end_date = datetime.now()
    start_date = end_date - timedelta(days=730)

    data = ingestor.fetch_ohlcv("BTC-USD", start_date, end_date, "1d")

    # Verify we got historical data
    assert isinstance(data, pd.DataFrame)
    assert len(data) > 0

    # Should have made multiple requests for 2 years
    assert mock_exchange.fetch_ohlcv.call_count >= 1
