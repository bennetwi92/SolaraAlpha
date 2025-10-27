"""Tests for strategy performance tracking."""
import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timedelta

from src.portfolio.performance_tracker import PerformanceTracker, StrategyPerformance


class TestPerformanceTracker:
    """Test suite for PerformanceTracker."""

    @pytest.fixture
    def tracker(self):
        """Create a performance tracker instance."""
        return PerformanceTracker(lookback_days=100, min_history_days=10)

    @pytest.fixture
    def sample_returns(self):
        """Generate sample return data."""
        np.random.seed(42)
        # Generate 50 days of returns
        returns = np.random.normal(0.001, 0.02, 50)  # Mean 0.1% daily, 2% vol
        dates = [datetime.now() - timedelta(days=i) for i in range(49, -1, -1)]
        return list(zip(dates, returns))

    def test_initialization(self, tracker):
        """Test tracker initialization."""
        assert tracker.lookback_days == 100
        assert tracker.min_history_days == 10
        assert len(tracker._returns) == 0
        assert len(tracker._performance) == 0

    def test_record_single_return(self, tracker):
        """Test recording a single return."""
        tracker.record_return("test_strategy", 0.02, datetime.now())

        assert "test_strategy" in tracker._returns
        assert len(tracker._returns["test_strategy"]) == 1

    def test_record_multiple_returns(self, tracker, sample_returns):
        """Test recording multiple returns."""
        for date, ret in sample_returns:
            tracker.record_return("test_strategy", ret, date)

        assert len(tracker._returns["test_strategy"]) == 50

        # Check performance calculated
        perf = tracker.get_performance("test_strategy")
        assert perf is not None
        assert perf.strategy_id == "test_strategy"

    def test_performance_metrics_calculation(self, tracker, sample_returns):
        """Test performance metrics are calculated correctly."""
        for date, ret in sample_returns:
            tracker.record_return("test_strategy", ret, date)

        perf = tracker.get_performance("test_strategy")

        # Check all metrics are calculated
        assert perf.total_return != 0.0
        assert perf.sharpe_ratio != 0.0
        assert perf.volatility > 0.0
        assert 0.0 <= perf.win_rate <= 100.0
        assert perf.max_drawdown >= 0.0

    def test_sharpe_ratio_calculation(self, tracker):
        """Test Sharpe ratio calculation."""
        # Create positive returns with known properties
        returns = [0.01] * 50  # 1% daily return, zero volatility
        dates = [datetime.now() - timedelta(days=i) for i in range(49, -1, -1)]

        for date, ret in zip(dates, returns):
            tracker.record_return("test_strategy", ret, date)

        perf = tracker.get_performance("test_strategy")

        # With zero volatility, Sharpe should be 0 (our implementation)
        # or very high (if we added small epsilon)
        assert perf.sharpe_ratio == 0.0

    def test_drawdown_calculation(self, tracker):
        """Test drawdown calculation."""
        # Create returns with known drawdown
        returns = [0.10, -0.05, -0.05, 0.02] * 10  # Pattern with drawdown
        dates = [datetime.now() - timedelta(days=i) for i in range(39, -1, -1)]

        for date, ret in zip(dates, returns):
            tracker.record_return("test_strategy", ret, date)

        perf = tracker.get_performance("test_strategy")

        # Should have some drawdown
        assert perf.max_drawdown > 0.0
        assert perf.current_drawdown >= 0.0

    def test_correlation_matrix(self, tracker):
        """Test correlation matrix calculation."""
        np.random.seed(42)

        # Create two correlated strategies
        base_returns = np.random.normal(0.001, 0.02, 50)
        dates = [datetime.now() - timedelta(days=i) for i in range(49, -1, -1)]

        # Strategy 1: base returns
        for date, ret in zip(dates, base_returns):
            tracker.record_return("strategy_1", ret, date)

        # Strategy 2: correlated with strategy 1
        correlated_returns = base_returns + np.random.normal(0, 0.005, 50)
        for date, ret in zip(dates, correlated_returns):
            tracker.record_return("strategy_2", ret, date)

        # Get correlation matrix
        corr_matrix = tracker.get_correlation_matrix()

        assert not corr_matrix.empty
        assert corr_matrix.shape == (2, 2)

        # Diagonal should be 1.0
        assert corr_matrix.loc["strategy_1", "strategy_1"] == pytest.approx(1.0)
        assert corr_matrix.loc["strategy_2", "strategy_2"] == pytest.approx(1.0)

        # Should be highly correlated
        assert corr_matrix.loc["strategy_1", "strategy_2"] > 0.7

    def test_strategy_correlation(self, tracker):
        """Test pairwise strategy correlation."""
        np.random.seed(42)

        returns = np.random.normal(0.001, 0.02, 50)
        dates = [datetime.now() - timedelta(days=i) for i in range(49, -1, -1)]

        # Record returns for two strategies
        for date, ret in zip(dates, returns):
            tracker.record_return("strategy_1", ret, date)
            tracker.record_return("strategy_2", ret + np.random.normal(0, 0.01), date)

        corr = tracker.get_strategy_correlation("strategy_1", "strategy_2")

        assert corr is not None
        assert -1.0 <= corr <= 1.0

    def test_insufficient_data(self, tracker):
        """Test behavior with insufficient data."""
        # Only 5 returns (below min_history_days of 10)
        for i in range(5):
            tracker.record_return("test_strategy", 0.01, datetime.now() - timedelta(days=i))

        perf = tracker.get_performance("test_strategy")

        # Performance object exists but metrics should be zeros/defaults
        assert perf is not None
        assert perf.sharpe_ratio == 0.0

    def test_lookback_window(self):
        """Test lookback window is enforced."""
        tracker = PerformanceTracker(lookback_days=20, min_history_days=10)

        # Add 30 returns
        for i in range(30):
            tracker.record_return("test_strategy", 0.01, datetime.now() - timedelta(days=i))

        # Should only keep last 20
        assert len(tracker._returns["test_strategy"]) == 20

    def test_multiple_strategies(self, tracker, sample_returns):
        """Test tracking multiple strategies."""
        # Add returns for three strategies
        for date, ret in sample_returns:
            tracker.record_return("strategy_1", ret, date)
            tracker.record_return("strategy_2", ret * 1.5, date)
            tracker.record_return("strategy_3", ret * 0.5, date)

        all_perf = tracker.get_all_performance()

        assert len(all_perf) == 3
        assert "strategy_1" in all_perf
        assert "strategy_2" in all_perf
        assert "strategy_3" in all_perf

    def test_clear_strategy(self, tracker, sample_returns):
        """Test clearing strategy data."""
        for date, ret in sample_returns:
            tracker.record_return("test_strategy", ret, date)

        assert "test_strategy" in tracker._returns

        tracker.clear_strategy("test_strategy")

        assert "test_strategy" not in tracker._returns
        assert "test_strategy" not in tracker._performance

    def test_win_rate_calculation(self, tracker):
        """Test win rate calculation."""
        # 30 positive, 20 negative returns
        returns = [0.01] * 30 + [-0.01] * 20
        dates = [datetime.now() - timedelta(days=i) for i in range(49, -1, -1)]

        for date, ret in zip(dates, returns):
            tracker.record_return("test_strategy", ret, date)

        perf = tracker.get_performance("test_strategy")

        # Win rate should be 60%
        assert perf.win_rate == pytest.approx(60.0, abs=0.1)

    def test_profit_factor_calculation(self, tracker):
        """Test profit factor calculation."""
        # Known wins and losses
        returns = [0.02, 0.03, -0.01, -0.01] * 10
        dates = [datetime.now() - timedelta(days=i) for i in range(39, -1, -1)]

        for date, ret in zip(dates, returns):
            tracker.record_return("test_strategy", ret, date)

        perf = tracker.get_performance("test_strategy")

        # Gross profit = 0.05 * 10 = 0.5
        # Gross loss = 0.02 * 10 = 0.2
        # Profit factor = 0.5 / 0.2 = 2.5
        assert perf.profit_factor == pytest.approx(2.5, abs=0.1)

    def test_sortino_ratio(self, tracker):
        """Test Sortino ratio calculation."""
        # Mix of positive and negative returns
        np.random.seed(42)
        returns = np.random.normal(0.002, 0.02, 50)
        dates = [datetime.now() - timedelta(days=i) for i in range(49, -1, -1)]

        for date, ret in zip(dates, returns):
            tracker.record_return("test_strategy", ret, date)

        perf = tracker.get_performance("test_strategy")

        # Sortino should be calculated (may be different from Sharpe)
        assert perf.sortino_ratio != 0.0
        # For positive returns, Sortino >= Sharpe (uses only downside deviation)
        # For negative returns, Sortino can be < Sharpe
        # Just verify both are calculated
        assert perf.sharpe_ratio != 0.0
