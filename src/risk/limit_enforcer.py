"""Position limit enforcement for SolaraAlpha risk management.

Validates proposed trades against position limits before execution to prevent
limit breaches. Enforces per-strategy, per-asset, and portfolio-level limits.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from .config.risk_config import RiskConfigManager
from .position_tracker import Position, PositionTracker

logger = logging.getLogger(__name__)


class LimitType(Enum):
    """Types of position limits."""

    PER_STRATEGY = "per_strategy"
    PER_ASSET = "per_asset"
    GROSS_EXPOSURE = "gross_exposure"
    NET_EXPOSURE = "net_exposure"


@dataclass
class LimitCheckResult:
    """Result of a limit check.

    Attributes:
        passed: Whether the trade passes all limit checks.
        violated_limit: Type of limit violated (None if passed).
        current_value: Current exposure value.
        limit_value: Limit threshold.
        proposed_value: Proposed exposure after trade.
        message: Human-readable explanation.
    """

    passed: bool
    violated_limit: Optional[LimitType] = None
    current_value: float = 0.0
    limit_value: float = 0.0
    proposed_value: float = 0.0
    message: str = ""


class LimitEnforcer:
    """Enforces position limits before trade execution.

    Validates proposed trades against configured limits:
    - Per-strategy position limit (% of portfolio)
    - Per-asset position limit (% of portfolio)
    - Gross exposure limit (long + short)
    - Net exposure limit (long - short)

    Example:
        >>> enforcer = LimitEnforcer(tracker, config, portfolio_value=100000)
        >>> result = enforcer.check_trade(
        ...     symbol='AAPL',
        ...     asset_class='equities',
        ...     strategy_id='pairs_1',
        ...     quantity=100,
        ...     price=150.0
        ... )
        >>> if not result.passed:
        ...     print(f"Trade rejected: {result.message}")
    """

    def __init__(
        self,
        position_tracker: PositionTracker,
        config: RiskConfigManager,
        portfolio_value: float,
    ) -> None:
        """Initialize limit enforcer.

        Args:
            position_tracker: Position tracker instance.
            config: Risk configuration manager.
            portfolio_value: Current total portfolio value for % calculations.
        """
        self.tracker = position_tracker
        self.config = config
        self.portfolio_value = portfolio_value

        # Load limits from config
        self.per_strategy_limit_pct = config.get_per_strategy_limit_pct()
        self.per_asset_limit_pct = config.get_per_asset_limit_pct()
        self.gross_exposure_limit_pct = config.get_gross_exposure_limit_pct()
        self.net_exposure_limit_pct = config.get_net_exposure_limit_pct()

        logger.info(
            f"Limit enforcer initialized with portfolio value: "
            f"${portfolio_value:,.2f}"
        )

    def update_portfolio_value(self, new_value: float) -> None:
        """Update portfolio value for limit calculations.

        Args:
            new_value: New total portfolio value.
        """
        self.portfolio_value = new_value
        logger.debug(f"Updated portfolio value: ${new_value:,.2f}")

    def check_trade(
        self,
        symbol: str,
        asset_class: str,
        strategy_id: str,
        quantity: float,
        price: float,
    ) -> LimitCheckResult:
        """Check if proposed trade violates any position limits.

        Args:
            symbol: Ticker symbol.
            asset_class: Asset class.
            strategy_id: Strategy identifier.
            quantity: Proposed trade quantity (positive=buy, negative=sell).
            price: Trade price per unit.

        Returns:
            LimitCheckResult indicating pass/fail and details.
        """
        # Check per-strategy limit
        strategy_result = self._check_strategy_limit(
            symbol, asset_class, strategy_id, quantity, price
        )
        if not strategy_result.passed:
            self._log_violation(strategy_result)
            return strategy_result

        # Check per-asset limit
        asset_result = self._check_asset_limit(
            symbol, asset_class, strategy_id, quantity, price
        )
        if not asset_result.passed:
            self._log_violation(asset_result)
            return asset_result

        # Check gross exposure limit
        gross_result = self._check_gross_exposure_limit(quantity, price)
        if not gross_result.passed:
            self._log_violation(gross_result)
            return gross_result

        # Check net exposure limit
        net_result = self._check_net_exposure_limit(quantity, price)
        if not net_result.passed:
            self._log_violation(net_result)
            return net_result

        # All checks passed
        return LimitCheckResult(
            passed=True, message="Trade passes all limit checks"
        )

    def _check_strategy_limit(
        self,
        symbol: str,
        asset_class: str,
        strategy_id: str,
        quantity: float,
        price: float,
    ) -> LimitCheckResult:
        """Check per-strategy position limit."""
        # Get current strategy exposure
        current_exposure = self.tracker.get_strategy_exposure(strategy_id)
        current_gross = current_exposure["gross"]

        # Check if we're reducing an existing position
        existing_position = self.tracker.get_position(symbol, strategy_id)
        if existing_position is not None:
            # If trade is opposite direction or reduces position, allow it
            same_direction = (existing_position.quantity > 0 and quantity > 0) or \
                           (existing_position.quantity < 0 and quantity < 0)
            if not same_direction or abs(quantity) < abs(existing_position.quantity):
                # Reducing exposure - always pass
                return LimitCheckResult(passed=True)

        # Calculate proposed exposure after trade
        trade_value = abs(quantity * price)
        proposed_gross = current_gross + trade_value

        # Calculate as % of portfolio
        current_pct = (current_gross / self.portfolio_value) * 100
        proposed_pct = (proposed_gross / self.portfolio_value) * 100
        limit_pct = self.per_strategy_limit_pct

        if proposed_pct > limit_pct:
            return LimitCheckResult(
                passed=False,
                violated_limit=LimitType.PER_STRATEGY,
                current_value=current_pct,
                limit_value=limit_pct,
                proposed_value=proposed_pct,
                message=(
                    f"Strategy {strategy_id} would exceed per-strategy limit: "
                    f"{proposed_pct:.2f}% > {limit_pct:.2f}%"
                ),
            )

        return LimitCheckResult(passed=True)

    def _check_asset_limit(
        self,
        symbol: str,
        asset_class: str,
        strategy_id: str,
        quantity: float,
        price: float,
    ) -> LimitCheckResult:
        """Check per-asset position limit across all strategies."""
        # Get current symbol exposure across all strategies
        current_exposure = self.tracker.get_symbol_exposure(symbol)
        current_market_value = abs(current_exposure["market_value"])
        current_net_quantity = current_exposure["quantity"]

        # Check if we're reducing overall symbol exposure
        proposed_net_quantity = current_net_quantity + quantity
        if abs(proposed_net_quantity) < abs(current_net_quantity):
            # Net exposure is decreasing - always pass
            return LimitCheckResult(passed=True)

        # Calculate proposed exposure after trade
        trade_value = abs(quantity * price)
        proposed_market_value = current_market_value + trade_value

        # Calculate as % of portfolio
        current_pct = (current_market_value / self.portfolio_value) * 100
        proposed_pct = (proposed_market_value / self.portfolio_value) * 100
        limit_pct = self.per_asset_limit_pct

        if proposed_pct > limit_pct:
            return LimitCheckResult(
                passed=False,
                violated_limit=LimitType.PER_ASSET,
                current_value=current_pct,
                limit_value=limit_pct,
                proposed_value=proposed_pct,
                message=(
                    f"Asset {symbol} would exceed per-asset limit: "
                    f"{proposed_pct:.2f}% > {limit_pct:.2f}%"
                ),
            )

        return LimitCheckResult(passed=True)

    def _check_gross_exposure_limit(
        self, quantity: float, price: float
    ) -> LimitCheckResult:
        """Check portfolio gross exposure limit."""
        # Get current portfolio exposure
        current_exposure = self.tracker.get_portfolio_exposure()
        current_gross = current_exposure["gross"]

        # Calculate proposed exposure
        trade_value = abs(quantity * price)
        proposed_gross = current_gross + trade_value

        # Calculate as % of portfolio
        current_pct = (current_gross / self.portfolio_value) * 100
        proposed_pct = (proposed_gross / self.portfolio_value) * 100
        limit_pct = self.gross_exposure_limit_pct

        if proposed_pct > limit_pct:
            return LimitCheckResult(
                passed=False,
                violated_limit=LimitType.GROSS_EXPOSURE,
                current_value=current_pct,
                limit_value=limit_pct,
                proposed_value=proposed_pct,
                message=(
                    f"Portfolio would exceed gross exposure limit: "
                    f"{proposed_pct:.2f}% > {limit_pct:.2f}%"
                ),
            )

        return LimitCheckResult(passed=True)

    def _check_net_exposure_limit(
        self, quantity: float, price: float
    ) -> LimitCheckResult:
        """Check portfolio net exposure limit (for market-neutral)."""
        # Get current portfolio exposure
        current_exposure = self.tracker.get_portfolio_exposure()
        current_net = current_exposure["net"]

        # Calculate proposed net exposure
        trade_value = quantity * price  # Signed value
        proposed_net = current_net + trade_value

        # Calculate as % of portfolio
        current_pct = (abs(current_net) / self.portfolio_value) * 100
        proposed_pct = (abs(proposed_net) / self.portfolio_value) * 100
        limit_pct = self.net_exposure_limit_pct

        if proposed_pct > limit_pct:
            return LimitCheckResult(
                passed=False,
                violated_limit=LimitType.NET_EXPOSURE,
                current_value=current_pct,
                limit_value=limit_pct,
                proposed_value=proposed_pct,
                message=(
                    f"Portfolio would exceed net exposure limit: "
                    f"{proposed_pct:.2f}% > {limit_pct:.2f}% "
                    f"(market-neutral constraint)"
                ),
            )

        return LimitCheckResult(passed=True)

    def _log_violation(self, result: LimitCheckResult) -> None:
        """Log limit violation with details."""
        if self.config.get_log_limit_breaches():
            logger.warning(
                f"LIMIT VIOLATION: {result.message} | "
                f"Current: {result.current_value:.2f}%, "
                f"Proposed: {result.proposed_value:.2f}%, "
                f"Limit: {result.limit_value:.2f}%"
            )

    def get_available_capacity(
        self, strategy_id: str, symbol: str
    ) -> Dict[str, float]:
        """Calculate available trading capacity for a strategy/symbol.

        Args:
            strategy_id: Strategy identifier.
            symbol: Ticker symbol.

        Returns:
            Dictionary with available capacity in dollars:
                - strategy_capacity: Available before hitting strategy limit
                - asset_capacity: Available before hitting asset limit
                - gross_capacity: Available before hitting gross limit
                - net_capacity: Available before hitting net limit
                - min_capacity: Minimum of all capacities (binding constraint)
        """
        # Strategy capacity
        strategy_exposure = self.tracker.get_strategy_exposure(strategy_id)
        strategy_limit_value = (
            self.portfolio_value * self.per_strategy_limit_pct / 100
        )
        strategy_capacity = strategy_limit_value - strategy_exposure["gross"]

        # Asset capacity
        symbol_exposure = self.tracker.get_symbol_exposure(symbol)
        asset_limit_value = (
            self.portfolio_value * self.per_asset_limit_pct / 100
        )
        asset_capacity = asset_limit_value - abs(
            symbol_exposure["market_value"]
        )

        # Gross capacity
        portfolio_exposure = self.tracker.get_portfolio_exposure()
        gross_limit_value = (
            self.portfolio_value * self.gross_exposure_limit_pct / 100
        )
        gross_capacity = gross_limit_value - portfolio_exposure["gross"]

        # Net capacity (symmetric around zero)
        net_limit_value = (
            self.portfolio_value * self.net_exposure_limit_pct / 100
        )
        current_net = abs(portfolio_exposure["net"])
        net_capacity = net_limit_value - current_net

        capacities = {
            "strategy_capacity": max(0, strategy_capacity),
            "asset_capacity": max(0, asset_capacity),
            "gross_capacity": max(0, gross_capacity),
            "net_capacity": max(0, net_capacity),
        }

        capacities["min_capacity"] = min(capacities.values())

        return capacities
