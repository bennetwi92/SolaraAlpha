"""
Unit tests for MarketDataClient.

Tests cover:
- Cache functionality (save, retrieve, expiration)
- Error handling and retry logic
- API integrations with mocking
- Rate limit handling
"""

import pytest
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta

import pandas as pd

from src.data.market_client import (
    MarketDataClient,
    CacheConfig,
    RateLimitError
)


@pytest.fixture
def temp_cache_dir():
    """Create a temporary directory for cache testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def cache_config(temp_cache_dir):
    """Create a cache configuration with temporary directory."""
    return CacheConfig(
        enabled=True,
        ttl_seconds=60,
        cache_dir=temp_cache_dir
    )


@pytest.fixture
def client(cache_config):
    """Create a MarketDataClient instance with test configuration."""
    return MarketDataClient(
        cache_config=cache_config,
        alpha_vantage_key="test_av_key",
        fred_api_key="test_fred_key"
    )


@pytest.fixture
def client_no_cache():
    """Create a MarketDataClient instance without caching."""
    return MarketDataClient(
        cache_config=CacheConfig(enabled=False)
    )


class TestCacheConfig:
    """Test CacheConfig dataclass."""

    def test_default_config(self):
        """Test default cache configuration."""
        config = CacheConfig()
        assert config.enabled is True
        assert config.ttl_seconds == 3600
        assert config.cache_dir == Path.home() / ".solaraalpha" / "cache"

    def test_custom_config(self, temp_cache_dir):
        """Test custom cache configuration."""
        config = CacheConfig(
            enabled=False,
            ttl_seconds=1800,
            cache_dir=temp_cache_dir
        )
        assert config.enabled is False
        assert config.ttl_seconds == 1800
        assert config.cache_dir == temp_cache_dir


class TestMarketDataClientInit:
    """Test MarketDataClient initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default settings."""
        client = MarketDataClient()
        assert client.cache_config.enabled is True
        assert client.alpha_vantage_key is None
        assert client.fred_api_key is None

    def test_init_with_api_keys(self):
        """Test initialization with API keys."""
        client = MarketDataClient(
            alpha_vantage_key="av_key",
            fred_api_key="fred_key"
        )
        assert client.alpha_vantage_key == "av_key"
        assert client.fred_api_key == "fred_key"
        assert client.av_fundamental is not None
        assert client.av_forex is not None
        assert client.fred is not None

    def test_init_without_api_keys(self):
        """Test initialization without API keys."""
        client = MarketDataClient()
        assert client.av_fundamental is None
        assert client.av_forex is None
        assert client.fred is None

    def test_cache_db_created(self, client, temp_cache_dir):
        """Test that cache database is created on init."""
        assert (temp_cache_dir / "market_data.db").exists()


class TestCacheOperations:
    """Test cache-related operations."""

    def test_get_cache_key(self, client):
        """Test cache key generation."""
        key1 = client._get_cache_key("test", param1="value1", param2="value2")
        key2 = client._get_cache_key("test", param2="value2", param1="value1")
        key3 = client._get_cache_key("test", param1="different")

        # Same parameters should generate same key
        assert key1 == key2
        # Different parameters should generate different key
        assert key1 != key3

    def test_save_and_retrieve_cache(self, client):
        """Test saving and retrieving data from cache."""
        cache_key = "test_key"
        test_data = {"value": 123, "name": "test"}

        client._save_to_cache(cache_key, test_data)
        retrieved = client._get_from_cache(cache_key)

        assert retrieved == test_data

    def test_cache_expiration(self, client):
        """Test that expired cache entries are not returned."""
        cache_key = "test_key"
        test_data = {"value": 123}

        # Save with very short TTL
        client._save_to_cache(cache_key, test_data, ttl=1)

        # Should retrieve immediately
        assert client._get_from_cache(cache_key) == test_data

        # Wait for expiration
        time.sleep(1.1)

        # Should not retrieve expired data
        assert client._get_from_cache(cache_key) is None

    def test_cache_disabled(self, client_no_cache):
        """Test that caching can be disabled."""
        cache_key = "test_key"
        test_data = {"value": 123}

        client_no_cache._save_to_cache(cache_key, test_data)
        retrieved = client_no_cache._get_from_cache(cache_key)

        assert retrieved is None

    def test_clear_cache(self, client):
        """Test clearing all cache entries."""
        # Add multiple cache entries
        client._save_to_cache("key1", {"data": 1})
        client._save_to_cache("key2", {"data": 2})
        client._save_to_cache("key3", {"data": 3})

        # Verify entries exist
        assert client._get_from_cache("key1") is not None
        assert client._get_from_cache("key2") is not None

        # Clear cache
        client.clear_cache()

        # Verify all entries removed
        assert client._get_from_cache("key1") is None
        assert client._get_from_cache("key2") is None
        assert client._get_from_cache("key3") is None

    def test_clear_expired_cache(self, client):
        """Test clearing only expired entries."""
        # Add entries with different TTLs
        client._save_to_cache("short_ttl", {"data": 1}, ttl=1)
        client._save_to_cache("long_ttl", {"data": 2}, ttl=3600)

        # Wait for short TTL to expire
        time.sleep(1.1)

        # Clear expired entries
        client._clear_expired_cache()

        # Short TTL should be gone, long TTL should remain
        assert client._get_from_cache("short_ttl") is None
        assert client._get_from_cache("long_ttl") is not None


class TestRetryLogic:
    """Test exponential backoff retry logic."""

    def test_retry_success_first_attempt(self, client):
        """Test successful function call on first attempt."""
        mock_func = Mock(return_value="success")
        result = client._retry_with_backoff(mock_func)

        assert result == "success"
        assert mock_func.call_count == 1

    def test_retry_success_after_failures(self, client):
        """Test successful function call after initial failures."""
        mock_func = Mock(side_effect=[
            ConnectionError("fail"),
            ConnectionError("fail"),
            "success"
        ])

        result = client._retry_with_backoff(mock_func, max_retries=3)

        assert result == "success"
        assert mock_func.call_count == 3

    def test_retry_max_retries_exceeded(self, client):
        """Test that exception is raised after max retries."""
        mock_func = Mock(side_effect=ConnectionError("always fail"))

        with pytest.raises(ConnectionError):
            client._retry_with_backoff(mock_func, max_retries=2)

        assert mock_func.call_count == 3  # initial + 2 retries

    def test_retry_rate_limit_no_retry(self, client):
        """Test that rate limit errors are not retried."""
        mock_func = Mock(side_effect=RateLimitError("rate limit"))

        with pytest.raises(RateLimitError):
            client._retry_with_backoff(mock_func, max_retries=3)

        assert mock_func.call_count == 1  # No retries

    def test_retry_backoff_timing(self, client):
        """Test that exponential backoff timing is correct."""
        call_times = []

        def failing_func():
            call_times.append(time.time())
            raise ConnectionError("fail")

        with pytest.raises(ConnectionError):
            client._retry_with_backoff(
                failing_func,
                max_retries=2,
                initial_delay=0.1,
                backoff_factor=2.0
            )

        # Verify exponential backoff delays
        assert len(call_times) == 3
        delay1 = call_times[1] - call_times[0]
        delay2 = call_times[2] - call_times[1]

        # Allow some tolerance for timing
        assert 0.08 < delay1 < 0.15  # ~0.1s
        assert 0.18 < delay2 < 0.25  # ~0.2s


class TestGetOHLCV:
    """Test get_ohlcv method."""

    @patch('src.data.market_client.yf.Ticker')
    def test_get_ohlcv_success(self, mock_ticker_class, client):
        """Test successful OHLCV data retrieval."""
        # Mock yfinance response
        mock_ticker = Mock()
        mock_df = pd.DataFrame({
            'Open': [100.0, 101.0],
            'High': [102.0, 103.0],
            'Low': [99.0, 100.0],
            'Close': [101.0, 102.0],
            'Volume': [1000000, 1100000],
            'Dividends': [0, 0],
            'Stock Splits': [0, 0]
        }, index=pd.date_range('2024-01-01', periods=2, freq='D'))
        mock_ticker.history.return_value = mock_df
        mock_ticker_class.return_value = mock_ticker

        result = client.get_ohlcv("AAPL", start="2024-01-01", end="2024-01-31")

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert list(result.columns) == ['Open', 'High', 'Low', 'Close', 'Volume']
        mock_ticker.history.assert_called_once()

    @patch('src.data.market_client.yf.Ticker')
    def test_get_ohlcv_with_defaults(self, mock_ticker_class, client):
        """Test OHLCV retrieval with default dates."""
        mock_ticker = Mock()
        mock_df = pd.DataFrame({
            'Open': [100.0],
            'High': [102.0],
            'Low': [99.0],
            'Close': [101.0],
            'Volume': [1000000],
        }, index=pd.date_range('2024-01-01', periods=1, freq='D'))
        mock_ticker.history.return_value = mock_df
        mock_ticker_class.return_value = mock_ticker

        result = client.get_ohlcv("AAPL")

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1

    @patch('src.data.market_client.yf.Ticker')
    def test_get_ohlcv_empty_response(self, mock_ticker_class, client):
        """Test handling of empty OHLCV response."""
        mock_ticker = Mock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_ticker_class.return_value = mock_ticker

        with pytest.raises(ValueError, match="No data available"):
            client.get_ohlcv("INVALID")

    @patch('src.data.market_client.yf.Ticker')
    def test_get_ohlcv_uses_cache(self, mock_ticker_class, client):
        """Test that OHLCV data is cached."""
        mock_ticker = Mock()
        mock_df = pd.DataFrame({
            'Open': [100.0],
            'High': [102.0],
            'Low': [99.0],
            'Close': [101.0],
            'Volume': [1000000],
        }, index=pd.date_range('2024-01-01', periods=1, freq='D'))
        mock_ticker.history.return_value = mock_df
        mock_ticker_class.return_value = mock_ticker

        # First call
        result1 = client.get_ohlcv("AAPL", start="2024-01-01", end="2024-01-31")

        # Second call (should use cache)
        result2 = client.get_ohlcv("AAPL", start="2024-01-01", end="2024-01-31")

        # Ticker should only be called once
        assert mock_ticker.history.call_count == 1
        pd.testing.assert_frame_equal(result1, result2)


class TestGetFundamentals:
    """Test get_fundamentals method."""

    def test_get_fundamentals_no_api_key(self, client_no_cache):
        """Test that error is raised when API key is missing."""
        with pytest.raises(ValueError, match="API key required"):
            client_no_cache.get_fundamentals("AAPL")

    @patch('src.data.market_client.FundamentalData')
    def test_get_fundamentals_income_statement(self, mock_fd_class, client):
        """Test fetching income statement data."""
        mock_fd = Mock()
        mock_df = pd.DataFrame({
            'revenue': [100000000],
            'netIncome': [20000000]
        })
        mock_fd.get_income_statement_annual.return_value = (mock_df, None)
        client.av_fundamental = mock_fd

        result = client.get_fundamentals("AAPL", report_type="income_statement")

        assert isinstance(result, pd.DataFrame)
        mock_fd.get_income_statement_annual.assert_called_once_with("AAPL")

    @patch('src.data.market_client.FundamentalData')
    def test_get_fundamentals_balance_sheet(self, mock_fd_class, client):
        """Test fetching balance sheet data."""
        mock_fd = Mock()
        mock_df = pd.DataFrame({
            'totalAssets': [500000000],
            'totalLiabilities': [200000000]
        })
        mock_fd.get_balance_sheet_annual.return_value = (mock_df, None)
        client.av_fundamental = mock_fd

        result = client.get_fundamentals("AAPL", report_type="balance_sheet")

        assert isinstance(result, pd.DataFrame)
        mock_fd.get_balance_sheet_annual.assert_called_once_with("AAPL")

    @patch('src.data.market_client.FundamentalData')
    def test_get_fundamentals_cash_flow(self, mock_fd_class, client):
        """Test fetching cash flow data."""
        mock_fd = Mock()
        mock_df = pd.DataFrame({
            'operatingCashflow': [30000000],
            'capitalExpenditures': [10000000]
        })
        mock_fd.get_cash_flow_annual.return_value = (mock_df, None)
        client.av_fundamental = mock_fd

        result = client.get_fundamentals("AAPL", report_type="cash_flow")

        assert isinstance(result, pd.DataFrame)
        mock_fd.get_cash_flow_annual.assert_called_once_with("AAPL")

    @patch('src.data.market_client.FundamentalData')
    def test_get_fundamentals_rate_limit(self, mock_fd_class, client):
        """Test handling of rate limit errors."""
        mock_fd = Mock()
        mock_fd.get_income_statement_annual.side_effect = Exception("rate limit exceeded")
        client.av_fundamental = mock_fd

        with pytest.raises(RateLimitError):
            client.get_fundamentals("AAPL")


class TestGetForexQuote:
    """Test get_forex_quote method."""

    def test_get_forex_quote_no_api_key(self, client_no_cache):
        """Test that error is raised when API key is missing."""
        with pytest.raises(ValueError, match="API key required"):
            client_no_cache.get_forex_quote("USD", "EUR")

    @patch('src.data.market_client.ForeignExchange')
    def test_get_forex_quote_success(self, mock_fx_class, client):
        """Test successful forex quote retrieval."""
        mock_fx = Mock()
        mock_df = pd.DataFrame({
            '5. Exchange Rate': [0.85],
            '8. Bid Price': [0.849],
            '9. Ask Price': [0.851],
            '6. Last Refreshed': ['2024-01-15 10:00:00']
        })
        mock_fx.get_currency_exchange_rate.return_value = (mock_df, None)
        client.av_forex = mock_fx

        result = client.get_forex_quote("USD", "EUR")

        assert isinstance(result, dict)
        assert 'rate' in result
        assert 'bid' in result
        assert 'ask' in result
        assert 'timestamp' in result
        assert result['rate'] == 0.85
        assert result['bid'] == 0.849
        assert result['ask'] == 0.851

    @patch('src.data.market_client.ForeignExchange')
    def test_get_forex_quote_rate_limit(self, mock_fx_class, client):
        """Test handling of rate limit errors for forex."""
        mock_fx = Mock()
        mock_fx.get_currency_exchange_rate.side_effect = Exception("rate limit exceeded")
        client.av_forex = mock_fx

        with pytest.raises(RateLimitError):
            client.get_forex_quote("USD", "EUR")


class TestGetEconomicIndicator:
    """Test get_economic_indicator method."""

    def test_get_economic_indicator_no_api_key(self, client_no_cache):
        """Test that error is raised when API key is missing."""
        with pytest.raises(ValueError, match="API key required"):
            client_no_cache.get_economic_indicator("GDP")

    @patch('src.data.market_client.Fred')
    def test_get_economic_indicator_success(self, mock_fred_class, client):
        """Test successful economic indicator retrieval."""
        mock_fred = Mock()
        mock_series = pd.Series(
            [20000.0, 20500.0, 21000.0],
            index=pd.date_range('2024-01-01', periods=3, freq='ME')
        )
        mock_fred.get_series.return_value = mock_series
        client.fred = mock_fred

        result = client.get_economic_indicator("GDP", start="2024-01-01", end="2024-03-31")

        assert isinstance(result, pd.Series)
        assert len(result) == 3
        assert result.iloc[0] == 20000.0

    @patch('src.data.market_client.Fred')
    def test_get_economic_indicator_not_found(self, mock_fred_class, client):
        """Test handling of invalid series ID."""
        mock_fred = Mock()
        mock_fred.get_series.side_effect = Exception("Series not found")
        client.fred = mock_fred

        with pytest.raises(ValueError, match="not found"):
            client.get_economic_indicator("INVALID_SERIES")

    @patch('src.data.market_client.Fred')
    def test_get_economic_indicator_with_defaults(self, mock_fred_class, client):
        """Test economic indicator with default date range."""
        mock_fred = Mock()
        mock_series = pd.Series([100.0], index=[datetime.now()])
        mock_fred.get_series.return_value = mock_series
        client.fred = mock_fred

        result = client.get_economic_indicator("GDP")

        assert isinstance(result, pd.Series)
        mock_fred.get_series.assert_called_once()


class TestIntegration:
    """Integration tests for cache and API interaction."""

    def test_multiple_sources_with_cache(self, client, temp_cache_dir):
        """Test that cache works correctly across different data sources."""
        # Mock all API calls
        with patch('src.data.market_client.yf.Ticker') as mock_yf, \
             patch.object(client, 'av_fundamental') as mock_av, \
             patch.object(client, 'fred') as mock_fred:

            # Setup mocks
            mock_ticker = Mock()
            mock_ticker.history.return_value = pd.DataFrame({
                'Open': [100.0], 'High': [102.0], 'Low': [99.0],
                'Close': [101.0], 'Volume': [1000000]
            }, index=pd.date_range('2024-01-01', periods=1, freq='D'))
            mock_yf.return_value = mock_ticker

            mock_av.get_income_statement_annual.return_value = (
                pd.DataFrame({'revenue': [100000]}), None
            )

            mock_fred.get_series.return_value = pd.Series([20000.0])

            # Make calls
            ohlcv1 = client.get_ohlcv("AAPL", start="2024-01-01", end="2024-01-31")
            fundamentals1 = client.get_fundamentals("AAPL")
            indicator1 = client.get_economic_indicator("GDP")

            # Make same calls again (should use cache)
            ohlcv2 = client.get_ohlcv("AAPL", start="2024-01-01", end="2024-01-31")
            fundamentals2 = client.get_fundamentals("AAPL")
            indicator2 = client.get_economic_indicator("GDP")

            # Verify each API was only called once
            assert mock_ticker.history.call_count == 1
            assert mock_av.get_income_statement_annual.call_count == 1
            assert mock_fred.get_series.call_count == 1

            # Verify results are identical
            pd.testing.assert_frame_equal(ohlcv1, ohlcv2)
            pd.testing.assert_frame_equal(fundamentals1, fundamentals2)
            pd.testing.assert_series_equal(indicator1, indicator2)
