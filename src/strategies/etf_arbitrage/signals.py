"""Signal generation logic for ETF arbitrage strategy.

Generates entry/exit signals based on z-score thresholds and mean reversion.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pandas as pd
import numpy as np


class SignalType(Enum):
    """Signal types for ETF arbitrage."""
    NO_SIGNAL = 'no_signal'
    ENTER_LONG_SPREAD = 'enter_long_spread'  # Long ETF1, Short ETF2
    ENTER_SHORT_SPREAD = 'enter_short_spread'  # Short ETF1, Long ETF2
    EXIT_POSITION = 'exit_position'
    STOP_LOSS = 'stop_loss'


@dataclass
class Signal:
    """Trading signal with metadata."""
    signal_type: SignalType
    z_score: float
    spread: float
    timestamp: pd.Timestamp
    reason: str


@dataclass
class SignalConfig:
    """Configuration for signal generation."""
    entry_threshold: float = 2.0  # Entry at ±2σ
    exit_threshold: float = 0.5   # Exit at ±0.5σ (mean reversion)
    stop_loss_threshold: float = 3.0  # Stop loss at ±3σ
    min_half_life: float = 1.0  # Minimum mean reversion half-life in days
    max_half_life: float = 30.0  # Maximum mean reversion half-life in days


class SignalGenerator:
    """Generate trading signals based on z-score thresholds."""

    def __init__(self, config: Optional[SignalConfig] = None):
        """Initialize signal generator.

        Args:
            config: Signal configuration (uses defaults if None)
        """
        self.config = config or SignalConfig()
        self._current_position = None  # 'long_spread', 'short_spread', or None

    def generate_signal(
        self,
        z_score: float,
        spread: float,
        half_life: float,
        timestamp: pd.Timestamp,
        current_position: Optional[str] = None
    ) -> Signal:
        """Generate trading signal based on current state.

        Args:
            z_score: Current z-score of spread
            spread: Current spread value
            half_life: Mean reversion half-life in days
            timestamp: Current timestamp
            current_position: 'long_spread', 'short_spread', or None

        Returns:
            Signal with recommended action
        """
        # Check if spread is mean-reverting (half-life in valid range)
        if not self._is_mean_reverting(half_life):
            return Signal(
                signal_type=SignalType.NO_SIGNAL,
                z_score=z_score,
                spread=spread,
                timestamp=timestamp,
                reason=f"Non-mean-reverting spread (half-life={half_life:.1f}d)"
            )

        # Check for invalid z-score
        if np.isnan(z_score) or np.isinf(z_score):
            return Signal(
                signal_type=SignalType.NO_SIGNAL,
                z_score=z_score,
                spread=spread,
                timestamp=timestamp,
                reason="Invalid z-score"
            )

        # If in position, check for exit or stop-loss
        if current_position is not None:
            return self._check_exit_signals(
                z_score, spread, timestamp, current_position
            )

        # If no position, check for entry signals
        return self._check_entry_signals(z_score, spread, timestamp)

    def _is_mean_reverting(self, half_life: float) -> bool:
        """Check if spread exhibits mean reversion.

        Args:
            half_life: Mean reversion half-life in days

        Returns:
            True if half-life is in valid range
        """
        if np.isnan(half_life) or np.isinf(half_life):
            return False

        return self.config.min_half_life <= half_life <= self.config.max_half_life

    def _check_entry_signals(
        self,
        z_score: float,
        spread: float,
        timestamp: pd.Timestamp
    ) -> Signal:
        """Check for entry signals when not in position.

        Args:
            z_score: Current z-score
            spread: Current spread
            timestamp: Current timestamp

        Returns:
            Entry signal or no signal
        """
        # Spread is significantly below mean (ETF1 underpriced vs ETF2)
        if z_score <= -self.config.entry_threshold:
            return Signal(
                signal_type=SignalType.ENTER_LONG_SPREAD,
                z_score=z_score,
                spread=spread,
                timestamp=timestamp,
                reason=f"Z-score {z_score:.2f} <= -{self.config.entry_threshold}"
            )

        # Spread is significantly above mean (ETF1 overpriced vs ETF2)
        if z_score >= self.config.entry_threshold:
            return Signal(
                signal_type=SignalType.ENTER_SHORT_SPREAD,
                z_score=z_score,
                spread=spread,
                timestamp=timestamp,
                reason=f"Z-score {z_score:.2f} >= {self.config.entry_threshold}"
            )

        return Signal(
            signal_type=SignalType.NO_SIGNAL,
            z_score=z_score,
            spread=spread,
            timestamp=timestamp,
            reason="No entry signal"
        )

    def _check_exit_signals(
        self,
        z_score: float,
        spread: float,
        timestamp: pd.Timestamp,
        current_position: str
    ) -> Signal:
        """Check for exit or stop-loss signals when in position.

        Args:
            z_score: Current z-score
            spread: Current spread
            timestamp: Current timestamp
            current_position: 'long_spread' or 'short_spread'

        Returns:
            Exit, stop-loss, or no signal
        """
        if current_position == 'long_spread':
            # Long spread (long ETF1, short ETF2)
            # Stop loss if spread continues to diverge downward
            if z_score <= -self.config.stop_loss_threshold:
                return Signal(
                    signal_type=SignalType.STOP_LOSS,
                    z_score=z_score,
                    spread=spread,
                    timestamp=timestamp,
                    reason=f"Stop loss: z-score {z_score:.2f} <= -{self.config.stop_loss_threshold}"
                )

            # Exit if spread reverts toward mean
            if z_score >= -self.config.exit_threshold:
                return Signal(
                    signal_type=SignalType.EXIT_POSITION,
                    z_score=z_score,
                    spread=spread,
                    timestamp=timestamp,
                    reason=f"Mean reversion: z-score {z_score:.2f} >= -{self.config.exit_threshold}"
                )

        elif current_position == 'short_spread':
            # Short spread (short ETF1, long ETF2)
            # Stop loss if spread continues to diverge upward
            if z_score >= self.config.stop_loss_threshold:
                return Signal(
                    signal_type=SignalType.STOP_LOSS,
                    z_score=z_score,
                    spread=spread,
                    timestamp=timestamp,
                    reason=f"Stop loss: z-score {z_score:.2f} >= {self.config.stop_loss_threshold}"
                )

            # Exit if spread reverts toward mean
            if z_score <= self.config.exit_threshold:
                return Signal(
                    signal_type=SignalType.EXIT_POSITION,
                    z_score=z_score,
                    spread=spread,
                    timestamp=timestamp,
                    reason=f"Mean reversion: z-score {z_score:.2f} <= {self.config.exit_threshold}"
                )

        return Signal(
            signal_type=SignalType.NO_SIGNAL,
            z_score=z_score,
            spread=spread,
            timestamp=timestamp,
            reason="Holding position"
        )

    def update_position(self, signal: Signal) -> None:
        """Update current position state based on signal.

        Args:
            signal: Generated signal
        """
        if signal.signal_type == SignalType.ENTER_LONG_SPREAD:
            self._current_position = 'long_spread'
        elif signal.signal_type == SignalType.ENTER_SHORT_SPREAD:
            self._current_position = 'short_spread'
        elif signal.signal_type in [SignalType.EXIT_POSITION, SignalType.STOP_LOSS]:
            self._current_position = None

    def get_current_position(self) -> Optional[str]:
        """Get current position state.

        Returns:
            'long_spread', 'short_spread', or None
        """
        return self._current_position
