"""Alpaca paper trading client implementation."""

import time
from datetime import datetime, timezone
from typing import Callable, Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import OrderType as AlpacaOrderType
from alpaca.trading.enums import TimeInForce
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.stream import TradingStream

from src.execution.clients.base import (
    ExecutionClient,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.execution.config import BrokerConfig


class AlpacaClient(ExecutionClient):
    """Alpaca paper trading execution client.

    Implements ExecutionClient interface for Alpaca's paper trading API.
    Supports market and limit orders for equities and crypto.
    """

    def __init__(self, config: BrokerConfig):
        """Initialize Alpaca client.

        Args:
            config: Broker configuration with API keys
        """
        self.config = config
        self._client: Optional[TradingClient] = None
        self._stream: Optional[TradingStream] = None
        self._fill_callback: Optional[Callable[[Order], None]] = None
        self._connected = False
        self._rate_limit_delay = 0.2  # 200ms between requests (5 req/sec)
        self._last_request_time = 0.0

    def connect(self) -> None:
        """Establish connection to Alpaca API.

        Raises:
            ConnectionError: If connection fails
        """
        try:
            self._client = TradingClient(
                api_key=self.config.api_key,
                secret_key=self.config.secret_key,
                paper=self.config.paper_trading,
            )

            # Test connection
            self._client.get_account()

            # Initialize streaming client for fill notifications
            self._stream = TradingStream(
                api_key=self.config.api_key,
                secret_key=self.config.secret_key,
                paper=self.config.paper_trading,
            )

            self._connected = True

        except Exception as e:
            raise ConnectionError(f"Failed to connect to Alpaca: {e}") from e

    def disconnect(self) -> None:
        """Close connection to Alpaca API."""
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass
        self._connected = False
        self._client = None
        self._stream = None

    def _rate_limit(self) -> None:
        """Apply rate limiting to respect Alpaca API constraints."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_limit_delay:
            time.sleep(self._rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    def _check_connection(self) -> None:
        """Verify client is connected.

        Raises:
            ConnectionError: If not connected
        """
        if not self._connected or not self._client:
            raise ConnectionError("Not connected to Alpaca. Call connect() first.")

    def submit_order(self, order: Order) -> Order:
        """Submit order to Alpaca.

        Args:
            order: Order to submit

        Returns:
            Order with broker-assigned order_id and SUBMITTED status

        Raises:
            ValueError: If order validation fails
            ConnectionError: If broker connection lost
        """
        self._check_connection()
        self._rate_limit()

        # Validate order
        if order.quantity <= 0:
            raise ValueError(f"Invalid quantity: {order.quantity}")

        if order.order_type == OrderType.LIMIT and not order.limit_price:
            raise ValueError("Limit price required for limit orders")

        if order.order_type == OrderType.LIMIT and order.limit_price <= 0:
            raise ValueError(f"Invalid limit price: {order.limit_price}")

        try:
            # Convert to Alpaca request
            alpaca_side = (
                AlpacaOrderSide.BUY if order.side == OrderSide.BUY
                else AlpacaOrderSide.SELL
            )

            if order.order_type == OrderType.MARKET:
                request = MarketOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=alpaca_side,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=order.client_order_id,
                )
            else:  # LIMIT
                request = LimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=alpaca_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=order.limit_price,
                    client_order_id=order.client_order_id,
                )

            # Submit to Alpaca
            alpaca_order = self._client.submit_order(request)

            # Update order with broker response
            order.order_id = alpaca_order.id
            order.status = self._convert_status(alpaca_order.status)
            order.submitted_at = datetime.now(timezone.utc)
            order.broker = "alpaca"

            return order

        except Exception as e:
            order.status = OrderStatus.REJECTED
            raise ValueError(f"Order submission failed: {e}") from e

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
        self._check_connection()
        self._rate_limit()

        try:
            # Cancel via Alpaca API
            self._client.cancel_order_by_id(order_id)

            # Get updated order status
            alpaca_order = self._client.get_order_by_id(order_id)

            return self._convert_order(alpaca_order)

        except Exception as e:
            raise ValueError(f"Order cancellation failed: {e}") from e

    def get_order_status(self, order_id: str) -> Order:
        """Get current order status from Alpaca.

        Args:
            order_id: Broker-assigned order ID

        Returns:
            Current order state

        Raises:
            ValueError: If order not found
            ConnectionError: If broker connection lost
        """
        self._check_connection()
        self._rate_limit()

        try:
            alpaca_order = self._client.get_order_by_id(order_id)
            return self._convert_order(alpaca_order)

        except Exception as e:
            raise ValueError(f"Failed to get order status: {e}") from e

    def get_positions(self) -> dict[str, float]:
        """Get current positions from Alpaca.

        Returns:
            Dictionary mapping symbol to quantity (positive for long)

        Raises:
            ConnectionError: If broker connection lost
        """
        self._check_connection()
        self._rate_limit()

        try:
            positions = self._client.get_all_positions()
            return {pos.symbol: float(pos.qty) for pos in positions}

        except Exception as e:
            raise ConnectionError(f"Failed to get positions: {e}") from e

    def register_fill_callback(self, callback: Callable[[Order], None]) -> None:
        """Register callback for fill notifications.

        Args:
            callback: Function to call with updated Order on fills
        """
        self._fill_callback = callback

        if self._stream and self._fill_callback:
            # Subscribe to trade updates
            async def handle_trade_update(data):
                """Handle trade update from Alpaca stream."""
                try:
                    order = self._convert_order(data.order)
                    if order.status in {
                        OrderStatus.FILLED,
                        OrderStatus.PARTIALLY_FILLED,
                    }:
                        self._fill_callback(order)
                except Exception:
                    pass  # Don't crash on callback errors

            self._stream.subscribe_trade_updates(handle_trade_update)

    def _convert_order(self, alpaca_order) -> Order:
        """Convert Alpaca order to internal Order format.

        Args:
            alpaca_order: Alpaca order object

        Returns:
            Internal Order representation
        """
        side = OrderSide.BUY if alpaca_order.side == "buy" else OrderSide.SELL
        order_type = (
            OrderType.MARKET if alpaca_order.type == "market" else OrderType.LIMIT
        )
        status = self._convert_status(alpaca_order.status)

        filled_qty = float(alpaca_order.filled_qty or 0)
        avg_fill_price = float(alpaca_order.filled_avg_price or 0) if filled_qty > 0 else None

        filled_at = None
        if status == OrderStatus.FILLED and alpaca_order.filled_at:
            filled_at = alpaca_order.filled_at

        return Order(
            order_id=alpaca_order.id,
            client_order_id=alpaca_order.client_order_id,
            symbol=alpaca_order.symbol,
            side=side,
            order_type=order_type,
            quantity=float(alpaca_order.qty),
            limit_price=float(alpaca_order.limit_price) if alpaca_order.limit_price else None,
            status=status,
            filled_quantity=filled_qty,
            avg_fill_price=avg_fill_price,
            submitted_at=alpaca_order.submitted_at,
            filled_at=filled_at,
            broker="alpaca",
        )

    def _convert_status(self, alpaca_status: str) -> OrderStatus:
        """Convert Alpaca order status to internal OrderStatus.

        Args:
            alpaca_status: Alpaca status string

        Returns:
            Internal OrderStatus enum
        """
        status_map = {
            "pending_new": OrderStatus.PENDING,
            "accepted": OrderStatus.SUBMITTED,
            "new": OrderStatus.SUBMITTED,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
            "expired": OrderStatus.CANCELLED,
        }
        return status_map.get(alpaca_status, OrderStatus.PENDING)
