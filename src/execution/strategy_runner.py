"""Strategy execution daemon for live paper trading operations.

Runs multiple strategies 24/7, managing scheduling, health monitoring,
performance reporting, and graceful shutdown. Integrates with risk
management and monitoring infrastructure.
"""
import asyncio
import logging
import signal
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from src.monitoring.alerting import AlertManager, AlertSeverity
from src.monitoring.health_checker import HealthChecker, HealthStatus
from src.monitoring.logging_config import setup_logging
from src.monitoring.reporting import PerformanceReporter

logger = logging.getLogger(__name__)


class StrategyStatus(Enum):
    """Strategy execution status."""

    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    FAILED = "failed"


class RunMode(Enum):
    """Strategy execution mode."""

    CONTINUOUS = "continuous"  # Run on schedule continuously
    MARKET_HOURS = "market_hours"  # Only during market hours
    ON_DEMAND = "on_demand"  # Manual trigger only


@dataclass
class StrategyConfig:
    """Configuration for a strategy instance.

    Attributes:
        strategy_id: Unique strategy identifier
        strategy_name: Human-readable strategy name
        run_mode: Execution mode (continuous, market_hours, on_demand)
        interval_seconds: Execution interval in seconds
        enabled: Whether strategy is active
        max_consecutive_failures: Max failures before pausing
        timeout_seconds: Max execution time before timeout
    """

    strategy_id: str
    strategy_name: str
    run_mode: RunMode = RunMode.CONTINUOUS
    interval_seconds: int = 300  # 5 minutes default
    enabled: bool = True
    max_consecutive_failures: int = 3
    timeout_seconds: int = 120


@dataclass
class StrategyMetrics:
    """Runtime metrics for a strategy.

    Attributes:
        strategy_id: Strategy identifier
        status: Current execution status
        total_iterations: Total iterations executed
        successful_iterations: Successful iterations
        failed_iterations: Failed iterations
        consecutive_failures: Current consecutive failure count
        last_run_time: Timestamp of last execution
        last_success_time: Timestamp of last successful execution
        avg_execution_time_ms: Average execution time in milliseconds
        total_trades: Total trades executed
        last_error: Last error message if any
    """

    strategy_id: str
    status: StrategyStatus = StrategyStatus.STOPPED
    total_iterations: int = 0
    successful_iterations: int = 0
    failed_iterations: int = 0
    consecutive_failures: int = 0
    last_run_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    avg_execution_time_ms: float = 0.0
    total_trades: int = 0
    last_error: Optional[str] = None


class Strategy(ABC):
    """Abstract base class for executable strategies.

    All strategies must implement the run_iteration() method which is
    called periodically by the StrategyRunner.
    """

    @abstractmethod
    def run_iteration(self) -> Dict[str, Any]:
        """Execute one iteration of the strategy.

        Returns:
            Dict with iteration results including:
                - trades: Number of trades executed
                - signals: Number of signals generated
                - errors: List of errors if any
                - metrics: Optional dict of strategy-specific metrics

        Raises:
            Exception: If iteration fails critically
        """
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Get current strategy status.

        Returns:
            Dict with strategy state (positions, portfolio value, etc.)
        """
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Cleanup before strategy termination."""
        pass


class StrategyRunner:
    """Daemon for running multiple strategies continuously.

    Manages strategy lifecycle, scheduling, health monitoring, performance
    reporting, and graceful shutdown. Coordinates with risk management
    and monitoring infrastructure.

    Example:
        >>> runner = StrategyRunner()
        >>> runner.register_strategy(pairs_strategy, config)
        >>> runner.start()  # Runs until SIGTERM
    """

    def __init__(
        self,
        health_check_interval: int = 300,  # 5 minutes
        report_generation_time: str = "23:59",  # Daily at 11:59 PM
        enable_alerts: bool = True,
    ) -> None:
        """Initialize strategy runner.

        Args:
            health_check_interval: Seconds between health checks
            report_generation_time: Time to generate daily report (HH:MM)
            enable_alerts: Enable alert notifications
        """
        # Setup logging
        setup_logging()

        # Strategy registry
        self._strategies: Dict[str, Strategy] = {}
        self._configs: Dict[str, StrategyConfig] = {}
        self._metrics: Dict[str, StrategyMetrics] = {}
        self._strategy_threads: Dict[str, threading.Thread] = {}
        self._stop_events: Dict[str, threading.Event] = {}

        # Monitoring components
        self.health_checker = HealthChecker()
        self.alert_manager = AlertManager() if enable_alerts else None
        self.reporter = PerformanceReporter()

        # Configuration
        self.health_check_interval = health_check_interval
        self.report_generation_time = report_generation_time
        self._parse_report_time()

        # Shutdown handling
        self._shutdown_requested = threading.Event()
        self._running = False

        # Market calendar
        self._market_calendar = USFederalHolidayCalendar()

        # Locks for thread safety
        self._metrics_lock = threading.Lock()

        logger.info("Strategy runner initialized")

    def register_strategy(
        self,
        strategy: Strategy,
        config: StrategyConfig,
    ) -> None:
        """Register a strategy for execution.

        Args:
            strategy: Strategy instance implementing Strategy interface
            config: Strategy configuration

        Raises:
            ValueError: If strategy_id already registered
        """
        if config.strategy_id in self._strategies:
            raise ValueError(
                f"Strategy {config.strategy_id} already registered"
            )

        self._strategies[config.strategy_id] = strategy
        self._configs[config.strategy_id] = config
        self._metrics[config.strategy_id] = StrategyMetrics(
            strategy_id=config.strategy_id
        )
        self._stop_events[config.strategy_id] = threading.Event()

        logger.info(
            f"Strategy registered: {config.strategy_name} "
            f"(ID: {config.strategy_id})",
            extra={
                "context": {
                    "strategy_id": config.strategy_id,
                    "run_mode": config.run_mode.value,
                    "interval_seconds": config.interval_seconds,
                }
            },
        )

    def unregister_strategy(self, strategy_id: str) -> None:
        """Unregister and stop a strategy.

        Args:
            strategy_id: Strategy identifier
        """
        if strategy_id in self._strategies:
            # Stop if running
            if strategy_id in self._strategy_threads:
                self._stop_strategy(strategy_id)

            # Cleanup
            strategy = self._strategies[strategy_id]
            strategy.shutdown()

            # Remove from registry
            del self._strategies[strategy_id]
            del self._configs[strategy_id]
            del self._metrics[strategy_id]

            logger.info(f"Strategy unregistered: {strategy_id}")

    def start(self) -> None:
        """Start the strategy runner daemon.

        Blocks until shutdown signal received (SIGTERM, SIGINT).
        """
        if self._running:
            logger.warning("Strategy runner already running")
            return

        logger.info("Starting strategy runner daemon")

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)

        self._running = True

        # Start all enabled strategies
        for strategy_id, config in self._configs.items():
            if config.enabled:
                self._start_strategy(strategy_id)

        # Start monitoring threads
        health_thread = threading.Thread(
            target=self._health_check_loop,
            name="health_checker",
            daemon=True,
        )
        health_thread.start()

        report_thread = threading.Thread(
            target=self._report_generation_loop,
            name="report_generator",
            daemon=True,
        )
        report_thread.start()

        logger.info(
            f"Strategy runner started | "
            f"Active strategies: {len([c for c in self._configs.values() if c.enabled])}"
        )

        # Main loop - wait for shutdown
        try:
            while not self._shutdown_requested.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        finally:
            self._shutdown()

    def stop(self) -> None:
        """Request graceful shutdown."""
        logger.info("Shutdown requested")
        self._shutdown_requested.set()

    def get_metrics(self) -> Dict[str, StrategyMetrics]:
        """Get current metrics for all strategies.

        Returns:
            Dict mapping strategy_id to StrategyMetrics
        """
        with self._metrics_lock:
            return self._metrics.copy()

    def get_status(self) -> Dict[str, Any]:
        """Get runner status and all strategy statuses.

        Returns:
            Dict with runner and strategy status information
        """
        metrics = self.get_metrics()

        return {
            "runner_status": "running" if self._running else "stopped",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active_strategies": len(
                [m for m in metrics.values() if m.status == StrategyStatus.RUNNING]
            ),
            "total_strategies": len(self._strategies),
            "strategies": {
                sid: {
                    "status": m.status.value,
                    "total_iterations": m.total_iterations,
                    "success_rate": (
                        m.successful_iterations / m.total_iterations
                        if m.total_iterations > 0
                        else 0.0
                    ),
                    "last_run_time": (
                        m.last_run_time.isoformat() if m.last_run_time else None
                    ),
                    "avg_execution_time_ms": m.avg_execution_time_ms,
                    "total_trades": m.total_trades,
                    "last_error": m.last_error,
                }
                for sid, m in metrics.items()
            },
        }

    def _start_strategy(self, strategy_id: str) -> None:
        """Start a strategy execution thread.

        Args:
            strategy_id: Strategy identifier
        """
        if strategy_id in self._strategy_threads:
            logger.warning(f"Strategy {strategy_id} already running")
            return

        config = self._configs[strategy_id]
        stop_event = self._stop_events[strategy_id]

        thread = threading.Thread(
            target=self._strategy_execution_loop,
            args=(strategy_id,),
            name=f"strategy_{strategy_id}",
            daemon=True,
        )
        thread.start()

        self._strategy_threads[strategy_id] = thread

        with self._metrics_lock:
            self._metrics[strategy_id].status = StrategyStatus.RUNNING

        logger.info(
            f"Strategy started: {config.strategy_name}",
            extra={"context": {"strategy_id": strategy_id}},
        )

    def _stop_strategy(self, strategy_id: str) -> None:
        """Stop a strategy execution thread.

        Args:
            strategy_id: Strategy identifier
        """
        if strategy_id not in self._strategy_threads:
            return

        # Signal stop
        self._stop_events[strategy_id].set()

        # Wait for thread to finish (with timeout)
        thread = self._strategy_threads[strategy_id]
        thread.join(timeout=10)

        if thread.is_alive():
            logger.warning(
                f"Strategy {strategy_id} did not stop gracefully"
            )

        # Cleanup
        del self._strategy_threads[strategy_id]
        self._stop_events[strategy_id].clear()

        with self._metrics_lock:
            self._metrics[strategy_id].status = StrategyStatus.STOPPED

        logger.info(f"Strategy stopped: {strategy_id}")

    def _strategy_execution_loop(self, strategy_id: str) -> None:
        """Main execution loop for a strategy.

        Args:
            strategy_id: Strategy identifier
        """
        strategy = self._strategies[strategy_id]
        config = self._configs[strategy_id]
        stop_event = self._stop_events[strategy_id]

        logger.info(
            f"Execution loop started for {config.strategy_name}",
            extra={"context": {"strategy_id": strategy_id}},
        )

        while not stop_event.is_set():
            try:
                # Check if should run based on mode
                if not self._should_run_now(config):
                    time.sleep(60)  # Check again in 1 minute
                    continue

                # Execute iteration with timeout
                start_time = time.time()
                result = self._execute_with_timeout(
                    strategy.run_iteration,
                    timeout=config.timeout_seconds,
                )
                execution_time_ms = (time.time() - start_time) * 1000

                # Update metrics
                self._update_metrics_success(
                    strategy_id, result, execution_time_ms
                )

                # Wait for next iteration
                stop_event.wait(config.interval_seconds)

            except TimeoutError:
                logger.error(
                    f"Strategy {strategy_id} timed out "
                    f"(>{config.timeout_seconds}s)",
                    extra={"context": {"strategy_id": strategy_id}},
                )
                self._update_metrics_failure(
                    strategy_id, "Execution timeout"
                )

            except Exception as e:
                logger.error(
                    f"Strategy {strategy_id} iteration failed: {e}",
                    extra={"context": {"strategy_id": strategy_id}},
                    exc_info=True,
                )
                self._update_metrics_failure(strategy_id, str(e))

            # Check if should pause due to failures
            with self._metrics_lock:
                metrics = self._metrics[strategy_id]
                if (
                    metrics.consecutive_failures
                    >= config.max_consecutive_failures
                ):
                    logger.critical(
                        f"Strategy {strategy_id} paused due to "
                        f"{metrics.consecutive_failures} consecutive failures"
                    )
                    metrics.status = StrategyStatus.FAILED

                    if self.alert_manager:
                        self.alert_manager.send_alert(
                            component=f"strategy_{strategy_id}",
                            severity=AlertSeverity.CRITICAL,
                            message=f"Strategy paused: {config.strategy_name}",
                            context={
                                "consecutive_failures": metrics.consecutive_failures,
                                "last_error": metrics.last_error,
                            },
                        )
                    break

        logger.info(
            f"Execution loop ended for {config.strategy_name}",
            extra={"context": {"strategy_id": strategy_id}},
        )

    def _should_run_now(self, config: StrategyConfig) -> bool:
        """Check if strategy should run based on run mode and market hours.

        Args:
            config: Strategy configuration

        Returns:
            True if strategy should run now
        """
        if config.run_mode == RunMode.ON_DEMAND:
            return False

        if config.run_mode == RunMode.MARKET_HOURS:
            return self._is_market_hours()

        return True  # CONTINUOUS mode

    def _is_market_hours(self) -> bool:
        """Check if currently in US market hours.

        Returns:
            True if in market hours (Mon-Fri, 9:30 AM - 4:00 PM ET)
        """
        now = datetime.now(timezone.utc)

        # Check if market holiday
        holidays = self._market_calendar.holidays(
            start=now.date(), end=now.date()
        )
        if len(holidays) > 0:
            return False

        # Check if weekend
        if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False

        # Convert to US Eastern time (market hours are in ET)
        # Simplified: just check UTC time (market hours ~14:30-21:00 UTC)
        current_time = now.time()
        market_open = dt_time(14, 30)  # 9:30 AM ET
        market_close = dt_time(21, 0)  # 4:00 PM ET

        return market_open <= current_time <= market_close

    def _execute_with_timeout(self, func, timeout: int) -> Any:
        """Execute function with timeout.

        Args:
            func: Function to execute
            timeout: Timeout in seconds

        Returns:
            Function result

        Raises:
            TimeoutError: If execution exceeds timeout
        """
        result = [None]
        exception = [None]

        def wrapper():
            try:
                result[0] = func()
            except Exception as e:
                exception[0] = e

        thread = threading.Thread(target=wrapper)
        thread.start()
        thread.join(timeout)

        if thread.is_alive():
            raise TimeoutError(f"Execution exceeded {timeout} seconds")

        if exception[0]:
            raise exception[0]

        return result[0]

    def _update_metrics_success(
        self,
        strategy_id: str,
        result: Dict[str, Any],
        execution_time_ms: float,
    ) -> None:
        """Update metrics after successful iteration.

        Args:
            strategy_id: Strategy identifier
            result: Iteration result
            execution_time_ms: Execution time in milliseconds
        """
        with self._metrics_lock:
            metrics = self._metrics[strategy_id]

            metrics.total_iterations += 1
            metrics.successful_iterations += 1
            metrics.consecutive_failures = 0
            metrics.last_run_time = datetime.now(timezone.utc)
            metrics.last_success_time = datetime.now(timezone.utc)

            # Update average execution time
            n = metrics.successful_iterations
            metrics.avg_execution_time_ms = (
                (metrics.avg_execution_time_ms * (n - 1) + execution_time_ms)
                / n
            )

            # Update trade count
            if "trades" in result:
                metrics.total_trades += result["trades"]

            metrics.last_error = None

        logger.info(
            f"Strategy {strategy_id} iteration completed successfully",
            extra={
                "context": {
                    "strategy_id": strategy_id,
                    "execution_time_ms": execution_time_ms,
                    "result": result,
                }
            },
        )

    def _update_metrics_failure(
        self, strategy_id: str, error_msg: str
    ) -> None:
        """Update metrics after failed iteration.

        Args:
            strategy_id: Strategy identifier
            error_msg: Error message
        """
        with self._metrics_lock:
            metrics = self._metrics[strategy_id]

            metrics.total_iterations += 1
            metrics.failed_iterations += 1
            metrics.consecutive_failures += 1
            metrics.last_run_time = datetime.now(timezone.utc)
            metrics.last_error = error_msg

    def _health_check_loop(self) -> None:
        """Periodic health check loop."""
        logger.info("Health check loop started")

        while not self._shutdown_requested.is_set():
            try:
                results = self.health_checker.check_all()

                # Alert on issues
                if self.alert_manager:
                    self.alert_manager.check_health_results(results)

                # Check for critical failures
                unhealthy = [
                    r
                    for r in results
                    if r.status == HealthStatus.UNHEALTHY
                ]
                if unhealthy:
                    logger.warning(
                        f"Health check found {len(unhealthy)} unhealthy components"
                    )

            except Exception as e:
                logger.error(
                    f"Health check loop error: {e}",
                    exc_info=True,
                )

            # Wait for next check
            self._shutdown_requested.wait(self.health_check_interval)

        logger.info("Health check loop ended")

    def _report_generation_loop(self) -> None:
        """Periodic report generation loop."""
        logger.info("Report generation loop started")

        while not self._shutdown_requested.is_set():
            try:
                now = datetime.now(timezone.utc)

                # Check if time to generate report
                if (
                    now.hour == self._report_hour
                    and now.minute == self._report_minute
                ):
                    logger.info("Generating daily performance report")

                    try:
                        report_path = self.reporter.generate_daily_report()
                        logger.info(f"Daily report saved: {report_path}")
                    except Exception as e:
                        logger.error(
                            f"Failed to generate daily report: {e}",
                            exc_info=True,
                        )

                    # Wait until next day
                    self._shutdown_requested.wait(3600)  # 1 hour

            except Exception as e:
                logger.error(
                    f"Report generation loop error: {e}",
                    exc_info=True,
                )

            # Check every minute
            self._shutdown_requested.wait(60)

        logger.info("Report generation loop ended")

    def _parse_report_time(self) -> None:
        """Parse report generation time string."""
        try:
            hour, minute = self.report_generation_time.split(":")
            self._report_hour = int(hour)
            self._report_minute = int(minute)
        except (ValueError, AttributeError):
            logger.warning(
                f"Invalid report time: {self.report_generation_time}, "
                "using default 23:59"
            )
            self._report_hour = 23
            self._report_minute = 59

    def _handle_shutdown_signal(self, signum, frame) -> None:
        """Handle shutdown signal.

        Args:
            signum: Signal number
            frame: Current stack frame
        """
        signal_name = signal.Signals(signum).name
        logger.info(f"Received {signal_name} signal")
        self._shutdown_requested.set()

    def _shutdown(self) -> None:
        """Graceful shutdown procedure."""
        if not self._running:
            return

        logger.info("Initiating graceful shutdown")

        # Stop all strategies
        for strategy_id in list(self._strategy_threads.keys()):
            logger.info(f"Stopping strategy: {strategy_id}")
            self._stop_strategy(strategy_id)

        # Cleanup strategies
        for strategy_id, strategy in self._strategies.items():
            try:
                strategy.shutdown()
                logger.info(f"Strategy cleaned up: {strategy_id}")
            except Exception as e:
                logger.error(
                    f"Error cleaning up strategy {strategy_id}: {e}",
                    exc_info=True,
                )

        # Generate final report
        try:
            logger.info("Generating final performance report")
            report_path = self.reporter.generate_daily_report()
            logger.info(f"Final report saved: {report_path}")
        except Exception as e:
            logger.error(
                f"Failed to generate final report: {e}",
                exc_info=True,
            )

        self._running = False
        logger.info("Strategy runner shutdown complete")
