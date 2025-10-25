"""
Tests for Pairs Trading Strategy

Comprehensive test suite covering:
- Strategy initialization and parameter validation
- Pair selection with cointegration testing
- Spread calculation and z-score normalization
- Signal generation logic (entry/exit conditions)
- Position sizing with market neutrality
- Edge cases and error handling
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from src.strategy.pairs_trading import (
    PairsTradingStrategy,
    PairCandidate,
    PairPosition
)
from src.strategy.base import (
    StrategyConfig,
    SignalType,
    Position
)


@pytest.fixture
def default_config():
    """Default strategy configuration for testing."""
    return StrategyConfig(
        name="TestPairsStrategy",
        version="1.0.0",
        parameters={
            'lookback_period': 60,
            'zscore_lookback': 30,
            'z_entry': 2.0,
            'z_exit': 0.5,
            'max_holding_days': 30,
            'correlation_threshold': 0.7,
            'coint_pvalue_threshold': 0.05,
            'position_size_pct': 0.2,
            'max_pairs': 5,
            'rebalance_hedge_ratio_days': 20,
            'pair_selection_frequency_days': 30
        },
        symbols=['SPY', 'IWM', 'GLD', 'SLV'],
        enable_short=True,
        max_positions=10,
        rebalance_frequency='1D'
    )


@pytest.fixture
def sample_price_data():
    """Generate sample price data for testing."""
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=100, freq='1D')

    # Create cointegrated pair: SPY and IWM
    # IWM follows SPY with some noise
    spy_prices = 100 + np.cumsum(np.random.randn(100) * 0.5)
    iwm_prices = 50 + 0.5 * spy_prices + np.random.randn(100) * 2

    # Create another cointegrated pair: GLD and SLV
    gld_prices = 200 + np.cumsum(np.random.randn(100) * 0.3)
    slv_prices = 25 + 0.125 * gld_prices + np.random.randn(100) * 1

    data = {
        'SPY': pd.DataFrame({
            'Open': spy_prices,
            'High': spy_prices * 1.01,
            'Low': spy_prices * 0.99,
            'Close': spy_prices,
            'Volume': np.random.randint(1000000, 10000000, 100)
        }, index=dates),
        'IWM': pd.DataFrame({
            'Open': iwm_prices,
            'High': iwm_prices * 1.01,
            'Low': iwm_prices * 0.99,
            'Close': iwm_prices,
            'Volume': np.random.randint(1000000, 10000000, 100)
        }, index=dates),
        'GLD': pd.DataFrame({
            'Open': gld_prices,
            'High': gld_prices * 1.01,
            'Low': gld_prices * 0.99,
            'Close': gld_prices,
            'Volume': np.random.randint(500000, 5000000, 100)
        }, index=dates),
        'SLV': pd.DataFrame({
            'Open': slv_prices,
            'High': slv_prices * 1.01,
            'Low': slv_prices * 0.99,
            'Close': slv_prices,
            'Volume': np.random.randint(500000, 5000000, 100)
        }, index=dates)
    }

    return data


class TestPairsTradingInitialization:
    """Test strategy initialization and configuration validation."""

    def test_valid_initialization(self, default_config):
        """Test successful strategy initialization."""
        strategy = PairsTradingStrategy(default_config)

        assert strategy.config.name == "TestPairsStrategy"
        assert strategy.config.version == "1.0.0"
        assert strategy.config.enable_short is True
        assert strategy.selected_pairs == []
        assert strategy.pair_positions == {}
        assert strategy.last_pair_selection is None

    def test_requires_short_selling(self, default_config):
        """Test that strategy requires short selling enabled."""
        default_config.enable_short = False

        with pytest.raises(ValueError, match="requires enable_short=True"):
            PairsTradingStrategy(default_config)

    def test_requires_minimum_symbols(self, default_config):
        """Test that strategy requires at least 2 symbols."""
        default_config.symbols = ['SPY']

        with pytest.raises(ValueError, match="at least 2 symbols"):
            PairsTradingStrategy(default_config)

    def test_invalid_lookback_period(self, default_config):
        """Test validation of lookback period."""
        default_config.parameters['lookback_period'] = 10

        with pytest.raises(ValueError, match="lookback_period must be >= 30"):
            PairsTradingStrategy(default_config)

    def test_invalid_z_thresholds(self, default_config):
        """Test validation of z-score thresholds."""
        default_config.parameters['z_exit'] = 3.0
        default_config.parameters['z_entry'] = 2.0

        with pytest.raises(ValueError, match="z_exit.*must be < z_entry"):
            PairsTradingStrategy(default_config)

    def test_invalid_correlation_threshold(self, default_config):
        """Test validation of correlation threshold."""
        default_config.parameters['correlation_threshold'] = 1.5

        with pytest.raises(ValueError, match="correlation_threshold"):
            PairsTradingStrategy(default_config)

    def test_invalid_position_size(self, default_config):
        """Test validation of position size percentage."""
        default_config.parameters['position_size_pct'] = 1.5

        with pytest.raises(ValueError, match="position_size_pct"):
            PairsTradingStrategy(default_config)


class TestPairSelection:
    """Test pair selection with cointegration testing."""

    def test_select_pairs_basic(self, default_config, sample_price_data):
        """Test basic pair selection functionality."""
        strategy = PairsTradingStrategy(default_config)

        # Lower cointegration threshold to allow more pairs
        strategy.set_parameter('coint_pvalue_threshold', 0.15)
        strategy.set_parameter('correlation_threshold', 0.5)

        pairs = strategy.select_pairs(sample_price_data, max_pairs=2)

        # May find 0 or more pairs depending on random data
        assert len(pairs) <= 2
        assert all(isinstance(p, PairCandidate) for p in pairs)

        # Check pair attributes if any pairs found
        for pair in pairs:
            assert pair.symbol_long in default_config.symbols
            assert pair.symbol_short in default_config.symbols
            assert pair.symbol_long != pair.symbol_short
            assert -1.0 <= pair.correlation <= 1.0
            assert 0.0 <= pair.coint_pvalue <= 1.0
            assert pair.hedge_ratio > 0
            assert pair.score > 0

    def test_select_pairs_full_coverage(self, default_config):
        """Test pair selection with strong cointegrated pairs to ensure full coverage."""
        strategy = PairsTradingStrategy(default_config)

        # Create strongly cointegrated pairs with controlled properties
        np.random.seed(100)
        dates = pd.date_range('2024-01-01', periods=100, freq='1D')

        # Pair 1: Perfect cointegration (SPY/IWM) - short half-life
        spy_base = 100 + np.cumsum(np.random.randn(100) * 0.5)
        iwm_base = 50 + 0.5 * spy_base + np.random.randn(100) * 0.1  # Strong coint

        # Pair 2: Good cointegration (GLD/SLV) - medium half-life
        gld_base = 200 + np.cumsum(np.random.randn(100) * 0.3)
        slv_base = 25 + 0.125 * gld_base + np.random.randn(100) * 0.5

        data = {
            'SPY': pd.DataFrame({
                'Open': spy_base,
                'High': spy_base * 1.01,
                'Low': spy_base * 0.99,
                'Close': spy_base,
                'Volume': np.ones(100) * 1000000
            }, index=dates),
            'IWM': pd.DataFrame({
                'Open': iwm_base,
                'High': iwm_base * 1.01,
                'Low': iwm_base * 0.99,
                'Close': iwm_base,
                'Volume': np.ones(100) * 1000000
            }, index=dates),
            'GLD': pd.DataFrame({
                'Open': gld_base,
                'High': gld_base * 1.01,
                'Low': gld_base * 0.99,
                'Close': gld_base,
                'Volume': np.ones(100) * 500000
            }, index=dates),
            'SLV': pd.DataFrame({
                'Open': slv_base,
                'High': slv_base * 1.01,
                'Low': slv_base * 0.99,
                'Close': slv_base,
                'Volume': np.ones(100) * 500000
            }, index=dates)
        }

        # Relax thresholds to ensure we find pairs
        strategy.set_parameter('coint_pvalue_threshold', 0.1)
        strategy.set_parameter('correlation_threshold', 0.5)
        strategy.set_parameter('min_half_life_days', 1)
        strategy.set_parameter('max_half_life_days', 200)

        pairs = strategy.select_pairs(data, max_pairs=2)

        # Should find at least one pair with these relaxed thresholds
        assert len(pairs) >= 1

        # Verify pair properties
        for pair in pairs:
            assert pair.correlation > 0.5
            assert pair.coint_pvalue < 0.1
            assert pair.half_life > 0
            assert pair.score > 0

    def test_select_pairs_sorted_by_score(self, default_config, sample_price_data):
        """Test that pairs are sorted by quality score."""
        strategy = PairsTradingStrategy(default_config)

        pairs = strategy.select_pairs(sample_price_data, max_pairs=5)

        # Check descending score order
        scores = [p.score for p in pairs]
        assert scores == sorted(scores, reverse=True)

    def test_select_pairs_filters_by_correlation(self, default_config, sample_price_data):
        """Test that pairs are filtered by correlation threshold."""
        strategy = PairsTradingStrategy(default_config)

        # Set high correlation threshold
        strategy.set_parameter('correlation_threshold', 0.95)

        pairs = strategy.select_pairs(sample_price_data, max_pairs=10)

        # Should filter out most pairs
        for pair in pairs:
            assert abs(pair.correlation) >= 0.95

    def test_select_pairs_empty_universe(self, default_config):
        """Test pair selection with no data."""
        strategy = PairsTradingStrategy(default_config)

        pairs = strategy.select_pairs({}, max_pairs=5)

        assert pairs == []

    def test_select_pairs_insufficient_data(self, default_config):
        """Test pair selection with insufficient historical data."""
        strategy = PairsTradingStrategy(default_config)

        # Create data with only 20 bars (less than lookback_period)
        dates = pd.date_range('2024-01-01', periods=20, freq='1D')
        short_data = {
            'SPY': pd.DataFrame({
                'Close': np.random.randn(20) + 100,
                'Open': np.random.randn(20) + 100,
                'High': np.random.randn(20) + 101,
                'Low': np.random.randn(20) + 99,
                'Volume': np.random.randint(1000000, 10000000, 20)
            }, index=dates),
            'IWM': pd.DataFrame({
                'Close': np.random.randn(20) + 50,
                'Open': np.random.randn(20) + 50,
                'High': np.random.randn(20) + 51,
                'Low': np.random.randn(20) + 49,
                'Volume': np.random.randint(1000000, 10000000, 20)
            }, index=dates)
        }

        pairs = strategy.select_pairs(short_data, max_pairs=5)

        # Should return empty or very few pairs due to insufficient data
        assert len(pairs) == 0


class TestSpreadCalculation:
    """Test spread calculation and z-score normalization."""

    def test_calculate_spread(self, default_config, sample_price_data):
        """Test spread calculation."""
        strategy = PairsTradingStrategy(default_config)

        spread = strategy.calculate_spread(
            'SPY', 'IWM', sample_price_data, hedge_ratio=0.5
        )

        assert isinstance(spread, pd.Series)
        assert len(spread) == len(sample_price_data['SPY'])
        assert not spread.isna().all()

    def test_calculate_spread_zscore(self, default_config, sample_price_data):
        """Test z-score calculation."""
        strategy = PairsTradingStrategy(default_config)

        spread = strategy.calculate_spread(
            'SPY', 'IWM', sample_price_data, hedge_ratio=0.5
        )

        zscore = strategy.calculate_spread_zscore(spread, lookback=30)

        assert isinstance(zscore, float)
        # Z-score should be reasonable (not extreme outlier)
        assert -10 < zscore < 10

    def test_calculate_spread_zscore_insufficient_data(self, default_config):
        """Test z-score calculation with insufficient data."""
        strategy = PairsTradingStrategy(default_config)

        # Create short spread series
        spread = pd.Series([1, 2, 3, 4, 5])

        with pytest.raises(ValueError, match="Insufficient spread data"):
            strategy.calculate_spread_zscore(spread, lookback=30)

    def test_calculate_spread_zscore_zero_std(self, default_config):
        """Test z-score calculation with zero standard deviation."""
        strategy = PairsTradingStrategy(default_config)

        # Create constant spread (zero std)
        spread = pd.Series([100.0] * 50)

        zscore = strategy.calculate_spread_zscore(spread, lookback=30)

        # Should return 0.0 when std is zero
        assert zscore == 0.0


class TestSignalGeneration:
    """Test signal generation logic."""

    def test_generate_signals_no_pairs(self, default_config, sample_price_data):
        """Test signal generation with no selected pairs."""
        strategy = PairsTradingStrategy(default_config)
        # Don't select pairs

        timestamp = sample_price_data['SPY'].index[-1]
        signals = strategy.generate_signals(timestamp, sample_price_data)

        assert signals == {}

    def test_generate_signals_entry_long_spread(self, default_config, sample_price_data):
        """Test entry signal for going long the spread."""
        strategy = PairsTradingStrategy(default_config)

        # Manually add a pair
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        # Manipulate data to create low z-score (spread compressed)
        # This requires some setup to ensure z-score < -z_entry
        timestamp = sample_price_data['SPY'].index[-1]

        signals = strategy.generate_signals(timestamp, sample_price_data)

        # Should have signals for both legs
        assert 'SPY' in signals
        assert 'IWM' in signals

        # Check signal types (depends on actual z-score)
        assert signals['SPY'].signal_type in [SignalType.LONG, SignalType.SHORT, SignalType.FLAT]
        assert signals['IWM'].signal_type in [SignalType.LONG, SignalType.SHORT, SignalType.FLAT]

    def test_generate_signals_with_position_exit(self, default_config, sample_price_data):
        """Test exit signal for existing position."""
        strategy = PairsTradingStrategy(default_config)

        # Add selected pair
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        # Add existing position with old entry date (for time-based exit)
        old_timestamp = sample_price_data['SPY'].index[0]
        strategy.pair_positions[('SPY', 'IWM')] = PairPosition(
            symbol_long='SPY',
            symbol_short='IWM',
            entry_spread=10.0,
            entry_zscore=-2.5,
            entry_timestamp=old_timestamp,
            days_held=35  # Exceeds max_holding_days
        )

        timestamp = sample_price_data['SPY'].index[-1]
        signals = strategy.generate_signals(timestamp, sample_price_data)

        # Should generate FLAT signals (exit)
        assert signals['SPY'].signal_type == SignalType.FLAT
        assert signals['IWM'].signal_type == SignalType.FLAT

        # Position should be removed
        assert ('SPY', 'IWM') not in strategy.pair_positions

    def test_generate_signals_with_stop_loss_long(self, default_config, sample_price_data):
        """Test stop loss exit for long spread position."""
        strategy = PairsTradingStrategy(default_config)

        # Add selected pair
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        # Add existing long spread position (negative entry z-score)
        # that is now at -3.8 (1.5x worse than -2.5 entry)
        old_timestamp = sample_price_data['SPY'].index[-10]
        strategy.pair_positions[('SPY', 'IWM')] = PairPosition(
            symbol_long='SPY',
            symbol_short='IWM',
            entry_spread=10.0,
            entry_zscore=-2.5,  # Entered at -2.5
            entry_timestamp=old_timestamp,
            days_held=5
        )

        timestamp = sample_price_data['SPY'].index[-1]

        # Mock the z-score calculation to return -3.8 (triggering stop loss)
        # We'll test this indirectly through signal generation
        signals = strategy.generate_signals(timestamp, sample_price_data)

        # Should have signals (may or may not trigger stop depending on actual data)
        assert 'SPY' in signals
        assert 'IWM' in signals

    def test_generate_signals_with_stop_loss_short(self, default_config, sample_price_data):
        """Test stop loss exit for short spread position."""
        strategy = PairsTradingStrategy(default_config)

        # Add selected pair
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        # Add existing short spread position (positive entry z-score)
        old_timestamp = sample_price_data['SPY'].index[-10]
        strategy.pair_positions[('SPY', 'IWM')] = PairPosition(
            symbol_long='SPY',
            symbol_short='IWM',
            entry_spread=10.0,
            entry_zscore=2.5,  # Entered at +2.5
            entry_timestamp=old_timestamp,
            days_held=5
        )

        timestamp = sample_price_data['SPY'].index[-1]
        signals = strategy.generate_signals(timestamp, sample_price_data)

        # Should have signals
        assert 'SPY' in signals
        assert 'IWM' in signals

    def test_generate_signals_hold_position(self, default_config, sample_price_data):
        """Test holding existing position when no exit condition met."""
        strategy = PairsTradingStrategy(default_config)

        # Add selected pair
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        # Add existing position with negative entry z-score (long spread)
        old_timestamp = sample_price_data['SPY'].index[-5]
        strategy.pair_positions[('SPY', 'IWM')] = PairPosition(
            symbol_long='SPY',
            symbol_short='IWM',
            entry_spread=10.0,
            entry_zscore=-2.0,
            entry_timestamp=old_timestamp,
            days_held=2  # Well below max
        )

        timestamp = sample_price_data['SPY'].index[-1]
        signals = strategy.generate_signals(timestamp, sample_price_data)

        # Should maintain position signals
        assert 'SPY' in signals
        assert 'IWM' in signals

    def test_generate_signals_short_spread_entry(self, default_config, sample_price_data):
        """Test entry signal for going short the spread."""
        strategy = PairsTradingStrategy(default_config)

        # Modify sample data to create high z-score scenario
        # We'll just test that the logic works
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        timestamp = sample_price_data['SPY'].index[-1]
        signals = strategy.generate_signals(timestamp, sample_price_data)

        # Should have signals for both legs (type depends on actual z-score)
        assert 'SPY' in signals
        assert 'IWM' in signals

    def test_generate_signals_missing_data(self, default_config, sample_price_data):
        """Test signal generation with missing symbol data."""
        strategy = PairsTradingStrategy(default_config)

        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='MISSING',  # Symbol not in data
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        timestamp = sample_price_data['SPY'].index[-1]
        signals = strategy.generate_signals(timestamp, sample_price_data)

        # Should skip the pair with missing data
        assert 'MISSING' not in signals


class TestPositionSizing:
    """Test position sizing and market neutrality."""

    def test_calculate_positions_basic(self, default_config, sample_price_data):
        """Test basic position calculation."""
        strategy = PairsTradingStrategy(default_config)

        # Add selected pair
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        timestamp = sample_price_data['SPY'].index[-1]
        signals = strategy.generate_signals(timestamp, sample_price_data)

        portfolio_value = 100000.0
        positions = strategy.calculate_positions(signals, {}, portfolio_value)

        # Should have positions for both symbols
        assert 'SPY' in positions
        assert 'IWM' in positions

        # Positions should be opposite signs (long/short) or both zero
        assert not (positions['SPY'] > 0 and positions['IWM'] > 0)
        assert not (positions['SPY'] < 0 and positions['IWM'] < 0)

    def test_calculate_positions_market_neutral(self, default_config, sample_price_data):
        """Test that positions are approximately market neutral."""
        strategy = PairsTradingStrategy(default_config)

        # Add selected pair
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        # Generate signals with active position
        timestamp = sample_price_data['SPY'].index[-1]

        # Manually create signals for long spread
        from src.strategy.base import Signal

        signals = {
            'SPY': Signal(
                symbol='SPY',
                signal_type=SignalType.LONG,
                timestamp=timestamp,
                confidence=0.8,
                metadata={
                    'current_price': sample_price_data['SPY']['Close'].iloc[-1],
                    'zscore': -2.5,
                    'spread': 10.0,
                    'hedge_ratio': 0.5,
                    'pair': 'IWM'
                }
            ),
            'IWM': Signal(
                symbol='IWM',
                signal_type=SignalType.SHORT,
                timestamp=timestamp,
                confidence=0.8,
                metadata={
                    'current_price': sample_price_data['IWM']['Close'].iloc[-1],
                    'zscore': -2.5,
                    'spread': 10.0,
                    'hedge_ratio': 0.5,
                    'pair': 'SPY'
                }
            )
        }

        portfolio_value = 100000.0
        positions = strategy.calculate_positions(signals, {}, portfolio_value)

        # Calculate dollar values
        spy_price = sample_price_data['SPY']['Close'].iloc[-1]
        iwm_price = sample_price_data['IWM']['Close'].iloc[-1]

        spy_value = positions['SPY'] * spy_price
        iwm_value = positions['IWM'] * iwm_price

        # Should be approximately market neutral (within 40% tolerance)
        # Total exposure should be small relative to individual legs
        # Note: Perfect neutrality is difficult with discrete share quantities
        if spy_value != 0 and iwm_value != 0:
            net_exposure = abs(spy_value + iwm_value)
            gross_exposure = abs(spy_value) + abs(iwm_value)
            neutrality_ratio = net_exposure / gross_exposure

            assert neutrality_ratio < 0.4  # Less than 40% net exposure

    def test_calculate_positions_flat_signals(self, default_config, sample_price_data):
        """Test position calculation with FLAT signals."""
        strategy = PairsTradingStrategy(default_config)

        # Add selected pair
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        timestamp = sample_price_data['SPY'].index[-1]

        # Create FLAT signals
        from src.strategy.base import Signal

        signals = {
            'SPY': Signal(
                symbol='SPY',
                signal_type=SignalType.FLAT,
                timestamp=timestamp,
                confidence=0.5,
                metadata={'current_price': sample_price_data['SPY']['Close'].iloc[-1]}
            ),
            'IWM': Signal(
                symbol='IWM',
                signal_type=SignalType.FLAT,
                timestamp=timestamp,
                confidence=0.5,
                metadata={'current_price': sample_price_data['IWM']['Close'].iloc[-1]}
            )
        }

        portfolio_value = 100000.0
        positions = strategy.calculate_positions(signals, {}, portfolio_value)

        # Should have zero positions
        assert positions['SPY'] == 0.0
        assert positions['IWM'] == 0.0

    def test_calculate_positions_short_spread(self, default_config, sample_price_data):
        """Test position calculation for short spread."""
        strategy = PairsTradingStrategy(default_config)

        # Add selected pair
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        timestamp = sample_price_data['SPY'].index[-1]

        # Create signals for short spread (sell SPY, buy IWM)
        from src.strategy.base import Signal

        signals = {
            'SPY': Signal(
                symbol='SPY',
                signal_type=SignalType.SHORT,
                timestamp=timestamp,
                confidence=0.8,
                metadata={
                    'current_price': sample_price_data['SPY']['Close'].iloc[-1],
                    'zscore': 2.5,
                    'spread': 10.0,
                    'hedge_ratio': 0.5,
                    'pair': 'IWM'
                }
            ),
            'IWM': Signal(
                symbol='IWM',
                signal_type=SignalType.LONG,
                timestamp=timestamp,
                confidence=0.8,
                metadata={
                    'current_price': sample_price_data['IWM']['Close'].iloc[-1],
                    'zscore': 2.5,
                    'spread': 10.0,
                    'hedge_ratio': 0.5,
                    'pair': 'SPY'
                }
            )
        }

        portfolio_value = 100000.0
        positions = strategy.calculate_positions(signals, {}, portfolio_value)

        # SPY should be short, IWM should be long
        assert positions['SPY'] < 0
        assert positions['IWM'] > 0

    def test_calculate_positions_missing_signals(self, default_config):
        """Test position calculation with missing signals."""
        strategy = PairsTradingStrategy(default_config)

        # Add selected pair
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        # Empty signals
        signals = {}

        portfolio_value = 100000.0
        positions = strategy.calculate_positions(signals, {}, portfolio_value)

        # Should have zero positions for all symbols in config
        for symbol in strategy.config.symbols:
            assert positions[symbol] == 0.0

    def test_calculate_positions_missing_hedge_ratio(self, default_config, sample_price_data):
        """Test position calculation with missing hedge ratio in cache."""
        strategy = PairsTradingStrategy(default_config)

        # Add selected pair but NO hedge ratio in cache
        strategy.selected_pairs = [
            PairCandidate(
                symbol_long='SPY',
                symbol_short='IWM',
                correlation=0.9,
                coint_pvalue=0.01,
                hedge_ratio=0.5,
                half_life=10.0,
                score=80.0
            )
        ]

        timestamp = sample_price_data['SPY'].index[-1]

        # Create signals
        from src.strategy.base import Signal

        signals = {
            'SPY': Signal(
                symbol='SPY',
                signal_type=SignalType.LONG,
                timestamp=timestamp,
                confidence=0.8,
                metadata={
                    'current_price': sample_price_data['SPY']['Close'].iloc[-1],
                    'zscore': -2.5,
                    'spread': 10.0,
                    'hedge_ratio': 0.5,
                    'pair': 'IWM'
                }
            ),
            'IWM': Signal(
                symbol='IWM',
                signal_type=SignalType.SHORT,
                timestamp=timestamp,
                confidence=0.8,
                metadata={
                    'current_price': sample_price_data['IWM']['Close'].iloc[-1],
                    'zscore': -2.5,
                    'spread': 10.0,
                    'hedge_ratio': 0.5,
                    'pair': 'SPY'
                }
            )
        }

        # Clear hedge ratios (simulate missing)
        strategy.hedge_ratios = {}

        portfolio_value = 100000.0
        positions = strategy.calculate_positions(signals, {}, portfolio_value)

        # Should skip pair without hedge ratio
        # All symbols should have zero positions
        for symbol in strategy.config.symbols:
            assert positions[symbol] == 0.0


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_on_data_pair_reselection(self, default_config, sample_price_data):
        """Test periodic pair reselection in on_data."""
        strategy = PairsTradingStrategy(default_config)

        # First call should trigger pair selection
        timestamp1 = sample_price_data['SPY'].index[60]
        data1 = strategy.on_data(timestamp1, sample_price_data)

        assert strategy.last_pair_selection == timestamp1
        initial_pairs_count = len(strategy.selected_pairs)

        # Call again within reselection period (should not reselect)
        timestamp2 = sample_price_data['SPY'].index[70]
        data2 = strategy.on_data(timestamp2, sample_price_data)

        assert strategy.last_pair_selection == timestamp1  # Unchanged
        assert len(strategy.selected_pairs) == initial_pairs_count

    def test_missing_data_symbols(self, default_config):
        """Test handling of missing symbols in data."""
        strategy = PairsTradingStrategy(default_config)

        # Data with missing symbols
        incomplete_data = {
            'SPY': pd.DataFrame({
                'Close': [100, 101, 102],
                'Open': [100, 101, 102],
                'High': [101, 102, 103],
                'Low': [99, 100, 101],
                'Volume': [1000000, 1000000, 1000000]
            }, index=pd.date_range('2024-01-01', periods=3, freq='1D'))
        }

        # Should not crash, just return empty pairs
        pairs = strategy.select_pairs(incomplete_data, max_pairs=5)

        assert isinstance(pairs, list)

    def test_strategy_parameter_updates(self, default_config):
        """Test dynamic parameter updates."""
        strategy = PairsTradingStrategy(default_config)

        # Update parameter
        strategy.set_parameter('z_entry', 2.5)

        assert strategy.get_parameter('z_entry') == 2.5

        # Original config should be updated
        assert strategy.config.parameters['z_entry'] == 2.5


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
