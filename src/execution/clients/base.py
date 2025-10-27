"""Abstract base class for execution clients."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Optional


class OrderSide(Enum):
    """Order side: buy or sell."""

    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Order type: market or limit."""

    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(Enum):
    """Order status states."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    """Order representation.

    Attributes:
        order_id: Unique order identifier (broker-assigned after submission)
        client_order_id: Client-assigned unique identifier
        symbol: Instrument symbol (e.g., 'AAPL', 'BTC/USD')
        side: Buy or sell
        order_type: Market or limit
        quantity: Number of shares/units to trade
        limit_price: Price for limit orders (None for market orders)
        status: Current order status
        filled_quantity: Shares/units filled so far
        avg_fill_price: Average price of filled shares
        submitted_at: Timestamp of order submission
        filled_at: Timestamp of complete fill (None if not fully filled)
        broker: Broker name (e.g., 'alpaca', 'mock')
    """

    order_id: Optional[str]
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    limit_price: Optional[float]
    status: OrderStatus
    filled_quantity: float = 0.0
    avg_fill_price: Optional[float] = None
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    broker: str = "unknown"

    def is_terminal(self) -> bool:
        """Check if order is in a terminal state (no further updates expected).

        Returns:
            True if status is FILLED, CANCELLED, or REJECTED
        """
        return self.status in {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        }


class ExecutionClient(ABC):
    """Abstract interface for broker execution clients.

    All broker integrations must implement this interface to ensure
    strategy-agnostic execution. Strategies interact with ExecutionClient
    without knowing which broker is used.
    """

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to broker API.

        Raises:
            ConnectionError: If connection fails
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection to broker API."""
        pass

    @abstractmethod
    def submit_order(self, order: Order) -> Order:
        """Submit order to broker.

        Args:
            order: Order to submit (order_id will be None initially)

        Returns:
            Order with broker-assigned order_id and SUBMITTED status

        Raises:
            ValueError: If order validation fails
            ConnectionError: If broker connection lost
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> Order:
        """Cancel an open order.

        Args:
            order_id: Broker-assigned order ID

        Returns:
            Order with CANCELLED status

        Raises:
            ValueError: If order not found or already terminal
            ConnectionError: If broker connection lost
        """
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> Order:
        """Get current order status from broker.

        Args:
            order_id: Broker-assigned order ID

        Returns:
            Current order state

        Raises:
            ValueError: If order not found
            ConnectionError: If broker connection lost
        """
        pass

    @abstractmethod
    def get_positions(self) -> dict[str, float]:
        """Get current positions from broker.

        Returns:
            Dictionary mapping symbol to quantity (positive for long,
            negative for short)

        Raises:
            ConnectionError: If broker connection lost
        """
        pass

    @abstractmethod
    def register_fill_callback(self, callback: Callable[[Order], None]) -> None:
        """Register callback for fill notifications.

        Broker will call this callback when orders are filled (fully or
        partially). Enables event-driven fill tracking.

        Args:
            callback: Function to call with updated Order on fills
        """
        pass
