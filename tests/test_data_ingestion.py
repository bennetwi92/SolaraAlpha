"""Unit tests for data ingestion."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.ingest.ingest_manager import IngestManager
from src.data.ingest.yfinance_ingestor import YFinanceIngestor


@pytest.fixture
def mock_config():
    """Create a mock configuration."""

    class MockConfig:
        def is_source_enabled(self, source):
            return source == "yfinance"

        def get_data_source_config(self, source):
            return {
                "rate_limit_per_second": 10,
                "timeout": 30,
            }

        def get_lookback_years(self):
            return 2

        def get_ingestion_config(self):
            return {
                "update_buffer_days": 5,
            }

    return MockConfig()


@pytest.fixture
def sample_yfinance_data():
    """Create sample data that mimics YFinance output."""
    dates = pd.date_range(start="2023-01-01", periods=50, freq="D")
    data = pd.DataFrame(
        {
            "Open": [100.0 + i * 0.5 for i in range(50)],
            "High": [105.0 + i * 0.5 for i in range(50)],
            "Low": [95.0 + i * 0.5 for i in range(50)],
            "Close": [102.0 + i * 0.5 for i in range(50)],
            "Volume": [1000000 + i * 10000 for i in range(50)],
        },
        index=dates,
    )
    return data


def test_yfinance_ingestor_initialization():
    """Test YFinanceIngestor initialization."""
    ingestor = YFinanceIngestor(rate_limit=5.0, timeout=60)

    assert ingestor.rate_limit == 5.0
    assert ingestor.timeout == 60
    assert ingestor.get_name() == "yfinance"


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_yfinance_fetch_ohlcv(mock_ticker, sample_yfinance_data):
    """Test fetching OHLCV data from YFinance."""
    # Mock the ticker.history() method
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.return_value = sample_yfinance_data
    mock_ticker.return_value = mock_ticker_instance

    ingestor = YFinanceIngestor(rate_limit=100)  # High rate limit for testing

    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 2, 20)

    data = ingestor.fetch_ohlcv("AAPL", start_date, end_date, "1d")

    # Verify data structure
    assert isinstance(data, pd.DataFrame)
    assert list(data.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(data.index, pd.DatetimeIndex)
    assert len(data) == 50


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_yfinance_fetch_empty_data_raises_error(mock_ticker):
    """Test that fetching empty data raises ValueError."""
    # Mock the ticker.history() to return empty DataFrame
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.return_value = pd.DataFrame()
    mock_ticker.return_value = mock_ticker_instance

    ingestor = YFinanceIngestor(rate_limit=100)

    start_date = datetime(2023, 1, 1)
    end_date = datetime(2023, 2, 20)

    with pytest.raises(ValueError, match="returned empty data"):
        ingestor.fetch_ohlcv("INVALID", start_date, end_date, "1d")


def test_ingest_manager_initialization(mock_config):
    """Test IngestManager initialization."""
    manager = IngestManager(config=mock_config)

    assert len(manager.ingestors) == 1
    assert manager.ingestors[0].get_name() == "yfinance"


def test_ingest_manager_get_available_sources(mock_config):
    """Test getting available data sources."""
    manager = IngestManager(config=mock_config)

    sources = manager.get_available_sources()

    assert sources == ["yfinance"]


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_ingest_manager_fetch(mock_ticker, mock_config, sample_yfinance_data):
    """Test IngestManager fetch with fallback logic."""
    # Mock YFinance to return sample data
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.return_value = sample_yfinance_data
    mock_ticker.return_value = mock_ticker_instance

    manager = IngestManager(config=mock_config)

    data, source = manager.fetch("AAPL", "equities", frequency="1d", lookback_years=2)

    assert isinstance(data, pd.DataFrame)
    assert source == "yfinance"
    assert len(data) > 0


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_ingest_manager_fetch_all_sources_fail(mock_ticker, mock_config):
    """Test that fetch raises exception when all sources fail."""
    # Mock YFinance to raise exception
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.side_effect = Exception("API Error")
    mock_ticker.return_value = mock_ticker_instance

    manager = IngestManager(config=mock_config)

    with pytest.raises(Exception, match="All data sources failed"):
        manager.fetch("AAPL", "equities", frequency="1d")


@patch("src.data.ingest.yfinance_ingestor.yf.Ticker")
def test_ingest_manager_fetch_incremental(
    mock_ticker, mock_config, sample_yfinance_data
):
    """Test incremental data fetch."""
    # Mock YFinance
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.history.return_value = sample_yfinance_data
    mock_ticker.return_value = mock_ticker_instance

    manager = IngestManager(config=mock_config)

    last_date = datetime(2023, 1, 15)
    data, source = manager.fetch_incremental(
        "AAPL", "equities", last_date, frequency="1d"
    )

    assert isinstance(data, pd.DataFrame)
    assert source == "yfinance"


@patch("src.data.ingest.yfinance_ingestor.time.sleep")
def test_yfinance_rate_limiting(mock_sleep, sample_yfinance_data):
    """Test that rate limiting is applied."""
    with patch("src.data.ingest.yfinance_ingestor.yf.Ticker") as mock_ticker:
        mock_ticker_instance = MagicMock()
        mock_ticker_instance.history.return_value = sample_yfinance_data
        mock_ticker.return_value = mock_ticker_instance

        ingestor = YFinanceIngestor(rate_limit=2.0)  # 2 requests per second

        # Make multiple requests
        start_date = datetime(2023, 1, 1)
        end_date = datetime(2023, 2, 20)

        ingestor.fetch_ohlcv("AAPL", start_date, end_date, "1d")
        ingestor.fetch_ohlcv("GOOGL", start_date, end_date, "1d")

        # Verify sleep was called for rate limiting
        assert mock_sleep.call_count >= 1
