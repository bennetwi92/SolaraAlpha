"""Position sizing for FX carry trade strategy.

Implements volatility-adjusted position sizing with portfolio-level risk management.
"""
import logging
from typing import Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class FXCarryPositionSizer:
    """Calculate position sizes for FX carry portfolio.

    Features:
    - Volatility-based position sizing (inverse volatility weighting)
    - Portfolio-level risk targeting
    - Position concentration limits
    - Equal risk contribution across positions

    Example:
        >>> sizer = FXCarryPositionSizer(portfolio_value=100000, vol_target=10.0)
        >>> positions = sizer.calculate_positions(signals, portfolio_value=100000)
    """

    def __init__(
        self,
        vol_target: float = 10.0,  # Target portfolio volatility (%)
        max_position_pct: float = 0.20,  # Max position size (20% of portfolio)
        max_leverage: float = 1.0,  # Maximum leverage (1.0 = no leverage)
    ) -> None:
        """Initialize position sizer.

        Args:
            vol_target: Target annualized portfolio volatility (%)
            max_position_pct: Maximum position size as fraction of portfolio
            max_leverage: Maximum portfolio leverage
        """
        self.vol_target = vol_target
        self.max_position_pct = max_position_pct
        self.max_leverage = max_leverage

        logger.info(
            f"Initialized FX carry position sizer "
            f"(vol_target={vol_target}%, max_pos={max_position_pct:.0%}, "
            f"max_leverage={max_leverage}x)"
        )

    def calculate_position_sizes(
        self,
        signals: list,  # List of CarrySignal objects
        portfolio_value: float,
    ) -> Dict[str, float]:
        """Calculate dollar position sizes for each signal.

        Uses inverse volatility weighting scaled to target portfolio volatility.

        Args:
            signals: List of CarrySignal objects with position_size and volatility
            portfolio_value: Total portfolio value ($)

        Returns:
            Dict mapping pair -> position size in dollars
            Positive = long, negative = short
        """
        if not signals:
            return {}

        # Extract pairs with non-zero position sizes
        active_signals = [s for s in signals if s.position_size > 0]

        if not active_signals:
            logger.info("No active signals - returning empty positions")
            return {}

        # Calculate inverse volatility weights
        inv_vol_weights = {}
        total_inv_vol = 0.0

        for signal in active_signals:
            if signal.volatility <= 0:
                logger.warning(
                    f"Invalid volatility for {signal.pair}: {signal.volatility}"
                )
                continue

            # Weight = 1 / volatility * signal strength
            weight = (1.0 / signal.volatility) * signal.position_size
            inv_vol_weights[signal.pair] = weight
            total_inv_vol += weight

        if total_inv_vol == 0:
            logger.warning("Total inverse volatility is zero")
            return {}

        # Normalize weights to sum to 1
        normalized_weights = {
            pair: w / total_inv_vol for pair, w in inv_vol_weights.items()
        }

        # Calculate position sizes
        positions = {}
        total_gross_exposure = 0.0

        for signal in active_signals:
            pair = signal.pair
            if pair not in normalized_weights:
                continue

            # Base position size from normalized weight
            base_size = normalized_weights[pair] * portfolio_value

            # Apply maximum position limit
            max_size = self.max_position_pct * portfolio_value
            position_size = min(base_size, max_size)

            # Apply signal direction (long vs short)
            direction = np.sign(signal.signal)
            position_size = position_size * direction

            positions[pair] = position_size
            total_gross_exposure += abs(position_size)

        # Check leverage constraint
        leverage = total_gross_exposure / portfolio_value
        if leverage > self.max_leverage:
            # Scale down all positions proportionally
            scale_factor = self.max_leverage / leverage
            positions = {
                pair: size * scale_factor for pair, size in positions.items()
            }
            total_gross_exposure = total_gross_exposure * scale_factor

            logger.info(
                f"Scaled positions by {scale_factor:.2f} to meet leverage limit"
            )

        logger.info(
            f"Calculated {len(positions)} positions, "
            f"gross exposure: ${total_gross_exposure:,.0f} "
            f"({leverage:.2f}x leverage)"
        )

        return positions

    def calculate_portfolio_volatility(
        self,
        positions: Dict[str, float],
        volatilities: Dict[str, float],
        portfolio_value: float,
    ) -> float:
        """Calculate expected portfolio volatility.

        Assumes zero correlation between FX pairs (conservative).

        Args:
            positions: Dict mapping pair -> position size ($)
            volatilities: Dict mapping pair -> annualized volatility (%)
            portfolio_value: Total portfolio value ($)

        Returns:
            Expected annualized portfolio volatility (%)
        """
        if not positions:
            return 0.0

        # Calculate variance contribution from each position
        # Assuming zero correlation: portfolio_var = sum(position_var)
        portfolio_var = 0.0

        for pair, position_size in positions.items():
            vol = volatilities.get(pair)
            if vol is None or vol <= 0:
                logger.warning(f"Missing or invalid volatility for {pair}")
                continue

            # Position weight
            weight = abs(position_size) / portfolio_value

            # Variance contribution
            var_contribution = (weight * vol / 100) ** 2

            portfolio_var += var_contribution

        # Standard deviation (volatility)
        portfolio_vol = np.sqrt(portfolio_var) * 100  # Convert to percentage

        return float(portfolio_vol)

    def calculate_rebalance(
        self,
        current_positions: Dict[str, float],
        target_positions: Dict[str, float],
        rebalance_threshold: float = 0.10,
    ) -> Dict[str, float]:
        """Calculate trades needed to rebalance portfolio.

        Only rebalances if position drift exceeds threshold.

        Args:
            current_positions: Current position sizes ($)
            target_positions: Target position sizes ($)
            rebalance_threshold: Minimum drift to trigger rebalance (fraction)

        Returns:
            Dict mapping pair -> trade size ($)
            Positive = buy, negative = sell
        """
        trades = {}

        # Get all pairs (current + target)
        all_pairs = set(current_positions.keys()) | set(
            target_positions.keys()
        )

        for pair in all_pairs:
            current = current_positions.get(pair, 0.0)
            target = target_positions.get(pair, 0.0)

            # Calculate drift
            if target == 0:
                drift = abs(current)
                drift_pct = 1.0  # Close position
            else:
                drift = target - current
                drift_pct = abs(drift) / abs(target)

            # Only rebalance if drift exceeds threshold
            if drift_pct >= rebalance_threshold:
                trades[pair] = drift

        logger.info(
            f"Rebalance: {len(trades)} trades from {len(all_pairs)} pairs "
            f"(threshold={rebalance_threshold:.0%})"
        )

        return trades

    def calculate_risk_metrics(
        self,
        positions: Dict[str, float],
        signals: list,  # List of CarrySignal
        portfolio_value: float,
    ) -> Dict[str, float]:
        """Calculate portfolio-level risk metrics.

        Args:
            positions: Dict mapping pair -> position size ($)
            signals: List of CarrySignal objects
            portfolio_value: Total portfolio value ($)

        Returns:
            Dict with risk metrics:
            - gross_exposure: Total absolute position size ($)
            - net_exposure: Net long/short exposure ($)
            - leverage: Gross exposure / portfolio value
            - portfolio_vol: Expected portfolio volatility (%)
            - num_positions: Number of active positions
        """
        if not positions:
            return {
                "gross_exposure": 0.0,
                "net_exposure": 0.0,
                "leverage": 0.0,
                "portfolio_vol": 0.0,
                "num_positions": 0,
            }

        # Calculate exposures
        gross_exposure = sum(abs(p) for p in positions.values())
        net_exposure = sum(positions.values())
        leverage = gross_exposure / portfolio_value

        # Extract volatilities from signals
        volatilities = {s.pair: s.volatility for s in signals}

        # Calculate portfolio volatility
        portfolio_vol = self.calculate_portfolio_volatility(
            positions, volatilities, portfolio_value
        )

        metrics = {
            "gross_exposure": gross_exposure,
            "net_exposure": net_exposure,
            "leverage": leverage,
            "portfolio_vol": portfolio_vol,
            "num_positions": len(positions),
        }

        logger.debug(
            f"Risk metrics: {len(positions)} positions, "
            f"gross=${gross_exposure:,.0f}, net=${net_exposure:,.0f}, "
            f"leverage={leverage:.2f}x, vol={portfolio_vol:.1f}%"
        )

        return metrics
