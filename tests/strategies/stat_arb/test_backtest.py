"""Tests for statistical arbitrage backtest."""

import numpy as np
import pandas as pd
import pytest

from src.strategies.stat_arb.backtest import StatArbBacktester, BacktestConfig


@pytest.fixture
def sample_prices():
    """Generate sample price data."""
    np.random.seed(42)
    n_periods = 200
    n_stocks = 30

    # Generate simple returns
    returns = np.random.randn(n_periods, n_stocks) * 0.01

    # Convert to prices
    prices = np.zeros((n_periods, n_stocks))
    prices[0] = 100
    for t in range(1, n_periods):
        prices[t] = prices[t - 1] * (1 + returns[t])

    dates = pd.date_range("2023-01-01", periods=n_periods, freq="D")
    symbols = [f"STOCK{i:02d}" for i in range(n_stocks)]

    return pd.DataFrame(prices, index=dates, columns=symbols)


def test_backtest_config():
    """Test backtest configuration."""
    config = BacktestConfig(
        portfolio_value=100_000, n_factors=3, target_positions=20
    )

    assert config.portfolio_value == 100_000
    assert config.n_factors == 3
    assert config.target_positions == 20


def test_backtest_initialization():
    """Test backtester initialization."""
    config = BacktestConfig()
    backtester = StatArbBacktester(config)

    assert backtester.config == config
    assert len(backtester.trades) == 0
    assert len(backtester.equity) == 0


def test_backtest_execution(sample_prices):
    """Test backtest runs without errors."""
    config = BacktestConfig(
        portfolio_value=100_000,
        n_factors=3,
        target_positions=20,
        min_positions=10,
        entry_threshold=1.5,
    )

    backtester = StatArbBacktester(config)
    result = backtester.backtest(sample_prices.iloc[100:])

    assert result is not None
    assert hasattr(result, "trades")
    assert hasattr(result, "equity_curve")
    assert hasattr(result, "returns")
    assert hasattr(result, "metrics")


def test_backtest_metrics(sample_prices):
    """Test backtest produces metrics."""
    config = BacktestConfig(
        n_factors=3, target_positions=20, min_positions=10, entry_threshold=1.5
    )

    backtester = StatArbBacktester(config)
    result = backtester.backtest(sample_prices.iloc[100:])

    assert "sharpe_ratio" in result.metrics
    assert "total_return" in result.metrics
    assert "max_drawdown" in result.metrics
    assert "num_trades" in result.metrics
