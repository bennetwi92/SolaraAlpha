"""Tests for dashboard generator."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.monitoring.dashboard import DashboardGenerator
from src.monitoring.health_checker import HealthCheckResult, HealthStatus


class TestDashboardGenerator:
    """Test DashboardGenerator class."""

    def test_initialization(self):
        """Test dashboard generator initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            assert generator.output_path == output_path
            assert generator.output_path.parent.exists()

    def test_creates_output_directory(self):
        """Test that output directory is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "subdir" / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            assert output_path.parent.exists()

    def test_generate_dashboard_basic(self):
        """Test generating basic dashboard without optional data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            # Mock health checker
            with patch.object(generator.health_checker, "check_all") as mock_check:
                mock_check.return_value = [
                    HealthCheckResult(
                        component="test",
                        status=HealthStatus.HEALTHY,
                        message="All OK",
                    )
                ]

                generator.generate_dashboard()

                assert output_path.exists()
                content = output_path.read_text()
                assert "SolaraAlpha Dashboard" in content
                assert "HEALTHY" in content

    def test_generate_dashboard_with_risk_data(self):
        """Test generating dashboard with risk metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            # Mock health checker
            with patch.object(generator.health_checker, "check_all") as mock_check:
                mock_check.return_value = [
                    HealthCheckResult(
                        component="test",
                        status=HealthStatus.HEALTHY,
                        message="OK",
                    )
                ]

                # Mock risk manager
                mock_risk_mgr = Mock()
                mock_metrics = Mock(
                    total_exposure=100000.0,
                    var_95=5000.0,
                    max_drawdown=0.05,
                    sharpe_ratio=1.5,
                )
                mock_risk_mgr.calculate_risk_metrics.return_value = mock_metrics
                mock_risk_mgr.get_all_positions.return_value = []
                mock_risk_mgr.circuit_breaker.get_state.return_value = Mock(
                    name="ACTIVE"
                )

                generator.generate_dashboard(risk_manager=mock_risk_mgr)

                content = output_path.read_text()
                assert "Risk Metrics" in content
                assert "100,000.00" in content  # Total exposure
                assert "1.5" in content  # Sharpe ratio

    def test_generate_dashboard_with_execution_data(self):
        """Test generating dashboard with execution metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            with patch.object(generator.health_checker, "check_all") as mock_check:
                mock_check.return_value = [
                    HealthCheckResult(
                        component="test",
                        status=HealthStatus.HEALTHY,
                        message="OK",
                    )
                ]

                # Mock execution metrics
                mock_exec = Mock()
                mock_exec.get_daily_summary.return_value = {
                    "total_orders": 100,
                    "filled_orders": 95,
                    "fill_rate_pct": 95.0,
                    "avg_slippage_bps": 2.5,
                    "total_commissions": 50.0,
                }

                generator.generate_dashboard(exec_metrics=mock_exec)

                content = output_path.read_text()
                assert "Execution Statistics" in content
                assert "95.0%" in content  # Fill rate

    def test_dashboard_overall_status_healthy(self):
        """Test dashboard shows HEALTHY when all checks pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            with patch.object(generator.health_checker, "check_all") as mock_check:
                mock_check.return_value = [
                    HealthCheckResult(
                        component="comp1",
                        status=HealthStatus.HEALTHY,
                        message="OK",
                    ),
                    HealthCheckResult(
                        component="comp2",
                        status=HealthStatus.HEALTHY,
                        message="OK",
                    ),
                ]

                generator.generate_dashboard()

                content = output_path.read_text()
                assert "HEALTHY" in content
                # Should not show DEGRADED or UNHEALTHY
                assert content.count("DEGRADED") <= 1  # Might be in legend
                assert content.count("UNHEALTHY") <= 1

    def test_dashboard_overall_status_degraded(self):
        """Test dashboard shows DEGRADED when some checks degraded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            with patch.object(generator.health_checker, "check_all") as mock_check:
                mock_check.return_value = [
                    HealthCheckResult(
                        component="comp1",
                        status=HealthStatus.HEALTHY,
                        message="OK",
                    ),
                    HealthCheckResult(
                        component="comp2",
                        status=HealthStatus.DEGRADED,
                        message="Slow",
                    ),
                ]

                generator.generate_dashboard()

                content = output_path.read_text()
                assert "DEGRADED" in content

    def test_dashboard_overall_status_unhealthy(self):
        """Test dashboard shows UNHEALTHY when checks fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            with patch.object(generator.health_checker, "check_all") as mock_check:
                mock_check.return_value = [
                    HealthCheckResult(
                        component="comp1",
                        status=HealthStatus.HEALTHY,
                        message="OK",
                    ),
                    HealthCheckResult(
                        component="comp2",
                        status=HealthStatus.UNHEALTHY,
                        message="Failed",
                    ),
                ]

                generator.generate_dashboard()

                content = output_path.read_text()
                assert "UNHEALTHY" in content

    def test_dashboard_html_structure(self):
        """Test that generated HTML has proper structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            with patch.object(generator.health_checker, "check_all") as mock_check:
                mock_check.return_value = [
                    HealthCheckResult(
                        component="test",
                        status=HealthStatus.HEALTHY,
                        message="OK",
                    )
                ]

                generator.generate_dashboard()

                content = output_path.read_text()

                # Check for basic HTML structure
                assert "<!DOCTYPE html>" in content
                assert "<html" in content
                assert "<head>" in content
                assert "<body>" in content
                assert "</html>" in content

                # Check for auto-refresh meta tag
                assert 'http-equiv="refresh"' in content
                assert "300" in content  # 5 minutes in seconds

    def test_dashboard_shows_timestamp(self):
        """Test that dashboard shows generation timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            with patch.object(generator.health_checker, "check_all") as mock_check:
                mock_check.return_value = [
                    HealthCheckResult(
                        component="test",
                        status=HealthStatus.HEALTHY,
                        message="OK",
                    )
                ]

                generator.generate_dashboard()

                content = output_path.read_text()
                assert "Last updated:" in content

    def test_dashboard_shows_metrics(self):
        """Test that dashboard displays health check metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            with patch.object(generator.health_checker, "check_all") as mock_check:
                mock_check.return_value = [
                    HealthCheckResult(
                        component="test",
                        status=HealthStatus.HEALTHY,
                        message="OK",
                        metrics={"cpu_percent": 45.0, "memory_percent": 60.0},
                    )
                ]

                generator.generate_dashboard()

                content = output_path.read_text()
                assert "cpu_percent" in content
                assert "45.00" in content

    def test_error_handling(self):
        """Test that dashboard generation handles errors gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            with patch.object(
                generator.health_checker,
                "check_all",
                side_effect=Exception("Test error"),
            ):
                # Should not raise exception
                generator.generate_dashboard()

    def test_get_risk_metrics_error_handling(self):
        """Test that _get_risk_metrics handles errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = DashboardGenerator(output_path=str(Path(tmpdir) / "dash.html"))

            mock_risk_mgr = Mock()
            mock_risk_mgr.calculate_risk_metrics.side_effect = Exception("Error")

            result = generator._get_risk_metrics(mock_risk_mgr)

            assert result == {}

    def test_get_execution_metrics_error_handling(self):
        """Test that _get_execution_metrics handles errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = DashboardGenerator(output_path=str(Path(tmpdir) / "dash.html"))

            mock_exec = Mock()
            mock_exec.get_daily_summary.side_effect = Exception("Error")

            result = generator._get_execution_metrics(mock_exec)

            assert result == {}


class TestIntegration:
    """Integration tests for dashboard generator."""

    def test_complete_dashboard_generation(self):
        """Test generating complete dashboard with all components."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "dashboard.html"
            generator = DashboardGenerator(output_path=str(output_path))

            # Mock all dependencies
            with patch.object(generator.health_checker, "check_all") as mock_check:
                mock_check.return_value = [
                    HealthCheckResult(
                        component="data_pipeline",
                        status=HealthStatus.HEALTHY,
                        message="Operational",
                        metrics={"storage_accessible": 1.0},
                    ),
                    HealthCheckResult(
                        component="risk_management",
                        status=HealthStatus.HEALTHY,
                        message="Operational",
                        metrics={"circuit_breaker_active": 1.0},
                    ),
                ]

                # Mock risk manager
                mock_risk = Mock()
                mock_risk.calculate_risk_metrics.return_value = Mock(
                    total_exposure=100000.0,
                    var_95=5000.0,
                    max_drawdown=0.05,
                    sharpe_ratio=1.5,
                )
                mock_risk.get_all_positions.return_value = []
                mock_risk.circuit_breaker.get_state.return_value = Mock(name="ACTIVE")

                # Mock execution metrics
                mock_exec = Mock()
                mock_exec.get_daily_summary.return_value = {
                    "total_orders": 100,
                    "filled_orders": 95,
                    "fill_rate_pct": 95.0,
                    "avg_slippage_bps": 2.5,
                    "total_commissions": 50.0,
                }

                generator.generate_dashboard(
                    risk_manager=mock_risk, exec_metrics=mock_exec
                )

                # Verify dashboard was created
                assert output_path.exists()

                content = output_path.read_text()

                # Verify all sections present
                assert "SolaraAlpha Dashboard" in content
                assert "System Health" in content
                assert "Risk Metrics" in content
                assert "Execution Statistics" in content
