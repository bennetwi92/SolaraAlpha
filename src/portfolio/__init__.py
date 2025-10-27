"""Portfolio management and capital allocation for SolaraAlpha.

This package provides the Portfolio Allocation and Coordination (PAC) system
that manages capital allocation across multiple trading strategies.
"""

from .pac import (
    AllocationAlgorithm,
    AllocationChange,
    PortfolioAllocator,
    StrategyAllocation,
    StrategyPerformance,
)
from .performance_tracker import PerformanceTracker
from .portfolio_metrics import PortfolioMetrics, PortfolioRiskMetrics

__all__ = [
    "AllocationAlgorithm",
    "AllocationChange",
    "PortfolioAllocator",
    "StrategyAllocation",
    "StrategyPerformance",
    "PerformanceTracker",
    "PortfolioMetrics",
    "PortfolioRiskMetrics",
]
