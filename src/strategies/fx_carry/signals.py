"""Signal generation for FX carry trade strategy.

Combines interest rate differentials with trend filtering and volatility adjustments.
"""
import logging
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CarrySignal:
    """Represents a carry trade signal for a currency pair."""

    pair: str
    timestamp: pd.Timestamp
    rate_differential: float  # Interest rate differential (%)
    trend_score: float  # Trend filter score [-1, 1]
    volatility: float  # Realized volatility (%)
    signal: float  # Final signal strength [-1, 1]
    position_size: float  # Recommended position size (0-1)


class FXCarrySignals:
    """Generate trading signals for FX carry strategy.

    Strategy Logic:
    1. Calculate interest rate differential (base - quote)
    2. Apply trend filter (moving average crossover)
    3. Adjust for volatility (higher vol = lower position size)
    4. Generate signal: positive = long base/short quote

    Features:
    - Interest rate differential calculation
    - Trend filtering (MA crossover, momentum)
    - Volatility-based position sizing
    - Risk-adjusted signal generation
    """

    def __init__(
        self,
        trend_lookback: int = 60,  # Days for trend calculation
        trend_threshold: float = 0.0,  # Minimum trend score to trade
        vol_lookback: int = 30,  # Days for volatility calculation
        vol_target: float = 10.0,  # Target annualized volatility (%)
        min_rate_diff: float = 1.0,  # Minimum rate differential to trade (%)
    ) -> None:
        """Initialize signal generator.

        Args:
            trend_lookback: Days to calculate trend indicators
            trend_threshold: Minimum trend score required to trade
            vol_lookback: Days for volatility calculation
            vol_target: Target annualized volatility for position sizing (%)
            min_rate_diff: Minimum interest rate differential required (%)
        """
        self.trend_lookback = trend_lookback
        self.trend_threshold = trend_threshold
        self.vol_lookback = vol_lookback
        self.vol_target = vol_target
        self.min_rate_diff = min_rate_diff

        logger.info(
            f"Initialized FX carry signals (trend={trend_lookback}d, "
            f"vol={vol_lookback}d, min_diff={min_rate_diff}%)"
        )

    def calculate_trend_score(
        self, prices: pd.Series, lookback: Optional[int] = None
    ) -> float:
        """Calculate trend score using multiple indicators.

        Combines:
        - Moving average crossover (fast vs slow)
        - Price momentum
        - Trend strength (R-squared)

        Args:
            prices: Price series (close prices)
            lookback: Days to look back (uses self.trend_lookback if None)

        Returns:
            Trend score in range [-1, 1]:
            - 1.0 = strong uptrend
            - 0.0 = no trend
            - -1.0 = strong downtrend
        """
        lookback = lookback or self.trend_lookback

        if len(prices) < lookback:
            logger.warning(
                f"Insufficient data for trend: {len(prices)} < {lookback}"
            )
            return 0.0

        recent_prices = prices.iloc[-lookback:]

        # 1. Moving average crossover (fast=20, slow=50)
        fast_ma = recent_prices.rolling(20, min_periods=20).mean()
        slow_ma = recent_prices.rolling(50, min_periods=50).mean()

        if fast_ma.isna().any() or slow_ma.isna().any():
            ma_score = 0.0
        else:
            # Normalize difference
            ma_diff = (fast_ma.iloc[-1] - slow_ma.iloc[-1]) / slow_ma.iloc[-1]
            ma_score = np.clip(ma_diff * 10, -1, 1)  # Scale to [-1, 1]

        # 2. Momentum (current price vs N days ago)
        momentum = (recent_prices.iloc[-1] / recent_prices.iloc[0]) - 1
        momentum_score = np.clip(momentum * 5, -1, 1)  # Scale to [-1, 1]

        # 3. Trend strength (linear regression R-squared)
        x = np.arange(len(recent_prices))
        y = recent_prices.values

        # Calculate R-squared
        z = np.polyfit(x, y, 1)
        p = np.poly1d(z)
        y_pred = p(x)

        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)

        if ss_tot == 0:
            r_squared = 0
        else:
            r_squared = 1 - (ss_res / ss_tot)

        # Direction from slope
        direction = 1 if z[0] > 0 else -1
        trend_strength = r_squared * direction

        # Combine scores (weighted average)
        trend_score = (
            0.4 * ma_score + 0.3 * momentum_score + 0.3 * trend_strength
        )

        return float(np.clip(trend_score, -1, 1))

    def calculate_volatility(
        self, prices: pd.Series, lookback: Optional[int] = None
    ) -> float:
        """Calculate realized volatility (annualized).

        Args:
            prices: Price series (close prices)
            lookback: Days to look back (uses self.vol_lookback if None)

        Returns:
            Annualized volatility in percentage points
        """
        lookback = lookback or self.vol_lookback

        if len(prices) < lookback + 1:
            logger.warning(
                f"Insufficient data for volatility: {len(prices)} < {lookback+1}"
            )
            return 0.0

        recent_prices = prices.iloc[-(lookback + 1) :]

        # Calculate daily returns
        returns = recent_prices.pct_change().dropna()

        if len(returns) == 0:
            return 0.0

        # Annualized volatility (assuming 252 trading days)
        vol = returns.std() * np.sqrt(252) * 100  # Convert to percentage

        return float(vol)

    def calculate_position_size(
        self,
        rate_differential: float,
        trend_score: float,
        volatility: float,
    ) -> float:
        """Calculate position size based on rate differential, trend, and volatility.

        Position sizing logic:
        1. Base size from rate differential (higher diff = larger position)
        2. Adjust for trend alignment (only trade with trend)
        3. Scale inverse to volatility (lower vol = larger position)

        Args:
            rate_differential: Interest rate differential (%)
            trend_score: Trend score [-1, 1]
            volatility: Realized volatility (%)

        Returns:
            Position size in range [0, 1]:
            - 0 = no position
            - 1 = maximum position
        """
        # Check minimum rate differential
        if abs(rate_differential) < self.min_rate_diff:
            return 0.0

        # Check trend alignment
        # Positive rate diff should align with uptrend (positive trend_score)
        # Negative rate diff should align with downtrend (negative trend_score)
        trend_alignment = np.sign(rate_differential) * trend_score

        if trend_alignment < self.trend_threshold:
            # Trend not aligned - reduce or zero position
            if trend_alignment < -0.3:
                return 0.0  # Strong counter-trend
            else:
                trend_adjustment = 0.5  # Weak trend - reduce size
        else:
            # Trend aligned - scale by strength
            trend_adjustment = np.clip(trend_alignment, 0.3, 1.0)

        # Base size from rate differential (higher carry = larger position)
        # Scale: 1% diff = 0.2 base size, 5% diff = 1.0 base size
        base_size = min(abs(rate_differential) / 5.0, 1.0)

        # Volatility adjustment (inverse relationship)
        if volatility <= 0:
            vol_adjustment = 1.0
        else:
            vol_adjustment = self.vol_target / max(volatility, self.vol_target)
            vol_adjustment = np.clip(vol_adjustment, 0.3, 1.0)

        # Final position size
        position_size = base_size * trend_adjustment * vol_adjustment

        return float(np.clip(position_size, 0, 1))

    def generate_signal(
        self,
        pair: str,
        prices: pd.DataFrame,
        rate_differential: float,
        timestamp: Optional[pd.Timestamp] = None,
    ) -> CarrySignal:
        """Generate carry trade signal for a currency pair.

        Args:
            pair: Currency pair (e.g., 'AUD-USD')
            prices: OHLCV DataFrame with datetime index
            rate_differential: Interest rate differential (%)
            timestamp: Signal timestamp (uses last price timestamp if None)

        Returns:
            CarrySignal with signal strength and position size
        """
        if len(prices) < max(self.trend_lookback, self.vol_lookback) + 1:
            logger.warning(
                f"Insufficient data for {pair}: {len(prices)} bars"
            )
            return CarrySignal(
                pair=pair,
                timestamp=timestamp or prices.index[-1],
                rate_differential=rate_differential,
                trend_score=0.0,
                volatility=0.0,
                signal=0.0,
                position_size=0.0,
            )

        # Get close prices
        close_prices = prices["Close"]

        # Calculate components
        trend_score = self.calculate_trend_score(close_prices)
        volatility = self.calculate_volatility(close_prices)
        position_size = self.calculate_position_size(
            rate_differential, trend_score, volatility
        )

        # Signal direction from rate differential
        # Positive diff = long base/short quote (buy the pair)
        # Negative diff = short base/long quote (sell the pair)
        signal = np.sign(rate_differential) * position_size

        timestamp = timestamp or prices.index[-1]

        logger.debug(
            f"{pair} @ {timestamp.date()}: "
            f"diff={rate_differential:.2f}%, trend={trend_score:.2f}, "
            f"vol={volatility:.1f}%, signal={signal:.2f}, size={position_size:.2f}"
        )

        return CarrySignal(
            pair=pair,
            timestamp=timestamp,
            rate_differential=rate_differential,
            trend_score=trend_score,
            volatility=volatility,
            signal=signal,
            position_size=position_size,
        )

    def generate_portfolio_signals(
        self,
        prices_dict: Dict[str, pd.DataFrame],
        rate_differentials: Dict[str, float],
        timestamp: Optional[pd.Timestamp] = None,
        max_positions: int = 10,
    ) -> list[CarrySignal]:
        """Generate signals for a portfolio of currency pairs.

        Ranks pairs by carry opportunity and selects top positions.

        Args:
            prices_dict: Dict mapping pair -> OHLCV DataFrame
            rate_differentials: Dict mapping pair -> rate differential
            timestamp: Signal timestamp (uses last available if None)
            max_positions: Maximum number of positions to hold

        Returns:
            List of CarrySignal for selected pairs, sorted by signal strength
        """
        signals = []

        for pair, prices in prices_dict.items():
            rate_diff = rate_differentials.get(pair)
            if rate_diff is None:
                logger.warning(f"No rate differential for {pair}")
                continue

            signal = self.generate_signal(pair, prices, rate_diff, timestamp)
            signals.append(signal)

        # Sort by absolute signal strength (best opportunities first)
        signals = sorted(signals, key=lambda s: abs(s.signal), reverse=True)

        # Select top N positions with non-zero signals
        selected = [s for s in signals if s.position_size > 0][:max_positions]

        logger.info(
            f"Generated {len(selected)} carry signals from {len(prices_dict)} pairs"
        )

        return selected
