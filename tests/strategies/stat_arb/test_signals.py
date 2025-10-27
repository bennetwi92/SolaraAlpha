"""Tests for signal generation."""

import numpy as np
import pandas as pd
import pytest

from src.strategies.stat_arb.signals import SignalGenerator, SignalType


@pytest.fixture
def sample_residuals():
    """Generate sample residuals."""
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=100, freq="D")

    # Generate mean-reverting residuals
    residuals = pd.DataFrame(
        np.random.randn(100, 10) * 0.01,
        index=dates,
        columns=[f"STOCK{i}" for i in range(10)],
    )

    return residuals


def test_signal_generator_init():
    """Test signal generator initialization."""
    gen = SignalGenerator(
        lookback=20, entry_threshold=2.0, exit_threshold=0.5, stop_threshold=3.5
    )

    assert gen.lookback == 20
    assert gen.entry_threshold == 2.0
    assert gen.exit_threshold == 0.5
    assert gen.stop_threshold == 3.5


def test_calculate_z_scores(sample_residuals):
    """Test z-score calculation."""
    gen = SignalGenerator(lookback=20)

    z_scores = gen.calculate_z_scores(sample_residuals)

    assert z_scores.shape == sample_residuals.shape
    # First lookback-1 rows should be NaN
    assert z_scores.iloc[: gen.lookback - 1].isna().all().all()


def test_generate_signals(sample_residuals):
    """Test signal generation."""
    gen = SignalGenerator(lookback=20, entry_threshold=2.0)

    signal = gen.generate_signals(sample_residuals)

    assert signal.timestamp == sample_residuals.index[-1]
    assert len(signal.signals) == 10
    assert len(signal.z_scores) == 10
    assert 0 <= signal.confidence <= 1


def test_signal_type_logic():
    """Test individual signal generation logic."""
    gen = SignalGenerator(entry_threshold=2.0, exit_threshold=0.5, stop_threshold=3.5)

    # Entry long signal (z < -2.0)
    signal = gen._generate_single_signal(-2.5, SignalType.NO_POSITION)
    assert signal == SignalType.LONG

    # Entry short signal (z > 2.0)
    signal = gen._generate_single_signal(2.5, SignalType.NO_POSITION)
    assert signal == SignalType.SHORT

    # Exit long signal (|z| < 0.5)
    signal = gen._generate_single_signal(0.3, SignalType.LONG)
    assert signal == SignalType.EXIT_LONG

    # Stop loss (|z| > 3.5)
    signal = gen._generate_single_signal(4.0, SignalType.LONG)
    assert signal == SignalType.EXIT_LONG
