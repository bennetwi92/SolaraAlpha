"""Signal generation for pairs trading strategy.

Z-score based mean-reversion signals with entry/exit thresholds.
Generates long/short signals when spread deviates from historical mean.
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Trading signal types."""

    NO_POSITION = 0  # Flat, no position
    LONG_SPREAD = 1  # Long asset1, short asset2 (spread undervalued)
    SHORT_SPREAD = -1  # Short asset1, long asset2 (spread overvalued)


@dataclass
class TradingSignal:
    """Trading signal with metadata.

    Attributes:
        signal: Signal type (long, short, or flat).
        z_score: Current spread z-score.
        spread_value: Raw spread value.
        entry_threshold: Z-score threshold that triggered entry.
        timestamp: Signal generation timestamp.
        confidence: Signal confidence [0, 1] based on z-score magnitude.
    """

    signal: SignalType
    z_score: float
    spread_value: float
    entry_threshold: float
    timestamp: pd.Timestamp
    confidence: float


class SignalGenerator:
    """Z-score based signal generation for pairs trading.

    Constructs spread from cointegrated pairs and generates mean-reversion
    signals based on z-score threshold crossings. Supports both entry and
    exit signal logic with configurable thresholds.

    Example:
        >>> generator = SignalGenerator(
        ...     entry_threshold=2.0,
        ...     exit_threshold=0.5,
        ...     lookback_window=60
        ... )
        >>> signals = generator.generate_signals(
        ...     prices_a, prices_b, hedge_ratio=1.2
        ... )
    """

    def __init__(
        self,
        entry_threshold: float = 2.0,
        exit_threshold: float = 0.5,
        stop_loss_threshold: float = 3.0,
        lookback_window: int = 60,
    ) -> None:
        """Initialize signal generator.

        Args:
            entry_threshold: Z-score threshold for trade entry (default 2.0σ).
            exit_threshold: Z-score threshold for position exit (default 0.5σ).
            stop_loss_threshold: Z-score threshold for stop loss (default 3.0σ).
            lookback_window: Rolling window for mean/std calculation (default 60).
        """
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.stop_loss_threshold = stop_loss_threshold
        self.lookback_window = lookback_window

    def construct_spread(
        self,
        series1: pd.Series,
        series2: pd.Series,
        hedge_ratio: float,
    ) -> pd.Series:
        """Construct price spread from cointegrated pair.

        Spread = series1 - hedge_ratio * series2

        When spread is positive (above mean), series1 is overvalued relative
        to series2 → Short spread (short series1, long series2).

        When spread is negative (below mean), series1 is undervalued relative
        to series2 → Long spread (long series1, short series2).

        Args:
            series1: First asset prices.
            series2: Second asset prices.
            hedge_ratio: Cointegration-derived hedge ratio.

        Returns:
            Spread series with same index as input.
        """
        spread = series1 - hedge_ratio * series2
        return spread

    def calculate_z_score(
        self,
        spread: pd.Series,
        lookback: Optional[int] = None,
    ) -> pd.Series:
        """Calculate rolling z-score of spread.

        Z-score = (spread - rolling_mean) / rolling_std

        Normalizes spread to standard deviations from mean, enabling
        threshold-based signal generation.

        Args:
            spread: Price spread series.
            lookback: Window size for rolling stats (uses instance default if None).

        Returns:
            Z-score series with same index as spread.
        """
        window = lookback or self.lookback_window

        rolling_mean = spread.rolling(window=window).mean()
        rolling_std = spread.rolling(window=window).std()

        # Avoid division by zero
        z_score = (spread - rolling_mean) / rolling_std.replace(0, np.nan)

        return z_score

    def generate_signals(
        self,
        series1: pd.Series,
        series2: pd.Series,
        hedge_ratio: float,
        current_position: SignalType = SignalType.NO_POSITION,
    ) -> pd.DataFrame:
        """Generate trading signals from price series.

        Args:
            series1: First asset prices.
            series2: Second asset prices.
            hedge_ratio: Cointegration hedge ratio.
            current_position: Current position state (default NO_POSITION).

        Returns:
            DataFrame with columns:
                - spread: Raw spread values
                - z_score: Spread z-scores
                - signal: Trading signal (-1, 0, 1)
                - position: Cumulative position from signals
        """
        # Construct spread
        spread = self.construct_spread(series1, series2, hedge_ratio)

        # Calculate z-scores
        z_score = self.calculate_z_score(spread)

        # Generate signals based on thresholds
        signals = pd.Series(0, index=spread.index, dtype=int)

        # Entry signals (no current position)
        # Long spread when z-score < -entry_threshold (spread undervalued)
        # Short spread when z-score > entry_threshold (spread overvalued)
        long_entry = z_score < -self.entry_threshold
        short_entry = z_score > self.entry_threshold

        # Exit signals (mean reversion)
        # Exit when |z-score| < exit_threshold
        mean_revert = z_score.abs() < self.exit_threshold

        # Stop loss signals (divergence beyond threshold)
        stop_loss_long = z_score > self.stop_loss_threshold
        stop_loss_short = z_score < -self.stop_loss_threshold

        # Construct signal series with state tracking
        position = current_position
        signal_list = []

        for i, idx in enumerate(spread.index):
            current_z = z_score.iloc[i]

            # Skip if insufficient data for z-score
            if pd.isna(current_z):
                signal_list.append(0)
                continue

            signal = 0

            # State machine for signal generation
            if position == SignalType.NO_POSITION:
                # Enter long spread position
                if long_entry.iloc[i]:
                    signal = 1
                    position = SignalType.LONG_SPREAD
                # Enter short spread position
                elif short_entry.iloc[i]:
                    signal = -1
                    position = SignalType.SHORT_SPREAD

            elif position == SignalType.LONG_SPREAD:
                # Exit long position on mean reversion or stop loss
                if mean_revert.iloc[i] or stop_loss_long.iloc[i]:
                    signal = -1  # Close position (opposite of entry)
                    position = SignalType.NO_POSITION

            elif position == SignalType.SHORT_SPREAD:
                # Exit short position on mean reversion or stop loss
                if mean_revert.iloc[i] or stop_loss_short.iloc[i]:
                    signal = 1  # Close position (opposite of entry)
                    position = SignalType.NO_POSITION

            signal_list.append(signal)

        signals = pd.Series(signal_list, index=spread.index, dtype=int)

        # Create output DataFrame
        result = pd.DataFrame(
            {
                "spread": spread,
                "z_score": z_score,
                "signal": signals,
                "position": signals.cumsum(),  # Track cumulative position
            }
        )

        return result

    def generate_signal_at_point(
        self,
        series1: pd.Series,
        series2: pd.Series,
        hedge_ratio: float,
        current_position: SignalType = SignalType.NO_POSITION,
    ) -> TradingSignal:
        """Generate single trading signal at most recent timestamp.

        Used for real-time signal generation in paper trading.

        Args:
            series1: First asset price history.
            series2: Second asset price history.
            hedge_ratio: Cointegration hedge ratio.
            current_position: Current position state.

        Returns:
            TradingSignal for latest timestamp.

        Raises:
            ValueError: If insufficient data for signal generation.
        """
        if len(series1) < self.lookback_window:
            raise ValueError(
                f"Insufficient data: {len(series1)} < {self.lookback_window}"
            )

        # Generate signals for full series
        signals_df = self.generate_signals(
            series1, series2, hedge_ratio, current_position
        )

        # Extract latest signal
        latest = signals_df.iloc[-1]

        # Determine signal type
        if latest["signal"] > 0:
            signal_type = SignalType.LONG_SPREAD
        elif latest["signal"] < 0:
            signal_type = SignalType.SHORT_SPREAD
        else:
            signal_type = SignalType.NO_POSITION

        # Calculate confidence (normalized z-score magnitude)
        confidence = min(abs(latest["z_score"]) / self.entry_threshold, 1.0)

        return TradingSignal(
            signal=signal_type,
            z_score=latest["z_score"],
            spread_value=latest["spread"],
            entry_threshold=self.entry_threshold,
            timestamp=series1.index[-1],
            confidence=confidence,
        )

    def calculate_spread_volatility(
        self, spread: pd.Series, lookback: Optional[int] = None
    ) -> float:
        """Calculate spread volatility for position sizing.

        Args:
            spread: Price spread series.
            lookback: Window size for volatility calc (uses instance default if None).

        Returns:
            Annualized volatility (standard deviation * sqrt(252)).
        """
        window = lookback or self.lookback_window

        if len(spread) < window:
            logger.warning(
                f"Insufficient data for volatility: {len(spread)} < {window}"
            )
            return 0.0

        # Calculate returns
        spread_returns = spread.pct_change().dropna()

        # Rolling volatility
        volatility = spread_returns.iloc[-window:].std()

        # Annualize (assuming daily data)
        annualized_vol = volatility * np.sqrt(252)

        return annualized_vol

    def backtest_signals(
        self,
        series1: pd.Series,
        series2: pd.Series,
        hedge_ratio: float,
    ) -> Tuple[pd.DataFrame, dict]:
        """Backtest signal generation with performance metrics.

        Args:
            series1: First asset prices.
            series2: Second asset prices.
            hedge_ratio: Cointegration hedge ratio.

        Returns:
            Tuple of (signals DataFrame, performance metrics dict).
        """
        signals_df = self.generate_signals(series1, series2, hedge_ratio)

        # Calculate spread returns
        spread_returns = signals_df["spread"].pct_change()

        # Strategy returns = position (lagged) * spread returns
        strategy_returns = (
            signals_df["position"].shift(1) * spread_returns
        )

        # Performance metrics
        total_return = (1 + strategy_returns).prod() - 1
        sharpe_ratio = (
            strategy_returns.mean()
            / strategy_returns.std()
            * np.sqrt(252)
            if strategy_returns.std() > 0
            else 0.0
        )

        # Max drawdown
        cumulative = (1 + strategy_returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()

        # Win rate
        winning_trades = (strategy_returns > 0).sum()
        total_trades = (signals_df["signal"] != 0).sum()
        win_rate = (
            winning_trades / total_trades if total_trades > 0 else 0.0
        )

        metrics = {
            "total_return": total_return,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "total_trades": total_trades,
        }

        logger.info(
            f"Backtest results | "
            f"Return: {total_return:.2%}, "
            f"Sharpe: {sharpe_ratio:.2f}, "
            f"Max DD: {max_drawdown:.2%}, "
            f"Win rate: {win_rate:.2%}"
        )

        return signals_df, metrics
