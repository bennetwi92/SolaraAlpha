"""Tests for Order Manager."""

import pytest

from src.execution.clients.base import Order, OrderSide, OrderStatus, OrderType
from src.execution.clients.mock_client import MockClient
from src.execution.order_manager import OrderManager


class TestOrderManager:
    """Test suite for OrderManager."""

    def test_submit_order_generates_client_id(self):
        """Test that OMS generates client order ID if missing."""
        client = MockClient(auto_fill=False)
        client.connect()
        oms = OrderManager(client)

        order = Order(
            order_id=None,
            client_order_id="",  # Empty
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = oms.submit_order(order)

        assert submitted.client_order_id != ""
        assert submitted.order_id is not None

    def test_submit_order_preserves_client_id(self):
        """Test that OMS preserves provided client order ID."""
        client = MockClient(auto_fill=False)
        client.connect()
        oms = OrderManager(client)

        order = Order(
            order_id=None,
            client_order_id="my-custom-id",
            symbol="MSFT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=50.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = oms.submit_order(order)

        assert submitted.client_order_id == "my-custom-id"

    def test_get_order(self):
        """Test retrieving order by broker order ID."""
        client = MockClient(auto_fill=True)
        client.connect()
        oms = OrderManager(client)

        order = Order(
            order_id=None,
            client_order_id="test-123",
            symbol="GOOGL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=25.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = oms.submit_order(order)
        retrieved = oms.get_order(submitted.order_id)

        assert retrieved is not None
        assert retrieved.order_id == submitted.order_id
        assert retrieved.symbol == "GOOGL"

    def test_get_order_by_client_id(self):
        """Test retrieving order by client order ID."""
        client = MockClient(auto_fill=True)
        client.connect()
        oms = OrderManager(client)

        order = Order(
            order_id=None,
            client_order_id="my-client-id",
            symbol="TSLA",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=10.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = oms.submit_order(order)
        retrieved = oms.get_order_by_client_id("my-client-id")

        assert retrieved is not None
        assert retrieved.client_order_id == "my-client-id"
        assert retrieved.order_id == submitted.order_id

    def test_cancel_order(self):
        """Test order cancellation via OMS."""
        client = MockClient(auto_fill=False)
        client.connect()
        oms = OrderManager(client)

        order = Order(
            order_id=None,
            client_order_id="cancel-test",
            symbol="NVDA",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=15.0,
            limit_price=600.0,
            status=OrderStatus.PENDING,
        )

        submitted = oms.submit_order(order)
        cancelled = oms.cancel_order(submitted.order_id)

        assert cancelled.status == OrderStatus.CANCELLED

        # Verify local cache updated
        cached = oms.get_order(submitted.order_id)
        assert cached.status == OrderStatus.CANCELLED

    def test_cancel_nonexistent_order_raises_error(self):
        """Test cancelling non-existent order raises error."""
        client = MockClient()
        client.connect()
        oms = OrderManager(client)

        with pytest.raises(ValueError, match="not found"):
            oms.cancel_order("nonexistent-id")

    def test_cancel_terminal_order_raises_error(self):
        """Test cancelling terminal order raises error."""
        client = MockClient(auto_fill=True)
        client.connect()
        oms = OrderManager(client)

        order = Order(
            order_id=None,
            client_order_id="filled-order",
            symbol="META",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=20.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = oms.submit_order(order)

        with pytest.raises(ValueError, match="already filled"):
            oms.cancel_order(submitted.order_id)

    def test_refresh_order(self):
        """Test refreshing order status from broker."""
        client = MockClient(auto_fill=False)
        client.connect()
        oms = OrderManager(client)

        order = Order(
            order_id=None,
            client_order_id="refresh-test",
            symbol="AMZN",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=30.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = oms.submit_order(order)

        # Manually fill order on broker side
        client.fill_order_manually(submitted.order_id, fill_price=120.0)

        # Refresh from broker
        refreshed = oms.refresh_order(submitted.order_id)

        assert refreshed.status == OrderStatus.FILLED
        assert refreshed.filled_quantity == 30.0

        # Verify cache updated
        cached = oms.get_order(submitted.order_id)
        assert cached.status == OrderStatus.FILLED

    def test_get_all_orders(self):
        """Test retrieving all orders."""
        client = MockClient(auto_fill=True)
        client.connect()
        oms = OrderManager(client)

        symbols = ["AAPL", "MSFT", "GOOGL"]
        for symbol in symbols:
            order = Order(
                order_id=None,
                client_order_id=f"order-{symbol}",
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=10.0,
                limit_price=None,
                status=OrderStatus.PENDING,
            )
            oms.submit_order(order)

        all_orders = oms.get_all_orders()

        assert len(all_orders) == 3
        order_symbols = {o.symbol for o in all_orders}
        assert order_symbols == {"AAPL", "MSFT", "GOOGL"}

    def test_get_open_orders(self):
        """Test retrieving only open orders."""
        client = MockClient(auto_fill=False)
        client.connect()
        oms = OrderManager(client)

        # Submit 3 orders
        orders = []
        for i in range(3):
            order = Order(
                order_id=None,
                client_order_id=f"open-{i}",
                symbol="AAPL",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=10.0,
                limit_price=None,
                status=OrderStatus.PENDING,
            )
            orders.append(oms.submit_order(order))

        # Fill one order
        client.fill_order_manually(orders[0].order_id, fill_price=100.0)
        oms.refresh_order(orders[0].order_id)

        # Cancel one order
        oms.cancel_order(orders[1].order_id)

        # Get open orders (should only be orders[2])
        open_orders = oms.get_open_orders()

        assert len(open_orders) == 1
        assert open_orders[0].order_id == orders[2].order_id
        assert open_orders[0].status == OrderStatus.SUBMITTED

    def test_update_order(self):
        """Test manual order update (fill callback scenario)."""
        client = MockClient(auto_fill=False)
        client.connect()
        oms = OrderManager(client)

        order = Order(
            order_id=None,
            client_order_id="update-test",
            symbol="NFLX",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=40.0,
            limit_price=None,
            status=OrderStatus.PENDING,
        )

        submitted = oms.submit_order(order)

        # Simulate fill callback updating order
        submitted.status = OrderStatus.FILLED
        submitted.filled_quantity = 40.0
        submitted.avg_fill_price = 150.0

        oms.update_order(submitted)

        # Verify cache updated
        cached = oms.get_order(submitted.order_id)
        assert cached.status == OrderStatus.FILLED
        assert cached.filled_quantity == 40.0
