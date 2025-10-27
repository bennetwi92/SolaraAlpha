"""Execution quality metrics calculation."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from src.execution.clients.base import Order, OrderStatus, OrderType


@dataclass
class ExecutionQuality:
    """Execution quality metrics for a single order.

    Attributes:
        order_id: Order identifier
        symbol: Instrument symbol
        slippage: Fill price vs. market price at submission (basis points)
        latency_ms: Order submission to fill time in milliseconds
        fill_rate: Percentage of order filled (0.0 to 1.0)
        spread_paid: Spread paid for limit orders (basis points)
    """

    order_id: str
    symbol: str
    slippage: Optional[float] = None
    latency_ms: Optional[float] = None
    fill_rate: float = 0.0
    spread_paid: Optional[float] = None


class ExecutionMetrics:
    """Calculates and tracks execution quality metrics.

    Provides analytics on execution quality including slippage,
    latency, fill rates, and spread costs.
    """

    def __init__(self):
        """Initialize metrics tracker."""
        self._metrics: dict[str, ExecutionQuality] = {}  # order_id -> metrics

    def calculate_metrics(
        self,
        order: Order,
        market_price_at_submission: Optional[float] = None,
    ) -> ExecutionQuality:
        """Calculate execution metrics for an order.

        Args:
            order: Filled or partially filled order
            market_price_at_submission: Market price when order was submitted
                (required for slippage calculation)

        Returns:
            ExecutionQuality metrics
        """
        if not order.order_id:
            raise ValueError("Order must have order_id to calculate metrics")

        metrics = ExecutionQuality(
            order_id=order.order_id,
            symbol=order.symbol,
        )

        # Fill rate
        if order.quantity > 0:
            metrics.fill_rate = order.filled_quantity / order.quantity

        # Latency (only if fully or partially filled)
        if order.submitted_at and order.filled_at and order.filled_quantity > 0:
            latency = order.filled_at - order.submitted_at
            metrics.latency_ms = latency.total_seconds() * 1000

        # Slippage (requires market price at submission)
        if (
            market_price_at_submission
            and order.avg_fill_price
            and order.filled_quantity > 0
        ):
            # Slippage in basis points (1 bp = 0.01%)
            price_diff = order.avg_fill_price - market_price_at_submission
            metrics.slippage = (price_diff / market_price_at_submission) * 10000

            # Adjust sign based on order side
            # Positive slippage = worse execution
            if order.side.value == "sell":
                metrics.slippage = -metrics.slippage

        # Spread paid for limit orders
        if (
            order.order_type == OrderType.LIMIT
            and order.limit_price
            and order.avg_fill_price
            and order.filled_quantity > 0
        ):
            # Spread = difference between limit price and fill price
            spread = abs(order.avg_fill_price - order.limit_price)
            metrics.spread_paid = (spread / order.limit_price) * 10000

        # Store metrics
        self._metrics[order.order_id] = metrics

        return metrics

    def get_metrics(self, order_id: str) -> Optional[ExecutionQuality]:
        """Get metrics for a specific order.

        Args:
            order_id: Order identifier

        Returns:
            ExecutionQuality if metrics exist, None otherwise
        """
        return self._metrics.get(order_id)

    def get_all_metrics(self) -> list[ExecutionQuality]:
        """Get all calculated metrics.

        Returns:
            List of all ExecutionQuality metrics
        """
        return list(self._metrics.values())

    def get_summary_stats(
        self,
        symbol: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> dict:
        """Calculate summary statistics for execution quality.

        Args:
            symbol: Optional symbol filter
            start_time: Optional start time filter
            end_time: Optional end time filter

        Returns:
            Dictionary with summary statistics:
                - avg_slippage: Average slippage in basis points
                - avg_latency_ms: Average execution latency
                - total_orders: Total orders
                - avg_fill_rate: Average fill rate
                - avg_spread_paid: Average spread for limit orders
        """
        metrics = self._metrics.values()

        # Apply filters (simplified - would need order timestamps)
        if symbol:
            metrics = [m for m in metrics if m.symbol == symbol]

        if not metrics:
            return {
                "avg_slippage": 0.0,
                "avg_latency_ms": 0.0,
                "total_orders": 0,
                "avg_fill_rate": 0.0,
                "avg_spread_paid": 0.0,
            }

        # Calculate averages
        slippages = [m.slippage for m in metrics if m.slippage is not None]
        latencies = [m.latency_ms for m in metrics if m.latency_ms is not None]
        fill_rates = [m.fill_rate for m in metrics]
        spreads = [m.spread_paid for m in metrics if m.spread_paid is not None]

        return {
            "avg_slippage": sum(slippages) / len(slippages) if slippages else 0.0,
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
            "total_orders": len(metrics),
            "avg_fill_rate": sum(fill_rates) / len(fill_rates) if fill_rates else 0.0,
            "avg_spread_paid": sum(spreads) / len(spreads) if spreads else 0.0,
        }

    def generate_daily_report(self, date: Optional[datetime] = None) -> str:
        """Generate daily execution quality report.

        Args:
            date: Date for report (defaults to today)

        Returns:
            Formatted report string
        """
        if not date:
            date = datetime.utcnow()

        stats = self.get_summary_stats()

        report = f"""
Execution Quality Report - {date.strftime('%Y-%m-%d')}
{'=' * 60}

Total Orders: {stats['total_orders']}
Average Fill Rate: {stats['avg_fill_rate']:.2%}
Average Slippage: {stats['avg_slippage']:.2f} bps
Average Latency: {stats['avg_latency_ms']:.1f} ms
Average Spread Paid: {stats['avg_spread_paid']:.2f} bps

{'=' * 60}
"""
        return report.strip()
