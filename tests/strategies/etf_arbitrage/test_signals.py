"""Tests for signal generation module."""
import pytest
import pandas as pd
import numpy as np

from src.strategies.etf_arbitrage.signals import (
    SignalGenerator, Signal, SignalType, SignalConfig
)


class TestSignalGenerator:
    """Test cases for SignalGenerator."""

    @pytest.fixture
    def generator(self):
        """Create SignalGenerator with default config."""
        return SignalGenerator()

    @pytest.fixture
    def custom_generator(self):
        """Create SignalGenerator with custom config."""
        config = SignalConfig(
            entry_threshold=1.5,
            exit_threshold=0.3,
            stop_loss_threshold=2.5,
            min_half_life=0.5,
            max_half_life=20.0
        )
        return SignalGenerator(config=config)

    def test_generate_long_entry_signal(self, generator):
        """Test long spread entry signal generation."""
        signal = generator.generate_signal(
            z_score=-2.5,
            spread=0.95,
            half_life=5.0,
            timestamp=pd.Timestamp.now(),
            current_position=None
        )

        assert signal.signal_type == SignalType.ENTER_LONG_SPREAD
        assert signal.z_score == -2.5
        assert 'Z-score' in signal.reason

    def test_generate_short_entry_signal(self, generator):
        """Test short spread entry signal generation."""
        signal = generator.generate_signal(
            z_score=2.5,
            spread=1.05,
            half_life=5.0,
            timestamp=pd.Timestamp.now(),
            current_position=None
        )

        assert signal.signal_type == SignalType.ENTER_SHORT_SPREAD
        assert signal.z_score == 2.5

    def test_no_signal_within_threshold(self, generator):
        """Test no signal when z-score within threshold."""
        signal = generator.generate_signal(
            z_score=1.0,
            spread=1.0,
            half_life=5.0,
            timestamp=pd.Timestamp.now(),
            current_position=None
        )

        assert signal.signal_type == SignalType.NO_SIGNAL

    def test_exit_signal_from_long(self, generator):
        """Test exit signal from long position."""
        signal = generator.generate_signal(
            z_score=-0.3,  # Reverted toward mean
            spread=0.99,
            half_life=5.0,
            timestamp=pd.Timestamp.now(),
            current_position='long_spread'
        )

        assert signal.signal_type == SignalType.EXIT_POSITION

    def test_exit_signal_from_short(self, generator):
        """Test exit signal from short position."""
        signal = generator.generate_signal(
            z_score=0.3,  # Reverted toward mean
            spread=1.01,
            half_life=5.0,
            timestamp=pd.Timestamp.now(),
            current_position='short_spread'
        )

        assert signal.signal_type == SignalType.EXIT_POSITION

    def test_stop_loss_long_position(self, generator):
        """Test stop loss from long position."""
        signal = generator.generate_signal(
            z_score=-3.5,  # Continued divergence
            spread=0.90,
            half_life=5.0,
            timestamp=pd.Timestamp.now(),
            current_position='long_spread'
        )

        assert signal.signal_type == SignalType.STOP_LOSS

    def test_stop_loss_short_position(self, generator):
        """Test stop loss from short position."""
        signal = generator.generate_signal(
            z_score=3.5,  # Continued divergence
            spread=1.10,
            half_life=5.0,
            timestamp=pd.Timestamp.now(),
            current_position='short_spread'
        )

        assert signal.signal_type == SignalType.STOP_LOSS

    def test_non_mean_reverting_filter(self, generator):
        """Test filtering of non-mean-reverting spreads."""
        # Half-life too short
        signal = generator.generate_signal(
            z_score=-2.5,
            spread=0.95,
            half_life=0.5,  # Below min threshold
            timestamp=pd.Timestamp.now(),
            current_position=None
        )

        assert signal.signal_type == SignalType.NO_SIGNAL
        assert 'Non-mean-reverting' in signal.reason

        # Half-life too long
        signal = generator.generate_signal(
            z_score=-2.5,
            spread=0.95,
            half_life=50.0,  # Above max threshold
            timestamp=pd.Timestamp.now(),
            current_position=None
        )

        assert signal.signal_type == SignalType.NO_SIGNAL

    def test_invalid_z_score(self, generator):
        """Test handling of invalid z-score."""
        signal = generator.generate_signal(
            z_score=np.nan,
            spread=1.0,
            half_life=5.0,
            timestamp=pd.Timestamp.now(),
            current_position=None
        )

        assert signal.signal_type == SignalType.NO_SIGNAL
        assert 'Invalid' in signal.reason

    def test_custom_thresholds(self, custom_generator):
        """Test custom signal thresholds."""
        # Should trigger with lower threshold
        signal = custom_generator.generate_signal(
            z_score=-1.6,
            spread=0.98,
            half_life=5.0,
            timestamp=pd.Timestamp.now(),
            current_position=None
        )

        assert signal.signal_type == SignalType.ENTER_LONG_SPREAD

    def test_update_position(self, generator):
        """Test position state updates."""
        signal = Signal(
            signal_type=SignalType.ENTER_LONG_SPREAD,
            z_score=-2.5,
            spread=0.95,
            timestamp=pd.Timestamp.now(),
            reason='Test'
        )

        generator.update_position(signal)
        assert generator.get_current_position() == 'long_spread'

        exit_signal = Signal(
            signal_type=SignalType.EXIT_POSITION,
            z_score=-0.5,
            spread=0.99,
            timestamp=pd.Timestamp.now(),
            reason='Test'
        )

        generator.update_position(exit_signal)
        assert generator.get_current_position() is None
