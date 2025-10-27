"""System health checking for SolaraAlpha components.

Validates connectivity and operational status across data pipeline, risk
management, execution layer, and system resources.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import psutil

from src.data.pipeline import DataPipeline
from src.risk.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """Health check status levels."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheckResult:
    """Result of a single health check.

    Attributes:
        component: Component name (e.g., 'data_pipeline', 'risk_manager')
        status: Health status (HEALTHY, DEGRADED, UNHEALTHY)
        message: Human-readable status message
        metrics: Optional dict of metrics (e.g., latency_ms, uptime_pct)
        timestamp: Check execution time
    """

    component: str
    status: HealthStatus
    message: str
    metrics: Optional[Dict[str, float]] = None
    timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        """Set timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict:
        """Convert to dictionary for logging/serialization."""
        return {
            "component": self.component,
            "status": self.status.value,
            "message": self.message,
            "metrics": self.metrics or {},
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class HealthChecker:
    """Validates system health across all components.

    Performs periodic health checks on:
    - Data pipeline connectivity (MCP servers, storage)
    - Risk management system (operational status)
    - Execution layer (broker connectivity)
    - System resources (CPU, memory, disk)

    Example:
        >>> checker = HealthChecker()
        >>> results = checker.check_all()
        >>> for result in results:
        ...     if result.status != HealthStatus.HEALTHY:
        ...         print(f"Issue in {result.component}: {result.message}")
    """

    def __init__(
        self,
        cpu_threshold: float = 80.0,
        memory_threshold: float = 85.0,
        disk_threshold: float = 90.0,
    ) -> None:
        """Initialize health checker.

        Args:
            cpu_threshold: CPU usage % threshold for DEGRADED status
            memory_threshold: Memory usage % threshold for DEGRADED status
            disk_threshold: Disk usage % threshold for DEGRADED status
        """
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.disk_threshold = disk_threshold

        # Track consecutive failures for alerting
        self._failure_counts: Dict[str, int] = {}

    def check_all(self) -> List[HealthCheckResult]:
        """Run all health checks.

        Returns:
            List of health check results for all components
        """
        results = []

        # Check each component
        results.append(self._check_data_pipeline())
        results.append(self._check_risk_management())
        results.append(self._check_execution_layer())
        results.append(self._check_system_resources())
        results.append(self._check_filesystem())

        # Log results
        for result in results:
            if result.status == HealthStatus.HEALTHY:
                logger.info(
                    f"Health check passed: {result.component}",
                    extra={"context": result.to_dict()},
                )
            elif result.status == HealthStatus.DEGRADED:
                logger.warning(
                    f"Health check degraded: {result.component}",
                    extra={"context": result.to_dict()},
                )
                self._increment_failure_count(result.component)
            else:
                logger.error(
                    f"Health check failed: {result.component}",
                    extra={"context": result.to_dict()},
                )
                self._increment_failure_count(result.component)

        # Reset failure count for healthy checks
        for result in results:
            if result.status == HealthStatus.HEALTHY:
                self._reset_failure_count(result.component)

        return results

    def get_failure_count(self, component: str) -> int:
        """Get consecutive failure count for component.

        Args:
            component: Component name

        Returns:
            Number of consecutive failures
        """
        return self._failure_counts.get(component, 0)

    def _check_data_pipeline(self) -> HealthCheckResult:
        """Check data pipeline connectivity and storage."""
        try:
            # Check if data directory exists and is writable
            data_dir = Path("data")
            if not data_dir.exists():
                return HealthCheckResult(
                    component="data_pipeline",
                    status=HealthStatus.UNHEALTHY,
                    message="Data directory does not exist",
                )

            # Try to instantiate pipeline (validates config)
            pipeline = DataPipeline()

            # Check storage directory access
            storage_dir = Path(pipeline.storage.base_path)
            if not storage_dir.exists():
                storage_dir.mkdir(parents=True, exist_ok=True)

            # Verify write access
            test_file = storage_dir / ".health_check"
            test_file.touch()
            test_file.unlink()

            return HealthCheckResult(
                component="data_pipeline",
                status=HealthStatus.HEALTHY,
                message="Data pipeline operational",
                metrics={"storage_accessible": 1.0},
            )

        except Exception as e:
            return HealthCheckResult(
                component="data_pipeline",
                status=HealthStatus.UNHEALTHY,
                message=f"Data pipeline check failed: {str(e)}",
                metrics={"storage_accessible": 0.0},
            )

    def _check_risk_management(self) -> HealthCheckResult:
        """Check risk management system operational status."""
        try:
            # Try to instantiate risk manager
            risk_mgr = RiskManager(portfolio_value=100000.0)

            # Verify core components initialized
            if not hasattr(risk_mgr, "position_tracker"):
                return HealthCheckResult(
                    component="risk_management",
                    status=HealthStatus.UNHEALTHY,
                    message="Risk manager missing position tracker",
                )

            if not hasattr(risk_mgr, "circuit_breaker"):
                return HealthCheckResult(
                    component="risk_management",
                    status=HealthStatus.UNHEALTHY,
                    message="Risk manager missing circuit breaker",
                )

            # Check circuit breaker state
            cb_state = risk_mgr.circuit_breaker.get_state()
            if cb_state.is_active:
                return HealthCheckResult(
                    component="risk_management",
                    status=HealthStatus.DEGRADED,
                    message=f"Circuit breaker is TRIPPED: {cb_state.trigger_reason}",
                    metrics={"circuit_breaker_active": 0.0},
                )

            return HealthCheckResult(
                component="risk_management",
                status=HealthStatus.HEALTHY,
                message="Risk management operational",
                metrics={"circuit_breaker_active": 1.0},
            )

        except Exception as e:
            return HealthCheckResult(
                component="risk_management",
                status=HealthStatus.UNHEALTHY,
                message=f"Risk management check failed: {str(e)}",
            )

    def _check_execution_layer(self) -> HealthCheckResult:
        """Check execution layer connectivity."""
        try:
            # Check if execution config exists
            from src.execution.config import load_alpaca_config

            config = load_alpaca_config()

            # For paper trading, verify API keys are set
            if not config.api_key or not config.secret_key:
                return HealthCheckResult(
                    component="execution_layer",
                    status=HealthStatus.UNHEALTHY,
                    message="Alpaca API credentials not configured",
                    metrics={"broker_connected": 0.0},
                )

            # Note: We don't test actual connection here to avoid rate limits
            # Connection tests should be done separately
            return HealthCheckResult(
                component="execution_layer",
                status=HealthStatus.HEALTHY,
                message="Execution layer configured",
                metrics={"broker_connected": 1.0},
            )

        except Exception as e:
            return HealthCheckResult(
                component="execution_layer",
                status=HealthStatus.DEGRADED,
                message=f"Execution layer check failed: {str(e)}",
                metrics={"broker_connected": 0.0},
            )

    def _check_system_resources(self) -> HealthCheckResult:
        """Check CPU and memory usage."""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            memory_percent = memory.percent

            metrics = {
                "cpu_percent": cpu_percent,
                "memory_percent": memory_percent,
                "memory_available_gb": memory.available / (1024**3),
            }

            if cpu_percent > self.cpu_threshold:
                return HealthCheckResult(
                    component="system_resources",
                    status=HealthStatus.DEGRADED,
                    message=f"CPU usage high: {cpu_percent:.1f}%",
                    metrics=metrics,
                )

            if memory_percent > self.memory_threshold:
                return HealthCheckResult(
                    component="system_resources",
                    status=HealthStatus.DEGRADED,
                    message=f"Memory usage high: {memory_percent:.1f}%",
                    metrics=metrics,
                )

            return HealthCheckResult(
                component="system_resources",
                status=HealthStatus.HEALTHY,
                message="System resources normal",
                metrics=metrics,
            )

        except Exception as e:
            return HealthCheckResult(
                component="system_resources",
                status=HealthStatus.UNHEALTHY,
                message=f"System resource check failed: {str(e)}",
            )

    def _check_filesystem(self) -> HealthCheckResult:
        """Check disk space availability."""
        try:
            disk = psutil.disk_usage("/")
            disk_percent = disk.percent

            metrics = {
                "disk_percent": disk_percent,
                "disk_free_gb": disk.free / (1024**3),
                "disk_total_gb": disk.total / (1024**3),
            }

            if disk_percent > self.disk_threshold:
                return HealthCheckResult(
                    component="filesystem",
                    status=HealthStatus.DEGRADED,
                    message=f"Disk usage high: {disk_percent:.1f}%",
                    metrics=metrics,
                )

            return HealthCheckResult(
                component="filesystem",
                status=HealthStatus.HEALTHY,
                message="Filesystem healthy",
                metrics=metrics,
            )

        except Exception as e:
            return HealthCheckResult(
                component="filesystem",
                status=HealthStatus.UNHEALTHY,
                message=f"Filesystem check failed: {str(e)}",
            )

    def _increment_failure_count(self, component: str) -> None:
        """Increment consecutive failure count for component."""
        self._failure_counts[component] = self._failure_counts.get(component, 0) + 1

    def _reset_failure_count(self, component: str) -> None:
        """Reset failure count for component."""
        if component in self._failure_counts:
            del self._failure_counts[component]
