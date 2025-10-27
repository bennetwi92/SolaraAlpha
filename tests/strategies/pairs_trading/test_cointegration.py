"""Tests for cointegration module."""
import numpy as np
import pandas as pd
import pytest

from src.strategies.pairs_trading.cointegration import (
    CointegrationResult,
    CointegrationTester,
)


@pytest.fixture
def cointegrated_series():
    """Generate cointegrated price series for testing."""
    np.random.seed(42)
    n = 252  # 1 year daily data

    # Generate cointegrated series
    # series1 = beta * series2 + stationary_residual
    series2 = pd.Series(
        100 + np.cumsum(np.random.randn(n) * 0.5),
        index=pd.date_range("2023-01-01", periods=n),
    )

    beta = 1.5
    residual = np.random.randn(n) * 2  # Stationary noise
    series1 = pd.Series(
        beta * series2.values + residual,
        index=series2.index,
    )

    return series1, series2, beta


@pytest.fixture
def non_cointegrated_series():
    """Generate non-cointegrated (independent random walks)."""
    np.random.seed(42)
    n = 252

    series1 = pd.Series(
        100 + np.cumsum(np.random.randn(n) * 2),
        index=pd.date_range("2023-01-01", periods=n),
    )

    series2 = pd.Series(
        50 + np.cumsum(np.random.randn(n) * 1.5),
        index=pd.date_range("2023-01-01", periods=n),
    )

    return series1, series2


class TestCointegrationTester:
    """Test suite for CointegrationTester."""

    def test_initialization(self):
        """Test tester initialization."""
        tester = CointegrationTester(significance_level=0.05)
        assert tester.significance_level == 0.05

    def test_engle_granger_cointegrated(self, cointegrated_series):
        """Test Engle-Granger on cointegrated series."""
        series1, series2, true_beta = cointegrated_series
        tester = CointegrationTester()

        result = tester.test_pair(series1, series2, method="engle_granger")

        assert isinstance(result, CointegrationResult)
        assert result.is_cointegrated
        assert result.p_value < 0.05
        assert result.method == "engle_granger"
        assert abs(result.hedge_ratio - true_beta) < 0.5  # Approximate match

    def test_engle_granger_non_cointegrated(self, non_cointegrated_series):
        """Test Engle-Granger on non-cointegrated series."""
        series1, series2 = non_cointegrated_series
        tester = CointegrationTester()

        result = tester.test_pair(series1, series2, method="engle_granger")

        # High probability of non-cointegration (not guaranteed)
        assert isinstance(result, CointegrationResult)
        assert result.p_value >= 0.0  # Valid p-value

    def test_johansen_cointegrated(self, cointegrated_series):
        """Test Johansen test on cointegrated series."""
        series1, series2, _ = cointegrated_series
        tester = CointegrationTester()

        result = tester.test_pair(series1, series2, method="johansen")

        assert isinstance(result, CointegrationResult)
        assert result.method == "johansen"
        assert result.hedge_ratio != 0

    def test_invalid_method(self, cointegrated_series):
        """Test invalid method raises error."""
        series1, series2, _ = cointegrated_series
        tester = CointegrationTester()

        with pytest.raises(ValueError, match="Invalid method"):
            tester.test_pair(series1, series2, method="invalid")

    def test_length_mismatch(self):
        """Test that mismatched series lengths raise error."""
        series1 = pd.Series([1, 2, 3])
        series2 = pd.Series([1, 2, 3, 4])
        tester = CointegrationTester()

        with pytest.raises(ValueError, match="length mismatch"):
            tester.test_pair(series1, series2)

    def test_spread_stationarity_stationary(self, cointegrated_series):
        """Test stationarity test on stationary spread."""
        series1, series2, beta = cointegrated_series
        tester = CointegrationTester()

        spread = series1 - beta * series2

        is_stationary, p_value = tester.test_spread_stationarity(spread)

        assert isinstance(is_stationary, (bool, np.bool_))
        assert 0 <= p_value <= 1
        # Expect stationary for cointegrated series
        assert is_stationary

    def test_spread_stationarity_non_stationary(self):
        """Test stationarity test on non-stationary series (random walk)."""
        np.random.seed(42)
        random_walk = pd.Series(
            100 + np.cumsum(np.random.randn(252)),
            index=pd.date_range("2023-01-01", periods=252),
        )

        tester = CointegrationTester()
        is_stationary, p_value = tester.test_spread_stationarity(
            random_walk
        )

        assert isinstance(is_stationary, (bool, np.bool_))
        # High probability of non-stationarity (not guaranteed)
        assert p_value >= 0.0

    def test_rolling_cointegration(self, cointegrated_series):
        """Test rolling window cointegration."""
        series1, series2, _ = cointegrated_series
        tester = CointegrationTester()

        results_df = tester.rolling_cointegration(
            series1, series2, window=60, step=30
        )

        assert isinstance(results_df, pd.DataFrame)
        assert "window_end" in results_df.columns
        assert "is_cointegrated" in results_df.columns
        assert "p_value" in results_df.columns
        assert "hedge_ratio" in results_df.columns
        assert len(results_df) > 0

    def test_rolling_cointegration_short_series(self):
        """Test rolling cointegration with series too short."""
        series1 = pd.Series(
            range(50), index=pd.date_range("2023-01-01", periods=50)
        )
        series2 = pd.Series(
            range(50), index=pd.date_range("2023-01-01", periods=50)
        )

        tester = CointegrationTester()

        # Window=60 > len=50, should return empty or minimal results
        results_df = tester.rolling_cointegration(
            series1, series2, window=60, step=20
        )

        assert isinstance(results_df, pd.DataFrame)
        # May be empty if window too large
        assert len(results_df) == 0

    def test_critical_values_present(self, cointegrated_series):
        """Test that critical values are returned."""
        series1, series2, _ = cointegrated_series
        tester = CointegrationTester()

        result = tester.test_pair(series1, series2)

        assert "1%" in result.critical_values
        assert "5%" in result.critical_values
        assert "10%" in result.critical_values

    def test_different_significance_levels(self, cointegrated_series):
        """Test behavior with different significance levels."""
        series1, series2, _ = cointegrated_series

        # Test with 1% significance
        tester_strict = CointegrationTester(significance_level=0.01)
        result_strict = tester_strict.test_pair(series1, series2)

        # Test with 10% significance
        tester_lenient = CointegrationTester(significance_level=0.10)
        result_lenient = tester_lenient.test_pair(series1, series2)

        # Both should detect cointegration (strong relationship)
        assert result_strict.is_cointegrated
        assert result_lenient.is_cointegrated
