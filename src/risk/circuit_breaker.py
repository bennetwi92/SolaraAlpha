"""Circuit breaker for automatic trading halt on drawdown threshold.

Monitors portfolio value against high-water mark and triggers automatic halt
when maximum drawdown threshold is breached. Implements IRM override authority.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from .config.risk_config import RiskConfigManager

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreakerState:
    """Circuit breaker state snapshot.

    Attributes:
        is_active: Whether circuit breaker is currently triggered.
        high_water_mark: Highest portfolio value achieved.
        current_value: Current portfolio value.
        drawdown_pct: Current drawdown percentage from HWM.
        trigger_timestamp: When circuit breaker was triggered (None if not).
        trigger_reason: Human-readable reason for trigger.
    """

    is_active: bool
    high_water_mark: float
    current_value: float
    drawdown_pct: float
    trigger_timestamp: Optional[datetime] = None
    trigger_reason: str = ""


class CircuitBreaker:
    """Automatic trading halt on maximum drawdown breach.

    Monitors portfolio value against high-water mark. When drawdown exceeds
    configured threshold, triggers circuit breaker to halt all trading.
    Persists HWM across restarts for continuous protection.

    Per fund_structure_ceo_insights.md: IRM has override authority with
    non-negotiable power to halt strategies and manage risk escalation.

    Example:
        >>> breaker = CircuitBreaker(config, initial_value=100000)
        >>> breaker.check_drawdown(current_value=89000)
        >>> if breaker.is_active:
        ...     print("CIRCUIT BREAKER ACTIVE - HALT ALL TRADING")
        ...     print(breaker.get_state().trigger_reason)
    """

    def __init__(
        self,
        config: RiskConfigManager,
        initial_value: Optional[float] = None,
    ) -> None:
        """Initialize circuit breaker.

        Args:
            config: Risk configuration manager.
            initial_value: Initial portfolio value. If None and HWM persisted,
                           loads from storage. Otherwise required.
        """
        self.config = config
        self.max_drawdown_pct = config.get_max_drawdown_pct()
        self.persist_enabled = config.get_hwm_persist_enabled()
        self.hwm_path = config.get_hwm_storage_path()

        # Circuit breaker state
        self._is_active = False
        self._trigger_timestamp: Optional[datetime] = None
        self._trigger_reason = ""

        # Load or initialize high-water mark
        if self.persist_enabled and self.hwm_path.exists():
            self._load_hwm()
        elif initial_value is not None:
            self.high_water_mark = initial_value
            self._save_hwm()
            logger.info(
                f"Initialized high-water mark: ${initial_value:,.2f}"
            )
        else:
            raise ValueError(
                "initial_value required when HWM not persisted or file missing"
            )

        self.current_value = self.high_water_mark

        logger.info(
            f"Circuit breaker initialized | "
            f"HWM: ${self.high_water_mark:,.2f}, "
            f"Max DD: {self.max_drawdown_pct}%"
        )

    def check_drawdown(self, current_value: float) -> bool:
        """Check if current portfolio value breaches drawdown threshold.

        Updates HWM if current value is higher. Triggers circuit breaker
        if drawdown exceeds threshold.

        Args:
            current_value: Current total portfolio value.

        Returns:
            True if circuit breaker triggered, False otherwise.
        """
        self.current_value = current_value

        # Update HWM if new high achieved
        if current_value > self.high_water_mark:
            old_hwm = self.high_water_mark
            self.high_water_mark = current_value
            self._save_hwm()
            logger.info(
                f"New high-water mark: ${current_value:,.2f} "
                f"(prev: ${old_hwm:,.2f})"
            )

        # Calculate current drawdown
        drawdown_pct = self._calculate_drawdown_pct(current_value)

        # Check if threshold breached
        if drawdown_pct >= self.max_drawdown_pct and not self._is_active:
            self._trigger(
                f"Maximum drawdown exceeded: {drawdown_pct:.2f}% >= "
                f"{self.max_drawdown_pct}% (HWM: ${self.high_water_mark:,.2f}, "
                f"Current: ${current_value:,.2f})"
            )
            return True

        return False

    def _trigger(self, reason: str) -> None:
        """Trigger circuit breaker with reason."""
        self._is_active = True
        self._trigger_timestamp = datetime.now()
        self._trigger_reason = reason

        # Alert logging
        if self.config.get_alert_on_circuit_breaker():
            logger.critical(
                f"🚨 CIRCUIT BREAKER TRIGGERED 🚨 | {reason}"
            )
            logger.critical(
                "ALL TRADING HALTED - Flatten positions and alert IRM"
            )

    def reset(self, justification: str = "") -> None:
        """Reset circuit breaker (requires IRM authority).

        Args:
            justification: Required justification for reset (logged).

        Raises:
            ValueError: If justification required but not provided.
        """
        if self.config.config.get("irm", {}).get("require_justification", True):
            if not justification:
                raise ValueError(
                    "Justification required for circuit breaker reset "
                    "(IRM authority)"
                )

        logger.warning(
            f"Circuit breaker reset by IRM | Justification: {justification}"
        )

        self._is_active = False
        self._trigger_timestamp = None
        self._trigger_reason = ""

    def manual_trigger(self, reason: str) -> None:
        """Manually trigger circuit breaker (IRM emergency kill switch).

        Args:
            reason: Reason for manual trigger (logged).
        """
        logger.critical(
            f"🚨 MANUAL CIRCUIT BREAKER TRIGGER (IRM) 🚨 | {reason}"
        )
        self._trigger(f"Manual trigger by IRM: {reason}")

    def _calculate_drawdown_pct(self, current_value: float) -> float:
        """Calculate drawdown percentage from high-water mark.

        Args:
            current_value: Current portfolio value.

        Returns:
            Drawdown as percentage (0-100).
        """
        if self.high_water_mark == 0:
            return 0.0

        drawdown = (self.high_water_mark - current_value) / self.high_water_mark
        return drawdown * 100

    def get_state(self) -> CircuitBreakerState:
        """Get current circuit breaker state.

        Returns:
            CircuitBreakerState with current status and metrics.
        """
        return CircuitBreakerState(
            is_active=self._is_active,
            high_water_mark=self.high_water_mark,
            current_value=self.current_value,
            drawdown_pct=self._calculate_drawdown_pct(self.current_value),
            trigger_timestamp=self._trigger_timestamp,
            trigger_reason=self._trigger_reason,
        )

    @property
    def is_active(self) -> bool:
        """Check if circuit breaker is currently active."""
        return self._is_active

    @property
    def current_drawdown_pct(self) -> float:
        """Get current drawdown percentage."""
        return self._calculate_drawdown_pct(self.current_value)

    def _save_hwm(self) -> None:
        """Persist high-water mark to storage."""
        if not self.persist_enabled:
            return

        # Ensure directory exists
        self.hwm_path.parent.mkdir(parents=True, exist_ok=True)

        hwm_data = {
            "high_water_mark": self.high_water_mark,
            "last_updated": datetime.now().isoformat(),
        }

        with open(self.hwm_path, "w") as f:
            json.dump(hwm_data, f, indent=2)

        logger.debug(f"Persisted HWM: ${self.high_water_mark:,.2f}")

    def _load_hwm(self) -> None:
        """Load high-water mark from storage."""
        with open(self.hwm_path, "r") as f:
            hwm_data = json.load(f)

        self.high_water_mark = hwm_data["high_water_mark"]
        last_updated = hwm_data.get("last_updated", "unknown")

        logger.info(
            f"Loaded persisted HWM: ${self.high_water_mark:,.2f} "
            f"(last updated: {last_updated})"
        )

    def get_distance_to_trigger(self) -> Dict[str, float]:
        """Calculate distance to circuit breaker trigger.

        Returns:
            Dictionary with:
                - trigger_value: Portfolio value that would trigger breaker
                - distance_dollars: Dollar distance to trigger
                - distance_pct: Percentage distance to trigger
                - buffer_pct: Remaining drawdown buffer before trigger
        """
        trigger_value = self.high_water_mark * (
            1 - self.max_drawdown_pct / 100
        )
        distance_dollars = self.current_value - trigger_value
        distance_pct = (distance_dollars / self.current_value) * 100
        buffer_pct = self.max_drawdown_pct - self.current_drawdown_pct

        return {
            "trigger_value": trigger_value,
            "distance_dollars": distance_dollars,
            "distance_pct": distance_pct,
            "buffer_pct": max(0, buffer_pct),
        }
