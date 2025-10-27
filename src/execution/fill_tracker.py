"""Fill tracking and position reconciliation."""

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Optional

from src.execution.clients.base import ExecutionClient, Order, OrderSide


@dataclass
class Position:
    """Position representation.

    Attributes:
        symbol: Instrument symbol
        quantity: Net quantity (positive for long, negative for short)
        avg_entry_price: Average entry price
        realized_pnl: Realized profit/loss from closed trades
        last_updated: Timestamp of last position update
    """

    symbol: str
    quantity: float
    avg_entry_price: float
    realized_pnl: float = 0.0
    last_updated: Optional[datetime] = None


class FillTracker:
    """Tracks fills and maintains position state.

    Aggregates order fills into positions and reconciles with broker
    positions to detect discrepancies. Thread-safe for concurrent access.
    """

    def __init__(self, client: ExecutionClient):
        """Initialize fill tracker.

        Args:
            client: Execution client for position reconciliation
        """
        self.client = client
        self._positions: dict[str, Position] = {}  # symbol -> Position
        self._fills: list[Order] = []  # All fills
        self._lock = Lock()

    def process_fill(self, order: Order) -> None:
        """Process a filled or partially filled order.

        Updates position state based on order fill. Handles both full
        and partial fills.

        Args:
            order: Filled or partially filled order
        """
        if order.filled_quantity <= 0 or not order.avg_fill_price:
            return  # Nothing to process

        with self._lock:
            # Record fill
            self._fills.append(order)

            # Get or create position
            position = self._positions.get(order.symbol)
            if not position:
                position = Position(
                    symbol=order.symbol,
                    quantity=0.0,
                    avg_entry_price=0.0,
                    last_updated=order.filled_at,
                )
                self._positions[order.symbol] = position

            # Calculate quantity change
            qty_change = order.filled_quantity
            if order.side == OrderSide.SELL:
                qty_change = -qty_change

            # Update position
            old_qty = position.quantity
            new_qty = old_qty + qty_change

            # Handle position flips and closes
            if old_qty == 0:
                # Opening new position
                position.avg_entry_price = order.avg_fill_price
            elif (old_qty > 0 and new_qty < 0) or (old_qty < 0 and new_qty > 0):
                # Position flip: realize PnL on old position, start new
                realized_pnl = self._calculate_pnl(
                    old_qty, position.avg_entry_price, order.avg_fill_price
                )
                position.realized_pnl += realized_pnl

                # New position starts at fill price
                position.avg_entry_price = order.avg_fill_price
            elif abs(new_qty) < abs(old_qty):
                # Reducing position: realize partial PnL
                closed_qty = old_qty - new_qty
                realized_pnl = self._calculate_pnl(
                    closed_qty, position.avg_entry_price, order.avg_fill_price
                )
                position.realized_pnl += realized_pnl
                # Avg entry price unchanged for remaining position
            else:
                # Adding to position: update average entry price
                if new_qty != 0:
                    total_cost = (
                        abs(old_qty) * position.avg_entry_price
                        + abs(qty_change) * order.avg_fill_price
                    )
                    position.avg_entry_price = total_cost / abs(new_qty)

            position.quantity = new_qty
            position.last_updated = order.filled_at or datetime.utcnow()

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get current position for symbol.

        Args:
            symbol: Instrument symbol

        Returns:
            Position if exists, None otherwise
        """
        with self._lock:
            return self._positions.get(symbol)

    def get_all_positions(self) -> dict[str, Position]:
        """Get all current positions.

        Returns:
            Dictionary mapping symbol to Position
        """
        with self._lock:
            return dict(self._positions)

    def get_fills(self, symbol: Optional[str] = None) -> list[Order]:
        """Get fill history.

        Args:
            symbol: Optional symbol filter

        Returns:
            List of filled orders (filtered by symbol if provided)
        """
        with self._lock:
            if symbol:
                return [f for f in self._fills if f.symbol == symbol]
            return list(self._fills)

    def reconcile_positions(self) -> dict[str, float]:
        """Reconcile local positions with broker positions.

        Compares local position state with broker positions and returns
        discrepancies. Used to detect tracking errors.

        Returns:
            Dictionary mapping symbol to quantity difference
            (local - broker). Empty dict if all positions match.

        Raises:
            ConnectionError: If broker connection lost
        """
        broker_positions = self.client.get_positions()

        with self._lock:
            discrepancies = {}

            # Check all local positions
            for symbol, position in self._positions.items():
                broker_qty = broker_positions.get(symbol, 0.0)
                local_qty = position.quantity

                if abs(local_qty - broker_qty) > 0.001:  # Tolerance for float precision
                    discrepancies[symbol] = local_qty - broker_qty

            # Check for broker positions we don't have locally
            for symbol, broker_qty in broker_positions.items():
                if symbol not in self._positions and abs(broker_qty) > 0.001:
                    discrepancies[symbol] = -broker_qty

        return discrepancies

    def sync_with_broker(self) -> None:
        """Sync local positions with broker (overwrite local state).

        Overwrites local position state with broker positions.
        Use with caution—only call if you trust broker state over local.

        Raises:
            ConnectionError: If broker connection lost
        """
        broker_positions = self.client.get_positions()

        with self._lock:
            # Clear local positions
            self._positions.clear()

            # Rebuild from broker state
            for symbol, quantity in broker_positions.items():
                if abs(quantity) > 0.001:
                    self._positions[symbol] = Position(
                        symbol=symbol,
                        quantity=quantity,
                        avg_entry_price=0.0,  # Unknown from broker API
                        realized_pnl=0.0,
                        last_updated=datetime.utcnow(),
                    )

    def _calculate_pnl(self, quantity: float, entry_price: float, exit_price: float) -> float:
        """Calculate realized PnL for a trade.

        Args:
            quantity: Quantity traded (positive for long, negative for short)
            entry_price: Entry price
            exit_price: Exit price

        Returns:
            Realized PnL
        """
        if quantity > 0:
            # Long position closed
            return quantity * (exit_price - entry_price)
        else:
            # Short position closed
            return abs(quantity) * (entry_price - exit_price)
