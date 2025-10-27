"""Tests for Fill Tracker."""

import pytest
from datetime import datetime, timezone

from src.execution.clients.base import Order, OrderSide, OrderStatus, OrderType
from src.execution.clients.mock_client import MockClient
from src.execution.fill_tracker import FillTracker, Position


class TestFillTracker:
    """Test suite for FillTracker."""

    def test_process_fill_opens_long_position(self):
        """Test processing fill that opens long position."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=150.0,
            filled_at=datetime.now(timezone.utc),
        )

        tracker.process_fill(order)

        position = tracker.get_position("AAPL")
        assert position is not None
        assert position.quantity == 100.0
        assert position.avg_entry_price == 150.0
        assert position.realized_pnl == 0.0

    def test_process_fill_opens_short_position(self):
        """Test processing fill that opens short position."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="MSFT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=50.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=50.0,
            avg_fill_price=300.0,
            filled_at=datetime.now(timezone.utc),
        )

        tracker.process_fill(order)

        position = tracker.get_position("MSFT")
        assert position is not None
        assert position.quantity == -50.0
        assert position.avg_entry_price == 300.0

    def test_process_fill_adds_to_position(self):
        """Test processing fill that adds to existing position."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        # First buy: 100 shares at $150
        order1 = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="GOOGL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=150.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order1)

        # Second buy: 50 shares at $160
        order2 = Order(
            order_id="order-2",
            client_order_id="test-2",
            symbol="GOOGL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=50.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=50.0,
            avg_fill_price=160.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order2)

        position = tracker.get_position("GOOGL")
        assert position.quantity == 150.0
        # Average: (100*150 + 50*160) / 150 = 153.33
        assert abs(position.avg_entry_price - 153.333) < 0.01
        assert position.realized_pnl == 0.0

    def test_process_fill_reduces_position(self):
        """Test processing fill that reduces position."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        # Buy 100 at $150
        order1 = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="TSLA",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=150.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order1)

        # Sell 40 at $160 (profit of $10/share)
        order2 = Order(
            order_id="order-2",
            client_order_id="test-2",
            symbol="TSLA",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=40.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=40.0,
            avg_fill_price=160.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order2)

        position = tracker.get_position("TSLA")
        assert position.quantity == 60.0
        assert position.avg_entry_price == 150.0  # Unchanged for remaining
        assert position.realized_pnl == 400.0  # 40 * (160 - 150)

    def test_process_fill_closes_position(self):
        """Test processing fill that closes position."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        # Buy 100 at $200
        order1 = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="NVDA",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=200.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order1)

        # Sell 100 at $220
        order2 = Order(
            order_id="order-2",
            client_order_id="test-2",
            symbol="NVDA",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=220.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order2)

        position = tracker.get_position("NVDA")
        assert position.quantity == 0.0
        assert position.realized_pnl == 2000.0  # 100 * (220 - 200)

    def test_process_fill_position_flip(self):
        """Test processing fill that flips position from long to short."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        # Buy 50 at $100
        order1 = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="META",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=50.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=50.0,
            avg_fill_price=100.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order1)

        # Sell 100 at $110 (close 50 long, open 50 short)
        order2 = Order(
            order_id="order-2",
            client_order_id="test-2",
            symbol="META",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=110.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order2)

        position = tracker.get_position("META")
        assert position.quantity == -50.0  # Now short
        assert position.avg_entry_price == 110.0  # New position entry
        assert position.realized_pnl == 500.0  # 50 * (110 - 100) from closed long

    def test_process_partial_fill(self):
        """Test processing partial fill."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="AMZN",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100.0,
            limit_price=150.0,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=60.0,
            avg_fill_price=150.0,
            filled_at=datetime.now(timezone.utc),
        )

        tracker.process_fill(order)

        position = tracker.get_position("AMZN")
        assert position.quantity == 60.0

    def test_get_all_positions(self):
        """Test retrieving all positions."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        symbols = ["AAPL", "MSFT", "GOOGL"]
        for i, symbol in enumerate(symbols):
            order = Order(
                order_id=f"order-{i}",
                client_order_id=f"test-{i}",
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=100.0,
                limit_price=None,
                status=OrderStatus.FILLED,
                filled_quantity=100.0,
                avg_fill_price=100.0 + i * 10,
                filled_at=datetime.now(timezone.utc),
            )
            tracker.process_fill(order)

        positions = tracker.get_all_positions()
        assert len(positions) == 3
        assert set(positions.keys()) == {"AAPL", "MSFT", "GOOGL"}

    def test_get_fills(self):
        """Test retrieving fill history."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        # Create fills for multiple symbols
        for i in range(3):
            order = Order(
                order_id=f"aapl-{i}",
                client_order_id=f"test-aapl-{i}",
                symbol="AAPL",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=10.0,
                limit_price=None,
                status=OrderStatus.FILLED,
                filled_quantity=10.0,
                avg_fill_price=150.0,
                filled_at=datetime.now(timezone.utc),
            )
            tracker.process_fill(order)

        order_msft = Order(
            order_id="msft-1",
            client_order_id="test-msft-1",
            symbol="MSFT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=20.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=20.0,
            avg_fill_price=300.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order_msft)

        # Get all fills
        all_fills = tracker.get_fills()
        assert len(all_fills) == 4

        # Get AAPL fills only
        aapl_fills = tracker.get_fills(symbol="AAPL")
        assert len(aapl_fills) == 3
        assert all(f.symbol == "AAPL" for f in aapl_fills)

    def test_reconcile_positions_no_discrepancy(self):
        """Test position reconciliation with no discrepancies."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        # Set broker position
        client.set_position("AAPL", 100.0)

        # Set matching local position
        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=150.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order)

        discrepancies = tracker.reconcile_positions()
        assert len(discrepancies) == 0

    def test_reconcile_positions_with_discrepancy(self):
        """Test position reconciliation with discrepancies."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        # Broker has 150 shares
        client.set_position("AAPL", 150.0)

        # Local tracker has 100 shares
        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=150.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order)

        discrepancies = tracker.reconcile_positions()
        assert len(discrepancies) == 1
        assert discrepancies["AAPL"] == -50.0  # Local is 50 less than broker

    def test_sync_with_broker(self):
        """Test syncing local positions with broker."""
        client = MockClient()
        client.connect()
        tracker = FillTracker(client)

        # Local has incorrect position
        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=150.0,
            filled_at=datetime.now(timezone.utc),
        )
        tracker.process_fill(order)

        # Broker has different positions
        client.set_position("AAPL", 200.0)
        client.set_position("MSFT", 50.0)

        # Sync with broker
        tracker.sync_with_broker()

        positions = tracker.get_all_positions()
        assert len(positions) == 2
        assert positions["AAPL"].quantity == 200.0
        assert positions["MSFT"].quantity == 50.0
