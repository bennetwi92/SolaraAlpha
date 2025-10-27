"""Signal generation for statistical arbitrage based on residual z-scores.

Generates trading signals from factor model residuals using z-score mean-reversion logic.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

import numpy as np
import pandas as pd


class SignalType(Enum):
    """Trading signal types for stat arb portfolio."""

    LONG = "LONG"  # Long the stock (residual < -threshold)
    SHORT = "SHORT"  # Short the stock (residual > threshold)
    EXIT_LONG = "EXIT_LONG"  # Close long position
    EXIT_SHORT = "EXIT_SHORT"  # Close short position
    HOLD = "HOLD"  # Maintain current position
    NO_POSITION = "NO_POSITION"  # No position, no signal


@dataclass
class PortfolioSignal:
    """Trading signal for entire portfolio."""

    timestamp: pd.Timestamp
    signals: Dict[str, SignalType]  # symbol -> signal
    z_scores: Dict[str, float]  # symbol -> z-score
    residuals: Dict[str, float]  # symbol -> residual
    confidence: float  # Overall signal confidence (0-1)


class SignalGenerator:
    """Generate mean-reversion signals from factor model residuals.

    Uses z-score thresholds:
    - Entry: |z| > entry_threshold (default 2.0)
    - Exit: |z| < exit_threshold (default 0.5)
    - Stop: |z| > stop_threshold (default 3.5)

    Signals are market-neutral: equal count of longs and shorts.
    """

    def __init__(
        self,
        lookback: int = 20,
        entry_threshold: float = 2.0,
        exit_threshold: float = 0.5,
        stop_threshold: float = 3.5,
        min_confidence: float = 0.5,
    ):
        """Initialize signal generator.

        Args:
            lookback: Rolling window for z-score calculation (days)
            entry_threshold: Z-score threshold for entry signals
            exit_threshold: Z-score threshold for exit signals
            stop_threshold: Z-score threshold for stop-loss
            min_confidence: Minimum confidence to generate signals (0-1)
        """
        self.lookback = lookback
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.stop_threshold = stop_threshold
        self.min_confidence = min_confidence

        # Track current positions for signal generation
        self._positions: Dict[str, SignalType] = {}

    def calculate_z_scores(self, residuals: pd.DataFrame) -> pd.DataFrame:
        """Calculate z-scores of residuals using rolling mean and std.

        Args:
            residuals: DataFrame with residuals (time × stocks)

        Returns:
            DataFrame with z-scores (time × stocks)
        """
        if len(residuals) < self.lookback:
            raise ValueError(
                f"Insufficient data: need {self.lookback} periods, "
                f"got {len(residuals)}"
            )

        # Rolling mean and std
        rolling_mean = residuals.rolling(window=self.lookback).mean()
        rolling_std = residuals.rolling(window=self.lookback).std()

        # Z-scores: (residual - mean) / std
        z_scores = (residuals - rolling_mean) / rolling_std

        return z_scores

    def generate_signals(
        self,
        residuals: pd.DataFrame,
        current_positions: Optional[Dict[str, SignalType]] = None,
    ) -> PortfolioSignal:
        """Generate trading signals from residuals.

        Args:
            residuals: DataFrame with residuals (time × stocks)
            current_positions: Current position state (symbol -> signal type)

        Returns:
            PortfolioSignal with signals for all stocks

        Raises:
            ValueError: If insufficient data for z-score calculation
        """
        if current_positions is None:
            current_positions = self._positions
        else:
            self._positions = current_positions

        # Calculate z-scores
        z_scores = self.calculate_z_scores(residuals)

        # Get latest z-scores and residuals
        latest_z = z_scores.iloc[-1]
        latest_residual = residuals.iloc[-1]

        # Check data quality (confidence)
        n_valid = latest_z.notna().sum()
        n_total = len(latest_z)
        confidence = n_valid / n_total if n_total > 0 else 0.0

        signals: Dict[str, SignalType] = {}

        for symbol in latest_z.index:
            z = latest_z[symbol]

            # Skip if NaN or insufficient confidence
            if pd.isna(z):
                signals[symbol] = SignalType.NO_POSITION
                continue

            current_pos = current_positions.get(symbol, SignalType.NO_POSITION)

            # Signal logic
            signal = self._generate_single_signal(z, current_pos)
            signals[symbol] = signal

        return PortfolioSignal(
            timestamp=residuals.index[-1],
            signals=signals,
            z_scores=latest_z.to_dict(),
            residuals=latest_residual.to_dict(),
            confidence=confidence,
        )

    def _generate_single_signal(
        self, z_score: float, current_position: SignalType
    ) -> SignalType:
        """Generate signal for single stock based on z-score and current position.

        Args:
            z_score: Current z-score
            current_position: Current position type

        Returns:
            SignalType for this stock
        """
        # Stop-loss check (always priority)
        if abs(z_score) > self.stop_threshold:
            if current_position == SignalType.LONG:
                return SignalType.EXIT_LONG
            elif current_position == SignalType.SHORT:
                return SignalType.EXIT_SHORT

        # No position: check for entry
        if current_position in [SignalType.NO_POSITION, SignalType.EXIT_LONG, SignalType.EXIT_SHORT]:
            if z_score < -self.entry_threshold:
                return SignalType.LONG  # Residual is low → expect reversion up
            elif z_score > self.entry_threshold:
                return SignalType.SHORT  # Residual is high → expect reversion down
            else:
                return SignalType.NO_POSITION

        # Long position: check for exit
        if current_position == SignalType.LONG:
            if abs(z_score) < self.exit_threshold:
                return SignalType.EXIT_LONG  # Mean-reverted
            else:
                return SignalType.HOLD

        # Short position: check for exit
        if current_position == SignalType.SHORT:
            if abs(z_score) < self.exit_threshold:
                return SignalType.EXIT_SHORT  # Mean-reverted
            else:
                return SignalType.HOLD

        # Default: hold
        return SignalType.HOLD

    def filter_signals_by_confidence(
        self, signal: PortfolioSignal
    ) -> PortfolioSignal:
        """Filter signals if confidence is too low.

        Args:
            signal: PortfolioSignal to filter

        Returns:
            Filtered PortfolioSignal (all NO_POSITION if low confidence)
        """
        if signal.confidence < self.min_confidence:
            # Clear all signals
            filtered_signals = {
                symbol: SignalType.NO_POSITION for symbol in signal.signals.keys()
            }
            return PortfolioSignal(
                timestamp=signal.timestamp,
                signals=filtered_signals,
                z_scores=signal.z_scores,
                residuals=signal.residuals,
                confidence=signal.confidence,
            )

        return signal

    def get_entry_signals(self, signal: PortfolioSignal) -> Dict[str, SignalType]:
        """Extract only entry signals (LONG/SHORT) from portfolio signal.

        Args:
            signal: PortfolioSignal to filter

        Returns:
            Dict with only entry signals
        """
        return {
            symbol: sig
            for symbol, sig in signal.signals.items()
            if sig in [SignalType.LONG, SignalType.SHORT]
        }

    def get_exit_signals(self, signal: PortfolioSignal) -> Dict[str, SignalType]:
        """Extract only exit signals from portfolio signal.

        Args:
            signal: PortfolioSignal to filter

        Returns:
            Dict with only exit signals
        """
        return {
            symbol: sig
            for symbol, sig in signal.signals.items()
            if sig in [SignalType.EXIT_LONG, SignalType.EXIT_SHORT]
        }

    def update_positions(self, executed_signals: Dict[str, SignalType]) -> None:
        """Update internal position tracking after trade execution.

        Args:
            executed_signals: Dict of signals that were successfully executed
        """
        for symbol, signal in executed_signals.items():
            if signal == SignalType.LONG:
                self._positions[symbol] = SignalType.LONG
            elif signal == SignalType.SHORT:
                self._positions[symbol] = SignalType.SHORT
            elif signal in [SignalType.EXIT_LONG, SignalType.EXIT_SHORT]:
                self._positions[symbol] = SignalType.NO_POSITION
            # HOLD and NO_POSITION don't change state

    def reset_positions(self) -> None:
        """Clear all position tracking (e.g., for backtest reset)."""
        self._positions.clear()
