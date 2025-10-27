"""Portfolio Allocation and Coordination (PAC) system.

Manages capital allocation across multiple trading strategies using
configurable allocation algorithms and dynamic rebalancing logic.
"""
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .performance_tracker import PerformanceTracker, StrategyPerformance
from .portfolio_metrics import PortfolioMetrics, PortfolioRiskCalculator, PortfolioRiskMetrics

logger = logging.getLogger(__name__)


class AllocationAlgorithm(Enum):
    """Capital allocation algorithm types."""

    EQUAL_WEIGHT = "equal_weight"
    VOLATILITY_WEIGHTED = "volatility_weighted"
    INVERSE_VOLATILITY = "inverse_volatility"
    SHARPE_OPTIMIZED = "sharpe_optimized"
    RISK_PARITY = "risk_parity"


@dataclass
class StrategyAllocation:
    """Capital allocation for a single strategy.

    Attributes:
        strategy_id: Strategy identifier
        allocated_capital: Capital allocated to strategy
        weight: Allocation weight (0-1)
        max_capacity: Maximum capital capacity for strategy
        timestamp: Allocation timestamp
    """

    strategy_id: str
    allocated_capital: float
    weight: float
    max_capacity: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class AllocationChange:
    """Record of an allocation change.

    Attributes:
        timestamp: Change timestamp
        algorithm: Algorithm used
        trigger: Reason for reallocation
        previous_allocations: Previous allocation state
        new_allocations: New allocation state
        total_capital: Total portfolio capital
    """

    timestamp: datetime
    algorithm: str
    trigger: str
    previous_allocations: Dict[str, float]
    new_allocations: Dict[str, float]
    total_capital: float


class PortfolioAllocator:
    """Portfolio Allocation and Coordination system.

    Manages capital allocation across strategies using configurable algorithms,
    monitors portfolio-level risk, and rebalances based on performance triggers.

    Example:
        >>> allocator = PortfolioAllocator(
        ...     total_capital=1000000,
        ...     algorithm=AllocationAlgorithm.SHARPE_OPTIMIZED
        ... )
        >>> allocator.register_strategy('pairs_1', max_capacity=100000)
        >>> allocator.record_strategy_return('pairs_1', 0.02)
        >>> allocations = allocator.get_allocations()
    """

    def __init__(
        self,
        total_capital: float,
        algorithm: AllocationAlgorithm = AllocationAlgorithm.EQUAL_WEIGHT,
        min_allocation_pct: float = 0.05,
        max_allocation_pct: float = 0.30,
        rebalance_threshold: float = 0.10,
        performance_lookback_days: int = 90,
        min_sharpe_threshold: float = 0.5,
        max_correlation_threshold: float = 0.7,
        audit_log_path: Optional[str] = None,
    ) -> None:
        """Initialize portfolio allocator.

        Args:
            total_capital: Total capital available for allocation
            algorithm: Allocation algorithm to use
            min_allocation_pct: Minimum allocation per strategy (as % of total)
            max_allocation_pct: Maximum allocation per strategy (as % of total)
            rebalance_threshold: Rebalance if weights drift by this amount
            performance_lookback_days: Days of history for allocation decisions
            min_sharpe_threshold: Minimum Sharpe for allocation
            max_correlation_threshold: Max correlation for diversification
            audit_log_path: Path to audit log file
        """
        self.total_capital = total_capital
        self.algorithm = algorithm
        self.min_allocation_pct = min_allocation_pct
        self.max_allocation_pct = max_allocation_pct
        self.rebalance_threshold = rebalance_threshold
        self.min_sharpe_threshold = min_sharpe_threshold
        self.max_correlation_threshold = max_correlation_threshold

        # Components
        self.performance_tracker = PerformanceTracker(
            lookback_days=performance_lookback_days
        )
        self.risk_calculator = PortfolioRiskCalculator()

        # Strategy registry
        self._strategy_allocations: Dict[str, StrategyAllocation] = {}
        self._strategy_max_capacity: Dict[str, float] = {}

        # Audit trail
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None
        self._allocation_history: List[AllocationChange] = []

        logger.info(
            f"Portfolio allocator initialized | "
            f"Capital: ${total_capital:,.0f}, "
            f"Algorithm: {algorithm.value}"
        )

    def register_strategy(
        self,
        strategy_id: str,
        max_capacity: Optional[float] = None,
    ) -> None:
        """Register a strategy for allocation.

        Args:
            strategy_id: Strategy identifier
            max_capacity: Maximum capital capacity (None = no limit)

        Raises:
            ValueError: If strategy already registered
        """
        if strategy_id in self._strategy_allocations:
            raise ValueError(f"Strategy {strategy_id} already registered")

        # Initialize with zero allocation
        self._strategy_allocations[strategy_id] = StrategyAllocation(
            strategy_id=strategy_id,
            allocated_capital=0.0,
            weight=0.0,
            max_capacity=max_capacity,
        )

        if max_capacity:
            self._strategy_max_capacity[strategy_id] = max_capacity

        logger.info(
            f"Strategy registered: {strategy_id}",
            extra={"context": {"strategy_id": strategy_id, "max_capacity": max_capacity}},
        )

    def unregister_strategy(self, strategy_id: str) -> None:
        """Unregister a strategy and reallocate its capital.

        Args:
            strategy_id: Strategy identifier
        """
        if strategy_id not in self._strategy_allocations:
            return

        # Free up allocated capital
        freed_capital = self._strategy_allocations[strategy_id].allocated_capital

        # Remove from registry
        del self._strategy_allocations[strategy_id]
        if strategy_id in self._strategy_max_capacity:
            del self._strategy_max_capacity[strategy_id]

        # Clear performance history
        self.performance_tracker.clear_strategy(strategy_id)

        logger.info(
            f"Strategy unregistered: {strategy_id} | "
            f"Freed capital: ${freed_capital:,.0f}"
        )

        # Trigger reallocation if we have other strategies
        if len(self._strategy_allocations) > 0:
            self.rebalance(trigger="strategy_unregistered")

    def record_strategy_return(
        self,
        strategy_id: str,
        daily_return: float,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Record a daily return for a strategy.

        Args:
            strategy_id: Strategy identifier
            daily_return: Daily return as decimal
            timestamp: Return timestamp (uses current time if None)
        """
        if strategy_id not in self._strategy_allocations:
            logger.warning(f"Strategy {strategy_id} not registered")
            return

        self.performance_tracker.record_return(strategy_id, daily_return, timestamp)

        # Check if rebalancing is needed
        self._check_rebalance_triggers()

    def rebalance(self, trigger: str = "manual") -> Dict[str, StrategyAllocation]:
        """Rebalance portfolio allocations.

        Args:
            trigger: Reason for rebalancing

        Returns:
            Dict mapping strategy_id to new StrategyAllocation
        """
        logger.info(f"Starting rebalance | Trigger: {trigger}")

        # Get current allocations
        previous_allocations = {
            sid: alloc.allocated_capital
            for sid, alloc in self._strategy_allocations.items()
        }

        # Calculate new allocations based on algorithm
        new_weights = self._calculate_allocation_weights()

        # Apply min/max constraints
        new_weights = self._apply_allocation_constraints(new_weights)

        # Convert weights to capital amounts
        new_allocations = self._weights_to_capital(new_weights)

        # Update allocations
        for strategy_id, capital in new_allocations.items():
            if strategy_id in self._strategy_allocations:
                weight = new_weights.get(strategy_id, 0.0)
                self._strategy_allocations[strategy_id].allocated_capital = capital
                self._strategy_allocations[strategy_id].weight = weight
                self._strategy_allocations[strategy_id].timestamp = datetime.now()

        # Record allocation change
        self._record_allocation_change(
            algorithm=self.algorithm.value,
            trigger=trigger,
            previous_allocations=previous_allocations,
            new_allocations=new_allocations,
        )

        logger.info(
            f"Rebalance completed | "
            f"Allocated: ${sum(new_allocations.values()):,.0f} / "
            f"${self.total_capital:,.0f}"
        )

        return self._strategy_allocations.copy()

    def _calculate_allocation_weights(self) -> Dict[str, float]:
        """Calculate allocation weights based on current algorithm.

        Returns:
            Dict mapping strategy_id to weight
        """
        strategy_ids = list(self._strategy_allocations.keys())

        if len(strategy_ids) == 0:
            return {}

        # Get performance data
        all_performance = self.performance_tracker.get_all_performance()

        # Filter strategies with sufficient history (but not by Sharpe yet)
        eligible_strategies = {
            sid: perf
            for sid, perf in all_performance.items()
            if len(perf.returns) >= 30  # Minimum history
        }

        if len(eligible_strategies) == 0:
            # Fall back to equal weight if no eligible strategies
            logger.warning("No eligible strategies with sufficient history, using equal weight")
            return self._equal_weight_allocation(strategy_ids)

        # Apply algorithm
        if self.algorithm == AllocationAlgorithm.EQUAL_WEIGHT:
            return self._equal_weight_allocation(list(eligible_strategies.keys()))

        elif self.algorithm == AllocationAlgorithm.VOLATILITY_WEIGHTED:
            return self._volatility_weighted_allocation(eligible_strategies)

        elif self.algorithm == AllocationAlgorithm.INVERSE_VOLATILITY:
            return self._inverse_volatility_allocation(eligible_strategies)

        elif self.algorithm == AllocationAlgorithm.SHARPE_OPTIMIZED:
            return self._sharpe_optimized_allocation(eligible_strategies)

        elif self.algorithm == AllocationAlgorithm.RISK_PARITY:
            return self._risk_parity_allocation(eligible_strategies)

        else:
            logger.warning(f"Unknown algorithm: {self.algorithm}, using equal weight")
            return self._equal_weight_allocation(list(eligible_strategies.keys()))

    def _equal_weight_allocation(self, strategy_ids: List[str]) -> Dict[str, float]:
        """Equal weight allocation.

        Args:
            strategy_ids: List of strategy identifiers

        Returns:
            Dict mapping strategy_id to weight
        """
        if len(strategy_ids) == 0:
            return {}

        weight = 1.0 / len(strategy_ids)
        return {sid: weight for sid in strategy_ids}

    def _volatility_weighted_allocation(
        self, strategies: Dict[str, StrategyPerformance]
    ) -> Dict[str, float]:
        """Allocate proportional to volatility (higher vol = higher allocation).

        Args:
            strategies: Dict of strategy performance

        Returns:
            Dict mapping strategy_id to weight
        """
        if len(strategies) == 0:
            return {}

        # Get volatilities
        volatilities = {sid: perf.volatility for sid, perf in strategies.items()}

        # Handle zero volatility
        total_vol = sum(volatilities.values())
        if total_vol < 1e-10:
            return self._equal_weight_allocation(list(strategies.keys()))

        # Calculate weights proportional to volatility
        weights = {sid: vol / total_vol for sid, vol in volatilities.items()}

        return weights

    def _inverse_volatility_allocation(
        self, strategies: Dict[str, StrategyPerformance]
    ) -> Dict[str, float]:
        """Allocate inversely proportional to volatility (lower vol = higher allocation).

        Args:
            strategies: Dict of strategy performance

        Returns:
            Dict mapping strategy_id to weight
        """
        if len(strategies) == 0:
            return {}

        # Get inverse volatilities
        inv_volatilities = {}
        for sid, perf in strategies.items():
            if perf.volatility > 1e-10:
                inv_volatilities[sid] = 1.0 / perf.volatility
            else:
                inv_volatilities[sid] = 1e10  # Very high weight for zero volatility

        # Normalize
        total_inv_vol = sum(inv_volatilities.values())
        weights = {sid: inv_vol / total_inv_vol for sid, inv_vol in inv_volatilities.items()}

        return weights

    def _sharpe_optimized_allocation(
        self, strategies: Dict[str, StrategyPerformance]
    ) -> Dict[str, float]:
        """Allocate proportional to Sharpe ratio.

        Args:
            strategies: Dict of strategy performance

        Returns:
            Dict mapping strategy_id to weight
        """
        if len(strategies) == 0:
            return {}

        # Get Sharpe ratios (only positive and above threshold)
        sharpe_ratios = {}
        for sid, perf in strategies.items():
            if perf.sharpe_ratio > self.min_sharpe_threshold:
                sharpe_ratios[sid] = perf.sharpe_ratio

        if len(sharpe_ratios) == 0:
            # Fall back to equal weight if no strategies meet Sharpe threshold
            return self._equal_weight_allocation(list(strategies.keys()))

        # Calculate weights proportional to Sharpe
        total_sharpe = sum(sharpe_ratios.values())
        weights = {sid: sharpe / total_sharpe for sid, sharpe in sharpe_ratios.items()}

        return weights

    def _risk_parity_allocation(
        self, strategies: Dict[str, StrategyPerformance]
    ) -> Dict[str, float]:
        """Risk parity allocation (equal risk contribution).

        Simplified version using inverse volatility as proxy.
        Full implementation would use optimization.

        Args:
            strategies: Dict of strategy performance

        Returns:
            Dict mapping strategy_id to weight
        """
        # Use inverse volatility as approximation for risk parity
        return self._inverse_volatility_allocation(strategies)

    def _apply_allocation_constraints(
        self, weights: Dict[str, float]
    ) -> Dict[str, float]:
        """Apply min/max allocation constraints.

        Args:
            weights: Unconstrained weights

        Returns:
            Constrained weights (may not sum to 1.0 if constrained)
        """
        if len(weights) == 0:
            return {}

        constrained_weights = {}

        for strategy_id, weight in weights.items():
            # Apply min/max constraints
            constrained_weight = max(
                self.min_allocation_pct,
                min(self.max_allocation_pct, weight)
            )
            constrained_weights[strategy_id] = constrained_weight

        # Only renormalize if total weight is < 1.0 (due to min constraints)
        # Don't renormalize if total weight is < 1.0 due to max constraints
        total_weight = sum(constrained_weights.values())

        # Check if we hit max constraints (total < sum of originals)
        original_total = sum(weights.values())
        hit_max_constraint = any(w > self.max_allocation_pct for w in weights.values())

        # Only renormalize if we didn't hit max constraints and total is > 0
        if not hit_max_constraint and total_weight > 1e-10 and total_weight < 0.99:
            # We hit min constraints, so renormalize
            constrained_weights = {
                sid: w / total_weight for sid, w in constrained_weights.items()
            }

        return constrained_weights

    def _weights_to_capital(self, weights: Dict[str, float]) -> Dict[str, float]:
        """Convert allocation weights to capital amounts.

        Respects max_capacity constraints.

        Args:
            weights: Allocation weights

        Returns:
            Dict mapping strategy_id to capital amount
        """
        capital_allocations = {}

        for strategy_id, weight in weights.items():
            capital = weight * self.total_capital

            # Apply max capacity constraint
            if strategy_id in self._strategy_max_capacity:
                max_cap = self._strategy_max_capacity[strategy_id]
                capital = min(capital, max_cap)

            capital_allocations[strategy_id] = capital

        return capital_allocations

    def _check_rebalance_triggers(self) -> None:
        """Check if rebalancing is needed based on triggers."""
        # Get current performance
        all_performance = self.performance_tracker.get_all_performance()

        if len(all_performance) < 2:
            return

        # Check for performance-based triggers
        for strategy_id, perf in all_performance.items():
            # Trigger: Poor Sharpe ratio
            if perf.sharpe_ratio < self.min_sharpe_threshold:
                logger.info(
                    f"Rebalance trigger: Low Sharpe for {strategy_id} "
                    f"({perf.sharpe_ratio:.2f})"
                )
                self.rebalance(trigger=f"low_sharpe_{strategy_id}")
                return

            # Trigger: Large drawdown
            if perf.current_drawdown > 0.15:  # 15% drawdown
                logger.info(
                    f"Rebalance trigger: Large drawdown for {strategy_id} "
                    f"({perf.current_drawdown:.2%})"
                )
                self.rebalance(trigger=f"drawdown_{strategy_id}")
                return

        # Trigger: High correlation
        corr_matrix = self.performance_tracker.get_correlation_matrix()
        if not corr_matrix.empty:
            avg_corr = (corr_matrix.sum().sum() - len(corr_matrix)) / (
                len(corr_matrix) * (len(corr_matrix) - 1)
            )
            if avg_corr > self.max_correlation_threshold:
                logger.info(
                    f"Rebalance trigger: High correlation ({avg_corr:.2f})"
                )
                self.rebalance(trigger="high_correlation")
                return

    def _record_allocation_change(
        self,
        algorithm: str,
        trigger: str,
        previous_allocations: Dict[str, float],
        new_allocations: Dict[str, float],
    ) -> None:
        """Record allocation change to audit trail.

        Args:
            algorithm: Algorithm used
            trigger: Reason for change
            previous_allocations: Previous allocation state
            new_allocations: New allocation state
        """
        change = AllocationChange(
            timestamp=datetime.now(),
            algorithm=algorithm,
            trigger=trigger,
            previous_allocations=previous_allocations,
            new_allocations=new_allocations,
            total_capital=self.total_capital,
        )

        self._allocation_history.append(change)

        # Write to audit log if configured
        if self.audit_log_path:
            try:
                self._write_audit_log(change)
            except Exception as e:
                logger.error(f"Failed to write audit log: {e}")

        logger.info(
            f"Allocation change recorded | "
            f"Algorithm: {algorithm}, Trigger: {trigger}"
        )

    def _write_audit_log(self, change: AllocationChange) -> None:
        """Write allocation change to audit log file.

        Args:
            change: AllocationChange to log
        """
        if not self.audit_log_path:
            return

        # Ensure directory exists
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)

        # Append to log file
        with open(self.audit_log_path, "a") as f:
            log_entry = {
                "timestamp": change.timestamp.isoformat(),
                "algorithm": change.algorithm,
                "trigger": change.trigger,
                "previous_allocations": change.previous_allocations,
                "new_allocations": change.new_allocations,
                "total_capital": change.total_capital,
            }
            f.write(json.dumps(log_entry) + "\n")

    def get_allocations(self) -> Dict[str, StrategyAllocation]:
        """Get current strategy allocations.

        Returns:
            Dict mapping strategy_id to StrategyAllocation
        """
        return self._strategy_allocations.copy()

    def get_allocation(self, strategy_id: str) -> Optional[StrategyAllocation]:
        """Get allocation for a specific strategy.

        Args:
            strategy_id: Strategy identifier

        Returns:
            StrategyAllocation or None if not found
        """
        return self._strategy_allocations.get(strategy_id)

    def get_portfolio_metrics(self) -> PortfolioMetrics:
        """Get comprehensive portfolio metrics.

        Returns:
            PortfolioMetrics object
        """
        # Calculate allocated capital
        allocated_capital = sum(
            alloc.allocated_capital for alloc in self._strategy_allocations.values()
        )
        unallocated_capital = self.total_capital - allocated_capital

        # Get strategy returns
        all_performance = self.performance_tracker.get_all_performance()
        strategy_returns = {
            sid: perf.returns
            for sid, perf in all_performance.items()
            if len(perf.returns) >= 30
        }

        # Get allocation weights
        allocation_weights = {
            sid: alloc.weight for sid, alloc in self._strategy_allocations.items()
        }

        # Calculate risk metrics
        if len(strategy_returns) > 0:
            risk_metrics = self.risk_calculator.calculate_portfolio_metrics(
                strategy_returns, allocation_weights
            )
            correlation_matrix = self.performance_tracker.get_correlation_matrix()
        else:
            risk_metrics = PortfolioRiskMetrics()
            correlation_matrix = pd.DataFrame()

        return PortfolioMetrics(
            total_value=self.total_capital,
            allocated_capital=allocated_capital,
            unallocated_capital=unallocated_capital,
            strategy_count=len(self._strategy_allocations),
            risk_metrics=risk_metrics,
            correlation_matrix=correlation_matrix,
            allocation_weights=allocation_weights,
        )

    def get_allocation_history(self) -> List[AllocationChange]:
        """Get allocation change history.

        Returns:
            List of AllocationChange objects
        """
        return self._allocation_history.copy()

    def update_total_capital(self, new_capital: float) -> None:
        """Update total portfolio capital.

        Args:
            new_capital: New total capital
        """
        old_capital = self.total_capital
        self.total_capital = new_capital

        logger.info(
            f"Portfolio capital updated: ${old_capital:,.0f} -> ${new_capital:,.0f}"
        )

        # Trigger reallocation
        self.rebalance(trigger="capital_update")

    def set_algorithm(self, algorithm: AllocationAlgorithm) -> None:
        """Change allocation algorithm.

        Args:
            algorithm: New allocation algorithm
        """
        old_algorithm = self.algorithm
        self.algorithm = algorithm

        logger.info(
            f"Allocation algorithm changed: {old_algorithm.value} -> {algorithm.value}"
        )

        # Trigger reallocation
        self.rebalance(trigger="algorithm_change")
