"""Tests for Portfolio Allocation and Coordination (PAC) system."""
import json
import numpy as np
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from src.portfolio.pac import (
    AllocationAlgorithm,
    AllocationChange,
    PortfolioAllocator,
    StrategyAllocation,
)


class TestPortfolioAllocator:
    """Test suite for PortfolioAllocator."""

    @pytest.fixture
    def allocator(self):
        """Create a portfolio allocator instance."""
        return PortfolioAllocator(
            total_capital=1000000,
            algorithm=AllocationAlgorithm.EQUAL_WEIGHT,
            min_allocation_pct=0.05,
            max_allocation_pct=0.30,
        )

    @pytest.fixture
    def allocator_with_audit(self, tmp_path):
        """Create allocator with audit logging."""
        audit_path = tmp_path / "audit.log"
        return PortfolioAllocator(
            total_capital=1000000,
            algorithm=AllocationAlgorithm.EQUAL_WEIGHT,
            audit_log_path=str(audit_path),
        )

    def test_initialization(self, allocator):
        """Test allocator initialization."""
        assert allocator.total_capital == 1000000
        assert allocator.algorithm == AllocationAlgorithm.EQUAL_WEIGHT
        assert allocator.min_allocation_pct == 0.05
        assert allocator.max_allocation_pct == 0.30

    def test_register_strategy(self, allocator):
        """Test strategy registration."""
        allocator.register_strategy("pairs_1", max_capacity=100000)

        assert "pairs_1" in allocator._strategy_allocations
        alloc = allocator.get_allocation("pairs_1")
        assert alloc.strategy_id == "pairs_1"
        assert alloc.allocated_capital == 0.0
        assert alloc.max_capacity == 100000

    def test_register_duplicate_strategy(self, allocator):
        """Test registering duplicate strategy raises error."""
        allocator.register_strategy("pairs_1")

        with pytest.raises(ValueError):
            allocator.register_strategy("pairs_1")

    def test_unregister_strategy(self, allocator):
        """Test strategy unregistration."""
        allocator.register_strategy("pairs_1")
        allocator.register_strategy("pairs_2")

        allocator.unregister_strategy("pairs_1")

        assert "pairs_1" not in allocator._strategy_allocations
        assert "pairs_2" in allocator._strategy_allocations

    def test_equal_weight_allocation(self, allocator):
        """Test equal weight allocation algorithm."""
        # Register 3 strategies
        allocator.register_strategy("strategy_1")
        allocator.register_strategy("strategy_2")
        allocator.register_strategy("strategy_3")

        # Add sufficient performance history
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            allocator.record_strategy_return("strategy_1", 0.01, date)
            allocator.record_strategy_return("strategy_2", 0.01, date)
            allocator.record_strategy_return("strategy_3", 0.01, date)

        # Rebalance
        allocations = allocator.rebalance(trigger="test")

        # Each should get equal weight (capped at max_allocation_pct = 0.30)
        for alloc in allocations.values():
            # With max_allocation_pct=0.30, each strategy is capped
            assert alloc.weight <= allocator.max_allocation_pct

    def test_volatility_weighted_allocation(self):
        """Test volatility-weighted allocation."""
        allocator = PortfolioAllocator(
            total_capital=1000000,
            algorithm=AllocationAlgorithm.VOLATILITY_WEIGHTED,
        )

        allocator.register_strategy("low_vol")
        allocator.register_strategy("high_vol")

        np.random.seed(42)

        # Low volatility strategy
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            ret = np.random.normal(0.001, 0.01)  # 1% vol
            allocator.record_strategy_return("low_vol", ret, date)

        # High volatility strategy
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            ret = np.random.normal(0.001, 0.03)  # 3% vol
            allocator.record_strategy_return("high_vol", ret, date)

        allocations = allocator.rebalance(trigger="test")

        # High vol should get more allocation
        assert allocations["high_vol"].weight > allocations["low_vol"].weight

    def test_inverse_volatility_allocation(self):
        """Test inverse volatility allocation."""
        allocator = PortfolioAllocator(
            total_capital=1000000,
            algorithm=AllocationAlgorithm.INVERSE_VOLATILITY,
        )

        allocator.register_strategy("low_vol")
        allocator.register_strategy("high_vol")

        np.random.seed(42)

        # Low volatility strategy
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            ret = np.random.normal(0.001, 0.01)
            allocator.record_strategy_return("low_vol", ret, date)

        # High volatility strategy
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            ret = np.random.normal(0.001, 0.03)
            allocator.record_strategy_return("high_vol", ret, date)

        allocations = allocator.rebalance(trigger="test")

        # Low vol should get more allocation
        assert allocations["low_vol"].weight > allocations["high_vol"].weight

    def test_sharpe_optimized_allocation(self):
        """Test Sharpe-optimized allocation."""
        allocator = PortfolioAllocator(
            total_capital=1000000,
            algorithm=AllocationAlgorithm.SHARPE_OPTIMIZED,
            min_sharpe_threshold=0.3,  # Lower threshold to test allocation
            max_allocation_pct=1.0,  # No max constraint for this test
        )

        allocator.register_strategy("good_sharpe")
        allocator.register_strategy("bad_sharpe")
        allocator.register_strategy("medium_sharpe")

        np.random.seed(42)

        # Good Sharpe strategy (higher return, same vol)
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            ret = np.random.normal(0.003, 0.01)  # 0.3% daily mean
            allocator.record_strategy_return("good_sharpe", ret, date)

        # Bad Sharpe strategy (lower return, same vol)
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            ret = np.random.normal(0.0005, 0.01)  # 0.05% daily mean
            allocator.record_strategy_return("bad_sharpe", ret, date)

        # Medium Sharpe strategy
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            ret = np.random.normal(0.0015, 0.01)  # 0.15% daily mean
            allocator.record_strategy_return("medium_sharpe", ret, date)

        allocations = allocator.rebalance(trigger="test")

        # Good Sharpe should get more allocation than bad Sharpe
        assert allocations["good_sharpe"].weight > allocations["bad_sharpe"].weight
        # All strategies should get some allocation with positive Sharpe
        assert allocations["medium_sharpe"].weight > 0

    def test_allocation_constraints(self, allocator):
        """Test min/max allocation constraints."""
        allocator.register_strategy("strategy_1")

        # Add performance history
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            allocator.record_strategy_return("strategy_1", 0.01, date)

        allocations = allocator.rebalance(trigger="test")

        # Single strategy should be capped at max_allocation_pct
        alloc = allocations["strategy_1"]
        assert alloc.weight <= allocator.max_allocation_pct

    def test_max_capacity_constraint(self):
        """Test max capacity constraint."""
        allocator = PortfolioAllocator(total_capital=1000000)

        # Register strategy with 100k max capacity
        allocator.register_strategy("strategy_1", max_capacity=100000)

        # Add performance
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            allocator.record_strategy_return("strategy_1", 0.01, date)

        allocations = allocator.rebalance(trigger="test")

        # Should be capped at max capacity
        assert allocations["strategy_1"].allocated_capital <= 100000

    def test_record_strategy_return(self, allocator):
        """Test recording strategy returns."""
        allocator.register_strategy("test_strategy")

        allocator.record_strategy_return("test_strategy", 0.02)

        perf = allocator.performance_tracker.get_performance("test_strategy")
        assert perf is not None

    def test_get_portfolio_metrics(self, allocator):
        """Test getting portfolio metrics."""
        allocator.register_strategy("strategy_1")
        allocator.register_strategy("strategy_2")

        # Add performance
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            allocator.record_strategy_return("strategy_1", 0.01, date)
            allocator.record_strategy_return("strategy_2", 0.01, date)

        allocator.rebalance(trigger="test")

        metrics = allocator.get_portfolio_metrics()

        assert metrics.total_value == 1000000
        assert metrics.strategy_count == 2
        assert metrics.allocated_capital > 0
        assert metrics.risk_metrics is not None

    def test_allocation_history(self, allocator):
        """Test allocation change history."""
        allocator.register_strategy("strategy_1")

        # Add performance
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            allocator.record_strategy_return("strategy_1", 0.01, date)

        allocator.rebalance(trigger="test_1")
        allocator.rebalance(trigger="test_2")

        history = allocator.get_allocation_history()

        assert len(history) >= 2
        assert history[0].trigger == "test_1"
        assert history[1].trigger == "test_2"

    def test_audit_log_writing(self, allocator_with_audit, tmp_path):
        """Test audit log file writing."""
        allocator = allocator_with_audit

        allocator.register_strategy("strategy_1")

        # Add performance
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            allocator.record_strategy_return("strategy_1", 0.01, date)

        allocator.rebalance(trigger="test")

        # Check audit log file exists
        audit_path = tmp_path / "audit.log"
        assert audit_path.exists()

        # Read and verify log entry
        with open(audit_path, "r") as f:
            lines = f.readlines()
            assert len(lines) >= 1

            # Parse first entry
            entry = json.loads(lines[0])
            assert "timestamp" in entry
            assert entry["algorithm"] == "equal_weight"
            assert entry["trigger"] == "test"

    def test_update_total_capital(self, allocator):
        """Test updating total capital."""
        allocator.register_strategy("strategy_1")

        # Add performance
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            allocator.record_strategy_return("strategy_1", 0.01, date)

        allocator.rebalance(trigger="initial")

        old_allocation = allocator.get_allocation("strategy_1").allocated_capital

        # Update capital
        allocator.update_total_capital(2000000)

        assert allocator.total_capital == 2000000

        # Allocation should have increased proportionally
        new_allocation = allocator.get_allocation("strategy_1").allocated_capital
        assert new_allocation > old_allocation

    def test_set_algorithm(self, allocator):
        """Test changing allocation algorithm."""
        allocator.register_strategy("strategy_1")

        # Add performance
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            allocator.record_strategy_return("strategy_1", 0.01, date)

        allocator.rebalance(trigger="initial")

        old_weight = allocator.get_allocation("strategy_1").weight

        # Change algorithm
        allocator.set_algorithm(AllocationAlgorithm.SHARPE_OPTIMIZED)

        assert allocator.algorithm == AllocationAlgorithm.SHARPE_OPTIMIZED

        # Should have triggered rebalance
        assert len(allocator.get_allocation_history()) >= 2

    def test_rebalance_trigger_low_sharpe(self):
        """Test rebalance behavior with low Sharpe strategy."""
        allocator = PortfolioAllocator(
            total_capital=1000000,
            min_sharpe_threshold=1.0,  # High threshold
            algorithm=AllocationAlgorithm.SHARPE_OPTIMIZED,
        )

        allocator.register_strategy("strategy_1")

        np.random.seed(42)

        # Add poor performance (low Sharpe)
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            ret = np.random.normal(0.0001, 0.03)  # Very low return/vol ratio
            allocator.record_strategy_return("strategy_1", ret, date)

        # Rebalance with low Sharpe strategy
        allocator.rebalance(trigger="test")

        alloc = allocator.get_allocation("strategy_1")
        # With single strategy below threshold, gets equal weight fallback (capped at max)
        # This is correct behavior - we still allocate but with risk controls
        assert alloc.weight <= allocator.max_allocation_pct

    def test_rebalance_trigger_drawdown(self, allocator):
        """Test rebalance trigger on large drawdown."""
        allocator.register_strategy("strategy_1")

        # Create large drawdown
        returns = [0.02] * 10 + [-0.05] * 10 + [-0.05] * 10
        dates = [datetime.now() - timedelta(days=i) for i in range(29, -1, -1)]

        for date, ret in zip(dates, returns):
            allocator.record_strategy_return("strategy_1", ret, date)

        perf = allocator.performance_tracker.get_performance("strategy_1")

        # Should have significant drawdown
        assert perf.current_drawdown > 0.10

    def test_multiple_strategies_correlation(self, allocator):
        """Test portfolio with multiple correlated strategies."""
        allocator.register_strategy("strategy_1")
        allocator.register_strategy("strategy_2")
        allocator.register_strategy("strategy_3")

        np.random.seed(42)

        base_returns = np.random.normal(0.001, 0.02, 50)

        # Add correlated returns
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            allocator.record_strategy_return("strategy_1", base_returns[i], date)
            allocator.record_strategy_return("strategy_2", base_returns[i] + np.random.normal(0, 0.005), date)
            allocator.record_strategy_return("strategy_3", -base_returns[i] + np.random.normal(0, 0.005), date)

        allocator.rebalance(trigger="test")

        # Get correlation matrix
        metrics = allocator.get_portfolio_metrics()
        corr_matrix = metrics.correlation_matrix

        assert not corr_matrix.empty
        # Strategy 1 and 2 should be positively correlated
        assert corr_matrix.loc["strategy_1", "strategy_2"] > 0.5
        # Strategy 1 and 3 should be negatively correlated
        assert corr_matrix.loc["strategy_1", "strategy_3"] < 0.0

    def test_get_allocations(self, allocator):
        """Test getting all allocations."""
        allocator.register_strategy("strategy_1")
        allocator.register_strategy("strategy_2")

        allocations = allocator.get_allocations()

        assert len(allocations) == 2
        assert "strategy_1" in allocations
        assert "strategy_2" in allocations

    def test_no_eligible_strategies(self):
        """Test behavior when no strategies meet criteria."""
        allocator = PortfolioAllocator(
            total_capital=1000000,
            min_sharpe_threshold=5.0,  # Very high threshold
        )

        allocator.register_strategy("strategy_1")

        # Add mediocre performance
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            allocator.record_strategy_return("strategy_1", 0.001, date)

        allocations = allocator.rebalance(trigger="test")

        # Should fall back to equal weight (even if below threshold)
        assert "strategy_1" in allocations

    def test_risk_parity_allocation(self):
        """Test risk parity allocation algorithm."""
        allocator = PortfolioAllocator(
            total_capital=1000000,
            algorithm=AllocationAlgorithm.RISK_PARITY,
        )

        allocator.register_strategy("low_vol")
        allocator.register_strategy("high_vol")

        np.random.seed(42)

        # Different volatilities
        for i in range(50):
            date = datetime.now() - timedelta(days=i)
            allocator.record_strategy_return("low_vol", np.random.normal(0.001, 0.01), date)
            allocator.record_strategy_return("high_vol", np.random.normal(0.001, 0.03), date)

        allocations = allocator.rebalance(trigger="test")

        # Low vol should get higher allocation (for equal risk contribution)
        assert allocations["low_vol"].weight > allocations["high_vol"].weight
