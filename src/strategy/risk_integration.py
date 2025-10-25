"""
Strategy Framework - Risk Management Integration

Strategy wrapper with integrated risk management.
"""

from src.risk.risk_manager import RiskManager, RiskDecision
from src.strategy.base import BaseStrategy, Signal, Position
import pandas as pd
from typing import Dict
import logging

logger = logging.getLogger(__name__)


class RiskAwareStrategy(BaseStrategy):
    """
    Strategy wrapper with integrated risk management.

    Automatically validates positions against risk limits before execution.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        risk_manager: RiskManager
    ):
        """
        Initialize risk-aware strategy wrapper.

        Args:
            strategy: Underlying strategy
            risk_manager: Risk manager instance
        """
        super().__init__(strategy.config)
        self.underlying_strategy = strategy
        self.risk_manager = risk_manager

    def generate_signals(
        self,
        timestamp: pd.Timestamp,
        data: Dict[str, pd.DataFrame]
    ) -> Dict[str, Signal]:
        """Generate signals from underlying strategy."""
        return self.underlying_strategy.generate_signals(timestamp, data)

    def calculate_positions(
        self,
        signals: Dict[str, Signal],
        current_positions: Dict[str, Position],
        portfolio_value: float
    ) -> Dict[str, float]:
        """
        Calculate positions with risk management overlay.

        Validates each position change against risk limits.
        """
        # Get target positions from underlying strategy
        target_positions = self.underlying_strategy.calculate_positions(
            signals, current_positions, portfolio_value
        )

        # Validate each position change
        validated_positions = {}

        for symbol, target_qty in target_positions.items():
            # Get current quantity
            current_qty = current_positions[symbol].quantity if symbol in current_positions else 0.0

            # Calculate trade quantity
            trade_qty = target_qty - current_qty

            if trade_qty == 0:
                validated_positions[symbol] = target_qty
                continue

            # Determine side
            side = "long" if trade_qty > 0 else "short"

            # Get current price from signal
            price = signals[symbol].metadata.get('current_price', 0)
            if price == 0:
                logger.warning(f"No price available for {symbol}, skipping")
                continue

            # Convert positions to portfolio DataFrame
            portfolio_df = self._positions_to_dataframe(current_positions)

            # Pre-trade check
            decision = self.risk_manager.pre_trade_check(
                symbol=symbol,
                quantity=abs(trade_qty),
                side=side,
                price=price,
                current_portfolio=portfolio_df
            )

            if decision.approved:
                # Use risk-adjusted size if provided
                if decision.suggested_size is not None:
                    risk_adjusted_qty = int(decision.suggested_size / price)
                    validated_positions[symbol] = risk_adjusted_qty
                    if risk_adjusted_qty != target_qty:
                        logger.info(
                            f"Position size adjusted by risk manager: {symbol} "
                            f"{target_qty} → {risk_adjusted_qty} shares"
                        )
                else:
                    validated_positions[symbol] = target_qty
            else:
                logger.warning(
                    f"Trade rejected by risk manager: {symbol} {trade_qty} @ {price} "
                    f"Reason: {decision.reason}"
                )
                # Notify strategy of rejection
                self.underlying_strategy.on_risk_rejection(
                    symbol=symbol,
                    requested_qty=trade_qty,
                    rejection_reason=decision.reason or "Unknown"
                )
                # Keep current position
                validated_positions[symbol] = current_qty

        return validated_positions

    def _positions_to_dataframe(
        self,
        positions: Dict[str, Position]
    ) -> pd.DataFrame:
        """Convert positions dict to DataFrame for risk manager."""
        if not positions:
            return pd.DataFrame(columns=['symbol', 'quantity', 'price', 'value', 'sector'])

        records = []
        for symbol, pos in positions.items():
            records.append({
                'symbol': symbol,
                'quantity': pos.quantity,
                'price': pos.current_price,
                'value': pos.quantity * pos.current_price,
                'sector': pos.metadata.get('sector', 'Unknown')
            })

        return pd.DataFrame(records)
