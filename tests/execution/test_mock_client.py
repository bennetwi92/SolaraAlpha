"""Tests for mock execution client."""

import pytest
from datetime import datetime

from src.execution.clients.base import Order, OrderSide, OrderStatus, OrderType
from src.execution.clients.mock_client import MockClient


class TestMockClient:
    """Test suite for MockClient."""

    def test_connect_disconnect(self):
        """Test connection lifecycle."""
        client = MockClient()
        assert not client._connected

        client.connect()
        assert client._connected

        client.disconnect()
        assert not client._connected

    def test_submit_market_order_auto_fill(self):
        """Test market order submission with auto-fill."""
        client = MockClient(auto_fill=True)
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-123",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = client.submit_order(order)

        assert submitted.order_id is not None
        assert submitted.status == OrderStatus.FILLED
        assert submitted.filled_quantity == 100.0
        assert submitted.avg_fill_price == 100.0
        assert submitted.broker == "mock"

    def test_submit_limit_order_auto_fill(self):
        """Test limit order submission with auto-fill."""
        client = MockClient(auto_fill=True)
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-456",
            symbol="MSFT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=50.0,
            limit_price=250.0,
            status=OrderStatus.PENDING,
        )

        submitted = client.submit_order(order)

        assert submitted.order_id is not None
        assert submitted.status == OrderStatus.FILLED
        assert submitted.filled_quantity == 50.0
        assert submitted.avg_fill_price == 250.0

    def test_submit_order_no_auto_fill(self):
        """Test order submission without auto-fill."""
        client = MockClient(auto_fill=False)
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-789",
            symbol="GOOGL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = client.submit_order(order)

        assert submitted.status == OrderStatus.SUBMITTED
        assert submitted.filled_quantity == 0.0
        assert submitted.avg_fill_price is None

    def test_manual_fill_full(self):
        """Test manual full fill."""
        client = MockClient(auto_fill=False)
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-full",
            symbol="TSLA",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = client.submit_order(order)
        filled = client.fill_order_manually(submitted.order_id, fill_price=200.0)

        assert filled.status == OrderStatus.FILLED
        assert filled.filled_quantity == 100.0
        assert filled.avg_fill_price == 200.0

    def test_manual_fill_partial(self):
        """Test manual partial fill."""
        client = MockClient(auto_fill=False)
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-partial",
            symbol="AMZN",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = client.submit_order(order)

        # First partial fill
        filled1 = client.fill_order_manually(
            submitted.order_id, fill_price=150.0, fill_quantity=30.0
        )
        assert filled1.status == OrderStatus.PARTIALLY_FILLED
        assert filled1.filled_quantity == 30.0
        assert filled1.avg_fill_price == 150.0

        # Second partial fill at different price
        filled2 = client.fill_order_manually(
            submitted.order_id, fill_price=160.0, fill_quantity=40.0
        )
        assert filled2.status == OrderStatus.PARTIALLY_FILLED
        assert filled2.filled_quantity == 70.0
        # Average: (30*150 + 40*160) / 70 = 155.71
        assert abs(filled2.avg_fill_price - 155.714) < 0.01

        # Final fill
        filled3 = client.fill_order_manually(
            submitted.order_id, fill_price=170.0, fill_quantity=30.0
        )
        assert filled3.status == OrderStatus.FILLED
        assert filled3.filled_quantity == 100.0

    def test_cancel_order(self):
        """Test order cancellation."""
        client = MockClient(auto_fill=False)
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-cancel",
            symbol="NVDA",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=25.0,
            limit_price=500.0,
            status=OrderStatus.PENDING,
        )

        submitted = client.submit_order(order)
        cancelled = client.cancel_order(submitted.order_id)

        assert cancelled.status == OrderStatus.CANCELLED

    def test_cancel_filled_order_fails(self):
        """Test that cancelling filled order raises error."""
        client = MockClient(auto_fill=True)
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-cancel-filled",
            symbol="META",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = client.submit_order(order)

        with pytest.raises(ValueError, match="already filled"):
            client.cancel_order(submitted.order_id)

    def test_get_order_status(self):
        """Test retrieving order status."""
        client = MockClient(auto_fill=True)
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-status",
            symbol="NFLX",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=15.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = client.submit_order(order)
        status = client.get_order_status(submitted.order_id)

        assert status.order_id == submitted.order_id
        assert status.status == OrderStatus.FILLED

    def test_get_positions(self):
        """Test retrieving positions."""
        client = MockClient(auto_fill=True)
        client.connect()

        # Buy 100 AAPL
        order1 = Order(
            order_id=None,
            client_order_id="pos-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )
        client.submit_order(order1)

        # Sell 50 AAPL
        order2 = Order(
            order_id=None,
            client_order_id="pos-2",
            symbol="AAPL",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=50.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )
        client.submit_order(order2)

        # Buy 25 MSFT
        order3 = Order(
            order_id=None,
            client_order_id="pos-3",
            symbol="MSFT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=25.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )
        client.submit_order(order3)

        positions = client.get_positions()

        assert positions["AAPL"] == 50.0  # 100 - 50
        assert positions["MSFT"] == 25.0

    def test_fill_callback(self):
        """Test fill callback registration and execution."""
        client = MockClient(auto_fill=True)
        client.connect()

        fills = []

        def callback(order: Order):
            fills.append(order)

        client.register_fill_callback(callback)

        order = Order(
            order_id=None,
            client_order_id="test-callback",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        client.submit_order(order)

        assert len(fills) == 1
        assert fills[0].symbol == "AAPL"
        assert fills[0].status == OrderStatus.FILLED

    def test_invalid_quantity_raises_error(self):
        """Test that invalid quantity raises error."""
        client = MockClient()
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-invalid",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.0,  # Invalid
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        with pytest.raises(ValueError, match="Invalid quantity"):
            client.submit_order(order)

    def test_limit_order_without_price_raises_error(self):
        """Test that limit order without price raises error."""
        client = MockClient()
        client.connect()

        order = Order(
            order_id=None,
            client_order_id="test-no-price",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10.0,
            limit_price=None,  # Missing for limit order
            status=OrderStatus.PENDING,
        )

        with pytest.raises(ValueError, match="Limit price required"):
            client.submit_order(order)

    def test_not_connected_raises_error(self):
        """Test that operations without connection raise error."""
        client = MockClient()

        order = Order(
            order_id=None,
            client_order_id="test-disconnected",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        with pytest.raises(ConnectionError, match="Not connected"):
            client.submit_order(order)
