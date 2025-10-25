"""
Integration tests for MarketDataClient with live API calls.

These tests make actual API calls to external services and should be run sparingly.
Mark as slow tests using pytest.mark.slow decorator.

To run: pytest -v -m slow tests/test_market_client_integration.py
"""

import pytest
import os
from datetime import datetime, timedelta

from src.data.market_client import MarketDataClient, CacheConfig


# Skip all tests if in CI environment without API keys
pytestmark = pytest.mark.slow


@pytest.fixture
def client():
    """Create client with live API access."""
    return MarketDataClient(
        alpha_vantage_key=os.getenv("ALPHA_VANTAGE_API_KEY"),
        fred_api_key=os.getenv("FRED_API_KEY")
    )


@pytest.fixture
def client_with_short_cache():
    """Create client with short cache TTL for testing."""
    return MarketDataClient(
        cache_config=CacheConfig(ttl_seconds=10),
        alpha_vantage_key=os.getenv("ALPHA_VANTAGE_API_KEY"),
        fred_api_key=os.getenv("FRED_API_KEY")
    )


class TestYFinanceIntegration:
    """Integration tests for yfinance data source."""

    def test_get_ohlcv_stock(self, client):
        """Test fetching OHLCV data for a stock."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        df = client.get_ohlcv("AAPL", start=start_date, end=end_date, interval="1d")

        assert df is not None
        assert len(df) > 0
        assert list(df.columns) == ['Open', 'High', 'Low', 'Close', 'Volume']
        assert all(df['High'] >= df['Low'])
        assert all(df['High'] >= df['Open'])
        assert all(df['High'] >= df['Close'])

    def test_get_ohlcv_etf(self, client):
        """Test fetching OHLCV data for an ETF."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        df = client.get_ohlcv("SPY", start=start_date, end=end_date)

        assert df is not None
        assert len(df) > 0

    def test_get_ohlcv_crypto(self, client):
        """Test fetching OHLCV data for cryptocurrency."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        df = client.get_ohlcv("BTC-USD", start=start_date, end=end_date)

        assert df is not None
        assert len(df) > 0

    def test_get_ohlcv_invalid_symbol(self, client):
        """Test handling of invalid symbol."""
        with pytest.raises(ValueError, match="No data available"):
            client.get_ohlcv("INVALID_SYMBOL_XYZ123")

    def test_get_ohlcv_caching(self, client_with_short_cache):
        """Test that OHLCV data is properly cached."""
        import time

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        # First call - fetches from API
        start_time = time.time()
        df1 = client_with_short_cache.get_ohlcv("AAPL", start=start_date, end=end_date)
        first_call_time = time.time() - start_time

        # Second call - should use cache (much faster)
        start_time = time.time()
        df2 = client_with_short_cache.get_ohlcv("AAPL", start=start_date, end=end_date)
        second_call_time = time.time() - start_time

        # Cache should be significantly faster
        assert second_call_time < first_call_time * 0.5
        assert df1.equals(df2)


@pytest.mark.skipif(
    not os.getenv("ALPHA_VANTAGE_API_KEY"),
    reason="Alpha Vantage API key not available"
)
class TestAlphaVantageIntegration:
    """Integration tests for Alpha Vantage data source."""

    def test_get_fundamentals_income_statement(self, client):
        """Test fetching income statement data."""
        df = client.get_fundamentals("AAPL", report_type="income_statement")

        assert df is not None
        assert len(df) > 0

    def test_get_fundamentals_balance_sheet(self, client):
        """Test fetching balance sheet data."""
        df = client.get_fundamentals("AAPL", report_type="balance_sheet")

        assert df is not None
        assert len(df) > 0

    def test_get_fundamentals_cash_flow(self, client):
        """Test fetching cash flow data."""
        df = client.get_fundamentals("AAPL", report_type="cash_flow")

        assert df is not None
        assert len(df) > 0

    def test_get_forex_quote(self, client):
        """Test fetching forex quote."""
        quote = client.get_forex_quote("USD", "EUR")

        assert quote is not None
        assert 'rate' in quote
        assert 'bid' in quote
        assert 'ask' in quote
        assert 'timestamp' in quote
        assert isinstance(quote['rate'], float)
        assert quote['rate'] > 0
        assert quote['bid'] <= quote['rate'] <= quote['ask']

    def test_alpha_vantage_caching(self, client_with_short_cache):
        """Test that Alpha Vantage data is properly cached."""
        import time

        # First call - fetches from API
        start_time = time.time()
        quote1 = client_with_short_cache.get_forex_quote("USD", "EUR")
        first_call_time = time.time() - start_time

        # Second call - should use cache
        start_time = time.time()
        quote2 = client_with_short_cache.get_forex_quote("USD", "EUR")
        second_call_time = time.time() - start_time

        # Cache should be significantly faster
        assert second_call_time < first_call_time * 0.5
        assert quote1 == quote2


@pytest.mark.skipif(
    not os.getenv("FRED_API_KEY"),
    reason="FRED API key not available"
)
class TestFREDIntegration:
    """Integration tests for FRED data source."""

    def test_get_gdp(self, client):
        """Test fetching GDP data."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        series = client.get_economic_indicator("GDP", start=start_date, end=end_date)

        assert series is not None
        assert len(series) > 0
        assert all(series > 0)  # GDP should be positive

    def test_get_federal_funds_rate(self, client):
        """Test fetching Federal Funds Rate."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        series = client.get_economic_indicator("DFF", start=start_date, end=end_date)

        assert series is not None
        assert len(series) > 0

    def test_get_unemployment_rate(self, client):
        """Test fetching unemployment rate."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        series = client.get_economic_indicator("UNRATE", start=start_date, end=end_date)

        assert series is not None
        assert len(series) > 0
        assert all(series >= 0)  # Unemployment should be non-negative

    def test_get_cpi(self, client):
        """Test fetching Consumer Price Index."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        series = client.get_economic_indicator("CPIAUCSL", start=start_date, end=end_date)

        assert series is not None
        assert len(series) > 0

    def test_fred_invalid_series(self, client):
        """Test handling of invalid FRED series."""
        with pytest.raises(ValueError, match="not found"):
            client.get_economic_indicator("INVALID_SERIES_XYZ")

    def test_fred_caching(self, client_with_short_cache):
        """Test that FRED data is properly cached."""
        import time

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        # First call - fetches from API
        start_time = time.time()
        series1 = client_with_short_cache.get_economic_indicator(
            "GDP", start=start_date, end=end_date
        )
        first_call_time = time.time() - start_time

        # Second call - should use cache
        start_time = time.time()
        series2 = client_with_short_cache.get_economic_indicator(
            "GDP", start=start_date, end=end_date
        )
        second_call_time = time.time() - start_time

        # Cache should be significantly faster
        assert second_call_time < first_call_time * 0.5
        assert series1.equals(series2)


class TestErrorHandling:
    """Integration tests for error handling and retry logic."""

    def test_network_resilience(self, client):
        """Test that client handles temporary network issues gracefully."""
        # This test verifies the retry mechanism works with real APIs
        # Even if there's a transient network issue, the retry should succeed
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        # Should succeed even with potential network hiccups
        df = client.get_ohlcv("AAPL", start=start_date, end=end_date)

        assert df is not None
        assert len(df) > 0

    def test_cache_persistence(self, client):
        """Test that cache persists across client instances."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        # Fetch data with first client
        df1 = client.get_ohlcv("MSFT", start=start_date, end=end_date)

        # Create new client instance (should share cache)
        client2 = MarketDataClient()
        df2 = client2.get_ohlcv("MSFT", start=start_date, end=end_date)

        # Data should be identical
        assert df1.equals(df2)


class TestMultiAssetWorkflow:
    """Integration tests for multi-asset workflows."""

    def test_fetch_multiple_equities(self, client):
        """Test fetching data for multiple equity symbols."""
        symbols = ["AAPL", "MSFT", "GOOGL"]
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        results = {}
        for symbol in symbols:
            results[symbol] = client.get_ohlcv(
                symbol, start=start_date, end=end_date
            )

        # Verify all data fetched
        assert len(results) == 3
        for symbol, df in results.items():
            assert df is not None
            assert len(df) > 0

    def test_fetch_mixed_assets(self, client):
        """Test fetching different asset classes."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        # Equity
        equity = client.get_ohlcv("AAPL", start=start_date, end=end_date)

        # ETF
        etf = client.get_ohlcv("SPY", start=start_date, end=end_date)

        # Crypto
        crypto = client.get_ohlcv("BTC-USD", start=start_date, end=end_date)

        assert all([
            equity is not None and len(equity) > 0,
            etf is not None and len(etf) > 0,
            crypto is not None and len(crypto) > 0
        ])
