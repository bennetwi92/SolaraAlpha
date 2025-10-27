"""Position sizing for statistical arbitrage portfolio.

Implements volatility-weighted position sizing to ensure equal risk contribution
across portfolio positions.
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .portfolio import PortfolioPosition
from .signals import SignalType


@dataclass
class SizedPosition:
    """Position with calculated size and dollar exposure."""

    symbol: str
    signal_type: SignalType
    quantity: int
    dollar_exposure: float
    weight: float  # Portfolio weight
    target_volatility: float  # Annualized volatility


class PositionSizer:
    """Calculate position sizes using volatility weighting.

    Each position is sized to contribute equal risk to the portfolio:
    - Position size ∝ 1 / volatility
    - Total long exposure = Total short exposure (market-neutral)
    - Risk-adjusted allocation across positions
    """

    def __init__(
        self,
        portfolio_value: float,
        target_leverage: float = 1.0,
        volatility_lookback: int = 20,
        min_position_size: float = 100.0,
        max_position_weight: float = 0.05,
    ):
        """Initialize position sizer.

        Args:
            portfolio_value: Total portfolio value (USD)
            target_leverage: Target gross leverage (1.0 = 100% long + 100% short)
            volatility_lookback: Lookback period for volatility calculation (days)
            min_position_size: Minimum position size in USD
            max_position_weight: Maximum position weight (0.05 = 5%)
        """
        if portfolio_value <= 0:
            raise ValueError(f"portfolio_value must be positive: {portfolio_value}")

        if target_leverage <= 0:
            raise ValueError(f"target_leverage must be positive: {target_leverage}")

        self.portfolio_value = portfolio_value
        self.target_leverage = target_leverage
        self.volatility_lookback = volatility_lookback
        self.min_position_size = min_position_size
        self.max_position_weight = max_position_weight

    def calculate_positions(
        self,
        portfolio: Dict[str, PortfolioPosition],
        prices: pd.DataFrame,
        returns: pd.DataFrame,
    ) -> Dict[str, SizedPosition]:
        """Calculate position sizes for portfolio.

        Args:
            portfolio: Dict of portfolio positions
            prices: DataFrame with current prices (time × stocks)
            returns: DataFrame with historical returns for volatility calculation

        Returns:
            Dict mapping symbol to SizedPosition

        Raises:
            ValueError: If insufficient data or prices unavailable
        """
        if len(returns) < self.volatility_lookback:
            raise ValueError(
                f"Insufficient return data: need {self.volatility_lookback} periods, "
                f"got {len(returns)}"
            )

        # Calculate volatilities
        volatilities = self._calculate_volatilities(returns, list(portfolio.keys()))

        # Calculate weights (inverse volatility weighting)
        weights = self._calculate_weights(portfolio, volatilities)

        # Get latest prices
        latest_prices = prices.iloc[-1]

        # Size positions
        sized_positions = {}

        for symbol, position in portfolio.items():
            volatility = volatilities.get(symbol, np.nan)
            weight = weights.get(symbol, 0.0)
            price = latest_prices.get(symbol, np.nan)

            if pd.isna(volatility) or pd.isna(price) or price <= 0:
                continue

            # Calculate dollar exposure
            dollar_exposure = abs(weight) * self.portfolio_value

            # Apply minimum position size
            if dollar_exposure < self.min_position_size:
                dollar_exposure = self.min_position_size

            # Calculate quantity
            quantity = int(dollar_exposure / price)

            # Skip if quantity is 0
            if quantity == 0:
                continue

            # Adjust for short positions (negative quantity)
            if position.signal_type == SignalType.SHORT:
                quantity = -quantity
                dollar_exposure = -dollar_exposure
                weight = -weight

            sized_position = SizedPosition(
                symbol=symbol,
                signal_type=position.signal_type,
                quantity=quantity,
                dollar_exposure=dollar_exposure,
                weight=weight,
                target_volatility=volatility,
            )

            sized_positions[symbol] = sized_position

        # Validate market-neutral constraint
        self._validate_market_neutral(sized_positions)

        return sized_positions

    def _calculate_volatilities(
        self, returns: pd.DataFrame, symbols: list
    ) -> Dict[str, float]:
        """Calculate annualized volatility for each stock.

        Args:
            returns: DataFrame with returns (time × stocks)
            symbols: List of symbols to calculate volatility for

        Returns:
            Dict mapping symbol to annualized volatility
        """
        volatilities = {}

        for symbol in symbols:
            if symbol not in returns.columns:
                continue

            stock_returns = returns[symbol].iloc[-self.volatility_lookback :]

            # Skip if insufficient data
            if stock_returns.isna().all():
                continue

            # Annualized volatility (assuming 252 trading days)
            vol = stock_returns.std() * np.sqrt(252)
            volatilities[symbol] = vol

        return volatilities

    def _calculate_weights(
        self,
        portfolio: Dict[str, PortfolioPosition],
        volatilities: Dict[str, float],
    ) -> Dict[str, float]:
        """Calculate portfolio weights using inverse volatility weighting.

        Args:
            portfolio: Dict of portfolio positions
            volatilities: Dict of annualized volatilities

        Returns:
            Dict mapping symbol to portfolio weight
        """
        # Separate long and short positions
        long_positions = {
            symbol: pos
            for symbol, pos in portfolio.items()
            if pos.signal_type == SignalType.LONG
        }
        short_positions = {
            symbol: pos
            for symbol, pos in portfolio.items()
            if pos.signal_type == SignalType.SHORT
        }

        # Calculate inverse volatility weights for each side
        long_weights = self._calculate_inverse_vol_weights(
            long_positions, volatilities
        )
        short_weights = self._calculate_inverse_vol_weights(
            short_positions, volatilities
        )

        # Scale to achieve target leverage
        # target_leverage = 1.0 means 50% long + 50% short = 100% gross
        target_long_exposure = (self.target_leverage / 2.0) * self.portfolio_value
        target_short_exposure = (self.target_leverage / 2.0) * self.portfolio_value

        # Normalize weights
        long_sum = sum(long_weights.values())
        short_sum = sum(short_weights.values())

        scaled_long_weights = {
            symbol: (weight / long_sum) * (target_long_exposure / self.portfolio_value)
            if long_sum > 0
            else 0.0
            for symbol, weight in long_weights.items()
        }

        scaled_short_weights = {
            symbol: (weight / short_sum) * (target_short_exposure / self.portfolio_value)
            if short_sum > 0
            else 0.0
            for symbol, weight in short_weights.items()
        }

        # Cap at max position weight
        scaled_long_weights = {
            symbol: min(weight, self.max_position_weight)
            for symbol, weight in scaled_long_weights.items()
        }
        scaled_short_weights = {
            symbol: min(weight, self.max_position_weight)
            for symbol, weight in scaled_short_weights.items()
        }

        # Combine
        weights = {**scaled_long_weights, **scaled_short_weights}

        return weights

    def _calculate_inverse_vol_weights(
        self,
        positions: Dict[str, PortfolioPosition],
        volatilities: Dict[str, float],
    ) -> Dict[str, float]:
        """Calculate inverse volatility weights.

        Args:
            positions: Dict of positions (all same side)
            volatilities: Dict of volatilities

        Returns:
            Dict mapping symbol to unnormalized weight
        """
        weights = {}

        for symbol, position in positions.items():
            vol = volatilities.get(symbol, np.nan)

            if pd.isna(vol) or vol <= 0:
                continue

            # Inverse volatility weight
            weights[symbol] = 1.0 / vol

        return weights

    def _validate_market_neutral(
        self, sized_positions: Dict[str, SizedPosition]
    ) -> None:
        """Validate portfolio is approximately market-neutral.

        Args:
            sized_positions: Dict of sized positions

        Raises:
            ValueError: If net exposure is too large
        """
        if not sized_positions:
            return

        long_exposure = sum(
            pos.dollar_exposure
            for pos in sized_positions.values()
            if pos.signal_type == SignalType.LONG
        )
        short_exposure = sum(
            abs(pos.dollar_exposure)
            for pos in sized_positions.values()
            if pos.signal_type == SignalType.SHORT
        )

        net_exposure = long_exposure - short_exposure
        gross_exposure = long_exposure + short_exposure

        # Allow 10% net exposure relative to gross
        if gross_exposure > 0:
            net_ratio = abs(net_exposure) / gross_exposure
            if net_ratio > 0.10:
                raise ValueError(
                    f"Portfolio not market-neutral: net exposure ratio = "
                    f"{net_ratio:.2%} (max 10%)"
                )

    def get_portfolio_statistics(
        self, sized_positions: Dict[str, SizedPosition]
    ) -> Dict[str, float]:
        """Calculate portfolio statistics.

        Args:
            sized_positions: Dict of sized positions

        Returns:
            Dict with portfolio statistics
        """
        if not sized_positions:
            return {
                "total_positions": 0,
                "long_exposure": 0.0,
                "short_exposure": 0.0,
                "gross_exposure": 0.0,
                "net_exposure": 0.0,
                "leverage": 0.0,
            }

        long_exposure = sum(
            pos.dollar_exposure
            for pos in sized_positions.values()
            if pos.signal_type == SignalType.LONG
        )
        short_exposure = sum(
            abs(pos.dollar_exposure)
            for pos in sized_positions.values()
            if pos.signal_type == SignalType.SHORT
        )

        gross_exposure = long_exposure + short_exposure
        net_exposure = long_exposure - short_exposure
        leverage = gross_exposure / self.portfolio_value if self.portfolio_value > 0 else 0.0

        return {
            "total_positions": len(sized_positions),
            "long_exposure": long_exposure,
            "short_exposure": short_exposure,
            "gross_exposure": gross_exposure,
            "net_exposure": net_exposure,
            "leverage": leverage,
        }
