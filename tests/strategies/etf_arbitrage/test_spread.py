"""Tests for spread calculation module."""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from src.strategies.etf_arbitrage.spread import SpreadCalculator, SpreadMetrics


class TestSpreadCalculator:
    """Test cases for SpreadCalculator."""

    @pytest.fixture
    def calculator(self):
        """Create SpreadCalculator instance."""
        return SpreadCalculator(lookback_window=20)

    @pytest.fixture
    def price_series(self):
        """Create sample price series."""
        dates = pd.date_range(start='2024-01-01', periods=100, freq='D')
        prices1 = pd.Series(np.random.randn(100).cumsum() + 100, index=dates)
        prices2 = pd.Series(np.random.randn(100).cumsum() + 100, index=dates)
        return prices1, prices2

    def test_calculate_spread_ratio(self, calculator, price_series):
        """Test ratio spread calculation."""
        prices1, prices2 = price_series
        spread = calculator.calculate_spread(prices1, prices2, method='ratio')

        assert len(spread) == len(prices1)
        assert isinstance(spread, pd.Series)
        assert not spread.isna().all()

    def test_calculate_spread_difference(self, calculator, price_series):
        """Test difference spread calculation."""
        prices1, prices2 = price_series
        spread = calculator.calculate_spread(prices1, prices2, method='difference')

        assert len(spread) == len(prices1)
        assert isinstance(spread, pd.Series)

    def test_calculate_spread_invalid_method(self, calculator, price_series):
        """Test invalid spread method raises error."""
        prices1, prices2 = price_series
        with pytest.raises(ValueError):
            calculator.calculate_spread(prices1, prices2, method='invalid')

    def test_calculate_z_score(self, calculator, price_series):
        """Test z-score calculation."""
        prices1, prices2 = price_series
        spread = calculator.calculate_spread(prices1, prices2)
        z_score = calculator.calculate_z_score(spread)

        assert len(z_score) == len(spread)
        # First lookback_window-1 values should be NaN (rolling window needs lookback_window points)
        assert z_score.iloc[:calculator.lookback_window-1].isna().all()
        # Subsequent values should be valid
        assert not z_score.iloc[calculator.lookback_window:].isna().all()

    def test_calculate_half_life(self, calculator):
        """Test half-life calculation."""
        # Create mean-reverting series
        np.random.seed(42)
        spread = pd.Series([10 + 5 * np.exp(-0.1 * i) + np.random.randn() * 0.1 for i in range(100)])

        half_life = calculator.calculate_half_life(spread)

        assert isinstance(half_life, (float, np.floating))
        assert half_life > 0
        assert half_life < 100  # Should be reasonable

    def test_calculate_half_life_insufficient_data(self, calculator):
        """Test half-life with insufficient data."""
        spread = pd.Series([1.0])
        half_life = calculator.calculate_half_life(spread)
        assert np.isnan(half_life)

    def test_get_current_metrics(self, calculator, price_series):
        """Test getting current spread metrics."""
        prices1, prices2 = price_series
        metrics = calculator.get_current_metrics(prices1, prices2)

        assert isinstance(metrics, SpreadMetrics)
        assert isinstance(metrics.spread, (float, np.floating))
        assert isinstance(metrics.z_score, (float, np.floating))
        assert isinstance(metrics.rolling_mean, (float, np.floating))
        assert isinstance(metrics.rolling_std, (float, np.floating))
        assert isinstance(metrics.half_life, (float, np.floating))

    def test_get_spread_history(self, calculator, price_series):
        """Test getting full spread history."""
        prices1, prices2 = price_series
        history = calculator.get_spread_history(prices1, prices2)

        assert isinstance(history, pd.DataFrame)
        assert 'spread' in history.columns
        assert 'z_score' in history.columns
        assert 'rolling_mean' in history.columns
        assert 'rolling_std' in history.columns
        assert len(history) == len(prices1)

    def test_zero_std_handling(self, calculator):
        """Test handling of zero standard deviation."""
        # Create constant spread (zero std)
        prices1 = pd.Series([100.0] * 50, index=pd.date_range('2024-01-01', periods=50))
        prices2 = pd.Series([50.0] * 50, index=pd.date_range('2024-01-01', periods=50))

        z_score = calculator.calculate_z_score(
            calculator.calculate_spread(prices1, prices2)
        )

        # Z-score should be NaN when std is zero
        assert z_score.iloc[-1] is np.nan or np.isnan(z_score.iloc[-1])
