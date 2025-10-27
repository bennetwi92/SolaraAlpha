"""Tests for strategy execution daemon."""
import signal
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.execution.strategy_runner import (
    RunMode,
    Strategy,
    StrategyConfig,
    StrategyRunner,
    StrategyStatus,
)


class MockStrategy(Strategy):
    """Mock strategy for testing."""

    def __init__(self, fail_on_iteration: bool = False, slow: bool = False):
        self.fail_on_iteration = fail_on_iteration
        self.slow = slow
        self.iterations = 0
        self.shutdown_called = False

    def run_iteration(self):
        self.iterations += 1
        if self.slow:
            time.sleep(5)
        if self.fail_on_iteration:
            raise ValueError("Mock strategy failure")
        return {"trades": 2, "signals": 3, "errors": []}

    def get_status(self):
        return {"iterations": self.iterations, "status": "mock"}

    def shutdown(self):
        self.shutdown_called = True


@pytest.fixture
def runner():
    """Create strategy runner for testing."""
    runner = StrategyRunner(
        health_check_interval=300,
        enable_alerts=False,
    )
    yield runner
    # Cleanup
    if runner._running:
        runner.stop()
        time.sleep(0.5)


@pytest.fixture
def mock_strategy():
    """Create mock strategy."""
    return MockStrategy()


@pytest.fixture
def strategy_config():
    """Create strategy config."""
    return StrategyConfig(
        strategy_id="test_strategy",
        strategy_name="Test Strategy",
        run_mode=RunMode.CONTINUOUS,
        interval_seconds=1,
        enabled=True,
        max_consecutive_failures=3,
        timeout_seconds=10,
    )


class TestStrategyRegistration:
    """Test strategy registration and unregistration."""

    def test_register_strategy(self, runner, mock_strategy, strategy_config):
        """Test registering a strategy."""
        runner.register_strategy(mock_strategy, strategy_config)

        assert "test_strategy" in runner._strategies
        assert runner._strategies["test_strategy"] == mock_strategy
        assert runner._configs["test_strategy"] == strategy_config
        assert "test_strategy" in runner._metrics

    def test_register_duplicate_strategy(
        self, runner, mock_strategy, strategy_config
    ):
        """Test registering duplicate strategy fails."""
        runner.register_strategy(mock_strategy, strategy_config)

        with pytest.raises(ValueError, match="already registered"):
            runner.register_strategy(mock_strategy, strategy_config)

    def test_unregister_strategy(
        self, runner, mock_strategy, strategy_config
    ):
        """Test unregistering a strategy."""
        runner.register_strategy(mock_strategy, strategy_config)
        runner.unregister_strategy("test_strategy")

        assert "test_strategy" not in runner._strategies
        assert "test_strategy" not in runner._configs
        assert "test_strategy" not in runner._metrics
        assert mock_strategy.shutdown_called


class TestStrategyExecution:
    """Test strategy execution logic."""

    def test_successful_iteration(self, runner, mock_strategy, strategy_config):
        """Test successful strategy iteration."""
        runner.register_strategy(mock_strategy, strategy_config)
        runner._start_strategy("test_strategy")

        # Wait for at least one iteration
        time.sleep(2)

        runner._stop_strategy("test_strategy")

        # Check metrics
        metrics = runner.get_metrics()["test_strategy"]
        assert metrics.total_iterations >= 1
        assert metrics.successful_iterations >= 1
        assert metrics.failed_iterations == 0
        assert metrics.consecutive_failures == 0
        assert metrics.status == StrategyStatus.STOPPED

    def test_failed_iteration(self, runner, strategy_config):
        """Test failed strategy iteration."""
        failing_strategy = MockStrategy(fail_on_iteration=True)
        runner.register_strategy(failing_strategy, strategy_config)
        runner._start_strategy("test_strategy")

        # Wait for iterations to fail
        time.sleep(2)

        runner._stop_strategy("test_strategy")

        # Check metrics
        metrics = runner.get_metrics()["test_strategy"]
        assert metrics.failed_iterations >= 1
        assert metrics.consecutive_failures >= 1
        assert metrics.last_error is not None

    def test_strategy_pause_on_max_failures(self, runner, strategy_config):
        """Test strategy pauses after max consecutive failures."""
        failing_strategy = MockStrategy(fail_on_iteration=True)
        strategy_config.max_consecutive_failures = 2
        strategy_config.interval_seconds = 0.5

        runner.register_strategy(failing_strategy, strategy_config)
        runner._start_strategy("test_strategy")

        # Wait for failures to accumulate
        time.sleep(3)

        # Check strategy paused
        metrics = runner.get_metrics()["test_strategy"]
        assert metrics.status == StrategyStatus.FAILED
        assert metrics.consecutive_failures >= 2

    def test_execution_timeout(self, runner, strategy_config):
        """Test strategy execution timeout."""
        slow_strategy = MockStrategy(slow=True)
        strategy_config.timeout_seconds = 2

        runner.register_strategy(slow_strategy, strategy_config)
        runner._start_strategy("test_strategy")

        # Wait for timeout
        time.sleep(4)

        runner._stop_strategy("test_strategy")

        # Check metrics show failure
        metrics = runner.get_metrics()["test_strategy"]
        assert metrics.failed_iterations >= 1
        assert "timeout" in metrics.last_error.lower()


class TestRunModes:
    """Test different run modes."""

    def test_continuous_mode_always_runs(self, runner):
        """Test continuous mode runs regardless of market hours."""
        config = StrategyConfig(
            strategy_id="test",
            strategy_name="Test",
            run_mode=RunMode.CONTINUOUS,
        )

        assert runner._should_run_now(config) is True

    def test_on_demand_never_runs(self, runner):
        """Test on_demand mode never runs automatically."""
        config = StrategyConfig(
            strategy_id="test",
            strategy_name="Test",
            run_mode=RunMode.ON_DEMAND,
        )

        assert runner._should_run_now(config) is False

    @patch("src.execution.strategy_runner.datetime")
    def test_market_hours_mode(self, mock_datetime, runner):
        """Test market hours mode respects trading hours."""
        # Mock weekday, market hours (15:00 UTC = 10:00 AM ET)
        mock_now = Mock()
        mock_now.weekday.return_value = 2  # Wednesday
        mock_now.time.return_value = datetime.strptime(
            "15:00", "%H:%M"
        ).time()
        mock_now.date.return_value = datetime(2025, 10, 27).date()
        mock_datetime.now.return_value = mock_now

        config = StrategyConfig(
            strategy_id="test",
            strategy_name="Test",
            run_mode=RunMode.MARKET_HOURS,
        )

        assert runner._should_run_now(config) is True

        # Mock weekend
        mock_now.weekday.return_value = 5  # Saturday
        assert runner._should_run_now(config) is False


class TestMetrics:
    """Test metrics collection and reporting."""

    def test_metrics_initialization(self, runner, mock_strategy, strategy_config):
        """Test metrics initialized correctly."""
        runner.register_strategy(mock_strategy, strategy_config)

        metrics = runner.get_metrics()["test_strategy"]
        assert metrics.strategy_id == "test_strategy"
        assert metrics.status == StrategyStatus.STOPPED
        assert metrics.total_iterations == 0
        assert metrics.successful_iterations == 0
        assert metrics.failed_iterations == 0

    def test_metrics_update_on_success(
        self, runner, mock_strategy, strategy_config
    ):
        """Test metrics updated correctly on success."""
        runner.register_strategy(mock_strategy, strategy_config)

        result = {"trades": 5, "signals": 10}
        runner._update_metrics_success("test_strategy", result, 150.5)

        metrics = runner.get_metrics()["test_strategy"]
        assert metrics.total_iterations == 1
        assert metrics.successful_iterations == 1
        assert metrics.consecutive_failures == 0
        assert metrics.total_trades == 5
        assert metrics.avg_execution_time_ms == 150.5
        assert metrics.last_error is None

    def test_metrics_update_on_failure(self, runner, mock_strategy, strategy_config):
        """Test metrics updated correctly on failure."""
        runner.register_strategy(mock_strategy, strategy_config)

        runner._update_metrics_failure("test_strategy", "Test error")

        metrics = runner.get_metrics()["test_strategy"]
        assert metrics.total_iterations == 1
        assert metrics.failed_iterations == 1
        assert metrics.consecutive_failures == 1
        assert metrics.last_error == "Test error"

    def test_get_status(self, runner, mock_strategy, strategy_config):
        """Test get_status returns correct format."""
        runner.register_strategy(mock_strategy, strategy_config)
        runner._start_strategy("test_strategy")
        time.sleep(1)
        runner._stop_strategy("test_strategy")

        status = runner.get_status()

        assert "runner_status" in status
        assert "timestamp" in status
        assert "active_strategies" in status
        assert "total_strategies" in status
        assert "strategies" in status
        assert "test_strategy" in status["strategies"]

        strategy_status = status["strategies"]["test_strategy"]
        assert "status" in strategy_status
        assert "total_iterations" in strategy_status
        assert "success_rate" in strategy_status


class TestShutdown:
    """Test shutdown handling."""

    def test_graceful_shutdown(self, runner, mock_strategy, strategy_config):
        """Test graceful shutdown stops all strategies."""
        runner.register_strategy(mock_strategy, strategy_config)
        runner._running = True
        runner._start_strategy("test_strategy")

        time.sleep(1)

        runner._shutdown()

        assert not runner._running
        assert len(runner._strategy_threads) == 0
        assert mock_strategy.shutdown_called

    def test_shutdown_signal_handling(self, runner):
        """Test shutdown signal sets shutdown event."""
        runner._handle_shutdown_signal(signal.SIGTERM, None)
        assert runner._shutdown_requested.is_set()

    def test_stop_method(self, runner):
        """Test stop method requests shutdown."""
        runner.stop()
        assert runner._shutdown_requested.is_set()


class TestExecutionWithTimeout:
    """Test timeout execution mechanism."""

    def test_normal_execution(self, runner):
        """Test normal function execution within timeout."""

        def fast_func():
            return "success"

        result = runner._execute_with_timeout(fast_func, timeout=5)
        assert result == "success"

    def test_timeout_execution(self, runner):
        """Test function execution exceeding timeout."""

        def slow_func():
            time.sleep(10)
            return "success"

        with pytest.raises(TimeoutError):
            runner._execute_with_timeout(slow_func, timeout=1)

    def test_exception_propagation(self, runner):
        """Test exceptions propagate correctly."""

        def failing_func():
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            runner._execute_with_timeout(failing_func, timeout=5)


class TestHealthCheckIntegration:
    """Test health check integration."""

    @patch("src.execution.strategy_runner.HealthChecker")
    def test_health_check_loop_runs(self, mock_health_checker, runner):
        """Test health check loop executes."""
        mock_checker = Mock()
        mock_checker.check_all.return_value = []
        mock_health_checker.return_value = mock_checker

        runner_with_mock = StrategyRunner(
            health_check_interval=1,
            enable_alerts=False,
        )

        # Start health check thread
        thread = threading.Thread(
            target=runner_with_mock._health_check_loop,
            daemon=True,
        )
        thread.start()

        time.sleep(2)

        runner_with_mock._shutdown_requested.set()
        thread.join(timeout=2)

        # Should have called check_all at least once
        assert mock_checker.check_all.call_count >= 1


class TestReportGeneration:
    """Test report generation integration."""

    def test_parse_report_time(self, runner):
        """Test report time parsing."""
        runner.report_generation_time = "14:30"
        runner._parse_report_time()

        assert runner._report_hour == 14
        assert runner._report_minute == 30

    def test_parse_invalid_report_time(self, runner):
        """Test invalid report time uses default."""
        runner.report_generation_time = "invalid"
        runner._parse_report_time()

        assert runner._report_hour == 23
        assert runner._report_minute == 59


@pytest.mark.integration
class TestIntegration:
    """Integration tests for complete workflow."""

    def test_full_lifecycle(self, mock_strategy, strategy_config):
        """Test complete strategy lifecycle."""
        runner = StrategyRunner(enable_alerts=False)

        # Register
        runner.register_strategy(mock_strategy, strategy_config)

        # Start in thread
        def run_daemon():
            runner._running = True
            runner._start_strategy("test_strategy")
            time.sleep(3)
            runner._shutdown()

        thread = threading.Thread(target=run_daemon)
        thread.start()
        thread.join(timeout=10)

        # Verify execution
        metrics = runner.get_metrics()["test_strategy"]
        assert metrics.total_iterations >= 2
        assert metrics.successful_iterations >= 2
        assert mock_strategy.shutdown_called
