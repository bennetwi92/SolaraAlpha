"""Order Management System (OMS) for tracking and managing orders."""

import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from src.execution.clients.base import ExecutionClient, Order, OrderStatus


class OrderManager:
    """Manages order lifecycle and state tracking.

    Thread-safe order tracking system that manages order submission,
    cancellation, and status updates. Maintains local order state and
    synchronizes with broker.
    """

    def __init__(self, client: ExecutionClient):
        """Initialize order manager.

        Args:
            client: Execution client for broker interaction
        """
        self.client = client
        self._orders: dict[str, Order] = {}  # order_id -> Order
        self._client_orders: dict[str, str] = {}  # client_order_id -> order_id
        self._lock = Lock()

    def submit_order(self, order: Order) -> Order:
        """Submit order to broker and track in OMS.

        Args:
            order: Order to submit (will generate client_order_id if missing)

        Returns:
            Order with broker-assigned order_id

        Raises:
            ValueError: If order validation fails
            ConnectionError: If broker connection lost
        """
        # Generate client order ID if not provided
        if not order.client_order_id:
            order.client_order_id = str(uuid.uuid4())

        # Submit to broker
        submitted_order = self.client.submit_order(order)

        # Track in OMS
        with self._lock:
            if submitted_order.order_id:
                self._orders[submitted_order.order_id] = submitted_order
                self._client_orders[submitted_order.client_order_id] = (
                    submitted_order.order_id
                )

        return submitted_order

    def cancel_order(self, order_id: str) -> Order:
        """Cancel an open order.

        Args:
            order_id: Broker-assigned order ID

        Returns:
            Order with CANCELLED status

        Raises:
            ValueError: If order not found or already terminal
        """
        # Verify order exists and is not terminal
        with self._lock:
            if order_id not in self._orders:
                raise ValueError(f"Order {order_id} not found")

            order = self._orders[order_id]
            if order.is_terminal():
                raise ValueError(
                    f"Cannot cancel order {order_id}: already {order.status.value}"
                )

        # Cancel via broker
        cancelled_order = self.client.cancel_order(order_id)

        # Update local state
        with self._lock:
            self._orders[order_id] = cancelled_order

        return cancelled_order

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order from local cache.

        Args:
            order_id: Broker-assigned order ID

        Returns:
            Order if found, None otherwise
        """
        with self._lock:
            return self._orders.get(order_id)

    def get_order_by_client_id(self, client_order_id: str) -> Optional[Order]:
        """Get order by client order ID.

        Args:
            client_order_id: Client-assigned order ID

        Returns:
            Order if found, None otherwise
        """
        with self._lock:
            order_id = self._client_orders.get(client_order_id)
            if order_id:
                return self._orders.get(order_id)
        return None

    def refresh_order(self, order_id: str) -> Order:
        """Refresh order status from broker.

        Args:
            order_id: Broker-assigned order ID

        Returns:
            Updated order

        Raises:
            ValueError: If order not found
            ConnectionError: If broker connection lost
        """
        # Get fresh status from broker
        updated_order = self.client.get_order_status(order_id)

        # Update local cache
        with self._lock:
            self._orders[order_id] = updated_order

        return updated_order

    def get_all_orders(self) -> list[Order]:
        """Get all orders tracked by OMS.

        Returns:
            List of all orders
        """
        with self._lock:
            return list(self._orders.values())

    def get_open_orders(self) -> list[Order]:
        """Get all non-terminal orders.

        Returns:
            List of orders that are not FILLED, CANCELLED, or REJECTED
        """
        with self._lock:
            return [
                order for order in self._orders.values() if not order.is_terminal()
            ]

    def update_order(self, order: Order) -> None:
        """Update order state (typically called by fill callback).

        Args:
            order: Updated order
        """
        with self._lock:
            if order.order_id:
                self._orders[order.order_id] = order
