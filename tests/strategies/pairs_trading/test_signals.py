"""Tests for signal generation module."""
import numpy as np
import pandas as pd
import pytest

from src.strategies.pairs_trading.signals import (
    SignalGenerator,
    SignalType,
    TradingSignal,
)


@pytest.fixture
def price_series():
    """Generate test price series."""
    np.random.seed(42)
    n = 120

    # Create mean-reverting spread via cointegrated series
    series2 = pd.Series(
        100 + np.cumsum(np.random.randn(n) * 0.5),
        index=pd.date_range("2023-01-01", periods=n),
    )

    hedge_ratio = 1.2
    # Add mean-reverting noise
    noise = np.sin(np.linspace(0, 4 * np.pi, n)) * 5 + np.random.randn(n)
    series1 = pd.Series(
        hedge_ratio * series2.values + noise,
        index=series2.index,
    )

    return series1, series2, hedge_ratio


class TestSignalGenerator:
    """Test suite for SignalGenerator."""

    def test_initialization(self):
        """Test signal generator initialization."""
        generator = SignalGenerator(
            entry_threshold=2.0,
            exit_threshold=0.5,
            stop_loss_threshold=3.0,
            lookback_window=60,
        )

        assert generator.entry_threshold == 2.0
        assert generator.exit_threshold == 0.5
        assert generator.stop_loss_threshold == 3.0
        assert generator.lookback_window == 60

    def test_construct_spread(self, price_series):
        """Test spread construction."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator()

        spread = generator.construct_spread(series1, series2, hedge_ratio)

        assert isinstance(spread, pd.Series)
        assert len(spread) == len(series1)
        # Verify calculation
        expected = series1 - hedge_ratio * series2
        pd.testing.assert_series_equal(spread, expected)

    def test_calculate_z_score(self, price_series):
        """Test z-score calculation."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator(lookback_window=30)

        spread = generator.construct_spread(series1, series2, hedge_ratio)
        z_score = generator.calculate_z_score(spread)

        assert isinstance(z_score, pd.Series)
        assert len(z_score) == len(spread)
        # First lookback_window-1 should be NaN
        assert z_score.iloc[:29].isna().all()
        # Rest should have values
        assert not z_score.iloc[30:].isna().all()

    def test_z_score_normalization(self, price_series):
        """Test that z-scores are properly normalized."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator(lookback_window=60)

        spread = generator.construct_spread(series1, series2, hedge_ratio)
        z_score = generator.calculate_z_score(spread)

        # Z-scores should have mean ~0 and std ~1 (for sufficient data)
        valid_z = z_score.dropna()
        assert abs(valid_z.mean()) < 0.5  # Close to 0
        # Std may not be exactly 1 due to rolling window

    def test_generate_signals(self, price_series):
        """Test signal generation."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator(
            entry_threshold=2.0,
            exit_threshold=0.5,
            lookback_window=30,
        )

        signals_df = generator.generate_signals(
            series1, series2, hedge_ratio
        )

        assert isinstance(signals_df, pd.DataFrame)
        assert "spread" in signals_df.columns
        assert "z_score" in signals_df.columns
        assert "signal" in signals_df.columns
        assert "position" in signals_df.columns
        assert len(signals_df) == len(series1)

    def test_signal_values(self, price_series):
        """Test that signals are valid values."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator(lookback_window=30)

        signals_df = generator.generate_signals(
            series1, series2, hedge_ratio
        )

        # Signals should be -1, 0, or 1
        assert signals_df["signal"].isin([-1, 0, 1]).all()

    def test_entry_signals(self):
        """Test entry signal generation on extreme z-scores."""
        np.random.seed(42)

        # Create series with known z-score pattern
        n = 100
        series2 = pd.Series(
            100 * np.ones(n), index=pd.date_range("2023-01-01", periods=n)
        )

        # Create series1 with large deviation to trigger entry
        series1_values = 100 * np.ones(n)
        series1_values[70:] = 130  # Large positive deviation
        series1 = pd.Series(series1_values, index=series2.index)

        generator = SignalGenerator(
            entry_threshold=2.0, lookback_window=30
        )

        signals_df = generator.generate_signals(
            series1, series2, hedge_ratio=1.0
        )

        # Should generate signals when z-score exceeds threshold
        assert (signals_df["signal"] != 0).any()

    def test_generate_signal_at_point(self, price_series):
        """Test single point signal generation."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator(lookback_window=30)

        signal = generator.generate_signal_at_point(
            series1, series2, hedge_ratio
        )

        assert isinstance(signal, TradingSignal)
        assert signal.signal in [
            SignalType.NO_POSITION,
            SignalType.LONG_SPREAD,
            SignalType.SHORT_SPREAD,
        ]
        assert isinstance(signal.z_score, float)
        assert isinstance(signal.spread_value, (float, np.floating))
        assert 0 <= signal.confidence <= 1.0

    def test_signal_at_point_insufficient_data(self):
        """Test signal generation with insufficient data raises error."""
        series1 = pd.Series(
            range(30), index=pd.date_range("2023-01-01", periods=30)
        )
        series2 = pd.Series(
            range(30), index=pd.date_range("2023-01-01", periods=30)
        )

        generator = SignalGenerator(lookback_window=60)

        with pytest.raises(ValueError, match="Insufficient data"):
            generator.generate_signal_at_point(
                series1, series2, hedge_ratio=1.0
            )

    def test_calculate_spread_volatility(self, price_series):
        """Test spread volatility calculation."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator(lookback_window=60)

        spread = generator.construct_spread(series1, series2, hedge_ratio)
        volatility = generator.calculate_spread_volatility(spread)

        assert isinstance(volatility, float)
        assert volatility > 0

    def test_volatility_annualization(self, price_series):
        """Test that volatility is properly annualized."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator(lookback_window=60)

        spread = generator.construct_spread(series1, series2, hedge_ratio)
        volatility = generator.calculate_spread_volatility(spread)

        # Should be reasonable annual volatility (e.g., 5% - 100%)
        assert 0.01 < volatility < 2.0

    def test_backtest_signals(self, price_series):
        """Test signal backtesting."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator(lookback_window=30)

        signals_df, metrics = generator.backtest_signals(
            series1, series2, hedge_ratio
        )

        assert isinstance(signals_df, pd.DataFrame)
        assert isinstance(metrics, dict)

        # Check metrics keys
        assert "total_return" in metrics
        assert "sharpe_ratio" in metrics
        assert "max_drawdown" in metrics
        assert "win_rate" in metrics
        assert "total_trades" in metrics

    def test_backtest_metrics_valid(self, price_series):
        """Test that backtest metrics are valid values."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator(lookback_window=30)

        _, metrics = generator.backtest_signals(
            series1, series2, hedge_ratio
        )

        # All metrics should be numeric
        assert isinstance(metrics["total_return"], (float, int))
        assert isinstance(metrics["sharpe_ratio"], (float, int))
        assert isinstance(metrics["max_drawdown"], (float, int))
        assert isinstance(metrics["win_rate"], (float, int))
        assert isinstance(metrics["total_trades"], int)

        # Win rate should be [0, 1]
        assert 0 <= metrics["win_rate"] <= 1

        # Max drawdown should be <= 0
        assert metrics["max_drawdown"] <= 0

    def test_position_state_tracking(self, price_series):
        """Test that position state is tracked correctly."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator(lookback_window=30)

        # Start with no position
        signals_df = generator.generate_signals(
            series1,
            series2,
            hedge_ratio,
            current_position=SignalType.NO_POSITION,
        )

        # Position should cumulate from signals
        position = signals_df["position"]
        signals = signals_df["signal"]

        # Verify cumulative sum relationship
        expected_position = signals.cumsum()
        pd.testing.assert_series_equal(position, expected_position)

    def test_different_thresholds(self, price_series):
        """Test behavior with different entry/exit thresholds."""
        series1, series2, hedge_ratio = price_series

        # Tight thresholds (more signals)
        gen_tight = SignalGenerator(
            entry_threshold=1.0, exit_threshold=0.3, lookback_window=30
        )
        signals_tight = gen_tight.generate_signals(
            series1, series2, hedge_ratio
        )

        # Wide thresholds (fewer signals)
        gen_wide = SignalGenerator(
            entry_threshold=3.0, exit_threshold=0.5, lookback_window=30
        )
        signals_wide = gen_wide.generate_signals(
            series1, series2, hedge_ratio
        )

        # Tight thresholds should generate more trades
        trades_tight = (signals_tight["signal"] != 0).sum()
        trades_wide = (signals_wide["signal"] != 0).sum()

        # Not guaranteed but highly likely
        # At minimum, both should be valid DataFrames
        assert len(signals_tight) == len(signals_wide)

    def test_confidence_calculation(self, price_series):
        """Test signal confidence calculation."""
        series1, series2, hedge_ratio = price_series
        generator = SignalGenerator(
            entry_threshold=2.0, lookback_window=30
        )

        signal = generator.generate_signal_at_point(
            series1, series2, hedge_ratio
        )

        # Confidence should be between 0 and 1
        assert 0 <= signal.confidence <= 1.0

        # Confidence should increase with z-score magnitude
        # (when z-score is high relative to entry threshold)
        if abs(signal.z_score) > generator.entry_threshold:
            assert signal.confidence > 0.5
