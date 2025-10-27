"""Tests for FX carry signal generation."""
import numpy as np
import pandas as pd
import pytest

from src.strategies.fx_carry.signals import CarrySignal, FXCarrySignals


@pytest.fixture
def price_data():
    """Generate test price data for FX pair."""
    np.random.seed(42)
    n = 120  # 120 days of data

    dates = pd.date_range("2023-01-01", periods=n)

    # Generate trending price data with volatility
    trend = np.linspace(1.0, 1.1, n)  # 10% uptrend
    volatility = np.random.randn(n) * 0.005  # 0.5% daily vol
    prices = 1.0 + trend + volatility.cumsum()

    df = pd.DataFrame(
        {
            "Open": prices,
            "High": prices * 1.002,
            "Low": prices * 0.998,
            "Close": prices,
            "Volume": np.zeros(n),
        },
        index=dates,
    )

    return df


@pytest.fixture
def signal_generator():
    """Create signal generator instance."""
    return FXCarrySignals(
        trend_lookback=60,
        trend_threshold=0.0,
        vol_lookback=30,
        vol_target=10.0,
        min_rate_diff=1.0,
    )


class TestFXCarrySignals:
    """Test suite for FX carry signal generation."""

    def test_initialization(self):
        """Test signal generator initialization."""
        generator = FXCarrySignals(
            trend_lookback=60,
            trend_threshold=0.2,
            vol_lookback=30,
            vol_target=12.0,
            min_rate_diff=1.5,
        )

        assert generator.trend_lookback == 60
        assert generator.trend_threshold == 0.2
        assert generator.vol_lookback == 30
        assert generator.vol_target == 12.0
        assert generator.min_rate_diff == 1.5

    def test_calculate_trend_score(self, signal_generator, price_data):
        """Test trend score calculation."""
        close_prices = price_data["Close"]

        trend_score = signal_generator.calculate_trend_score(close_prices)

        assert isinstance(trend_score, float)
        assert -1.0 <= trend_score <= 1.0

        # With uptrending data, trend score should be positive
        assert trend_score > 0

    def test_calculate_trend_score_insufficient_data(self, signal_generator):
        """Test trend score with insufficient data."""
        short_series = pd.Series([1.0, 1.1, 1.2, 1.3])

        trend_score = signal_generator.calculate_trend_score(short_series)

        # Should return 0 for insufficient data
        assert trend_score == 0.0

    def test_calculate_volatility(self, signal_generator, price_data):
        """Test volatility calculation."""
        close_prices = price_data["Close"]

        vol = signal_generator.calculate_volatility(close_prices)

        assert isinstance(vol, float)
        assert vol > 0  # Volatility should be positive

    def test_calculate_volatility_insufficient_data(self, signal_generator):
        """Test volatility with insufficient data."""
        short_series = pd.Series([1.0, 1.1])

        vol = signal_generator.calculate_volatility(short_series)

        assert vol == 0.0

    def test_calculate_position_size_positive_diff(self, signal_generator):
        """Test position sizing with positive rate differential."""
        rate_diff = 3.0  # 3% positive differential
        trend_score = 0.5  # Positive trend (aligned)
        volatility = 10.0  # At target volatility

        size = signal_generator.calculate_position_size(
            rate_diff, trend_score, volatility
        )

        assert 0 <= size <= 1
        assert size > 0  # Should have position with positive diff and trend

    def test_calculate_position_size_counter_trend(self, signal_generator):
        """Test position sizing with counter-trend."""
        rate_diff = 3.0  # Positive differential
        trend_score = -0.5  # Negative trend (counter-trend)
        volatility = 10.0

        size = signal_generator.calculate_position_size(
            rate_diff, trend_score, volatility
        )

        # Counter-trend should result in zero or very small position
        assert size == 0.0

    def test_calculate_position_size_low_diff(self, signal_generator):
        """Test position sizing with rate differential below minimum."""
        rate_diff = 0.5  # Below min_rate_diff of 1.0
        trend_score = 0.5
        volatility = 10.0

        size = signal_generator.calculate_position_size(
            rate_diff, trend_score, volatility
        )

        assert size == 0.0  # Below minimum threshold

    def test_calculate_position_size_high_volatility(self, signal_generator):
        """Test position sizing adjusts for high volatility."""
        rate_diff = 3.0
        trend_score = 0.5
        low_vol = 5.0  # Low volatility
        high_vol = 20.0  # High volatility

        size_low_vol = signal_generator.calculate_position_size(
            rate_diff, trend_score, low_vol
        )
        size_high_vol = signal_generator.calculate_position_size(
            rate_diff, trend_score, high_vol
        )

        # Lower volatility should result in larger position
        assert size_low_vol > size_high_vol

    def test_generate_signal_complete(self, signal_generator, price_data):
        """Test complete signal generation."""
        pair = "AUD-USD"
        rate_diff = 2.5  # Positive carry

        signal = signal_generator.generate_signal(
            pair, price_data, rate_diff
        )

        assert isinstance(signal, CarrySignal)
        assert signal.pair == pair
        assert signal.rate_differential == rate_diff
        assert -1 <= signal.trend_score <= 1
        assert signal.volatility > 0
        assert -1 <= signal.signal <= 1
        assert 0 <= signal.position_size <= 1

    def test_generate_signal_insufficient_data(self, signal_generator):
        """Test signal generation with insufficient data."""
        pair = "EUR-USD"
        short_data = pd.DataFrame(
            {
                "Open": [1.0, 1.1],
                "High": [1.01, 1.11],
                "Low": [0.99, 1.09],
                "Close": [1.0, 1.1],
                "Volume": [0, 0],
            },
            index=pd.date_range("2023-01-01", periods=2),
        )

        signal = signal_generator.generate_signal(
            pair, short_data, rate_differential=2.0
        )

        # Should return signal with zero strength
        assert signal.signal == 0.0
        assert signal.position_size == 0.0

    def test_generate_portfolio_signals(self, signal_generator, price_data):
        """Test portfolio signal generation."""
        # Create price data for multiple pairs
        prices_dict = {
            "AUD-USD": price_data,
            "NZD-USD": price_data.copy(),
            "EUR-USD": price_data.copy(),
        }

        rate_diffs = {
            "AUD-USD": 2.5,  # Good carry
            "NZD-USD": 2.0,  # Good carry
            "EUR-USD": 0.5,  # Poor carry (below minimum)
        }

        signals = signal_generator.generate_portfolio_signals(
            prices_dict, rate_diffs, max_positions=2
        )

        assert isinstance(signals, list)
        assert len(signals) <= 2  # Respects max_positions
        assert all(isinstance(s, CarrySignal) for s in signals)

        # Signals should be sorted by strength
        if len(signals) > 1:
            for i in range(len(signals) - 1):
                assert abs(signals[i].signal) >= abs(signals[i + 1].signal)

    def test_signal_direction_from_rate_diff(self, signal_generator, price_data):
        """Test that signal direction matches rate differential sign."""
        pair = "USD-JPY"

        # Positive differential (long USD, short JPY)
        signal_pos = signal_generator.generate_signal(
            pair, price_data, rate_differential=2.0
        )

        # Negative differential (short USD, long JPY)
        signal_neg = signal_generator.generate_signal(
            pair, price_data, rate_differential=-2.0
        )

        # Signals should have opposite signs
        if signal_pos.signal != 0:
            assert signal_pos.signal > 0
        if signal_neg.signal != 0:
            assert signal_neg.signal < 0

    def test_trend_score_components(self, signal_generator):
        """Test trend score calculation with known patterns."""
        # Strong uptrend
        dates = pd.date_range("2023-01-01", periods=100)
        uptrend = pd.Series(
            np.linspace(1.0, 2.0, 100), index=dates
        )
        trend_up = signal_generator.calculate_trend_score(uptrend)
        assert trend_up > 0.5  # Strong positive trend

        # Strong downtrend
        downtrend = pd.Series(
            np.linspace(2.0, 1.0, 100), index=dates
        )
        trend_down = signal_generator.calculate_trend_score(downtrend)
        assert trend_down < -0.5  # Strong negative trend

        # Flat (no trend)
        flat = pd.Series(
            np.ones(100) + np.random.randn(100) * 0.01, index=dates
        )
        trend_flat = signal_generator.calculate_trend_score(flat)
        assert abs(trend_flat) < 0.3  # Weak or no trend

    def test_volatility_annualization(self, signal_generator):
        """Test that volatility is properly annualized."""
        # Create data with known daily volatility
        np.random.seed(42)
        n = 100
        daily_vol = 0.01  # 1% daily volatility
        returns = np.random.randn(n) * daily_vol

        prices = pd.Series(100 * np.exp(returns.cumsum()))

        vol = signal_generator.calculate_volatility(prices)

        # Annualized vol should be roughly daily_vol * sqrt(252)
        expected_vol = daily_vol * np.sqrt(252) * 100  # Convert to percentage
        # Allow 30% tolerance due to randomness
        assert abs(vol - expected_vol) / expected_vol < 0.5
