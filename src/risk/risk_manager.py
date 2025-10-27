"""Risk manager orchestration layer for SolaraAlpha.

Unified interface for the complete risk management framework. Coordinates
position tracking, limit enforcement, circuit breakers, and metrics calculation.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from ..data.storage.parquet_storage import ParquetStorage
from .circuit_breaker import CircuitBreaker, CircuitBreakerState
from .config.risk_config import RiskConfigManager
from .limit_enforcer import LimitCheckResult, LimitEnforcer
from .metrics_calculator import RiskMetrics, RiskMetricsCalculator
from .position_tracker import Position, PositionTracker

logger = logging.getLogger(__name__)


class RiskManager:
    """Orchestrates complete risk management framework.

    Provides unified interface for:
    - Real-time position tracking
    - Pre-trade limit validation
    - Circuit breaker monitoring
    - Risk metrics calculation
    - Portfolio exposure reporting

    This is the primary interface for strategies and orchestrator to interact
    with the risk management system.

    Example:
        >>> risk_mgr = RiskManager(portfolio_value=100000)
        >>>
        >>> # Check if trade is allowed
        >>> result = risk_mgr.validate_trade(
        ...     symbol='AAPL',
        ...     asset_class='equities',
        ...     strategy_id='pairs_1',
        ...     quantity=100,
        ...     price=150.0
        ... )
        >>> if result.passed:
        ...     risk_mgr.record_trade(...)
        >>>
        >>> # Check circuit breaker
        >>> if risk_mgr.check_circuit_breaker():
        ...     print("HALT ALL TRADING")
        >>>
        >>> # Get risk metrics
        >>> metrics = risk_mgr.calculate_risk_metrics()
        >>> print(f"VaR: ${metrics.var_95:,.2f}")
    """

    def __init__(
        self,
        portfolio_value: float,
        config_path: Optional[str] = None,
    ) -> None:
        """Initialize risk manager.

        Args:
            portfolio_value: Initial total portfolio value.
            config_path: Path to risk_limits.yaml (optional).
        """
        # Load configuration
        self.config = RiskConfigManager(config_path)

        # Initialize components
        self.position_tracker = PositionTracker()
        self.limit_enforcer = LimitEnforcer(
            self.position_tracker, self.config, portfolio_value
        )
        self.circuit_breaker = CircuitBreaker(
            self.config, initial_value=portfolio_value
        )

        # Data storage for metrics calculation
        self.storage = ParquetStorage()
        self.metrics_calculator = RiskMetricsCalculator(
            self.config, self.storage
        )

        self.portfolio_value = portfolio_value

        logger.info(
            f"Risk manager initialized | "
            f"Portfolio: ${portfolio_value:,.2f}, "
            f"Max DD: {self.config.get_max_drawdown_pct()}%"
        )

    # ==================================================================
    # Pre-Trade Validation
    # ==================================================================

    def validate_trade(
        self,
        symbol: str,
        asset_class: str,
        strategy_id: str,
        quantity: float,
        price: float,
    ) -> LimitCheckResult:
        """Validate proposed trade against all risk limits.

        MUST be called before executing any trade. Returns detailed result
        indicating whether trade is allowed or which limit would be violated.

        Args:
            symbol: Ticker symbol.
            asset_class: Asset class.
            strategy_id: Strategy identifier.
            quantity: Proposed trade quantity (positive=buy, negative=sell).
            price: Trade price per unit.

        Returns:
            LimitCheckResult indicating pass/fail and violation details.
        """
        # Check circuit breaker first
        if self.circuit_breaker.is_active:
            return LimitCheckResult(
                passed=False,
                message=(
                    "Circuit breaker active - ALL TRADING HALTED | "
                    f"{self.circuit_breaker.get_state().trigger_reason}"
                ),
            )

        # Check position limits
        return self.limit_enforcer.check_trade(
            symbol, asset_class, strategy_id, quantity, price
        )

    # ==================================================================
    # Position Management
    # ==================================================================

    def record_trade(
        self,
        symbol: str,
        asset_class: str,
        strategy_id: str,
        quantity: float,
        entry_price: float,
        current_price: float,
    ) -> Optional[Position]:
        """Record executed trade and update position tracking.

        Should be called AFTER trade execution to maintain accurate state.

        Args:
            symbol: Ticker symbol.
            asset_class: Asset class.
            strategy_id: Strategy identifier.
            quantity: Executed quantity (positive=buy, negative=sell).
            entry_price: Execution price per unit.
            current_price: Current market price per unit.

        Returns:
            Updated Position object or None if position closed.
        """
        position = self.position_tracker.add_position(
            symbol, asset_class, strategy_id, quantity, entry_price, current_price
        )

        # Update portfolio value after trade
        self._update_portfolio_value()

        return position

    def update_prices(self, price_updates: Dict[str, float]) -> None:
        """Update current prices for multiple symbols.

        Args:
            price_updates: Dictionary mapping symbol to current price.
        """
        for symbol, price in price_updates.items():
            self.position_tracker.update_price(symbol, price)

        # Update portfolio value after price changes
        self._update_portfolio_value()

    def get_position(
        self, symbol: str, strategy_id: str
    ) -> Optional[Position]:
        """Get current position for symbol and strategy.

        Args:
            symbol: Ticker symbol.
            strategy_id: Strategy identifier.

        Returns:
            Position object or None if no position.
        """
        return self.position_tracker.get_position(symbol, strategy_id)

    def get_all_positions(self) -> List[Position]:
        """Get all active positions across all strategies.

        Returns:
            List of Position objects.
        """
        return self.position_tracker.get_all_positions()

    # ==================================================================
    # Circuit Breaker
    # ==================================================================

    def check_circuit_breaker(self) -> bool:
        """Check and update circuit breaker based on current portfolio value.

        Should be called regularly (e.g., after price updates) to monitor
        drawdown and trigger halt if threshold breached.

        Returns:
            True if circuit breaker triggered, False otherwise.
        """
        current_value = self._calculate_portfolio_value()
        triggered = self.circuit_breaker.check_drawdown(current_value)

        if triggered:
            logger.critical(
                "🚨 RISK MANAGER: CIRCUIT BREAKER ACTIVE 🚨"
            )

        return triggered

    def get_circuit_breaker_state(self) -> CircuitBreakerState:
        """Get current circuit breaker state and drawdown metrics.

        Returns:
            CircuitBreakerState with current status.
        """
        return self.circuit_breaker.get_state()

    def manual_circuit_breaker_trigger(self, reason: str) -> None:
        """Manually trigger circuit breaker (IRM emergency authority).

        Args:
            reason: Justification for manual trigger (logged).
        """
        self.circuit_breaker.manual_trigger(reason)
        logger.critical(
            "Risk manager: Manual circuit breaker trigger executed"
        )

    def reset_circuit_breaker(self, justification: str) -> None:
        """Reset circuit breaker after halt (requires IRM authority).

        Args:
            justification: Required justification (logged).

        Raises:
            ValueError: If justification missing but required.
        """
        self.circuit_breaker.reset(justification)
        logger.warning("Risk manager: Circuit breaker reset")

    # ==================================================================
    # Risk Metrics
    # ==================================================================

    def calculate_risk_metrics(
        self, lookback_days: Optional[int] = None
    ) -> Optional[RiskMetrics]:
        """Calculate current risk metrics from portfolio returns.

        Args:
            lookback_days: Historical window for calculation (uses config default if None).

        Returns:
            RiskMetrics object or None if insufficient data.
        """
        try:
            # Calculate portfolio returns from current positions
            positions = self.position_tracker.get_all_positions()

            if not positions:
                logger.warning(
                    "No positions for risk metrics calculation"
                )
                return None

            # Get historical returns
            end_date = datetime.now()
            days_back = lookback_days or self.config.get_var_lookback_days()
            start_date = end_date - pd.Timedelta(days=days_back)

            portfolio_returns = (
                self.metrics_calculator.portfolio_returns_from_positions(
                    positions, start_date, end_date
                )
            )

            if len(portfolio_returns) < self.config.get_min_history_days():
                logger.warning(
                    f"Insufficient data for metrics: {len(portfolio_returns)} days"
                )
                return None

            # Calculate metrics
            metrics = self.metrics_calculator.calculate_metrics(
                portfolio_returns
            )

            return metrics

        except Exception as e:
            logger.error(f"Risk metrics calculation failed: {e}")
            return None

    # ==================================================================
    # Exposure Reporting
    # ==================================================================

    def get_portfolio_exposure(self) -> Dict[str, float]:
        """Get current portfolio-level exposure metrics.

        Returns:
            Dictionary with gross, net, long, short exposure and P&L.
        """
        exposure = self.position_tracker.get_portfolio_exposure()
        exposure["portfolio_value"] = self.portfolio_value
        exposure["cash"] = (
            self.portfolio_value - exposure["gross"]
        )  # Simplified
        return exposure

    def get_strategy_exposure(self, strategy_id: str) -> Dict[str, float]:
        """Get exposure metrics for a specific strategy.

        Args:
            strategy_id: Strategy identifier.

        Returns:
            Dictionary with strategy exposure metrics.
        """
        return self.position_tracker.get_strategy_exposure(strategy_id)

    def get_symbol_exposure(self, symbol: str) -> Dict[str, float]:
        """Get exposure for a symbol across all strategies.

        Args:
            symbol: Ticker symbol.

        Returns:
            Dictionary with symbol exposure metrics.
        """
        return self.position_tracker.get_symbol_exposure(symbol)

    def get_available_capacity(
        self, strategy_id: str, symbol: str
    ) -> Dict[str, float]:
        """Get available trading capacity before hitting limits.

        Args:
            strategy_id: Strategy identifier.
            symbol: Ticker symbol.

        Returns:
            Dictionary with available capacity in dollars per limit type.
        """
        return self.limit_enforcer.get_available_capacity(
            strategy_id, symbol
        )

    # ==================================================================
    # Internal Helpers
    # ==================================================================

    def _calculate_portfolio_value(self) -> float:
        """Calculate current total portfolio value.

        Includes cash + position market values + unrealized P&L.
        """
        exposure = self.position_tracker.get_portfolio_exposure()

        # Simplified: portfolio value = initial value + total P&L
        # Production version would track cash flows, deposits, withdrawals
        current_value = self.portfolio_value + exposure["total_pnl"]

        return current_value

    def _update_portfolio_value(self) -> None:
        """Update portfolio value and propagate to components."""
        new_value = self._calculate_portfolio_value()

        # Update limit enforcer (for % calculations)
        self.limit_enforcer.update_portfolio_value(new_value)

        # Note: Don't update circuit breaker here - it tracks HWM separately
        # Circuit breaker update happens via check_circuit_breaker()

        self.portfolio_value = new_value

    # ==================================================================
    # System Status
    # ==================================================================

    def get_system_status(self) -> Dict:
        """Get comprehensive risk management system status.

        Returns:
            Dictionary with all key metrics and state.
        """
        exposure = self.get_portfolio_exposure()
        cb_state = self.get_circuit_breaker_state()

        status = {
            "timestamp": datetime.now().isoformat(),
            "portfolio_value": self.portfolio_value,
            "position_count": self.position_tracker.get_position_count(),
            "exposure": exposure,
            "circuit_breaker": {
                "is_active": cb_state.is_active,
                "high_water_mark": cb_state.high_water_mark,
                "current_drawdown_pct": cb_state.drawdown_pct,
                "trigger_reason": cb_state.trigger_reason,
            },
            "limits": {
                "per_strategy_pct": self.config.get_per_strategy_limit_pct(),
                "per_asset_pct": self.config.get_per_asset_limit_pct(),
                "max_drawdown_pct": self.config.get_max_drawdown_pct(),
            },
        }

        return status
