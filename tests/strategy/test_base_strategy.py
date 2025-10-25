"""
Tests for BaseStrategy and RelativeValueStrategy
"""

import pytest
import pandas as pd
import numpy as np
from src.strategy.base import (
    BaseStrategy,
    RelativeValueStrategy,
    StrategyConfig,
    Signal,
    SignalType,
    Position,
    FillEvent,
    StrategyState,
    HedgeRatio
)


class SimpleMovingAverageStrategy(BaseStrategy):
    """Test strategy for unit tests."""

    def generate_signals(self, timestamp, data):
        signals = {}

        for symbol in self.config.symbols:
            df = data[symbol]

            if len(df) < 50:
                continue

            # Simple MA crossover
            sma_20 = df['Close'].rolling(20).mean().iloc[-1]
            sma_50 = df['Close'].rolling(50).mean().iloc[-1]

            if sma_20 > sma_50:
                signals[symbol] = Signal(
                    symbol=symbol,
                    signal_type=SignalType.LONG,
                    timestamp=timestamp,
                    confidence=0.8,
                    metadata={'current_price': df['Close'].iloc[-1]}
                )
            elif sma_20 < sma_50:
                signals[symbol] = Signal(
                    symbol=symbol,
                    signal_type=SignalType.FLAT,
                    timestamp=timestamp
                )

        return signals

    def calculate_positions(self, signals, current_positions, portfolio_value):
        positions = {}

        for symbol, signal in signals.items():
            if signal.signal_type == SignalType.LONG:
                price = signal.metadata.get('current_price', 0)
                if price > 0:
                    capital_per_position = portfolio_value * 0.1  # 10% per position
                    positions[symbol] = int(capital_per_position / price)
            else:
                positions[symbol] = 0.0

        return positions


class SimplePairsTradingStrategy(RelativeValueStrategy):
    """Test pairs strategy for unit tests."""

    def generate_signals(self, timestamp, data):
        signals = {}

        if len(self.config.symbols) < 2:
            return signals

        symbol_long = self.config.symbols[0]
        symbol_short = self.config.symbols[1]

        # Calculate hedge ratio and spread
        hedge_ratio = self.calculate_hedge_ratio(symbol_long, symbol_short, data)
        spread = self.calculate_spread(symbol_long, symbol_short, data)
        zscore = self.calculate_spread_zscore(spread, lookback=30)

        # Generate signals based on z-score
        if zscore < -2.0:
            # Long spread
            signals[symbol_long] = Signal(
                symbol=symbol_long,
                signal_type=SignalType.LONG,
                timestamp=timestamp,
                metadata={'current_price': data[symbol_long]['Close'].iloc[-1]}
            )
            signals[symbol_short] = Signal(
                symbol=symbol_short,
                signal_type=SignalType.SHORT,
                timestamp=timestamp,
                metadata={'current_price': data[symbol_short]['Close'].iloc[-1]}
            )
        elif zscore > 2.0:
            # Short spread
            signals[symbol_long] = Signal(
                symbol=symbol_long,
                signal_type=SignalType.SHORT,
                timestamp=timestamp,
                metadata={'current_price': data[symbol_long]['Close'].iloc[-1]}
            )
            signals[symbol_short] = Signal(
                symbol=symbol_short,
                signal_type=SignalType.LONG,
                timestamp=timestamp,
                metadata={'current_price': data[symbol_short]['Close'].iloc[-1]}
            )

        return signals

    def calculate_positions(self, signals, current_positions, portfolio_value):
        positions = {}

        if len(self.config.symbols) < 2:
            return positions

        symbol_long = self.config.symbols[0]
        symbol_short = self.config.symbols[1]

        if symbol_long not in signals or symbol_short not in signals:
            return positions

        signal_long = signals[symbol_long]
        signal_short = signals[symbol_short]

        # Get hedge ratio
        pair_key = (symbol_long, symbol_short)
        if pair_key not in self.hedge_ratios:
            return positions

        hedge_ratio = self.hedge_ratios[pair_key].ratio

        # Calculate position sizes
        capital_per_leg = portfolio_value * 0.5 * 0.5  # 50% of portfolio, 50% per leg

        price_long = signal_long.metadata.get('current_price', 0)
        price_short = signal_short.metadata.get('current_price', 0)

        if price_long == 0 or price_short == 0:
            return positions

        if signal_long.signal_type == SignalType.LONG:
            qty_long = int(capital_per_leg / price_long)
            qty_long_balanced, qty_short_balanced = self.balance_positions(
                symbol_long, symbol_short, qty_long, hedge_ratio
            )
            positions[symbol_long] = qty_long_balanced
            positions[symbol_short] = qty_short_balanced
        elif signal_long.signal_type == SignalType.SHORT:
            qty_short = int(capital_per_leg / price_long)
            qty_short_balanced, qty_long_balanced = self.balance_positions(
                symbol_short, symbol_long, qty_short, 1.0 / hedge_ratio
            )
            positions[symbol_long] = qty_long_balanced
            positions[symbol_short] = qty_short_balanced

        return positions


@pytest.fixture
def sample_ohlcv_data():
    """Create sample OHLCV data for testing."""
    dates = pd.date_range('2024-01-01', periods=100, freq='1D')
    data = pd.DataFrame({
        'Open': np.linspace(100, 200, 100),
        'High': np.linspace(102, 202, 100),
        'Low': np.linspace(98, 198, 100),
        'Close': np.linspace(100, 200, 100),
        'Volume': [1000000] * 100
    }, index=dates)
    return data


@pytest.fixture
def sample_config():
    """Create sample strategy configuration."""
    return StrategyConfig(
        name="TestStrategy",
        version="1.0.0",
        parameters={'fast_period': 20, 'slow_period': 50},
        symbols=['AAPL', 'MSFT'],
        max_positions=10,
        enable_short=False
    )


@pytest.fixture
def pairs_config():
    """Create sample pairs strategy configuration."""
    return StrategyConfig(
        name="PairsStrategy",
        version="1.0.0",
        parameters={'zscore_entry': 2.0, 'zscore_exit': 0.5},
        symbols=['SPY', 'QQQ'],
        max_positions=2,
        enable_short=True
    )


def test_strategy_initialization(sample_config):
    """Test strategy initialization."""
    strategy = SimpleMovingAverageStrategy(sample_config)

    assert strategy.config.name == "TestStrategy"
    assert strategy.config.version == "1.0.0"
    assert len(strategy.config.symbols) == 2
    assert strategy.state == StrategyState.INITIALIZED


def test_strategy_validation_missing_name():
    """Test strategy validation with missing name."""
    config = StrategyConfig(
        name="",
        version="1.0.0",
        symbols=['AAPL']
    )

    with pytest.raises(ValueError, match="Strategy name required"):
        SimpleMovingAverageStrategy(config)


def test_strategy_validation_missing_symbols():
    """Test strategy validation with missing symbols."""
    config = StrategyConfig(
        name="TestStrategy",
        version="1.0.0",
        symbols=[]
    )

    with pytest.raises(ValueError, match="Strategy symbols required"):
        SimpleMovingAverageStrategy(config)


def test_signal_generation(sample_config, sample_ohlcv_data):
    """Test signal generation."""
    strategy = SimpleMovingAverageStrategy(sample_config)

    data = {
        'AAPL': sample_ohlcv_data,
        'MSFT': sample_ohlcv_data
    }

    timestamp = sample_ohlcv_data.index[-1]
    signals = strategy.generate_signals(timestamp, data)

    assert 'AAPL' in signals
    assert signals['AAPL'].signal_type == SignalType.LONG  # Uptrend
    assert signals['AAPL'].timestamp == timestamp
    assert signals['AAPL'].confidence == 0.8


def test_position_calculation(sample_config):
    """Test position calculation."""
    strategy = SimpleMovingAverageStrategy(sample_config)

    # Create signal
    signal = Signal(
        symbol='AAPL',
        signal_type=SignalType.LONG,
        timestamp=pd.Timestamp.now(),
        metadata={'current_price': 150.0}
    )

    signals = {'AAPL': signal}
    current_positions = {}
    portfolio_value = 100000.0

    positions = strategy.calculate_positions(signals, current_positions, portfolio_value)

    assert 'AAPL' in positions
    assert positions['AAPL'] > 0  # Long position
    # 10% of $100k = $10k, at $150/share = 66 shares
    assert positions['AAPL'] == 66


def test_fill_handling(sample_config):
    """Test fill event handling."""
    strategy = SimpleMovingAverageStrategy(sample_config)

    # Create fill event
    fill = FillEvent(
        symbol='AAPL',
        quantity=100,
        price=150.0,
        timestamp=pd.Timestamp.now(),
        commission=10.0,
        slippage=5.0
    )

    strategy.on_fill(fill)

    # Check position created
    assert 'AAPL' in strategy.current_positions
    assert strategy.current_positions['AAPL'].quantity == 100
    assert strategy.current_positions['AAPL'].entry_price == 150.0


def test_fill_handling_position_close(sample_config):
    """Test fill event closing a position."""
    strategy = SimpleMovingAverageStrategy(sample_config)

    # Open position
    fill1 = FillEvent(
        symbol='AAPL',
        quantity=100,
        price=150.0,
        timestamp=pd.Timestamp.now()
    )
    strategy.on_fill(fill1)

    # Close position
    fill2 = FillEvent(
        symbol='AAPL',
        quantity=-100,
        price=160.0,
        timestamp=pd.Timestamp.now()
    )
    strategy.on_fill(fill2)

    # Position should be removed
    assert 'AAPL' not in strategy.current_positions


def test_parameter_get_set(sample_config):
    """Test parameter get/set methods."""
    strategy = SimpleMovingAverageStrategy(sample_config)

    assert strategy.get_parameter('fast_period') == 20
    assert strategy.get_parameter('nonexistent', default=999) == 999

    strategy.set_parameter('new_param', 123)
    assert strategy.get_parameter('new_param') == 123


def test_position_helper_methods(sample_config):
    """Test position helper methods."""
    strategy = SimpleMovingAverageStrategy(sample_config)

    # Initially no position
    assert not strategy.has_position('AAPL')
    assert strategy.get_position('AAPL') is None

    # Add position
    fill = FillEvent(
        symbol='AAPL',
        quantity=100,
        price=150.0,
        timestamp=pd.Timestamp.now()
    )
    strategy.on_fill(fill)

    # Now has position
    assert strategy.has_position('AAPL')
    pos = strategy.get_position('AAPL')
    assert pos is not None
    assert pos.quantity == 100


def test_state_data_methods(sample_config):
    """Test state data methods."""
    strategy = SimpleMovingAverageStrategy(sample_config)

    assert strategy.get_state('test_key') is None
    assert strategy.get_state('test_key', default='default_value') == 'default_value'

    strategy.update_state('test_key', 'test_value')
    assert strategy.get_state('test_key') == 'test_value'


def test_on_risk_rejection(sample_config):
    """Test on_risk_rejection callback."""
    strategy = SimpleMovingAverageStrategy(sample_config)

    # Should not raise error (default implementation just logs)
    strategy.on_risk_rejection('AAPL', 100, "Position size limit exceeded")


def test_relative_value_strategy_requires_short(sample_config):
    """Test RelativeValueStrategy requires enable_short=True."""
    config_no_short = StrategyConfig(
        name="PairsStrategy",
        version="1.0.0",
        symbols=['SPY', 'QQQ'],
        enable_short=False
    )

    with pytest.raises(ValueError, match="RelativeValueStrategy requires enable_short=True"):
        SimplePairsTradingStrategy(config_no_short)


def test_hedge_ratio_calculation_ols(pairs_config, sample_ohlcv_data):
    """Test hedge ratio calculation using OLS method."""
    strategy = SimplePairsTradingStrategy(pairs_config)

    data = {
        'SPY': sample_ohlcv_data,
        'QQQ': sample_ohlcv_data * 1.2  # Different price levels
    }

    hedge_ratio = strategy.calculate_hedge_ratio('SPY', 'QQQ', data, method='ols', lookback=30)

    assert isinstance(hedge_ratio, HedgeRatio)
    assert hedge_ratio.symbol_long == 'SPY'
    assert hedge_ratio.symbol_short == 'QQQ'
    assert hedge_ratio.ratio > 0
    assert hedge_ratio.lookback_period == 30


def test_hedge_ratio_calculation_ratio(pairs_config, sample_ohlcv_data):
    """Test hedge ratio calculation using ratio method."""
    strategy = SimplePairsTradingStrategy(pairs_config)

    data = {
        'SPY': sample_ohlcv_data,
        'QQQ': sample_ohlcv_data * 1.5
    }

    hedge_ratio = strategy.calculate_hedge_ratio('SPY', 'QQQ', data, method='ratio', lookback=30)

    assert hedge_ratio.ratio > 0


def test_hedge_ratio_caching(pairs_config, sample_ohlcv_data):
    """Test hedge ratio caching behavior."""
    strategy = SimplePairsTradingStrategy(pairs_config)

    data = {
        'SPY': sample_ohlcv_data,
        'QQQ': sample_ohlcv_data * 1.2
    }

    # First call calculates
    hedge_ratio1 = strategy.calculate_hedge_ratio('SPY', 'QQQ', data, recalc_frequency_bars=20)

    # Second call uses cache
    hedge_ratio2 = strategy.calculate_hedge_ratio('SPY', 'QQQ', data, recalc_frequency_bars=20)

    assert hedge_ratio1.ratio == hedge_ratio2.ratio
    assert ('SPY', 'QQQ') in strategy.hedge_ratios


def test_spread_calculation(pairs_config, sample_ohlcv_data):
    """Test spread calculation."""
    strategy = SimplePairsTradingStrategy(pairs_config)

    data = {
        'SPY': sample_ohlcv_data,
        'QQQ': sample_ohlcv_data * 1.2
    }

    # Calculate hedge ratio first
    hedge_ratio = strategy.calculate_hedge_ratio('SPY', 'QQQ', data)

    # Calculate spread
    spread = strategy.calculate_spread('SPY', 'QQQ', data, hedge_ratio.ratio)

    assert isinstance(spread, pd.Series)
    assert len(spread) == len(sample_ohlcv_data)


def test_spread_zscore_calculation(pairs_config, sample_ohlcv_data):
    """Test spread z-score calculation."""
    strategy = SimplePairsTradingStrategy(pairs_config)

    # Create spread with known mean and std
    dates = pd.date_range('2024-01-01', periods=100, freq='1D')
    spread = pd.Series(np.random.randn(100), index=dates)

    zscore = strategy.calculate_spread_zscore(spread, lookback=30)

    assert isinstance(zscore, float)


def test_balance_positions(pairs_config, sample_ohlcv_data):
    """Test position balancing for market neutrality."""
    strategy = SimplePairsTradingStrategy(pairs_config)

    data = {
        'SPY': sample_ohlcv_data,
        'QQQ': sample_ohlcv_data * 1.2
    }

    # Calculate hedge ratio
    hedge_ratio = strategy.calculate_hedge_ratio('SPY', 'QQQ', data)

    # Balance positions
    qty_long, qty_short = strategy.balance_positions('SPY', 'QQQ', 100, hedge_ratio.ratio)

    assert qty_long == 100
    assert qty_short < 0  # Short is negative
    assert abs(qty_short) > 0
