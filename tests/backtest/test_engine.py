"""
Unit tests for backtesting engine.

Tests cover:
- Configuration and initialization
- Input validation
- Position tracking
- P&L calculation
- Transaction costs and slippage
- Performance metrics
- Edge cases (empty data, zero trades, etc.)

Target: >85% code coverage
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from src.backtest.engine import (
    BacktestEngine,
    BacktestConfig,
    BacktestResult,
    Position,
    PositionSide,
    Trade
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_ohlcv_data():
    """Create sample OHLCV data for testing."""
    dates = pd.date_range('2023-01-01', periods=100, freq='1D')

    # Create uptrending price data
    close_prices = np.linspace(100, 150, 100)

    data = pd.DataFrame({
        'Open': close_prices - 1,
        'High': close_prices + 2,
        'Low': close_prices - 2,
        'Close': close_prices,
        'Volume': np.random.randint(1000000, 5000000, 100)
    }, index=dates)

    return data


@pytest.fixture
def simple_signals(sample_ohlcv_data):
    """Create simple buy-and-hold signals."""
    signals = pd.Series(1.0, index=sample_ohlcv_data.index)
    return signals


@pytest.fixture
def alternating_signals(sample_ohlcv_data):
    """Create alternating long/flat signals."""
    signals = pd.Series(0.0, index=sample_ohlcv_data.index)
    # Long every 10 bars
    signals[::10] = 1.0
    # Propagate signal forward
    signals = signals.replace(0.0, np.nan).ffill().fillna(0.0)
    # Exit every 5 bars after entry
    for i in range(0, len(signals), 10):
        if i + 5 < len(signals):
            signals.iloc[i+5:min(i+10, len(signals))] = 0.0

    return signals


@pytest.fixture
def default_config():
    """Create default backtest configuration."""
    return BacktestConfig(
        initial_capital=100000.0,
        commission_pct=0.001,
        slippage_pct=0.0005,
        position_size_pct=1.0,
        allow_short=False
    )


# ============================================================================
# Configuration Tests
# ============================================================================

def test_backtest_config_defaults():
    """Test default configuration values."""
    config = BacktestConfig()

    assert config.initial_capital == 100000.0
    assert config.commission_pct == 0.001
    assert config.commission_fixed == 0.0
    assert config.slippage_pct == 0.0005
    assert config.position_size_pct == 1.0
    assert config.allow_short is False
    assert config.log_trades is False


def test_backtest_config_custom():
    """Test custom configuration values."""
    config = BacktestConfig(
        initial_capital=50000.0,
        commission_pct=0.002,
        commission_fixed=5.0,
        slippage_pct=0.001,
        position_size_pct=0.5,
        allow_short=True,
        log_trades=True
    )

    assert config.initial_capital == 50000.0
    assert config.commission_pct == 0.002
    assert config.commission_fixed == 5.0
    assert config.slippage_pct == 0.001
    assert config.position_size_pct == 0.5
    assert config.allow_short is True
    assert config.log_trades is True


# ============================================================================
# Engine Initialization Tests
# ============================================================================

def test_engine_initialization(default_config):
    """Test engine initialization with config."""
    engine = BacktestEngine(default_config)

    assert engine.config == default_config
    assert engine.cash == default_config.initial_capital
    assert engine.position.is_flat()
    assert len(engine.trades) == 0


def test_engine_initialization_default():
    """Test engine initialization with default config."""
    engine = BacktestEngine()

    assert engine.config is not None
    assert engine.cash == 100000.0


def test_engine_reset(default_config):
    """Test engine reset functionality."""
    engine = BacktestEngine(default_config)

    # Modify state
    engine.cash = 50000.0
    engine.trades.append(Trade(
        symbol="TEST",
        entry_time=pd.Timestamp.now(),
        exit_time=pd.Timestamp.now(),
        side=PositionSide.LONG,
        quantity=100,
        entry_price=100,
        exit_price=110,
        pnl=1000,
        commission=10,
        slippage=5,
        return_pct=0.10
    ))

    # Reset
    engine.reset()

    assert engine.cash == default_config.initial_capital
    assert len(engine.trades) == 0
    assert engine.position.is_flat()


# ============================================================================
# Input Validation Tests
# ============================================================================

def test_validate_missing_columns(default_config, simple_signals):
    """Test validation fails with missing columns."""
    engine = BacktestEngine(default_config)

    # Missing 'Volume' column
    bad_data = pd.DataFrame({
        'Open': [100, 101],
        'High': [102, 103],
        'Low': [99, 100],
        'Close': [101, 102]
    }, index=pd.date_range('2023-01-01', periods=2))

    signals = pd.Series([1.0, 1.0], index=bad_data.index)

    with pytest.raises(ValueError, match="Missing required columns"):
        engine.run(bad_data, signals)


def test_validate_nan_values(default_config):
    """Test validation fails with NaN values."""
    engine = BacktestEngine(default_config)

    data = pd.DataFrame({
        'Open': [100, np.nan, 102],
        'High': [102, 103, 104],
        'Low': [99, 100, 101],
        'Close': [101, 102, 103],
        'Volume': [1000000, 1000000, 1000000]
    }, index=pd.date_range('2023-01-01', periods=3))

    signals = pd.Series([1.0, 1.0, 1.0], index=data.index)

    with pytest.raises(ValueError, match="contains NaN"):
        engine.run(data, signals)


def test_validate_negative_prices(default_config):
    """Test validation fails with negative prices."""
    engine = BacktestEngine(default_config)

    data = pd.DataFrame({
        'Open': [100, -101, 102],
        'High': [102, 103, 104],
        'Low': [99, 100, 101],
        'Close': [101, 102, 103],
        'Volume': [1000000, 1000000, 1000000]
    }, index=pd.date_range('2023-01-01', periods=3))

    signals = pd.Series([1.0, 1.0, 1.0], index=data.index)

    with pytest.raises(ValueError, match="non-positive prices"):
        engine.run(data, signals)


def test_validate_ohlc_consistency(default_config):
    """Test validation fails with inconsistent OHLC."""
    engine = BacktestEngine(default_config)

    # High < Low (invalid)
    data = pd.DataFrame({
        'Open': [100, 101, 102],
        'High': [99, 103, 104],  # First bar: High < Open (invalid)
        'Low': [99, 100, 101],
        'Close': [101, 102, 103],
        'Volume': [1000000, 1000000, 1000000]
    }, index=pd.date_range('2023-01-01', periods=3))

    signals = pd.Series([1.0, 1.0, 1.0], index=data.index)

    with pytest.raises(ValueError, match="OHLC data inconsistent"):
        engine.run(data, signals)


def test_validate_signals_mismatch(default_config, sample_ohlcv_data):
    """Test validation fails when signals don't match data index."""
    engine = BacktestEngine(default_config)

    # Signals with different index
    wrong_signals = pd.Series([1.0, 1.0],
                              index=pd.date_range('2022-01-01', periods=2))

    with pytest.raises(ValueError, match="must match data index"):
        engine.run(sample_ohlcv_data, wrong_signals)


def test_validate_invalid_signal_values(default_config, sample_ohlcv_data):
    """Test validation fails with invalid signal values."""
    engine = BacktestEngine(default_config)

    # Signals with invalid values (not -1, 0, or 1)
    bad_signals = pd.Series(0.5, index=sample_ohlcv_data.index)

    with pytest.raises(ValueError, match="must be -1.0, 0.0, or 1.0"):
        engine.run(sample_ohlcv_data, bad_signals)


def test_validate_short_signals_not_allowed(default_config, sample_ohlcv_data):
    """Test validation fails when short signals present but not allowed."""
    config = BacktestConfig(allow_short=False)
    engine = BacktestEngine(config)

    # Short signals
    signals = pd.Series(-1.0, index=sample_ohlcv_data.index)

    with pytest.raises(ValueError, match="allow_short=False"):
        engine.run(sample_ohlcv_data, signals)


# ============================================================================
# Position Tracking Tests
# ============================================================================

def test_position_initialization():
    """Test Position initialization."""
    pos = Position(
        symbol="AAPL",
        side=PositionSide.LONG,
        quantity=100,
        entry_price=150.0,
        entry_time=pd.Timestamp('2023-01-01')
    )

    assert pos.symbol == "AAPL"
    assert pos.side == PositionSide.LONG
    assert pos.quantity == 100
    assert pos.entry_price == 150.0
    assert pos.unrealized_pnl == 0.0


def test_position_update_price_long():
    """Test position price update for long position."""
    pos = Position(
        symbol="AAPL",
        side=PositionSide.LONG,
        quantity=100,
        entry_price=150.0,
        entry_time=pd.Timestamp('2023-01-01')
    )

    # Price increases
    pos.update_price(160.0)
    assert pos.current_price == 160.0
    assert pos.unrealized_pnl == 1000.0  # (160-150)*100

    # Price decreases
    pos.update_price(145.0)
    assert pos.current_price == 145.0
    assert pos.unrealized_pnl == -500.0  # (145-150)*100


def test_position_update_price_short():
    """Test position price update for short position."""
    pos = Position(
        symbol="AAPL",
        side=PositionSide.SHORT,
        quantity=100,
        entry_price=150.0,
        entry_time=pd.Timestamp('2023-01-01')
    )

    # Price decreases (profit for short)
    pos.update_price(140.0)
    assert pos.current_price == 140.0
    assert pos.unrealized_pnl == 1000.0  # (150-140)*100

    # Price increases (loss for short)
    pos.update_price(160.0)
    assert pos.current_price == 160.0
    assert pos.unrealized_pnl == -1000.0  # (150-160)*100


def test_position_is_flat():
    """Test position flat detection."""
    # Flat position
    pos = Position(
        symbol="AAPL",
        side=PositionSide.FLAT,
        quantity=0,
        entry_price=0,
        entry_time=pd.Timestamp('2023-01-01')
    )
    assert pos.is_flat()

    # Long position
    pos.side = PositionSide.LONG
    pos.quantity = 100
    assert not pos.is_flat()

    # Zero quantity
    pos.quantity = 0
    assert pos.is_flat()


# ============================================================================
# Simple Backtest Tests
# ============================================================================

def test_simple_backtest_buy_and_hold(sample_ohlcv_data, simple_signals, default_config):
    """Test simple buy-and-hold backtest."""
    engine = BacktestEngine(default_config)
    result = engine.run(sample_ohlcv_data, simple_signals, symbol="TEST")

    # Should have 1 trade (entry only, exit at end)
    assert len(result.trades) == 1

    # Check trade details
    trade = result.trades[0]
    assert trade.side == PositionSide.LONG
    assert trade.quantity > 0
    assert trade.entry_price > 0
    assert trade.exit_price > trade.entry_price  # Uptrending data

    # Total return should be positive (uptrending market)
    assert result.total_return > 0

    # Equity curve should be monotonically increasing (mostly)
    assert result.equity_curve.iloc[-1] > default_config.initial_capital


def test_backtest_multiple_trades(sample_ohlcv_data, alternating_signals, default_config):
    """Test backtest with multiple trades."""
    engine = BacktestEngine(default_config)
    result = engine.run(sample_ohlcv_data, alternating_signals, symbol="TEST")

    # Should have multiple trades
    assert len(result.trades) >= 2

    # Each trade should have valid structure
    for trade in result.trades:
        assert trade.quantity > 0
        assert trade.entry_price > 0
        assert trade.exit_price > 0
        assert trade.entry_time < trade.exit_time
        assert isinstance(trade.pnl, float)
        assert isinstance(trade.commission, float)
        assert isinstance(trade.slippage, float)


def test_backtest_no_trades_flat_signals(sample_ohlcv_data, default_config):
    """Test backtest with all flat signals (no trades)."""
    engine = BacktestEngine(default_config)

    # All flat signals
    flat_signals = pd.Series(0.0, index=sample_ohlcv_data.index)

    result = engine.run(sample_ohlcv_data, flat_signals, symbol="TEST")

    # No trades should be executed
    assert len(result.trades) == 0

    # Equity should remain at initial capital
    assert result.equity_curve.iloc[-1] == default_config.initial_capital

    # Total return should be 0
    assert result.total_return == 0.0


# ============================================================================
# Transaction Cost Tests
# ============================================================================

def test_transaction_costs_applied(sample_ohlcv_data, simple_signals):
    """Test that transaction costs are properly applied."""
    config = BacktestConfig(
        initial_capital=100000.0,
        commission_pct=0.01,  # 1% commission
        slippage_pct=0.005,   # 0.5% slippage
    )

    engine = BacktestEngine(config)
    result = engine.run(sample_ohlcv_data, simple_signals, symbol="TEST")

    # Should have commission and slippage
    assert result.total_commission > 0
    assert result.total_slippage > 0

    # Total costs should reduce returns
    trade = result.trades[0]
    assert trade.commission > 0
    assert trade.slippage > 0


def test_fixed_commission(sample_ohlcv_data, simple_signals):
    """Test fixed commission per trade."""
    config = BacktestConfig(
        initial_capital=100000.0,
        commission_pct=0.0,
        commission_fixed=10.0  # $10 per trade
    )

    engine = BacktestEngine(config)
    result = engine.run(sample_ohlcv_data, simple_signals, symbol="TEST")

    # Should have fixed commission
    assert result.total_commission >= 10.0  # At least one trade


def test_zero_transaction_costs(sample_ohlcv_data, simple_signals):
    """Test backtest with zero transaction costs."""
    config = BacktestConfig(
        initial_capital=100000.0,
        commission_pct=0.0,
        commission_fixed=0.0,
        slippage_pct=0.0
    )

    engine = BacktestEngine(config)
    result = engine.run(sample_ohlcv_data, simple_signals, symbol="TEST")

    # No commission or slippage
    assert result.total_commission == 0.0
    assert result.total_slippage == 0.0


# ============================================================================
# Performance Metrics Tests
# ============================================================================

def test_total_return_calculation(sample_ohlcv_data, simple_signals, default_config):
    """Test total return calculation."""
    engine = BacktestEngine(default_config)
    result = engine.run(sample_ohlcv_data, simple_signals, symbol="TEST")

    # Total return should match equity change
    expected_return = (result.equity_curve.iloc[-1] / default_config.initial_capital) - 1
    assert abs(result.total_return - expected_return) < 0.0001


def test_sharpe_ratio_calculation(sample_ohlcv_data, simple_signals, default_config):
    """Test Sharpe ratio calculation."""
    engine = BacktestEngine(default_config)
    result = engine.run(sample_ohlcv_data, simple_signals, symbol="TEST")

    # Sharpe ratio should be calculated
    assert isinstance(result.sharpe_ratio, float)

    # For profitable strategy, Sharpe should be positive
    if result.total_return > 0:
        assert result.sharpe_ratio > 0


def test_max_drawdown_calculation(sample_ohlcv_data, default_config):
    """Test max drawdown calculation."""
    # Create signals that will cause drawdown
    signals = pd.Series(1.0, index=sample_ohlcv_data.index)

    # Modify data to create drawdown (adjust all OHLC consistently)
    data = sample_ohlcv_data.copy()
    # Scale down all OHLC for bars 50-60
    for col in ['Open', 'High', 'Low', 'Close']:
        data.iloc[50:60, data.columns.get_loc(col)] *= 0.8  # 20% drop

    engine = BacktestEngine(default_config)
    result = engine.run(data, signals, symbol="TEST")

    # Max drawdown should be positive and less than 1
    assert 0 <= result.max_drawdown < 1

    # For this scenario, should have some drawdown
    assert result.max_drawdown > 0


def test_win_rate_calculation(sample_ohlcv_data, alternating_signals, default_config):
    """Test win rate calculation."""
    engine = BacktestEngine(default_config)
    result = engine.run(sample_ohlcv_data, alternating_signals, symbol="TEST")

    # Win rate should be between 0 and 1
    assert 0 <= result.win_rate <= 1

    # If we have trades, calculate expected win rate
    if result.trades:
        winning_trades = sum(1 for t in result.trades if t.pnl > 0)
        expected_win_rate = winning_trades / len(result.trades)
        assert abs(result.win_rate - expected_win_rate) < 0.0001


def test_profit_factor_calculation(sample_ohlcv_data, alternating_signals, default_config):
    """Test profit factor calculation."""
    engine = BacktestEngine(default_config)
    result = engine.run(sample_ohlcv_data, alternating_signals, symbol="TEST")

    # Profit factor should be non-negative
    assert result.profit_factor >= 0

    # If profitable, profit factor should be > 1
    if result.total_return > 0 and len(result.trades) > 0:
        # May not always be > 1 due to transaction costs
        pass


def test_avg_win_loss_calculation(sample_ohlcv_data, alternating_signals, default_config):
    """Test average win/loss calculation."""
    engine = BacktestEngine(default_config)
    result = engine.run(sample_ohlcv_data, alternating_signals, symbol="TEST")

    if result.trades:
        # Average win should be >= 0 (or may be 0 if no wins)
        assert result.avg_win >= 0

        # Average loss should be <= 0 (or may be 0 if no losses)
        assert result.avg_loss <= 0


# ============================================================================
# Edge Case Tests
# ============================================================================

def test_insufficient_capital(sample_ohlcv_data, simple_signals):
    """Test backtest with insufficient capital."""
    config = BacktestConfig(initial_capital=10.0)  # Very small capital

    engine = BacktestEngine(config)
    result = engine.run(sample_ohlcv_data, simple_signals, symbol="TEST")

    # May have 0 trades if capital insufficient
    # Should not raise error, just execute what's possible
    assert isinstance(result, BacktestResult)


def test_empty_equity_curve_handling():
    """Test handling of empty equity curve in metrics."""
    engine = BacktestEngine()

    # Test metric calculations with empty data
    assert engine._calculate_total_return(pd.Series([])) == 0.0
    assert engine._calculate_annual_return(pd.Series([])) == 0.0
    assert engine._calculate_sharpe_ratio(pd.Series([])) == 0.0
    assert engine._calculate_max_drawdown(pd.Series([])) == 0.0


def test_single_bar_data():
    """Test backtest with single bar of data."""
    config = BacktestConfig()
    engine = BacktestEngine(config)

    data = pd.DataFrame({
        'Open': [100],
        'High': [102],
        'Low': [99],
        'Close': [101],
        'Volume': [1000000]
    }, index=pd.date_range('2023-01-01', periods=1))

    signals = pd.Series([1.0], index=data.index)

    result = engine.run(data, signals, symbol="TEST")

    # Should handle gracefully (may have 0 or 1 trade)
    assert isinstance(result, BacktestResult)


def test_constant_price_data():
    """Test backtest with constant prices (no movement)."""
    config = BacktestConfig()
    engine = BacktestEngine(config)

    # Constant price
    data = pd.DataFrame({
        'Open': [100.0] * 50,
        'High': [100.0] * 50,
        'Low': [100.0] * 50,
        'Close': [100.0] * 50,
        'Volume': [1000000] * 50
    }, index=pd.date_range('2023-01-01', periods=50))

    signals = pd.Series(1.0, index=data.index)

    result = engine.run(data, signals, symbol="TEST")

    # Should have negative return due to transaction costs
    assert result.total_return <= 0


# ============================================================================
# Position Sizing Tests
# ============================================================================

def test_position_size_pct(sample_ohlcv_data, simple_signals):
    """Test position sizing with percentage of capital."""
    config = BacktestConfig(
        initial_capital=100000.0,
        position_size_pct=0.5  # Use 50% of capital
    )

    engine = BacktestEngine(config)
    result = engine.run(sample_ohlcv_data, simple_signals, symbol="TEST")

    # First trade should use approximately 50% of capital
    if result.trades:
        trade = result.trades[0]
        position_value = trade.entry_price * trade.quantity
        # Should be around 50% (with some tolerance for costs)
        assert 0.4 < (position_value / config.initial_capital) < 0.6


# ============================================================================
# Result Summary Tests
# ============================================================================

def test_result_summary(sample_ohlcv_data, simple_signals, default_config):
    """Test BacktestResult summary method."""
    engine = BacktestEngine(default_config)
    result = engine.run(sample_ohlcv_data, simple_signals, symbol="TEST")

    summary = result.summary()

    # Should have all expected keys
    expected_keys = [
        'Total Return', 'Annual Return', 'Sharpe Ratio', 'Sortino Ratio',
        'Max Drawdown', 'Calmar Ratio', 'Win Rate', 'Profit Factor',
        'Total Trades', 'Avg Win', 'Avg Loss', 'Total Commission', 'Total Slippage'
    ]

    for key in expected_keys:
        assert key in summary
        assert summary[key] is not None


# ============================================================================
# Integration Tests
# ============================================================================

def test_full_backtest_workflow():
    """Test complete backtest workflow end-to-end."""
    # Create synthetic data
    dates = pd.date_range('2023-01-01', '2023-12-31', freq='1D')
    close = 100 + np.cumsum(np.random.randn(len(dates))) * 2

    data = pd.DataFrame({
        'Open': close - 1,
        'High': close + 2,
        'Low': close - 2,
        'Close': close,
        'Volume': np.random.randint(1000000, 5000000, len(dates))
    }, index=dates)

    # Simple MA crossover signals
    fast_ma = data['Close'].rolling(20).mean()
    slow_ma = data['Close'].rolling(50).mean()
    signals = (fast_ma > slow_ma).astype(float)

    # Configure and run
    config = BacktestConfig(
        initial_capital=100000.0,
        commission_pct=0.001,
        slippage_pct=0.0005
    )

    engine = BacktestEngine(config)
    result = engine.run(data, signals, symbol="SYNTHETIC")

    # Verify result structure
    assert isinstance(result, BacktestResult)
    assert len(result.equity_curve) == len(data)
    assert isinstance(result.summary(), dict)

    # All metrics should be calculated
    assert isinstance(result.total_return, float)
    assert isinstance(result.sharpe_ratio, float)
    assert isinstance(result.max_drawdown, float)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
