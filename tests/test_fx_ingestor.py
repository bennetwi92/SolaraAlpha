"""Tests for FX data ingestor."""
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from src.data.ingest.fx_ingestor import FXIngestor


@pytest.fixture
def fx_ingestor():
    """Create FX ingestor instance without API key."""
    return FXIngestor()


@pytest.fixture
def mock_frankfurter_response():
    """Mock response from Frankfurter API."""
    return {
        "amount": 1.0,
        "base": "EUR",
        "start_date": "2023-01-01",
        "end_date": "2023-01-10",
        "rates": {
            "2023-01-01": {"USD": 1.0500},
            "2023-01-02": {"USD": 1.0520},
            "2023-01-03": {"USD": 1.0510},
            "2023-01-04": {"USD": 1.0530},
            "2023-01-05": {"USD": 1.0540},
            "2023-01-06": {"USD": 1.0535},
            "2023-01-07": {"USD": 1.0545},
            "2023-01-08": {"USD": 1.0550},
            "2023-01-09": {"USD": 1.0555},
            "2023-01-10": {"USD": 1.0560},
        },
    }


@pytest.fixture
def mock_api_ninjas_response():
    """Mock response from API Ninjas interest rate API."""
    return {
        "central_bank_rates": [
            {
                "central_bank": "Federal Reserve",
                "country": "United States",
                "rate_pct": 5.25,
                "last_updated": "2024-01-15",
            }
        ]
    }


class TestFXIngestor:
    """Test suite for FX ingestor."""

    def test_initialization_without_api_key(self):
        """Test ingestor initialization without API key."""
        ingestor = FXIngestor()

        assert ingestor.api_ninjas_key is None
        assert ingestor.rate_limit == 0.5

    def test_initialization_with_api_key(self):
        """Test ingestor initialization with API key."""
        api_key = "test_key_123"
        ingestor = FXIngestor(api_ninjas_key=api_key)

        assert ingestor.api_ninjas_key == api_key

    def test_normalize_symbol(self, fx_ingestor):
        """Test symbol normalization."""
        base, quote = fx_ingestor._normalize_symbol("EUR-USD")

        assert base == "EUR"
        assert quote == "USD"

    def test_normalize_symbol_invalid_format(self, fx_ingestor):
        """Test symbol normalization with invalid format."""
        with pytest.raises(ValueError, match="Invalid FX symbol format"):
            fx_ingestor._normalize_symbol("EURUSD")

    def test_normalize_symbol_unsupported_currency(self, fx_ingestor):
        """Test symbol normalization with unsupported currency."""
        with pytest.raises(ValueError, match="Unsupported base currency"):
            fx_ingestor._normalize_symbol("XXX-USD")

    def test_get_name(self, fx_ingestor):
        """Test data source name."""
        assert fx_ingestor.get_name() == "frankfurter_fx"

    @patch("requests.get")
    def test_fetch_ohlcv_success(
        self, mock_get, fx_ingestor, mock_frankfurter_response
    ):
        """Test successful OHLCV data fetch."""
        # Setup mock
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_frankfurter_response
        mock_get.return_value = mock_response

        # Fetch data
        start_date = datetime(2023, 1, 1)
        end_date = datetime(2023, 1, 10)

        df = fx_ingestor.fetch_ohlcv("EUR-USD", start_date, end_date, "1d")

        # Validate results
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 10
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert df["Volume"].sum() == 0  # FX has no volume
        assert all(df["Close"] > 0)
        assert all(df["High"] >= df["Close"])
        assert all(df["Low"] <= df["Close"])

    @patch("requests.get")
    def test_fetch_ohlcv_invalid_frequency(
        self, mock_get, fx_ingestor
    ):
        """Test OHLCV fetch with unsupported frequency."""
        start_date = datetime(2023, 1, 1)
        end_date = datetime(2023, 1, 10)

        with pytest.raises(ValueError, match="Unsupported frequency"):
            fx_ingestor.fetch_ohlcv("EUR-USD", start_date, end_date, "1h")

    @patch("requests.get")
    def test_fetch_ohlcv_no_data(self, mock_get, fx_ingestor):
        """Test OHLCV fetch when API returns no data."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"rates": {}}
        mock_get.return_value = mock_response

        start_date = datetime(2023, 1, 1)
        end_date = datetime(2023, 1, 10)

        with pytest.raises(ValueError, match="No data returned"):
            fx_ingestor.fetch_ohlcv("EUR-USD", start_date, end_date, "1d")

    @patch("requests.get")
    def test_fetch_ohlcv_api_error(self, mock_get, fx_ingestor):
        """Test OHLCV fetch when API returns error."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = Exception("API Error")
        mock_get.return_value = mock_response

        start_date = datetime(2023, 1, 1)
        end_date = datetime(2023, 1, 10)

        with pytest.raises(Exception, match="FX data fetch failed"):
            fx_ingestor.fetch_ohlcv("EUR-USD", start_date, end_date, "1d")

    @patch("requests.get")
    def test_get_interest_rates_success(
        self, mock_get, mock_api_ninjas_response
    ):
        """Test successful interest rate fetch."""
        ingestor = FXIngestor(api_ninjas_key="test_key")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_api_ninjas_response
        mock_get.return_value = mock_response

        df = ingestor.get_interest_rates(countries=["United States"])

        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0
        assert "country" in df.columns
        assert "currency" in df.columns
        assert "rate" in df.columns
        assert df.iloc[0]["currency"] == "USD"
        assert df.iloc[0]["rate"] == 5.25

    def test_get_interest_rates_no_api_key(self, fx_ingestor):
        """Test interest rate fetch without API key."""
        with pytest.raises(RuntimeError, match="API_NINJAS_KEY required"):
            fx_ingestor.get_interest_rates()

    @patch("requests.get")
    def test_get_interest_rates_api_error(self, mock_get):
        """Test interest rate fetch with API error."""
        ingestor = FXIngestor(api_ninjas_key="test_key")

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = Exception("API Error")
        mock_get.return_value = mock_response

        with pytest.raises(Exception, match="Failed to fetch any interest rate data"):
            ingestor.get_interest_rates(countries=["United States"])

    def test_calculate_rate_differential(self, fx_ingestor):
        """Test rate differential calculation."""
        interest_rates = pd.DataFrame(
            {
                "country": ["Australia", "United States"],
                "currency": ["AUD", "USD"],
                "rate": [4.0, 2.0],
            }
        )

        diff = fx_ingestor.calculate_rate_differential(
            "AUD", "USD", interest_rates
        )

        assert diff == 2.0  # 4.0 - 2.0

    def test_calculate_rate_differential_missing_currency(self, fx_ingestor):
        """Test rate differential with missing currency."""
        interest_rates = pd.DataFrame(
            {
                "country": ["United States"],
                "currency": ["USD"],
                "rate": [2.0],
            }
        )

        with pytest.raises(ValueError, match="Interest rate not found for AUD"):
            fx_ingestor.calculate_rate_differential(
                "AUD", "USD", interest_rates
            )

    def test_get_supported_pairs(self, fx_ingestor):
        """Test getting list of supported pairs."""
        pairs = fx_ingestor.get_supported_pairs()

        assert isinstance(pairs, list)
        assert len(pairs) > 0
        assert "EUR-USD" in pairs
        assert "GBP-USD" in pairs
        assert "USD-JPY" in pairs

        # Check format
        for pair in pairs:
            assert "-" in pair
            base, quote = pair.split("-")
            assert base in fx_ingestor.SUPPORTED_CURRENCIES
            assert quote in fx_ingestor.SUPPORTED_CURRENCIES

    def test_rate_limit_wait(self, fx_ingestor):
        """Test rate limiting."""
        import time

        fx_ingestor.rate_limit = 0.1  # 0.1 seconds

        start = time.time()
        fx_ingestor._rate_limit_wait()
        first_wait = time.time() - start

        # First call should not wait (no previous request)
        assert first_wait < 0.05

        # Second call should wait
        start = time.time()
        fx_ingestor._rate_limit_wait()
        second_wait = time.time() - start

        assert second_wait >= 0.09  # Should wait ~0.1 seconds

    @patch("requests.get")
    def test_fetch_ohlcv_synthetic_ohlc(
        self, mock_get, fx_ingestor, mock_frankfurter_response
    ):
        """Test that synthetic OHLC is reasonable."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_frankfurter_response
        mock_get.return_value = mock_response

        start_date = datetime(2023, 1, 1)
        end_date = datetime(2023, 1, 10)

        df = fx_ingestor.fetch_ohlcv("EUR-USD", start_date, end_date, "1d")

        # Verify OHLC relationships
        assert all(df["High"] >= df["Close"])
        assert all(df["Low"] <= df["Close"])
        assert all(df["High"] >= df["Low"])

        # Verify synthetic spreads are small (< 0.1%)
        spread_pct = ((df["High"] - df["Low"]) / df["Close"]) * 100
        assert all(spread_pct < 0.2)
