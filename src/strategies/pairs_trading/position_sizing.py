"""Position sizing for pairs trading strategy.

Volatility-based position sizing with risk-adjusted allocation.
Ensures market-neutral positions and compliance with risk limits.
"""
import logging
from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PairPosition:
    """Position sizing for a pair trade.

    Attributes:
        symbol1: First asset symbol.
        symbol2: Second asset symbol.
        quantity1: Units of asset1 (positive=long, negative=short).
        quantity2: Units of asset2 (positive=long, negative=short).
        dollar_exposure1: Dollar exposure for asset1.
        dollar_exposure2: Dollar exposure for asset2.
        total_capital: Total capital allocated to pair.
        hedge_ratio: Hedge ratio used for sizing.
    """

    symbol1: str
    symbol2: str
    quantity1: float
    quantity2: float
    dollar_exposure1: float
    dollar_exposure2: float
    total_capital: float
    hedge_ratio: float


class PositionSizer:
    """Volatility-adjusted position sizing for pairs trading.

    Sizes positions based on spread volatility to maintain consistent
    risk across different pairs. Integrates with risk management framework
    for limit compliance.

    Example:
        >>> sizer = PositionSizer(
        ...     portfolio_value=100000,
        ...     max_position_pct=0.05,
        ...     target_volatility=0.10
        ... )
        >>> position = sizer.calculate_position_size(
        ...     'AAPL', 'MSFT',
        ...     price1=150.0, price2=300.0,
        ...     hedge_ratio=0.5,
        ...     spread_volatility=0.15
        ... )
    """

    def __init__(
        self,
        portfolio_value: float,
        max_position_pct: float = 0.05,
        target_volatility: float = 0.10,
        min_capital_per_pair: float = 1000.0,
    ) -> None:
        """Initialize position sizer.

        Args:
            portfolio_value: Total portfolio value.
            max_position_pct: Max capital allocation per pair (default 5%).
            target_volatility: Target annualized volatility (default 10%).
            min_capital_per_pair: Minimum capital per pair trade (default $1000).
        """
        self.portfolio_value = portfolio_value
        self.max_position_pct = max_position_pct
        self.target_volatility = target_volatility
        self.min_capital_per_pair = min_capital_per_pair

    def calculate_position_size(
        self,
        symbol1: str,
        symbol2: str,
        price1: float,
        price2: float,
        hedge_ratio: float,
        spread_volatility: float,
    ) -> PairPosition:
        """Calculate position sizes for a pair trade.

        Sizes positions to achieve target volatility while respecting
        capital limits and maintaining hedge ratio.

        Args:
            symbol1: First asset symbol.
            symbol2: Second asset symbol.
            price1: Current price of asset1.
            price2: Current price of asset2.
            hedge_ratio: Cointegration hedge ratio.
            spread_volatility: Annualized spread volatility.

        Returns:
            PairPosition with calculated quantities and exposures.

        Raises:
            ValueError: If volatility is zero or prices are invalid.
        """
        if price1 <= 0 or price2 <= 0:
            raise ValueError(f"Invalid prices: {price1}, {price2}")

        if spread_volatility <= 0:
            raise ValueError(
                f"Invalid spread volatility: {spread_volatility}"
            )

        # Calculate maximum capital allocation
        max_capital = self.portfolio_value * self.max_position_pct

        # Volatility-adjusted capital allocation
        # Capital = (Target Vol / Spread Vol) * Max Capital
        # Higher spread vol → Lower capital allocation (inverse scaling)
        vol_adjusted_capital = (
            self.target_volatility / spread_volatility
        ) * max_capital

        # Cap at max and floor at minimum
        allocated_capital = max(
            min(vol_adjusted_capital, max_capital), self.min_capital_per_pair
        )

        # Position sizing maintaining hedge ratio
        # For long spread: long asset1, short asset2
        # Position in asset1: capital / (price1 * (1 + hedge_ratio))
        # Position in asset2: -hedge_ratio * position1

        # Simplified: Split capital 50/50 between legs, adjust by hedge ratio
        capital_leg1 = allocated_capital / 2
        capital_leg2 = allocated_capital / 2

        # Quantities
        quantity1 = capital_leg1 / price1
        quantity2 = -(hedge_ratio * quantity1)  # Negative for short side

        # Dollar exposures
        dollar_exposure1 = quantity1 * price1
        dollar_exposure2 = abs(quantity2 * price2)

        logger.info(
            f"Position sizing | "
            f"{symbol1}/{symbol2} | "
            f"Capital: ${allocated_capital:,.2f}, "
            f"Q1: {quantity1:.2f}, Q2: {quantity2:.2f}"
        )

        return PairPosition(
            symbol1=symbol1,
            symbol2=symbol2,
            quantity1=quantity1,
            quantity2=quantity2,
            dollar_exposure1=dollar_exposure1,
            dollar_exposure2=dollar_exposure2,
            total_capital=allocated_capital,
            hedge_ratio=hedge_ratio,
        )

    def calculate_kelly_fraction(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> float:
        """Calculate Kelly Criterion fraction for position sizing.

        Kelly fraction = (p * b - q) / b
        where:
            p = win rate
            q = loss rate (1 - p)
            b = avg_win / avg_loss

        Args:
            win_rate: Historical win rate [0, 1].
            avg_win: Average winning trade return.
            avg_loss: Average losing trade return (absolute value).

        Returns:
            Kelly fraction [0, 1] for optimal position sizing.
        """
        if avg_loss == 0:
            logger.warning("Average loss is zero, cannot calculate Kelly")
            return 0.0

        q = 1 - win_rate
        b = avg_win / avg_loss

        kelly_fraction = (win_rate * b - q) / b

        # Cap at reasonable max (50% Kelly = half Kelly for safety)
        kelly_fraction = max(0.0, min(kelly_fraction * 0.5, 0.5))

        return kelly_fraction

    def adjust_for_risk_limits(
        self,
        position: PairPosition,
        current_strategy_exposure: float,
        current_symbol1_exposure: float,
        current_symbol2_exposure: float,
        per_strategy_limit: float,
        per_asset_limit: float,
    ) -> PairPosition:
        """Adjust position size to comply with risk limits.

        Scales down position proportionally if it would breach any limit.

        Args:
            position: Initial PairPosition.
            current_strategy_exposure: Current strategy dollar exposure.
            current_symbol1_exposure: Current exposure to asset1.
            current_symbol2_exposure: Current exposure to asset2.
            per_strategy_limit: Max strategy exposure (dollar).
            per_asset_limit: Max per-asset exposure (dollar).

        Returns:
            Adjusted PairPosition compliant with all limits.
        """
        # Calculate post-trade exposures
        new_strategy_exposure = (
            current_strategy_exposure + position.total_capital
        )
        new_symbol1_exposure = (
            current_symbol1_exposure + position.dollar_exposure1
        )
        new_symbol2_exposure = (
            current_symbol2_exposure + position.dollar_exposure2
        )

        # Determine scaling factor (most restrictive limit)
        scale_factors = []

        if new_strategy_exposure > per_strategy_limit:
            scale_factors.append(
                (per_strategy_limit - current_strategy_exposure)
                / position.total_capital
            )

        if new_symbol1_exposure > per_asset_limit:
            scale_factors.append(
                (per_asset_limit - current_symbol1_exposure)
                / position.dollar_exposure1
            )

        if new_symbol2_exposure > per_asset_limit:
            scale_factors.append(
                (per_asset_limit - current_symbol2_exposure)
                / position.dollar_exposure2
            )

        # No adjustment needed
        if not scale_factors:
            return position

        # Scale by most restrictive factor
        scale = max(0.0, min(scale_factors))

        if scale < 1.0:
            logger.info(
                f"Position scaled by {scale:.2f} for risk limit compliance"
            )

        # Scale all quantities proportionally
        return PairPosition(
            symbol1=position.symbol1,
            symbol2=position.symbol2,
            quantity1=position.quantity1 * scale,
            quantity2=position.quantity2 * scale,
            dollar_exposure1=position.dollar_exposure1 * scale,
            dollar_exposure2=position.dollar_exposure2 * scale,
            total_capital=position.total_capital * scale,
            hedge_ratio=position.hedge_ratio,
        )

    def update_portfolio_value(self, new_value: float) -> None:
        """Update portfolio value for position sizing.

        Args:
            new_value: New portfolio value.
        """
        logger.info(
            f"Portfolio value updated: ${self.portfolio_value:,.2f} → ${new_value:,.2f}"
        )
        self.portfolio_value = new_value

    def get_sizing_metrics(self) -> Dict[str, float]:
        """Get current position sizing parameters.

        Returns:
            Dictionary with sizing configuration.
        """
        return {
            "portfolio_value": self.portfolio_value,
            "max_position_pct": self.max_position_pct,
            "max_position_capital": self.portfolio_value
            * self.max_position_pct,
            "target_volatility": self.target_volatility,
            "min_capital_per_pair": self.min_capital_per_pair,
        }
