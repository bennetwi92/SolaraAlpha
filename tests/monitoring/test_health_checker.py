"""Tests for health checker."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.monitoring.health_checker import (
    HealthCheckResult,
    HealthChecker,
    HealthStatus,
)


class TestHealthStatus:
    """Test HealthStatus enum."""

    def test_status_values(self):
        """Test that status enum has expected values."""
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"


class TestHealthCheckResult:
    """Test HealthCheckResult dataclass."""

    def test_basic_creation(self):
        """Test creating a basic health check result."""
        result = HealthCheckResult(
            component="test_component",
            status=HealthStatus.HEALTHY,
            message="All systems operational",
        )

        assert result.component == "test_component"
        assert result.status == HealthStatus.HEALTHY
        assert result.message == "All systems operational"
        assert result.metrics is None
        assert result.timestamp is not None

    def test_with_metrics(self):
        """Test result with metrics dict."""
        metrics = {"cpu_percent": 25.5, "memory_percent": 60.0}
        result = HealthCheckResult(
            component="system",
            status=HealthStatus.HEALTHY,
            message="System healthy",
            metrics=metrics,
        )

        assert result.metrics == metrics

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = HealthCheckResult(
            component="test",
            status=HealthStatus.DEGRADED,
            message="Performance degraded",
            metrics={"latency_ms": 150.0},
        )

        data = result.to_dict()

        assert data["component"] == "test"
        assert data["status"] == "degraded"
        assert data["message"] == "Performance degraded"
        assert data["metrics"]["latency_ms"] == 150.0
        assert "timestamp" in data

    def test_timestamp_auto_set(self):
        """Test that timestamp is automatically set if not provided."""
        result = HealthCheckResult(
            component="test", status=HealthStatus.HEALTHY, message="OK"
        )

        assert result.timestamp is not None


class TestHealthChecker:
    """Test HealthChecker class."""

    def test_initialization(self):
        """Test health checker initialization."""
        checker = HealthChecker(
            cpu_threshold=75.0, memory_threshold=80.0, disk_threshold=85.0
        )

        assert checker.cpu_threshold == 75.0
        assert checker.memory_threshold == 80.0
        assert checker.disk_threshold == 85.0

    def test_default_thresholds(self):
        """Test default threshold values."""
        checker = HealthChecker()

        assert checker.cpu_threshold == 80.0
        assert checker.memory_threshold == 85.0
        assert checker.disk_threshold == 90.0

    def test_check_filesystem_healthy(self):
        """Test filesystem check when disk usage is normal."""
        checker = HealthChecker(disk_threshold=90.0)

        with patch("psutil.disk_usage") as mock_disk:
            mock_disk.return_value = Mock(
                percent=50.0, free=500 * 1024**3, total=1000 * 1024**3
            )

            result = checker._check_filesystem()

            assert result.component == "filesystem"
            assert result.status == HealthStatus.HEALTHY
            assert result.metrics["disk_percent"] == 50.0

    def test_check_filesystem_degraded(self):
        """Test filesystem check when disk usage is high."""
        checker = HealthChecker(disk_threshold=85.0)

        with patch("psutil.disk_usage") as mock_disk:
            mock_disk.return_value = Mock(
                percent=90.0, free=100 * 1024**3, total=1000 * 1024**3
            )

            result = checker._check_filesystem()

            assert result.status == HealthStatus.DEGRADED
            assert "high" in result.message.lower()

    def test_check_filesystem_error(self):
        """Test filesystem check when error occurs."""
        checker = HealthChecker()

        with patch("psutil.disk_usage", side_effect=Exception("Disk error")):
            result = checker._check_filesystem()

            assert result.status == HealthStatus.UNHEALTHY
            assert "failed" in result.message.lower()

    def test_check_system_resources_healthy(self):
        """Test system resources check when usage is normal."""
        checker = HealthChecker(cpu_threshold=80.0, memory_threshold=85.0)

        with patch("psutil.cpu_percent", return_value=45.0):
            with patch("psutil.virtual_memory") as mock_mem:
                mock_mem.return_value = Mock(
                    percent=60.0, available=8 * 1024**3
                )

                result = checker._check_system_resources()

                assert result.status == HealthStatus.HEALTHY
                assert result.metrics["cpu_percent"] == 45.0
                assert result.metrics["memory_percent"] == 60.0

    def test_check_system_resources_cpu_high(self):
        """Test system resources check when CPU usage is high."""
        checker = HealthChecker(cpu_threshold=75.0)

        with patch("psutil.cpu_percent", return_value=85.0):
            with patch("psutil.virtual_memory") as mock_mem:
                mock_mem.return_value = Mock(
                    percent=60.0, available=8 * 1024**3
                )

                result = checker._check_system_resources()

                assert result.status == HealthStatus.DEGRADED
                assert "CPU" in result.message

    def test_check_system_resources_memory_high(self):
        """Test system resources check when memory usage is high."""
        checker = HealthChecker(memory_threshold=80.0)

        with patch("psutil.cpu_percent", return_value=50.0):
            with patch("psutil.virtual_memory") as mock_mem:
                mock_mem.return_value = Mock(
                    percent=90.0, available=1 * 1024**3
                )

                result = checker._check_system_resources()

                assert result.status == HealthStatus.DEGRADED
                assert "Memory" in result.message

    def test_check_data_pipeline_error(self):
        """Test data pipeline check when error occurs."""
        checker = HealthChecker()

        with patch(
            "src.monitoring.health_checker.DataPipeline",
            side_effect=Exception("Pipeline error"),
        ):
            result = checker._check_data_pipeline()

            assert result.component == "data_pipeline"
            assert result.status == HealthStatus.UNHEALTHY
            assert "failed" in result.message.lower()

    def test_check_risk_management_healthy(self):
        """Test risk management check when operational."""
        checker = HealthChecker()

        with patch(
            "src.monitoring.health_checker.RiskManager"
        ) as mock_risk_manager:
            # Mock risk manager components
            mock_rm = Mock()
            mock_rm.position_tracker = Mock()
            mock_rm.circuit_breaker = Mock()
            mock_rm.circuit_breaker.get_state.return_value = Mock(name="ACTIVE")
            mock_risk_manager.return_value = mock_rm

            result = checker._check_risk_management()

            assert result.component == "risk_management"
            assert result.status == HealthStatus.HEALTHY

    def test_check_risk_management_error(self):
        """Test risk management check when error occurs."""
        checker = HealthChecker()

        with patch(
            "src.monitoring.health_checker.RiskManager",
            side_effect=Exception("Risk error"),
        ):
            result = checker._check_risk_management()

            assert result.component == "risk_management"
            assert result.status == HealthStatus.UNHEALTHY
            assert "failed" in result.message.lower()


    def test_check_all(self):
        """Test running all health checks."""
        checker = HealthChecker()

        with patch.object(
            checker, "_check_data_pipeline"
        ) as mock_data, patch.object(
            checker, "_check_risk_management"
        ) as mock_risk, patch.object(
            checker, "_check_execution_layer"
        ) as mock_exec, patch.object(
            checker, "_check_system_resources"
        ) as mock_system, patch.object(
            checker, "_check_filesystem"
        ) as mock_fs:

            # Mock all checks to return healthy
            mock_data.return_value = HealthCheckResult(
                component="data_pipeline",
                status=HealthStatus.HEALTHY,
                message="OK",
            )
            mock_risk.return_value = HealthCheckResult(
                component="risk_management",
                status=HealthStatus.HEALTHY,
                message="OK",
            )
            mock_exec.return_value = HealthCheckResult(
                component="execution_layer",
                status=HealthStatus.HEALTHY,
                message="OK",
            )
            mock_system.return_value = HealthCheckResult(
                component="system_resources",
                status=HealthStatus.HEALTHY,
                message="OK",
            )
            mock_fs.return_value = HealthCheckResult(
                component="filesystem", status=HealthStatus.HEALTHY, message="OK"
            )

            results = checker.check_all()

            assert len(results) == 5
            assert all(r.status == HealthStatus.HEALTHY for r in results)

    def test_failure_count_tracking(self):
        """Test consecutive failure count tracking."""
        checker = HealthChecker()

        # Initially no failures
        assert checker.get_failure_count("test_component") == 0

        # Increment failures
        checker._increment_failure_count("test_component")
        assert checker.get_failure_count("test_component") == 1

        checker._increment_failure_count("test_component")
        assert checker.get_failure_count("test_component") == 2

        # Reset failures
        checker._reset_failure_count("test_component")
        assert checker.get_failure_count("test_component") == 0

    def test_failure_count_multiple_components(self):
        """Test tracking failures for multiple components."""
        checker = HealthChecker()

        checker._increment_failure_count("component1")
        checker._increment_failure_count("component1")
        checker._increment_failure_count("component2")

        assert checker.get_failure_count("component1") == 2
        assert checker.get_failure_count("component2") == 1

        checker._reset_failure_count("component1")
        assert checker.get_failure_count("component1") == 0
        assert checker.get_failure_count("component2") == 1


class TestIntegration:
    """Integration tests for health checker."""

    def test_check_all_returns_results(self):
        """Test that check_all returns results for all checks."""
        checker = HealthChecker()

        # Mock all components to avoid real checks
        with patch.object(checker, "_check_data_pipeline") as mock_data, patch.object(
            checker, "_check_risk_management"
        ) as mock_risk, patch.object(
            checker, "_check_execution_layer"
        ) as mock_exec, patch.object(
            checker, "_check_system_resources"
        ) as mock_sys, patch.object(
            checker, "_check_filesystem"
        ) as mock_fs:

            # Return successful checks
            mock_data.return_value = HealthCheckResult(
                component="data", status=HealthStatus.HEALTHY, message="OK"
            )
            mock_risk.return_value = HealthCheckResult(
                component="risk", status=HealthStatus.HEALTHY, message="OK"
            )
            mock_exec.return_value = HealthCheckResult(
                component="exec", status=HealthStatus.HEALTHY, message="OK"
            )
            mock_sys.return_value = HealthCheckResult(
                component="sys", status=HealthStatus.HEALTHY, message="OK"
            )
            mock_fs.return_value = HealthCheckResult(
                component="fs", status=HealthStatus.HEALTHY, message="OK"
            )

            results = checker.check_all()

            # Should return 5 results
            assert len(results) == 5

            # All should be HealthCheckResult instances
            for result in results:
                assert isinstance(result, HealthCheckResult)
