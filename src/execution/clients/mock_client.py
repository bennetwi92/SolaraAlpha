"""Mock execution client for testing without broker dependencies."""

import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from src.execution.clients.base import (
    ExecutionClient,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)


class MockClient(ExecutionClient):
    """Mock execution client for deterministic testing.

    Simulates broker behavior without external dependencies.
    Useful for unit tests and development without API keys.
    """

    def __init__(
        self,
        auto_fill: bool = True,
        fill_delay_ms: float = 0.0,
        default_fill_price: Optional[float] = None,
    ):
        """Initialize mock client.

        Args:
            auto_fill: Automatically fill orders on submission
            fill_delay_ms: Simulated fill delay in milliseconds
            default_fill_price: Default fill price (uses limit price or 100.0)
        """
        self.auto_fill = auto_fill
        self.fill_delay_ms = fill_delay_ms
        self.default_fill_price = default_fill_price
        self._connected = False
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, float] = {}
        self._fill_callback: Optional[Callable[[Order], None]] = None

    def connect(self) -> None:
        """Establish mock connection."""
        self._connected = True

    def disconnect(self) -> None:
        """Close mock connection."""
        self._connected = False
        self._orders.clear()
        self._positions.clear()

    def submit_order(self, order: Order) -> Order:
        """Submit order to mock broker.

        Args:
            order: Order to submit

        Returns:
            Order with mock broker-assigned order_id

        Raises:
            ValueError: If order validation fails
            ConnectionError: If not connected
        """
        if not self._connected:
            raise ConnectionError("Not connected to mock broker")

        # Validate order
        if order.quantity <= 0:
            raise ValueError(f"Invalid quantity: {order.quantity}")

        if order.order_type == OrderType.LIMIT and not order.limit_price:
            raise ValueError("Limit price required for limit orders")

        # Assign broker order ID
        order.order_id = str(uuid.uuid4())
        order.status = OrderStatus.SUBMITTED
        order.submitted_at = datetime.now(timezone.utc)
        order.broker = "mock"

        # Store order
        self._orders[order.order_id] = order

        # Auto-fill if enabled
        if self.auto_fill:
            self._fill_order(order)

        return order

    def cancel_order(self, order_id: str) -> Order:
        """Cancel mock order.

        Args:
            order_id: Mock broker order ID

        Returns:
            Order with CANCELLED status

        Raises:
            ValueError: If order not found or already terminal
        """
        if not self._connected:
            raise ConnectionError("Not connected to mock broker")

        if order_id not in self._orders:
            raise ValueError(f"Order {order_id} not found")

        order = self._orders[order_id]

        if order.is_terminal():
            raise ValueError(
                f"Cannot cancel order {order_id}: already {order.status.value}"
            )

        order.status = OrderStatus.CANCELLED
        self._orders[order_id] = order

        return order

    def get_order_status(self, order_id: str) -> Order:
        """Get mock order status.

        Args:
            order_id: Mock broker order ID

        Returns:
            Current order state

        Raises:
            ValueError: If order not found
        """
        if not self._connected:
            raise ConnectionError("Not connected to mock broker")

        if order_id not in self._orders:
            raise ValueError(f"Order {order_id} not found")

        return self._orders[order_id]

    def get_positions(self) -> dict[str, float]:
        """Get mock positions.

        Returns:
            Dictionary mapping symbol to quantity
        """
        if not self._connected:
            raise ConnectionError("Not connected to mock broker")

        return dict(self._positions)

    def register_fill_callback(self, callback: Callable[[Order], None]) -> None:
        """Register callback for mock fill notifications.

        Args:
            callback: Function to call with updated Order on fills
        """
        self._fill_callback = callback

    def _fill_order(self, order: Order) -> None:
        """Fill order in mock broker.

        Args:
            order: Order to fill
        """
        # Determine fill price
        if self.default_fill_price:
            fill_price = self.default_fill_price
        elif order.order_type == OrderType.LIMIT and order.limit_price:
            fill_price = order.limit_price
        else:
            fill_price = 100.0  # Default market price

        # Update order
        order.filled_quantity = order.quantity
        order.avg_fill_price = fill_price
        order.status = OrderStatus.FILLED
        order.filled_at = datetime.now(timezone.utc)

        # Update positions
        qty_change = order.quantity if order.side == OrderSide.BUY else -order.quantity
        current_qty = self._positions.get(order.symbol, 0.0)
        self._positions[order.symbol] = current_qty + qty_change

        # Store updated order
        self._orders[order.order_id] = order

        # Trigger callback
        if self._fill_callback:
            self._fill_callback(order)

    def set_position(self, symbol: str, quantity: float) -> None:
        """Manually set position (for testing).

        Args:
            symbol: Instrument symbol
            quantity: Position quantity
        """
        self._positions[symbol] = quantity

    def fill_order_manually(
        self,
        order_id: str,
        fill_price: float,
        fill_quantity: Optional[float] = None,
    ) -> Order:
        """Manually fill order (for testing partial fills).

        Args:
            order_id: Order ID to fill
            fill_price: Fill price
            fill_quantity: Quantity to fill (defaults to full order)

        Returns:
            Updated order

        Raises:
            ValueError: If order not found or already filled
        """
        if order_id not in self._orders:
            raise ValueError(f"Order {order_id} not found")

        order = self._orders[order_id]

        if order.status == OrderStatus.FILLED:
            raise ValueError(f"Order {order_id} already filled")

        # Determine fill quantity
        remaining = order.quantity - order.filled_quantity
        if fill_quantity is None:
            fill_quantity = remaining
        else:
            fill_quantity = min(fill_quantity, remaining)

        # Update order
        old_filled_qty = order.filled_quantity
        new_filled_qty = old_filled_qty + fill_quantity

        # Update average fill price
        if old_filled_qty > 0 and order.avg_fill_price:
            total_cost = (
                old_filled_qty * order.avg_fill_price + fill_quantity * fill_price
            )
            order.avg_fill_price = total_cost / new_filled_qty
        else:
            order.avg_fill_price = fill_price

        order.filled_quantity = new_filled_qty

        # Update status
        if new_filled_qty >= order.quantity:
            order.status = OrderStatus.FILLED
            order.filled_at = datetime.now(timezone.utc)
        else:
            order.status = OrderStatus.PARTIALLY_FILLED

        # Update positions
        qty_change = (
            fill_quantity if order.side == OrderSide.BUY else -fill_quantity
        )
        current_qty = self._positions.get(order.symbol, 0.0)
        self._positions[order.symbol] = current_qty + qty_change

        # Store updated order
        self._orders[order_id] = order

        # Trigger callback
        if self._fill_callback:
            self._fill_callback(order)

        return order
