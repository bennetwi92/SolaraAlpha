"""Tests for backtesting framework."""
import numpy as np
import pandas as pd
import pytest

from src.strategies.pairs_trading.backtest import (
    BacktestConfig,
    BacktestResult,
    PairsBacktester,
    Trade,
)


@pytest.fixture
def cointegrated_series():
    """Generate cointegrated test series."""
    np.random.seed(42)
    n = 252  # 1 year

    # Generate base random walk for series2
    series2 = pd.Series(
        100 + np.cumsum(np.random.randn(n) * 0.5),
        index=pd.date_range("2023-01-01", periods=n),
    )

    hedge_ratio = 1.5
    # Create cointegrated series with mean-reverting spread
    # Use OU process for the spread: dx = theta * (mu - x) * dt + sigma * dW
    spread = np.zeros(n)
    theta = 0.5  # Mean reversion speed
    mu = 0  # Long-term mean
    sigma = 0.5  # Volatility
    dt = 1.0

    for i in range(1, n):
        spread[i] = spread[i-1] + theta * (mu - spread[i-1]) * dt + \
                   sigma * np.random.randn() * np.sqrt(dt)

    # Construct series1 from series2 and mean-reverting spread
    series1 = pd.Series(
        hedge_ratio * series2.values + spread,
        index=series2.index,
    )

    return series1, series2, hedge_ratio


class TestBacktestConfig:
    """Test suite for BacktestConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = BacktestConfig()

        assert config.train_pct == 0.6
        assert config.lookback_window == 60
        assert config.reestimate_every == 20
        assert config.commission_pct == 0.001
        assert config.slippage_pct == 0.0005
        assert config.initial_capital == 100000.0

    def test_custom_config(self):
        """Test custom configuration."""
        config = BacktestConfig(
            train_pct=0.7,
            lookback_window=90,
            initial_capital=50000,
        )

        assert config.train_pct == 0.7
        assert config.lookback_window == 90
        assert config.initial_capital == 50000


class TestTrade:
    """Test suite for Trade dataclass."""

    def test_trade_creation(self):
        """Test trade record creation."""
        trade = Trade(
            timestamp=pd.Timestamp("2023-06-01"),
            symbol1="AAPL",
            symbol2="MSFT",
            quantity1=10,
            quantity2=-5,
            price1=150.0,
            price2=300.0,
            signal=1,
            pnl=100.0,
            costs=5.0,
        )

        assert trade.symbol1 == "AAPL"
        assert trade.quantity1 == 10
        assert trade.pnl == 100.0


class TestPairsBacktester:
    """Test suite for PairsBacktester."""

    def test_initialization(self):
        """Test backtester initialization."""
        config = BacktestConfig(initial_capital=50000)
        backtester = PairsBacktester(config)

        assert backtester.config.initial_capital == 50000
        assert backtester.coint_tester is not None
        assert backtester.signal_generator is not None

    def test_split_train_test(self, cointegrated_series):
        """Test train/test splitting."""
        series1, series2, _ = cointegrated_series
        data = pd.DataFrame({"series1": series1, "series2": series2})

        backtester = PairsBacktester()
        train, test = backtester.split_train_test(data)

        assert len(train) + len(test) == len(data)
        assert len(train) == int(len(data) * 0.6)
        # Verify temporal ordering
        assert train.index[-1] < test.index[0]

    def test_estimate_parameters(self, cointegrated_series):
        """Test parameter estimation."""
        series1, series2, _ = cointegrated_series
        backtester = PairsBacktester()

        params = backtester.estimate_parameters(series1, series2)

        if params is not None:
            assert "hedge_ratio" in params
            assert "p_value" in params
            assert "is_cointegrated" in params
            assert params["is_cointegrated"] is True

    def test_estimate_parameters_non_cointegrated(self):
        """Test parameter estimation on non-cointegrated series."""
        np.random.seed(42)
        n = 252

        # Independent random walks
        series1 = pd.Series(
            100 + np.cumsum(np.random.randn(n)),
            index=pd.date_range("2023-01-01", periods=n),
        )
        series2 = pd.Series(
            50 + np.cumsum(np.random.randn(n)),
            index=pd.date_range("2023-01-01", periods=n),
        )

        backtester = PairsBacktester()
        params = backtester.estimate_parameters(series1, series2)

        # May return None if not cointegrated
        # (not guaranteed due to randomness)
        assert params is None or isinstance(params, dict)

    def test_simulate_trade_costs(self):
        """Test transaction cost calculation."""
        backtester = PairsBacktester()

        costs = backtester.simulate_trade_costs(10000)

        expected_commission = 10000 * 0.001
        expected_slippage = 10000 * 0.0005
        expected_total = expected_commission + expected_slippage

        assert abs(costs - expected_total) < 0.01

    def test_backtest_pair(self, cointegrated_series):
        """Test full backtest on pair."""
        series1, series2, _ = cointegrated_series
        backtester = PairsBacktester()

        result = backtester.backtest_pair(
            series1, series2, "TEST1", "TEST2"
        )

        assert isinstance(result, BacktestResult)
        assert isinstance(result.trades, list)
        assert isinstance(result.equity_curve, pd.Series)
        assert isinstance(result.returns, pd.Series)
        assert isinstance(result.metrics, dict)

    def test_backtest_metrics(self, cointegrated_series):
        """Test that backtest produces valid metrics."""
        series1, series2, _ = cointegrated_series
        backtester = PairsBacktester()

        result = backtester.backtest_pair(
            series1, series2, "TEST1", "TEST2"
        )

        metrics = result.metrics

        # Check all expected metrics present
        assert "total_return" in metrics
        assert "sharpe_ratio" in metrics
        assert "max_drawdown" in metrics
        assert "win_rate" in metrics
        assert "total_trades" in metrics
        assert "avg_trade" in metrics
        assert "profit_factor" in metrics

        # Validate metric ranges
        assert 0 <= metrics["win_rate"] <= 1
        assert metrics["max_drawdown"] <= 0

    def test_backtest_equity_curve(self, cointegrated_series):
        """Test equity curve generation."""
        series1, series2, _ = cointegrated_series
        backtester = PairsBacktester()

        result = backtester.backtest_pair(
            series1, series2, "TEST1", "TEST2"
        )

        equity = result.equity_curve

        # Equity curve should exist
        assert len(equity) > 0
        # Should start at initial capital
        assert equity.iloc[0] == backtester.config.initial_capital
        # All values should be positive
        assert (equity > 0).all()

    def test_backtest_parameter_reestimation(self, cointegrated_series):
        """Test parameter re-estimation during walk-forward."""
        series1, series2, _ = cointegrated_series

        config = BacktestConfig(reestimate_every=10)
        backtester = PairsBacktester(config)

        result = backtester.backtest_pair(
            series1, series2, "TEST1", "TEST2"
        )

        # Parameter history should contain re-estimations
        if not result.parameter_history.empty:
            assert "hedge_ratio" in result.parameter_history.columns
            assert len(result.parameter_history) > 0

    def test_backtest_multiple_pairs(self, cointegrated_series):
        """Test backtesting multiple pairs."""
        series1, series2, _ = cointegrated_series

        # Create second pair (slightly modified)
        series3 = series1 * 1.1
        series4 = series2 * 0.9

        pairs = [
            (series1, series2, "PAIR1_A", "PAIR1_B"),
            (series3, series4, "PAIR2_A", "PAIR2_B"),
        ]

        backtester = PairsBacktester()
        results = backtester.backtest_multiple_pairs(pairs)

        assert isinstance(results, dict)
        assert len(results) == 2
        assert "PAIR1_A/PAIR1_B" in results
        assert "PAIR2_A/PAIR2_B" in results

    def test_transaction_costs_impact(self, cointegrated_series):
        """Test that transaction costs reduce returns."""
        series1, series2, _ = cointegrated_series

        # Backtest with no costs
        config_no_cost = BacktestConfig(
            commission_pct=0.0, slippage_pct=0.0
        )
        backtester_no_cost = PairsBacktester(config_no_cost)
        result_no_cost = backtester_no_cost.backtest_pair(
            series1, series2, "TEST1", "TEST2"
        )

        # Backtest with costs
        config_with_cost = BacktestConfig(
            commission_pct=0.01, slippage_pct=0.01
        )
        backtester_with_cost = PairsBacktester(config_with_cost)
        result_with_cost = backtester_with_cost.backtest_pair(
            series1, series2, "TEST1", "TEST2"
        )

        # With costs should have lower returns (if trades executed)
        if result_no_cost.metrics["total_trades"] > 0:
            assert (
                result_with_cost.metrics["total_return"]
                <= result_no_cost.metrics["total_return"]
            )

    def test_walk_forward_validation(self, cointegrated_series):
        """Test that walk-forward uses only past data."""
        series1, series2, _ = cointegrated_series

        backtester = PairsBacktester()
        result = backtester.backtest_pair(
            series1, series2, "TEST1", "TEST2"
        )

        # Equity curve should only cover test period
        data = pd.DataFrame({"series1": series1, "series2": series2})
        _, test_data = backtester.split_train_test(data)

        # Equity curve length should match test period
        assert len(result.equity_curve) <= len(test_data)

    def test_insufficient_data_handling(self):
        """Test handling of insufficient data."""
        # Very short series
        series1 = pd.Series(
            range(30), index=pd.date_range("2023-01-01", periods=30)
        )
        series2 = pd.Series(
            range(30), index=pd.date_range("2023-01-01", periods=30)
        )

        backtester = PairsBacktester()
        result = backtester.backtest_pair(
            series1, series2, "TEST1", "TEST2"
        )

        # Should return empty or minimal result
        assert isinstance(result, BacktestResult)
        # May have no trades due to insufficient data
        assert isinstance(result.trades, list)

    def test_returns_calculation(self, cointegrated_series):
        """Test that returns are correctly calculated."""
        series1, series2, _ = cointegrated_series
        backtester = PairsBacktester()

        result = backtester.backtest_pair(
            series1, series2, "TEST1", "TEST2"
        )

        if len(result.equity_curve) > 1:
            # Returns should be equity percentage changes
            expected_returns = result.equity_curve.pct_change().dropna()
            pd.testing.assert_series_equal(
                result.returns, expected_returns, check_names=False
            )
