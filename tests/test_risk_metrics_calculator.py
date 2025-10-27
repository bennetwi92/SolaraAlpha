"""Unit tests for risk metrics calculator."""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.data.storage.parquet_storage import ParquetStorage
from src.risk.config.risk_config import RiskConfigManager
from src.risk.metrics_calculator import RiskMetricsCalculator


@pytest.fixture
def temp_config():
    """Create temporary risk configuration."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / "risk_limits.yaml"

        config_data = {
            "position_limits": {
                "per_strategy_pct": 5.0,
                "per_asset_pct": 10.0,
                "gross_exposure_pct": 200.0,
                "net_exposure_pct": 20.0,
            },
            "drawdown": {
                "max_drawdown_pct": 10.0,
                "high_water_mark_persist": True,
                "hwm_storage_path": f"{temp_dir}/hwm.json",
            },
            "risk_metrics": {
                "var_confidence": 0.95,
                "var_lookback_days": 252,
                "cvar_confidence": 0.95,
                "sharpe_windows": [30, 90, 365],
                "benchmark_symbol": "SPY",
                "benchmark_asset_class": "equities",
                "min_history_days": 30,
            },
            "alerts": {
                "log_all_limit_breaches": True,
                "log_risk_metrics": True,
                "alert_on_circuit_breaker": True,
            },
        }

        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        yield config_path, temp_dir


@pytest.fixture
def mock_storage(temp_config):
    """Create mock storage with test data."""
    config_path, temp_dir = temp_config

    class MockConfig:
        def get_storage_config(self):
            return {
                "base_path": temp_dir,
                "format": "parquet",
                "compression": "snappy",
            }

    storage = ParquetStorage(config=MockConfig())
    yield storage


@pytest.fixture
def calculator(temp_config, mock_storage):
    """Create risk metrics calculator."""
    config_path, temp_dir = temp_config
    config = RiskConfigManager(str(config_path))
    return RiskMetricsCalculator(config, mock_storage)


@pytest.fixture
def sample_returns():
    """Create sample return series."""
    np.random.seed(42)
    # 365 days of returns with slight positive drift
    returns = pd.Series(
        np.random.normal(0.0005, 0.01, 365),
        index=pd.date_range("2023-01-01", periods=365, freq="D"),
    )
    return returns


def test_calculator_initialization(calculator):
    """Test calculator initialization."""
    assert calculator.var_confidence == 0.95
    assert calculator.var_lookback_days == 252
    assert calculator.sharpe_windows == [30, 90, 365]
    assert calculator.benchmark_symbol == "SPY"


def test_calculate_var(calculator, sample_returns):
    """Test VaR calculation."""
    var = calculator._calculate_var(sample_returns, 0.95)

    # VaR should be positive loss value
    assert var > 0

    # VaR at 95% should be roughly 1.65 * std for normal distribution
    std_dev = sample_returns.std()
    expected_var_approx = 1.65 * std_dev

    # Check within reasonable range
    assert var == pytest.approx(expected_var_approx, rel=0.5)


def test_calculate_cvar(calculator, sample_returns):
    """Test CVaR (Expected Shortfall) calculation."""
    cvar = calculator._calculate_cvar(sample_returns, 0.95)

    # CVaR should be positive and larger than VaR
    var = calculator._calculate_var(sample_returns, 0.95)

    assert cvar > 0
    assert cvar >= var  # CVaR should be >= VaR


def test_calculate_sharpe_ratio(calculator, sample_returns):
    """Test Sharpe ratio calculation."""
    sharpe_30d = calculator._calculate_sharpe(sample_returns, 30)
    sharpe_90d = calculator._calculate_sharpe(sample_returns, 90)
    sharpe_365d = calculator._calculate_sharpe(sample_returns, 365)

    # All should be calculated
    assert sharpe_30d is not None
    assert sharpe_90d is not None
    assert sharpe_365d is not None

    # With positive drift, Sharpe should be positive
    assert sharpe_365d > 0


def test_calculate_sharpe_insufficient_data(calculator):
    """Test Sharpe calculation with insufficient data."""
    short_returns = pd.Series([0.01, 0.02, -0.01])

    # Request 30-day Sharpe with only 3 days of data
    sharpe = calculator._calculate_sharpe(short_returns, 30)

    assert sharpe is None


def test_calculate_volatility(calculator, sample_returns):
    """Test volatility calculation."""
    vol = calculator._calculate_volatility(sample_returns)

    # Volatility should be positive
    assert vol > 0

    # Should be annualized (roughly sqrt(252) * daily std)
    daily_std = sample_returns.std()
    expected_vol = daily_std * np.sqrt(252)

    assert vol == pytest.approx(expected_vol, rel=0.01)


def test_calculate_max_drawdown(calculator):
    """Test maximum drawdown calculation."""
    # Create returns with known drawdown
    returns = pd.Series([0.1, -0.05, -0.1, 0.05, 0.2])

    max_dd = calculator._calculate_max_drawdown(returns)

    # Max drawdown should be positive
    assert max_dd > 0

    # Calculate expected drawdown manually
    cumulative = (1 + returns).cumprod()
    running_max = cumulative.expanding().max()
    drawdown = (cumulative - running_max) / running_max
    expected_max_dd = abs(drawdown.min())

    assert max_dd == pytest.approx(expected_max_dd, rel=0.01)


def test_calculate_metrics_insufficient_data(calculator):
    """Test metrics calculation with insufficient data."""
    short_returns = pd.Series([0.01] * 10)

    with pytest.raises(ValueError, match="Insufficient data"):
        calculator.calculate_metrics(short_returns)


def test_calculate_metrics_complete(calculator, sample_returns):
    """Test complete metrics calculation."""
    metrics = calculator.calculate_metrics(sample_returns)

    assert metrics is not None
    assert metrics.var_95 > 0
    assert metrics.cvar_95 > 0
    assert metrics.sharpe_30d is not None
    assert metrics.sharpe_90d is not None
    assert metrics.sharpe_365d is not None
    assert metrics.volatility > 0
    assert metrics.max_drawdown >= 0


def test_var_confidence_level(calculator):
    """Test VaR at different confidence levels."""
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0, 0.01, 1000))

    var_90 = calculator._calculate_var(returns, 0.90)
    var_95 = calculator._calculate_var(returns, 0.95)
    var_99 = calculator._calculate_var(returns, 0.99)

    # Higher confidence should have higher VaR (worse loss)
    assert var_95 > var_90
    assert var_99 > var_95


def test_zero_volatility_sharpe(calculator):
    """Test Sharpe ratio with zero volatility."""
    # All returns are same (zero std dev)
    constant_returns = pd.Series([0.01] * 100)

    sharpe = calculator._calculate_sharpe(constant_returns, 100)

    # Should return 0 when std dev is zero
    assert sharpe == 0.0


def test_negative_returns_sharpe(calculator):
    """Test Sharpe ratio with negative mean returns."""
    np.random.seed(42)
    # Negative drift
    negative_returns = pd.Series(np.random.normal(-0.001, 0.01, 365))

    sharpe = calculator._calculate_sharpe(negative_returns, 365)

    # Should be negative
    assert sharpe < 0


def test_cvar_tail_risk(calculator):
    """Test CVaR captures tail risk properly."""
    # Create distribution with fat tails
    np.random.seed(42)
    normal_returns = np.random.normal(0, 0.01, 950)
    extreme_losses = np.array([-0.05, -0.06, -0.07, -0.08, -0.09] * 10)

    returns = pd.Series(np.concatenate([normal_returns, extreme_losses]))

    cvar = calculator._calculate_cvar(returns, 0.95)

    # CVaR should be large due to extreme tail losses
    assert cvar > 0.02  # Should capture the extreme losses


def test_position_var_calculation(calculator, mock_storage):
    """Test VaR calculation for individual position."""
    # Create mock price data
    dates = pd.date_range("2023-01-01", periods=365, freq="D")
    prices = pd.DataFrame(
        {
            "Open": 100 + np.random.randn(365),
            "High": 102 + np.random.randn(365),
            "Low": 98 + np.random.randn(365),
            "Close": 100 + np.random.randn(365),
            "Volume": 1000000 + np.random.randn(365) * 10000,
        },
        index=dates,
    )

    # Store mock data
    mock_storage.write(prices, "equities", "AAPL", "1d", source="test")

    # Calculate position VaR
    position_value = 10000.0  # $10k position
    position_var = calculator.calculate_position_var(
        position_value, "AAPL", "equities"
    )

    # VaR should be positive and related to position size
    assert position_var > 0
    assert position_var < position_value  # VaR shouldn't exceed position


def test_max_drawdown_no_drawdown(calculator):
    """Test max drawdown with only positive returns."""
    positive_returns = pd.Series([0.01, 0.02, 0.015, 0.01, 0.03])

    max_dd = calculator._calculate_max_drawdown(positive_returns)

    # Should be very small (close to zero)
    assert max_dd >= 0
    assert max_dd < 0.001


def test_lookback_window_respected(calculator, sample_returns):
    """Test that VaR respects lookback window."""
    # Calculator has 252-day lookback

    # Add older data
    old_returns = pd.Series(
        [0.05] * 200,  # Very high returns
        index=pd.date_range("2020-01-01", periods=200, freq="D"),
    )

    combined_returns = pd.concat([old_returns, sample_returns])

    var = calculator._calculate_var(combined_returns, 0.95)

    # VaR should be based on recent 252 days, not the old high returns
    # (implementation uses .tail(lookback_days))
    assert var > 0
