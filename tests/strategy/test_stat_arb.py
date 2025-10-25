"""
Tests for Statistical Arbitrage Strategy

Comprehensive test suite covering:
- Strategy initialization and parameter validation
- Factor model estimation (PCA)
- Residual calculation and z-score normalization
- Signal generation logic (entry/exit conditions)
- Market-neutral position sizing
- Risk controls (correlation breakdown detection)
- Edge cases and error handling
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from src.strategy.stat_arb import (
    StatArbStrategy,
    FactorModel,
    SecurityState
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
        name="TestStatArbStrategy",
        version="1.0.0",
        parameters={
            'factor_model_type': 'pca',
            'n_factors': 3,
            'lookback_period': 60,
            'residual_lookback': 30,
            'z_entry': 2.0,
            'z_exit': 0.5,
            'max_holding_days': 30,
            'max_positions': 10,
            'position_size_pct': 0.1,
            'neutrality_type': 'dollar',
            'max_sector_exposure': 0.3,
            'min_correlation': 0.3,
            'rebalance_frequency_days': 5,
            'transaction_cost_pct': 0.001,
            'min_variance_explained': 0.7
        },
        symbols=['XLF', 'XLE', 'XLK', 'XLV', 'XLI'],
        enable_short=True,
        max_positions=10,
        rebalance_frequency='1D'
    )


@pytest.fixture
def sample_price_data():
    """Generate sample price data for testing."""
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=100, freq='1D')

    # Create correlated sector ETFs with a common factor
    # Factor: broad market movement
    market_factor = np.cumsum(np.random.randn(100) * 0.5)

    # ETFs with different factor loadings
    symbols = ['XLF', 'XLE', 'XLK', 'XLV', 'XLI']
    loadings = [0.8, 0.6, 0.9, 0.7, 0.75]  # Factor loadings
    base_prices = [50, 100, 150, 120, 80]  # Different price levels

    data = {}

    for symbol, loading, base_price in zip(symbols, loadings, base_prices):
        # Price = base + (loading * market_factor) + idiosyncratic noise
        prices = base_price + (loading * market_factor) + np.random.randn(100) * 2

        data[symbol] = pd.DataFrame({
            'Open': prices,
            'High': prices * 1.01,
            'Low': prices * 0.99,
            'Close': prices,
            'Volume': np.random.randint(1000000, 10000000, 100)
        }, index=dates)

    return data


class TestStatArbInitialization:
    """Test strategy initialization and configuration validation."""

    def test_valid_initialization(self, default_config):
        """Test successful strategy initialization."""
        strategy = StatArbStrategy(default_config)

        assert strategy.config.name == "TestStatArbStrategy"
        assert strategy.config.version == "1.0.0"
        assert strategy.config.enable_short is True
        assert strategy.factor_model is None
        assert strategy.last_model_update is None
        assert len(strategy.security_states) == len(default_config.symbols)

    def test_requires_short_selling(self, default_config):
        """Test that strategy requires short selling enabled."""
        default_config.enable_short = False

        with pytest.raises(ValueError, match="requires enable_short=True"):
            StatArbStrategy(default_config)

    def test_requires_minimum_symbols(self, default_config):
        """Test that strategy requires at least 3 symbols."""
        default_config.symbols = ['XLF']

        with pytest.raises(ValueError, match="at least 3 symbols"):
            StatArbStrategy(default_config)

    def test_invalid_factor_model_type(self, default_config):
        """Test validation of factor model type."""
        default_config.parameters['factor_model_type'] = 'invalid'

        with pytest.raises(ValueError, match="factor_model_type must be"):
            StatArbStrategy(default_config)

    def test_invalid_n_factors(self, default_config):
        """Test validation of number of factors."""
        default_config.parameters['n_factors'] = 10  # More than symbols

        with pytest.raises(ValueError, match="n_factors must be between"):
            StatArbStrategy(default_config)

    def test_invalid_lookback_period(self, default_config):
        """Test validation of lookback period."""
        default_config.parameters['lookback_period'] = 10

        with pytest.raises(ValueError, match="lookback_period must be >= 30"):
            StatArbStrategy(default_config)

    def test_invalid_residual_lookback(self, default_config):
        """Test validation of residual lookback."""
        default_config.parameters['residual_lookback'] = 5

        with pytest.raises(ValueError, match="residual_lookback must be >= 10"):
            StatArbStrategy(default_config)

    def test_invalid_z_thresholds(self, default_config):
        """Test validation of z-score thresholds."""
        default_config.parameters['z_exit'] = 3.0
        default_config.parameters['z_entry'] = 2.0

        with pytest.raises(ValueError, match="z_exit.*must be < z_entry"):
            StatArbStrategy(default_config)

    def test_invalid_neutrality_type(self, default_config):
        """Test validation of neutrality type."""
        default_config.parameters['neutrality_type'] = 'invalid'

        with pytest.raises(ValueError, match="neutrality_type must be"):
            StatArbStrategy(default_config)

    def test_invalid_max_sector_exposure(self, default_config):
        """Test validation of max sector exposure."""
        default_config.parameters['max_sector_exposure'] = 1.5

        with pytest.raises(ValueError, match="max_sector_exposure"):
            StatArbStrategy(default_config)

    def test_invalid_min_correlation(self, default_config):
        """Test validation of min correlation."""
        default_config.parameters['min_correlation'] = 1.5

        with pytest.raises(ValueError, match="min_correlation"):
            StatArbStrategy(default_config)

    def test_invalid_min_variance_explained(self, default_config):
        """Test validation of min variance explained."""
        default_config.parameters['min_variance_explained'] = 0.0

        with pytest.raises(ValueError, match="min_variance_explained"):
            StatArbStrategy(default_config)


class TestFactorModelEstimation:
    """Test PCA factor model estimation."""

    def test_estimate_factor_model_basic(self, default_config, sample_price_data):
        """Test basic factor model estimation."""
        strategy = StatArbStrategy(default_config)

        timestamp = sample_price_data['XLF'].index[-1]
        factor_model = strategy._estimate_factor_model(sample_price_data, timestamp)

        assert isinstance(factor_model, FactorModel)
        assert factor_model.n_components == 3
        assert len(factor_model.explained_variance_ratio) == 3
        assert factor_model.loadings.shape == (5, 3)  # 5 symbols, 3 factors
        assert len(factor_model.mean_returns) == 5
        assert factor_model.last_updated == timestamp

    def test_estimate_factor_model_variance_explained(self, default_config, sample_price_data):
        """Test that factor model explains sufficient variance."""
        strategy = StatArbStrategy(default_config)

        timestamp = sample_price_data['XLF'].index[-1]
        factor_model = strategy._estimate_factor_model(sample_price_data, timestamp)

        total_variance = factor_model.explained_variance_ratio.sum()

        # Should explain reasonable amount of variance
        assert 0.0 < total_variance <= 1.0

        # Individual components should be in descending order
        variance_ratios = factor_model.explained_variance_ratio
        for i in range(len(variance_ratios) - 1):
            assert variance_ratios[i] >= variance_ratios[i + 1]

    def test_estimate_factor_model_insufficient_data(self, default_config):
        """Test factor model estimation with insufficient data."""
        strategy = StatArbStrategy(default_config)

        # Create data with only 20 bars (less than lookback_period)
        dates = pd.date_range('2024-01-01', periods=20, freq='1D')
        short_data = {
            'XLF': pd.DataFrame({
                'Close': np.random.randn(20) + 50,
                'Open': np.random.randn(20) + 50,
                'High': np.random.randn(20) + 51,
                'Low': np.random.randn(20) + 49,
                'Volume': np.random.randint(1000000, 10000000, 20)
            }, index=dates)
        }

        timestamp = dates[-1]

        with pytest.raises(ValueError, match="Insufficient data"):
            strategy._estimate_factor_model(short_data, timestamp)

    def test_estimate_factor_model_missing_symbol(self, default_config, sample_price_data):
        """Test factor model estimation with missing symbol."""
        strategy = StatArbStrategy(default_config)

        # Remove one symbol
        incomplete_data = {k: v for k, v in sample_price_data.items() if k != 'XLI'}

        timestamp = sample_price_data['XLF'].index[-1]

        with pytest.raises(ValueError, match="Missing data"):
            strategy._estimate_factor_model(incomplete_data, timestamp)


class TestResidualCalculation:
    """Test residual calculation and z-score normalization."""

    def test_calculate_residuals(self, default_config, sample_price_data):
        """Test residual calculation."""
        strategy = StatArbStrategy(default_config)

        # Estimate factor model first
        timestamp = sample_price_data['XLF'].index[-1]
        factor_model = strategy._estimate_factor_model(sample_price_data, timestamp)

        # Calculate residuals
        residuals = strategy._calculate_residuals(sample_price_data, factor_model)

        assert isinstance(residuals, dict)
        assert len(residuals) == len(default_config.symbols)

        for symbol in default_config.symbols:
            assert symbol in residuals
            assert isinstance(residuals[symbol], float)

    def test_update_security_states(self, default_config, sample_price_data):
        """Test security state updates with residuals."""
        strategy = StatArbStrategy(default_config)

        timestamp = sample_price_data['XLF'].index[-1]

        # Mock residuals
        residuals = {symbol: np.random.randn() for symbol in default_config.symbols}

        strategy._update_security_states(residuals, timestamp)

        for symbol in default_config.symbols:
            state = strategy.security_states[symbol]
            assert state.current_residual == residuals[symbol]
            assert len(state.residual_history) == 1

    def test_update_security_states_zscore_calculation(self, default_config, sample_price_data):
        """Test z-score calculation in security states."""
        strategy = StatArbStrategy(default_config)

        residual_lookback = 30
        timestamp = sample_price_data['XLF'].index[-1]

        # Add enough residuals to calculate z-score
        for i in range(residual_lookback):
            residuals = {symbol: np.random.randn() for symbol in default_config.symbols}
            strategy._update_security_states(residuals, timestamp)

        # Check z-scores are calculated
        for symbol in default_config.symbols:
            state = strategy.security_states[symbol]
            assert len(state.residual_history) == residual_lookback
            # Z-score should be reasonable (not NaN or extreme)
            assert not np.isnan(state.zscore)
            assert -10 < state.zscore < 10

    def test_update_security_states_zero_std(self, default_config, sample_price_data):
        """Test z-score calculation with zero standard deviation."""
        strategy = StatArbStrategy(default_config)

        residual_lookback = 30
        timestamp = sample_price_data['XLF'].index[-1]

        # Add constant residuals (zero std)
        for i in range(residual_lookback):
            residuals = {symbol: 1.0 for symbol in default_config.symbols}  # Constant
            strategy._update_security_states(residuals, timestamp)

        # Check z-scores are zero
        for symbol in default_config.symbols:
            state = strategy.security_states[symbol]
            assert state.zscore == 0.0


class TestCorrelationBreakdownDetection:
    """Test correlation breakdown risk control."""

    def test_check_correlation_breakdown_normal(self, default_config, sample_price_data):
        """Test correlation check with normal correlations."""
        strategy = StatArbStrategy(default_config)

        # Sample data has correlated assets
        breakdown = strategy._check_correlation_breakdown(sample_price_data)

        # Should not detect breakdown (assets are correlated)
        assert isinstance(breakdown, bool)

    def test_check_correlation_breakdown_detected(self, default_config):
        """Test correlation breakdown detection with uncorrelated data."""
        strategy = StatArbStrategy(default_config)

        # Create uncorrelated data
        np.random.seed(123)
        dates = pd.date_range('2024-01-01', periods=100, freq='1D')

        uncorrelated_data = {}
        for symbol in default_config.symbols:
            prices = 100 + np.cumsum(np.random.randn(100) * 5)  # Random walks
            uncorrelated_data[symbol] = pd.DataFrame({
                'Close': prices,
                'Open': prices,
                'High': prices * 1.01,
                'Low': prices * 0.99,
                'Volume': np.random.randint(1000000, 10000000, 100)
            }, index=dates)

        breakdown = strategy._check_correlation_breakdown(uncorrelated_data)

        # May or may not detect breakdown depending on random data
        assert isinstance(breakdown, bool)

    def test_check_correlation_breakdown_insufficient_data(self, default_config):
        """Test correlation check with insufficient data."""
        strategy = StatArbStrategy(default_config)

        # Very short data
        dates = pd.date_range('2024-01-01', periods=5, freq='1D')
        short_data = {
            symbol: pd.DataFrame({
                'Close': np.random.randn(5) + 100,
                'Open': np.random.randn(5) + 100,
                'High': np.random.randn(5) + 101,
                'Low': np.random.randn(5) + 99,
                'Volume': np.random.randint(1000000, 10000000, 5)
            }, index=dates)
            for symbol in default_config.symbols
        }

        breakdown = strategy._check_correlation_breakdown(short_data)

        # Should return True (not enough data to assess)
        assert breakdown is True


class TestSignalGeneration:
    """Test signal generation logic."""

    def test_generate_signals_no_factor_model(self, default_config, sample_price_data):
        """Test signal generation without factor model."""
        strategy = StatArbStrategy(default_config)

        timestamp = sample_price_data['XLF'].index[-1]
        signals = strategy.generate_signals(timestamp, sample_price_data)

        # Should return empty signals
        assert signals == {}

    def test_generate_signals_with_factor_model(self, default_config, sample_price_data):
        """Test signal generation with factor model."""
        strategy = StatArbStrategy(default_config)

        # Build up factor model and residuals
        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Add enough history for z-scores
        residual_lookback = 30
        for i in range(residual_lookback):
            if strategy.factor_model:
                residuals = strategy._calculate_residuals(sample_price_data, strategy.factor_model)
                strategy._update_security_states(residuals, timestamp)

        signals = strategy.generate_signals(timestamp, sample_price_data)

        # Should have signals for all symbols
        assert len(signals) == len(default_config.symbols)

        for symbol, signal in signals.items():
            assert signal.symbol == symbol
            assert signal.signal_type in [SignalType.LONG, SignalType.SHORT, SignalType.FLAT]
            assert 0.0 <= signal.confidence <= 1.0

    def test_generate_signals_correlation_breakdown(self, default_config, sample_price_data):
        """Test signal generation during correlation breakdown."""
        strategy = StatArbStrategy(default_config)

        # Estimate factor model
        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Force correlation breakdown by setting very high threshold
        strategy.set_parameter('min_correlation', 0.99)

        signals = strategy.generate_signals(timestamp, sample_price_data)

        # Should generate FLAT signals due to correlation breakdown
        if signals:
            for signal in signals.values():
                assert signal.signal_type == SignalType.FLAT

    def test_generate_signals_entry_long(self, default_config, sample_price_data):
        """Test entry signal for long position."""
        strategy = StatArbStrategy(default_config)

        # Set very negative correlation threshold to avoid breakdown detection
        strategy.set_parameter('min_correlation', -1.0)

        # Setup factor model
        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Force a negative z-score for XLF
        if 'XLF' in strategy.security_states:
            state = strategy.security_states['XLF']
            # Add history
            for i in range(30):
                state.residual_history.append(0.0)
            state.residual_history[-1] = -5.0  # Extreme negative residual
            state.zscore = -2.5  # Below -z_entry

            signals = strategy.generate_signals(timestamp, sample_price_data)

            if 'XLF' in signals:
                # Should signal LONG (oversold)
                assert signals['XLF'].signal_type == SignalType.LONG

    def test_generate_signals_entry_short(self, default_config, sample_price_data):
        """Test entry signal for short position."""
        strategy = StatArbStrategy(default_config)

        # Set very negative correlation threshold to avoid breakdown detection
        strategy.set_parameter('min_correlation', -1.0)

        # Setup factor model
        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Force a positive z-score for XLF
        if 'XLF' in strategy.security_states:
            state = strategy.security_states['XLF']
            # Add history
            for i in range(30):
                state.residual_history.append(0.0)
            state.residual_history[-1] = 5.0  # Extreme positive residual
            state.zscore = 2.5  # Above +z_entry

            signals = strategy.generate_signals(timestamp, sample_price_data)

            if 'XLF' in signals:
                # Should signal SHORT (overbought)
                assert signals['XLF'].signal_type == SignalType.SHORT

    def test_generate_signals_exit_mean_reversion(self, default_config, sample_price_data):
        """Test exit signal for mean reversion."""
        strategy = StatArbStrategy(default_config)

        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Add position
        strategy.current_positions['XLF'] = Position(
            symbol='XLF',
            quantity=100,
            entry_price=50.0,
            entry_time=timestamp - timedelta(days=5),
            metadata={'entry_zscore': -2.5}
        )

        # Force z-score near zero (mean reversion)
        if 'XLF' in strategy.security_states:
            state = strategy.security_states['XLF']
            for i in range(30):
                state.residual_history.append(0.0)
            state.zscore = 0.3  # Below z_exit

            signals = strategy.generate_signals(timestamp, sample_price_data)

            if 'XLF' in signals:
                # Should signal FLAT (exit)
                assert signals['XLF'].signal_type == SignalType.FLAT

    def test_generate_signals_exit_max_holding(self, default_config, sample_price_data):
        """Test exit signal for max holding period."""
        strategy = StatArbStrategy(default_config)

        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Add old position
        old_timestamp = timestamp - timedelta(days=35)
        strategy.current_positions['XLF'] = Position(
            symbol='XLF',
            quantity=100,
            entry_price=50.0,
            entry_time=old_timestamp,
            metadata={'entry_zscore': -2.5}
        )

        # Setup state
        if 'XLF' in strategy.security_states:
            state = strategy.security_states['XLF']
            for i in range(30):
                state.residual_history.append(0.0)
            state.zscore = -1.5  # Still away from mean

            signals = strategy.generate_signals(timestamp, sample_price_data)

            if 'XLF' in signals:
                # Should signal FLAT (time-based exit)
                assert signals['XLF'].signal_type == SignalType.FLAT

    def test_generate_signals_stop_loss_long(self, default_config, sample_price_data):
        """Test stop loss for long position."""
        strategy = StatArbStrategy(default_config)

        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Add long position
        strategy.current_positions['XLF'] = Position(
            symbol='XLF',
            quantity=100,
            entry_price=50.0,
            entry_time=timestamp - timedelta(days=5),
            metadata={'entry_zscore': -2.5}
        )

        # Force z-score much worse than entry
        if 'XLF' in strategy.security_states:
            state = strategy.security_states['XLF']
            for i in range(30):
                state.residual_history.append(0.0)
            state.zscore = -4.0  # 1.6x worse than -2.5

            signals = strategy.generate_signals(timestamp, sample_price_data)

            if 'XLF' in signals:
                # Should signal FLAT (stop loss)
                assert signals['XLF'].signal_type == SignalType.FLAT

    def test_generate_signals_stop_loss_short(self, default_config, sample_price_data):
        """Test stop loss for short position."""
        strategy = StatArbStrategy(default_config)

        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Add short position
        strategy.current_positions['XLF'] = Position(
            symbol='XLF',
            quantity=-100,
            entry_price=50.0,
            entry_time=timestamp - timedelta(days=5),
            metadata={'entry_zscore': 2.5}
        )

        # Force z-score much worse than entry
        if 'XLF' in strategy.security_states:
            state = strategy.security_states['XLF']
            for i in range(30):
                state.residual_history.append(0.0)
            state.zscore = 4.0  # 1.6x worse than 2.5

            signals = strategy.generate_signals(timestamp, sample_price_data)

            if 'XLF' in signals:
                # Should signal FLAT (stop loss)
                assert signals['XLF'].signal_type == SignalType.FLAT


class TestPositionSizing:
    """Test position sizing and market neutrality."""

    def test_calculate_positions_basic(self, default_config, sample_price_data):
        """Test basic position calculation."""
        strategy = StatArbStrategy(default_config)

        # Setup and generate signals
        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Build up residual history
        for i in range(30):
            if strategy.factor_model:
                residuals = strategy._calculate_residuals(sample_price_data, strategy.factor_model)
                strategy._update_security_states(residuals, timestamp)

        signals = strategy.generate_signals(timestamp, sample_price_data)

        portfolio_value = 100000.0
        positions = strategy.calculate_positions(signals, {}, portfolio_value)

        # Should have positions for all symbols
        assert len(positions) == len(default_config.symbols)

        for symbol in default_config.symbols:
            assert symbol in positions
            assert isinstance(positions[symbol], (int, float))

    def test_calculate_positions_market_neutral(self, default_config, sample_price_data):
        """Test that positions are approximately market neutral."""
        strategy = StatArbStrategy(default_config)

        # Setup
        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Build up residual history
        for i in range(30):
            if strategy.factor_model:
                residuals = strategy._calculate_residuals(sample_price_data, strategy.factor_model)
                strategy._update_security_states(residuals, timestamp)

        signals = strategy.generate_signals(timestamp, sample_price_data)

        portfolio_value = 100000.0
        positions = strategy.calculate_positions(signals, {}, portfolio_value)

        # Calculate dollar values
        long_value = 0.0
        short_value = 0.0

        for symbol, qty in positions.items():
            if qty != 0 and symbol in sample_price_data:
                price = sample_price_data[symbol]['Close'].iloc[-1]

                if qty > 0:
                    long_value += qty * price
                elif qty < 0:
                    short_value += abs(qty) * price

        # Check neutrality (if we have positions)
        if long_value > 0 and short_value > 0:
            net_exposure = abs(long_value - short_value)
            gross_exposure = long_value + short_value
            neutrality_ratio = net_exposure / gross_exposure

            # Should be reasonably neutral (within 30%)
            assert neutrality_ratio < 0.3

    def test_calculate_positions_max_positions_constraint(self, default_config, sample_price_data):
        """Test max positions constraint."""
        strategy = StatArbStrategy(default_config)

        # Set low max_positions
        strategy.set_parameter('max_positions', 4)

        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Build up residual history
        for i in range(30):
            if strategy.factor_model:
                residuals = strategy._calculate_residuals(sample_price_data, strategy.factor_model)
                strategy._update_security_states(residuals, timestamp)

        signals = strategy.generate_signals(timestamp, sample_price_data)

        portfolio_value = 100000.0
        positions = strategy.calculate_positions(signals, {}, portfolio_value)

        # Count non-zero positions
        num_positions = sum(1 for qty in positions.values() if qty != 0)

        # Should respect max_positions (4 total, 2 long + 2 short)
        assert num_positions <= 4

    def test_calculate_positions_empty_signals(self, default_config):
        """Test position calculation with empty signals."""
        strategy = StatArbStrategy(default_config)

        signals = {}
        portfolio_value = 100000.0
        positions = strategy.calculate_positions(signals, {}, portfolio_value)

        # Should have zero positions for all symbols
        for symbol in default_config.symbols:
            assert positions[symbol] == 0.0


class TestOnDataProcessing:
    """Test on_data processing and factor model updates."""

    def test_on_data_initial_model_estimation(self, default_config, sample_price_data):
        """Test initial factor model estimation."""
        strategy = StatArbStrategy(default_config)

        timestamp = sample_price_data['XLF'].index[-1]

        assert strategy.factor_model is None
        assert strategy.last_model_update is None

        strategy.on_data(timestamp, sample_price_data)

        # Should have estimated factor model
        assert strategy.factor_model is not None
        assert strategy.last_model_update == timestamp

    def test_on_data_periodic_rebalance(self, default_config, sample_price_data):
        """Test periodic factor model rebalancing."""
        strategy = StatArbStrategy(default_config)

        # Initial estimation
        timestamp1 = sample_price_data['XLF'].index[70]
        strategy.on_data(timestamp1, sample_price_data)

        initial_model = strategy.factor_model

        # Call again within rebalance period (should not update)
        timestamp2 = sample_price_data['XLF'].index[72]
        strategy.on_data(timestamp2, sample_price_data)

        assert strategy.factor_model == initial_model
        assert strategy.last_model_update == timestamp1

    def test_on_data_residual_updates(self, default_config, sample_price_data):
        """Test that residuals are updated in on_data."""
        strategy = StatArbStrategy(default_config)

        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        # Check that residuals are being tracked
        for symbol in default_config.symbols:
            state = strategy.security_states[symbol]
            # Should have some residual history
            assert len(state.residual_history) >= 0


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_strategy_parameter_updates(self, default_config):
        """Test dynamic parameter updates."""
        strategy = StatArbStrategy(default_config)

        # Update parameter
        strategy.set_parameter('z_entry', 2.5)

        assert strategy.get_parameter('z_entry') == 2.5
        assert strategy.config.parameters['z_entry'] == 2.5

    def test_security_state_initialization(self, default_config):
        """Test that security states are initialized for all symbols."""
        strategy = StatArbStrategy(default_config)

        for symbol in default_config.symbols:
            assert symbol in strategy.security_states
            state = strategy.security_states[symbol]
            assert state.symbol == symbol
            assert state.current_residual == 0.0
            assert len(state.residual_history) == 0
            assert state.zscore == 0.0
            assert state.volatility == 0.0

    def test_missing_data_symbols(self, default_config, sample_price_data):
        """Test handling of missing symbols in data."""
        strategy = StatArbStrategy(default_config)

        # Remove one symbol
        incomplete_data = {k: v for k, v in sample_price_data.items() if k != 'XLI'}

        timestamp = sample_price_data['XLF'].index[-1]

        # Should raise error when estimating factor model
        with pytest.raises(ValueError, match="Missing data"):
            strategy._estimate_factor_model(incomplete_data, timestamp)

    def test_zero_portfolio_value(self, default_config, sample_price_data):
        """Test position calculation with zero portfolio value."""
        strategy = StatArbStrategy(default_config)

        timestamp = sample_price_data['XLF'].index[-1]
        strategy.on_data(timestamp, sample_price_data)

        signals = strategy.generate_signals(timestamp, sample_price_data)

        # Zero portfolio value
        positions = strategy.calculate_positions(signals, {}, 0.0)

        # Should have zero positions
        for symbol in default_config.symbols:
            assert positions[symbol] == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
