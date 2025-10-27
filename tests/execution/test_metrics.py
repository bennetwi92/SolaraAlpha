"""Tests for Execution Metrics."""

import pytest
from datetime import datetime, timedelta, timezone

from src.execution.clients.base import Order, OrderSide, OrderStatus, OrderType
from src.execution.metrics import ExecutionMetrics, ExecutionQuality


class TestExecutionMetrics:
    """Test suite for ExecutionMetrics."""

    def test_calculate_fill_rate_full_fill(self):
        """Test fill rate calculation for fully filled order."""
        metrics = ExecutionMetrics()

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
        )

        quality = metrics.calculate_metrics(order)

        assert quality.fill_rate == 1.0

    def test_calculate_fill_rate_partial_fill(self):
        """Test fill rate calculation for partially filled order."""
        metrics = ExecutionMetrics()

        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="MSFT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100.0,
            limit_price=300.0,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=60.0,
            avg_fill_price=300.0,
        )

        quality = metrics.calculate_metrics(order)

        assert quality.fill_rate == 0.6

    def test_calculate_latency(self):
        """Test latency calculation."""
        metrics = ExecutionMetrics()

        submitted = datetime.now(timezone.utc)
        filled = submitted + timedelta(milliseconds=250)

        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="GOOGL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=50.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=50.0,
            avg_fill_price=2800.0,
            submitted_at=submitted,
            filled_at=filled,
        )

        quality = metrics.calculate_metrics(order)

        assert quality.latency_ms is not None
        assert abs(quality.latency_ms - 250.0) < 1.0

    def test_calculate_slippage_buy_order(self):
        """Test slippage calculation for buy order."""
        metrics = ExecutionMetrics()

        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="TSLA",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=202.0,  # Filled $2 higher than market
            submitted_at=datetime.now(timezone.utc),
            filled_at=datetime.now(timezone.utc),
        )

        market_price = 200.0

        quality = metrics.calculate_metrics(order, market_price_at_submission=market_price)

        # Slippage: (202 - 200) / 200 * 10000 = 100 bps
        assert quality.slippage is not None
        assert abs(quality.slippage - 100.0) < 0.1

    def test_calculate_slippage_sell_order(self):
        """Test slippage calculation for sell order."""
        metrics = ExecutionMetrics()

        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="NVDA",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=50.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=50.0,
            avg_fill_price=498.0,  # Filled $2 lower than market
            submitted_at=datetime.now(timezone.utc),
            filled_at=datetime.now(timezone.utc),
        )

        market_price = 500.0

        quality = metrics.calculate_metrics(order, market_price_at_submission=market_price)

        # For sell: (498 - 500) / 500 * 10000 = -40 bps, then negated = 40 bps
        assert quality.slippage is not None
        assert abs(quality.slippage - 40.0) < 0.1

    def test_calculate_spread_paid(self):
        """Test spread paid calculation for limit order."""
        metrics = ExecutionMetrics()

        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="META",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100.0,
            limit_price=300.0,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=298.0,  # Filled $2 better than limit
            submitted_at=datetime.now(timezone.utc),
            filled_at=datetime.now(timezone.utc),
        )

        quality = metrics.calculate_metrics(order)

        # Spread: |298 - 300| / 300 * 10000 = 66.67 bps
        assert quality.spread_paid is not None
        assert abs(quality.spread_paid - 66.67) < 0.1

    def test_get_metrics(self):
        """Test retrieving metrics for specific order."""
        metrics = ExecutionMetrics()

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
        )

        metrics.calculate_metrics(order)

        retrieved = metrics.get_metrics("order-1")
        assert retrieved is not None
        assert retrieved.order_id == "order-1"
        assert retrieved.symbol == "AAPL"

    def test_get_all_metrics(self):
        """Test retrieving all metrics."""
        metrics = ExecutionMetrics()

        for i in range(3):
            order = Order(
                order_id=f"order-{i}",
                client_order_id=f"test-{i}",
                symbol="AAPL",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=100.0,
                limit_price=None,
                status=OrderStatus.FILLED,
                filled_quantity=100.0,
                avg_fill_price=150.0,
            )
            metrics.calculate_metrics(order)

        all_metrics = metrics.get_all_metrics()
        assert len(all_metrics) == 3

    def test_summary_stats(self):
        """Test summary statistics calculation."""
        metrics = ExecutionMetrics()

        # Create orders with varying metrics
        orders = [
            Order(
                order_id="order-1",
                client_order_id="test-1",
                symbol="AAPL",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=100.0,
                limit_price=None,
                status=OrderStatus.FILLED,
                filled_quantity=100.0,
                avg_fill_price=151.0,
                submitted_at=datetime.now(timezone.utc),
                filled_at=datetime.now(timezone.utc) + timedelta(milliseconds=100),
            ),
            Order(
                order_id="order-2",
                client_order_id="test-2",
                symbol="MSFT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=100.0,
                limit_price=None,
                status=OrderStatus.FILLED,
                filled_quantity=100.0,
                avg_fill_price=303.0,
                submitted_at=datetime.now(timezone.utc),
                filled_at=datetime.now(timezone.utc) + timedelta(milliseconds=200),
            ),
            Order(
                order_id="order-3",
                client_order_id="test-3",
                symbol="GOOGL",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=100.0,
                limit_price=None,
                status=OrderStatus.PARTIALLY_FILLED,
                filled_quantity=50.0,
                avg_fill_price=2802.0,
                submitted_at=datetime.now(timezone.utc),
                filled_at=datetime.now(timezone.utc) + timedelta(milliseconds=150),
            ),
        ]

        market_prices = [150.0, 300.0, 2800.0]

        for order, market_price in zip(orders, market_prices):
            metrics.calculate_metrics(order, market_price_at_submission=market_price)

        stats = metrics.get_summary_stats()

        assert stats["total_orders"] == 3
        assert stats["avg_fill_rate"] > 0.8  # (1.0 + 1.0 + 0.5) / 3
        assert stats["avg_latency_ms"] > 0
        assert stats["avg_slippage"] > 0

    def test_summary_stats_symbol_filter(self):
        """Test summary statistics with symbol filter."""
        metrics = ExecutionMetrics()

        # Create orders for multiple symbols
        symbols = ["AAPL", "AAPL", "MSFT"]
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
                avg_fill_price=150.0,
            )
            metrics.calculate_metrics(order)

        # Filter for AAPL only
        stats = metrics.get_summary_stats(symbol="AAPL")

        assert stats["total_orders"] == 2

    def test_generate_daily_report(self):
        """Test daily report generation."""
        metrics = ExecutionMetrics()

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
            submitted_at=datetime.now(timezone.utc),
            filled_at=datetime.now(timezone.utc) + timedelta(milliseconds=100),
        )

        metrics.calculate_metrics(order, market_price_at_submission=150.0)

        report = metrics.generate_daily_report()

        assert "Execution Quality Report" in report
        assert "Total Orders: 1" in report
        assert "Average Fill Rate" in report
        assert "Average Slippage" in report
        assert "Average Latency" in report

    def test_no_metrics_order_without_id(self):
        """Test that calculating metrics without order_id raises error."""
        metrics = ExecutionMetrics()

        order = Order(
            order_id=None,  # Missing
            client_order_id="test-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100.0,
            limit_price=None,
            status=OrderStatus.FILLED,
            filled_quantity=100.0,
            avg_fill_price=150.0,
        )

        with pytest.raises(ValueError, match="must have order_id"):
            metrics.calculate_metrics(order)

    def test_metrics_unfilled_order(self):
        """Test metrics for unfilled order (should have minimal data)."""
        metrics = ExecutionMetrics()

        order = Order(
            order_id="order-1",
            client_order_id="test-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100.0,
            limit_price=150.0,
            status=OrderStatus.SUBMITTED,
            filled_quantity=0.0,
            avg_fill_price=None,
            submitted_at=datetime.now(timezone.utc),
            filled_at=None,
        )

        quality = metrics.calculate_metrics(order)

        assert quality.fill_rate == 0.0
        assert quality.latency_ms is None
        assert quality.slippage is None
        assert quality.spread_paid is None
