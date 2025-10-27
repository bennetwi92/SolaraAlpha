"""Real-time position tracking for SolaraAlpha trading strategies.

Maintains current position state including symbol, quantity, entry price,
current price, and unrealized P&L across all strategies and asset classes.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents a single trading position.

    Attributes:
        symbol: Ticker symbol (e.g., 'AAPL', 'BTC-USD').
        asset_class: Asset class (e.g., 'equities', 'crypto').
        strategy_id: Unique identifier for the strategy holding position.
        quantity: Number of shares/units (positive=long, negative=short).
        entry_price: Average entry price per unit.
        current_price: Current market price per unit.
        timestamp: Last update timestamp.
    """

    symbol: str
    asset_class: str
    strategy_id: str
    quantity: float
    entry_price: float
    current_price: float
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def market_value(self) -> float:
        """Calculate current market value of position."""
        return self.quantity * self.current_price

    @property
    def cost_basis(self) -> float:
        """Calculate cost basis of position."""
        return self.quantity * self.entry_price

    @property
    def unrealized_pnl(self) -> float:
        """Calculate unrealized profit/loss."""
        return self.market_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float:
        """Calculate unrealized P&L as percentage of cost basis."""
        if self.cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl / abs(self.cost_basis)) * 100

    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.quantity < 0

    def update_price(self, new_price: float) -> None:
        """Update current market price.

        Args:
            new_price: New market price per unit.
        """
        self.current_price = new_price
        self.timestamp = datetime.now()


class PositionTracker:
    """Tracks all positions across strategies and asset classes.

    Maintains real-time position state, calculates portfolio-level exposure,
    and provides position lookup/aggregation capabilities.

    Example:
        >>> tracker = PositionTracker()
        >>> tracker.add_position(
        ...     symbol='AAPL',
        ...     asset_class='equities',
        ...     strategy_id='pairs_1',
        ...     quantity=100,
        ...     entry_price=150.0,
        ...     current_price=155.0
        ... )
        >>> exposure = tracker.get_portfolio_exposure()
        >>> print(f"Gross exposure: ${exposure['gross']:,.2f}")
    """

    def __init__(self) -> None:
        """Initialize position tracker."""
        # Key: (symbol, strategy_id) -> Position
        self._positions: Dict[tuple[str, str], Position] = {}
        logger.info("Position tracker initialized")

    def add_position(
        self,
        symbol: str,
        asset_class: str,
        strategy_id: str,
        quantity: float,
        entry_price: float,
        current_price: float,
    ) -> Position:
        """Add or update a position.

        If position already exists, updates quantity using weighted average.

        Args:
            symbol: Ticker symbol.
            asset_class: Asset class.
            strategy_id: Strategy identifier.
            quantity: Number of units (positive=long, negative=short).
            entry_price: Entry price per unit.
            current_price: Current market price per unit.

        Returns:
            Updated Position object.
        """
        key = (symbol, strategy_id)

        if key in self._positions:
            # Update existing position with weighted average entry price
            existing = self._positions[key]
            total_quantity = existing.quantity + quantity

            if total_quantity != 0:
                # Weighted average entry price
                new_entry_price = (
                    existing.quantity * existing.entry_price
                    + quantity * entry_price
                ) / total_quantity

                existing.quantity = total_quantity
                existing.entry_price = new_entry_price
                existing.current_price = current_price
                existing.timestamp = datetime.now()

                logger.info(
                    f"Updated position: {symbol} ({strategy_id}), "
                    f"qty={total_quantity:.2f}, entry=${new_entry_price:.2f}"
                )
            else:
                # Position closed
                del self._positions[key]
                logger.info(
                    f"Closed position: {symbol} ({strategy_id})"
                )
                return None

            return existing
        else:
            # New position
            position = Position(
                symbol=symbol,
                asset_class=asset_class,
                strategy_id=strategy_id,
                quantity=quantity,
                entry_price=entry_price,
                current_price=current_price,
            )
            self._positions[key] = position

            logger.info(
                f"New position: {symbol} ({strategy_id}), "
                f"qty={quantity:.2f}, entry=${entry_price:.2f}"
            )

            return position

    def update_price(self, symbol: str, price: float) -> None:
        """Update current price for all positions of a symbol.

        Args:
            symbol: Ticker symbol.
            price: New market price.
        """
        updated_count = 0
        for (pos_symbol, _), position in self._positions.items():
            if pos_symbol == symbol:
                position.update_price(price)
                updated_count += 1

        if updated_count > 0:
            logger.debug(
                f"Updated price for {updated_count} positions of {symbol}"
            )

    def get_position(
        self, symbol: str, strategy_id: str
    ) -> Optional[Position]:
        """Get a specific position.

        Args:
            symbol: Ticker symbol.
            strategy_id: Strategy identifier.

        Returns:
            Position object or None if not found.
        """
        return self._positions.get((symbol, strategy_id))

    def get_all_positions(self) -> List[Position]:
        """Get all active positions.

        Returns:
            List of Position objects.
        """
        return list(self._positions.values())

    def get_positions_by_strategy(self, strategy_id: str) -> List[Position]:
        """Get all positions for a strategy.

        Args:
            strategy_id: Strategy identifier.

        Returns:
            List of Position objects for the strategy.
        """
        return [
            pos
            for (_, strat_id), pos in self._positions.items()
            if strat_id == strategy_id
        ]

    def get_positions_by_symbol(self, symbol: str) -> List[Position]:
        """Get all positions for a symbol across strategies.

        Args:
            symbol: Ticker symbol.

        Returns:
            List of Position objects for the symbol.
        """
        return [
            pos
            for (sym, _), pos in self._positions.items()
            if sym == symbol
        ]

    def get_portfolio_exposure(self) -> Dict[str, float]:
        """Calculate portfolio-level exposure metrics.

        Returns:
            Dictionary with exposure metrics:
                - gross: Sum of absolute position values (long + short)
                - net: Net position value (long - short)
                - long: Total long position value
                - short: Total short position value (absolute)
                - total_pnl: Total unrealized P&L
        """
        gross = 0.0
        long_value = 0.0
        short_value = 0.0
        total_pnl = 0.0

        for position in self._positions.values():
            market_val = position.market_value
            abs_market_val = abs(market_val)

            gross += abs_market_val
            total_pnl += position.unrealized_pnl

            if position.is_long:
                long_value += market_val
            else:
                short_value += abs_market_val

        return {
            "gross": gross,
            "net": long_value - short_value,
            "long": long_value,
            "short": short_value,
            "total_pnl": total_pnl,
        }

    def get_strategy_exposure(self, strategy_id: str) -> Dict[str, float]:
        """Calculate exposure for a specific strategy.

        Args:
            strategy_id: Strategy identifier.

        Returns:
            Dictionary with strategy exposure metrics.
        """
        positions = self.get_positions_by_strategy(strategy_id)

        gross = 0.0
        long_value = 0.0
        short_value = 0.0
        total_pnl = 0.0

        for position in positions:
            market_val = position.market_value
            abs_market_val = abs(market_val)

            gross += abs_market_val
            total_pnl += position.unrealized_pnl

            if position.is_long:
                long_value += market_val
            else:
                short_value += abs_market_val

        return {
            "gross": gross,
            "net": long_value - short_value,
            "long": long_value,
            "short": short_value,
            "total_pnl": total_pnl,
        }

    def get_symbol_exposure(self, symbol: str) -> Dict[str, float]:
        """Calculate exposure for a specific symbol across strategies.

        Args:
            symbol: Ticker symbol.

        Returns:
            Dictionary with symbol exposure metrics.
        """
        positions = self.get_positions_by_symbol(symbol)

        total_quantity = sum(pos.quantity for pos in positions)
        total_market_value = sum(pos.market_value for pos in positions)
        total_pnl = sum(pos.unrealized_pnl for pos in positions)

        return {
            "quantity": total_quantity,
            "market_value": total_market_value,
            "total_pnl": total_pnl,
        }

    def clear_all_positions(self) -> None:
        """Clear all positions (for testing or emergency flatten)."""
        count = len(self._positions)
        self._positions.clear()
        logger.warning(f"Cleared all {count} positions")

    def get_position_count(self) -> int:
        """Get total number of active positions."""
        return len(self._positions)
