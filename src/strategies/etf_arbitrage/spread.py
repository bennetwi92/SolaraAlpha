"""Spread calculation module for ETF arbitrage strategy.

Calculates z-score normalized spread between two ETFs tracking the same index.
"""

from dataclasses import dataclass
import pandas as pd
import numpy as np


@dataclass
class SpreadMetrics:
    """Spread metrics at a point in time."""
    spread: float
    z_score: float
    rolling_mean: float
    rolling_std: float
    half_life: float  # Mean reversion half-life in days


class SpreadCalculator:
    """Calculate and track spread statistics between two ETFs."""

    def __init__(self, lookback_window: int = 60):
        """Initialize spread calculator.

        Args:
            lookback_window: Number of days for rolling statistics (default 60)
        """
        self.lookback_window = lookback_window
        self._spread_series = None

    def calculate_spread(
        self,
        prices1: pd.Series,
        prices2: pd.Series,
        method: str = 'ratio'
    ) -> pd.Series:
        """Calculate spread between two price series.

        Args:
            prices1: Price series for first ETF
            prices2: Price series for second ETF
            method: 'ratio' or 'difference'

        Returns:
            Spread time series
        """
        if method == 'ratio':
            # Price ratio spread (more stable for trending markets)
            spread = prices1 / prices2
        elif method == 'difference':
            # Price difference spread
            spread = prices1 - prices2
        else:
            raise ValueError(f"Invalid spread method: {method}")

        self._spread_series = spread
        return spread

    def calculate_z_score(self, spread: pd.Series) -> pd.Series:
        """Calculate rolling z-score for spread series.

        Args:
            spread: Spread time series

        Returns:
            Z-score time series
        """
        rolling_mean = spread.rolling(window=self.lookback_window).mean()
        rolling_std = spread.rolling(window=self.lookback_window).std()

        # Avoid division by zero
        rolling_std = rolling_std.replace(0, np.nan)

        z_score = (spread - rolling_mean) / rolling_std
        return z_score

    def calculate_half_life(self, spread: pd.Series) -> float:
        """Calculate mean reversion half-life using Ornstein-Uhlenbeck process.

        Uses AR(1) regression: spread[t] = α + β*spread[t-1] + ε
        Half-life = -log(2) / log(β)

        Args:
            spread: Spread time series

        Returns:
            Half-life in days
        """
        # Remove NaN values
        spread_clean = spread.dropna()

        if len(spread_clean) < 2:
            return np.nan

        # Lag the spread by 1 period
        spread_lag = spread_clean.shift(1).dropna()
        spread_current = spread_clean[1:]

        # Align indices
        spread_current = spread_current.loc[spread_lag.index]

        # OLS regression
        X = spread_lag.values
        y = spread_current.values

        # Add constant
        X_with_const = np.column_stack([np.ones(len(X)), X])

        # Solve normal equations
        try:
            beta = np.linalg.lstsq(X_with_const, y, rcond=None)[0]
            beta_coef = beta[1]

            # Calculate half-life
            if 0 < beta_coef < 1:
                half_life = -np.log(2) / np.log(beta_coef)
            else:
                # Non-stationary or explosive process
                half_life = np.inf
        except np.linalg.LinAlgError:
            half_life = np.nan

        return half_life

    def get_current_metrics(
        self,
        prices1: pd.Series,
        prices2: pd.Series,
        method: str = 'ratio'
    ) -> SpreadMetrics:
        """Get current spread metrics.

        Args:
            prices1: Price series for first ETF
            prices2: Price series for second ETF
            method: Spread calculation method

        Returns:
            SpreadMetrics with current values
        """
        # Calculate spread
        spread = self.calculate_spread(prices1, prices2, method)

        # Calculate z-score
        z_score_series = self.calculate_z_score(spread)

        # Get current values
        current_spread = spread.iloc[-1]
        current_z_score = z_score_series.iloc[-1]

        # Calculate rolling statistics
        rolling_mean = spread.rolling(window=self.lookback_window).mean().iloc[-1]
        rolling_std = spread.rolling(window=self.lookback_window).std().iloc[-1]

        # Calculate half-life on recent window
        recent_spread = spread.iloc[-self.lookback_window:]
        half_life = self.calculate_half_life(recent_spread)

        return SpreadMetrics(
            spread=current_spread,
            z_score=current_z_score,
            rolling_mean=rolling_mean,
            rolling_std=rolling_std,
            half_life=half_life
        )

    def get_spread_history(
        self,
        prices1: pd.Series,
        prices2: pd.Series,
        method: str = 'ratio'
    ) -> pd.DataFrame:
        """Get full spread history with z-scores.

        Args:
            prices1: Price series for first ETF
            prices2: Price series for second ETF
            method: Spread calculation method

        Returns:
            DataFrame with spread, z_score, rolling_mean, rolling_std
        """
        spread = self.calculate_spread(prices1, prices2, method)
        z_score = self.calculate_z_score(spread)
        rolling_mean = spread.rolling(window=self.lookback_window).mean()
        rolling_std = spread.rolling(window=self.lookback_window).std()

        return pd.DataFrame({
            'spread': spread,
            'z_score': z_score,
            'rolling_mean': rolling_mean,
            'rolling_std': rolling_std
        })
