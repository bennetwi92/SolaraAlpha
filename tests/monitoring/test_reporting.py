"""Tests for performance reporting."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.monitoring.reporting import PerformanceReporter


class TestPerformanceReporter:
    """Test PerformanceReporter class."""

    def test_initialization(self):
        """Test reporter initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir) / "reports"
            reporter = PerformanceReporter(reports_dir=str(reports_dir))

            assert reporter.reports_dir == reports_dir
            assert reports_dir.exists()

    def test_creates_reports_directory(self):
        """Test that reports directory is created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir) / "subdir" / "reports"
            reporter = PerformanceReporter(reports_dir=str(reports_dir))

            assert reports_dir.exists()

    def test_generate_daily_report_basic(self):
        """Test generating basic daily report."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            with patch.object(
                reporter.health_checker, "check_all"
            ) as mock_health:
                mock_health.return_value = []

                report_path = reporter.generate_daily_report()

                assert report_path.exists()
                content = report_path.read_text()
                assert "SolaraAlpha Daily Report" in content
                assert "System Health" in content

    def test_report_filename_format(self):
        """Test that report filename uses YYYY-MM-DD format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            with patch.object(reporter.health_checker, "check_all"):
                report_path = reporter.generate_daily_report()

                # Filename should be YYYY-MM-DD.md
                filename = report_path.name
                assert filename.endswith(".md")
                date_part = filename[:-3]
                # Should be parseable as date
                datetime.strptime(date_part, "%Y-%m-%d")

    def test_generate_report_with_risk_metrics(self):
        """Test generating report with risk metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            with patch.object(reporter.health_checker, "check_all"):
                # Mock risk manager
                mock_risk = Mock()
                mock_risk.calculate_risk_metrics.return_value = Mock(
                    total_exposure=100000.0,
                    net_exposure=50000.0,
                    gross_exposure=150000.0,
                    var_95=5000.0,
                    var_99=7500.0,
                    max_drawdown=0.05,
                    sharpe_ratio=1.5,
                )
                mock_risk.portfolio_tracker.portfolio_value = 1000000.0
                mock_risk.get_all_positions.return_value = []

                report_path = reporter.generate_daily_report(
                    risk_manager=mock_risk
                )

                content = report_path.read_text()
                assert "P&L Overview" in content
                assert "Risk Metrics" in content
                assert "1,000,000.00" in content  # Portfolio value
                assert "1.5" in content  # Sharpe ratio

    def test_generate_report_with_execution_metrics(self):
        """Test generating report with execution metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            with patch.object(reporter.health_checker, "check_all"):
                # Mock execution metrics
                mock_exec = Mock()
                mock_exec.get_daily_summary.return_value = {
                    "total_orders": 100,
                    "filled_orders": 95,
                    "cancelled_orders": 3,
                    "rejected_orders": 2,
                    "fill_rate_pct": 95.0,
                    "avg_fill_time_ms": 150.0,
                    "avg_slippage_bps": 2.5,
                    "total_commissions": 50.0,
                }

                report_path = reporter.generate_daily_report(
                    exec_metrics=mock_exec
                )

                content = report_path.read_text()
                assert "Execution Quality" in content
                assert "95.0%" in content  # Fill rate

    def test_generate_report_with_positions(self):
        """Test generating report with top positions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            with patch.object(reporter.health_checker, "check_all"):
                # Mock risk manager with positions
                mock_risk = Mock()
                mock_risk.calculate_risk_metrics.return_value = Mock(
                    total_exposure=100000.0,
                    net_exposure=50000.0,
                    gross_exposure=150000.0,
                    var_95=5000.0,
                    var_99=7500.0,
                    max_drawdown=0.05,
                    sharpe_ratio=1.5,
                )
                mock_risk.portfolio_tracker.portfolio_value = 1000000.0

                # Mock positions
                mock_pos1 = Mock(
                    symbol="AAPL",
                    asset_class="equities",
                    quantity=100,
                    average_price=150.0,
                    unrealized_pnl=500.0,
                )
                mock_pos2 = Mock(
                    symbol="GOOGL",
                    asset_class="equities",
                    quantity=50,
                    average_price=2800.0,
                    unrealized_pnl=-200.0,
                )
                mock_risk.get_all_positions.return_value = [
                    mock_pos1,
                    mock_pos2,
                ]

                report_path = reporter.generate_daily_report(
                    risk_manager=mock_risk
                )

                content = report_path.read_text()
                assert "Top 5 Positions" in content
                assert "AAPL" in content
                assert "GOOGL" in content

    def test_get_top_positions_sorting(self):
        """Test that positions are sorted by absolute market value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            # Create mock positions
            pos1 = Mock(
                symbol="AAPL",
                quantity=100,
                average_price=150.0,
                unrealized_pnl=0,
            )  # 15000
            pos2 = Mock(
                symbol="GOOGL",
                quantity=50,
                average_price=2800.0,
                unrealized_pnl=0,
            )  # 140000
            pos3 = Mock(
                symbol="TSLA",
                quantity=-200,
                average_price=250.0,
                unrealized_pnl=0,
            )  # -50000 (abs 50000)

            mock_risk = Mock()
            mock_risk.get_all_positions.return_value = [pos1, pos2, pos3]

            positions = reporter._get_top_positions(mock_risk, limit=3)

            # Should be sorted by absolute value: GOOGL, TSLA, AAPL
            assert positions[0]["symbol"] == "GOOGL"
            assert positions[1]["symbol"] == "TSLA"
            assert positions[2]["symbol"] == "AAPL"

    def test_get_top_positions_limit(self):
        """Test that only top N positions are returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            # Create 10 mock positions
            positions = []
            for i in range(10):
                pos = Mock(
                    symbol=f"SYM{i}",
                    quantity=100,
                    average_price=float(i + 1),
                    unrealized_pnl=0,
                )
                positions.append(pos)

            mock_risk = Mock()
            mock_risk.get_all_positions.return_value = positions

            top_positions = reporter._get_top_positions(mock_risk, limit=5)

            assert len(top_positions) == 5

    def test_report_markdown_structure(self):
        """Test that report has proper markdown structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            with patch.object(reporter.health_checker, "check_all"):
                report_path = reporter.generate_daily_report()

                content = report_path.read_text()

                # Check for markdown headers
                assert content.startswith("# ")
                assert "## " in content

                # Check for sections
                assert "System Health" in content

    def test_get_system_metrics(self):
        """Test extracting system metrics from health results."""
        from src.monitoring.health_checker import (
            HealthCheckResult,
            HealthStatus,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            results = [
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
                HealthCheckResult(
                    component="comp3",
                    status=HealthStatus.DEGRADED,
                    message="Slow",
                ),
                HealthCheckResult(
                    component="comp4",
                    status=HealthStatus.UNHEALTHY,
                    message="Failed",
                ),
            ]

            metrics = reporter._get_system_metrics(results)

            assert metrics["healthy_count"] == 2
            assert metrics["degraded_count"] == 1
            assert metrics["unhealthy_count"] == 1
            assert metrics["uptime_pct"] == 50.0  # 2/4 healthy

    def test_error_handling(self):
        """Test that report generation handles errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            with patch.object(
                reporter.health_checker,
                "check_all",
                side_effect=Exception("Test error"),
            ):
                # Should raise exception
                with pytest.raises(Exception):
                    reporter.generate_daily_report()

    def test_get_risk_metrics_error_handling(self):
        """Test that _get_risk_metrics handles errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            mock_risk = Mock()
            mock_risk.calculate_risk_metrics.side_effect = Exception("Error")

            result = reporter._get_risk_metrics(mock_risk)

            assert result == {}

    def test_get_execution_metrics_error_handling(self):
        """Test that _get_execution_metrics handles errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            mock_exec = Mock()
            mock_exec.get_daily_summary.side_effect = Exception("Error")

            result = reporter._get_execution_metrics(mock_exec)

            assert result == {}

    def test_send_email_report_missing_config(self):
        """Test that email sending skips when config incomplete."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(
                reports_dir=str(tmpdir), smtp_config={}
            )

            # Should not raise exception
            reporter._send_email_report("Test report", "2025-10-27")

    def test_send_email_report_with_complete_config(self):
        """Test email sending with complete config."""
        smtp_config = {
            "host": "smtp.gmail.com",
            "port": 587,
            "username": "test@example.com",
            "password": "password",
            "from_email": "test@example.com",
            "to_emails": ["recipient@example.com"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(
                reports_dir=str(tmpdir), smtp_config=smtp_config
            )

            with patch("smtplib.SMTP") as mock_smtp:
                mock_server = Mock()
                mock_smtp.return_value.__enter__.return_value = mock_server

                reporter._send_email_report("Test report", "2025-10-27")

                # Verify SMTP calls
                mock_smtp.assert_called_once()
                mock_server.starttls.assert_called_once()
                mock_server.login.assert_called_once()
                mock_server.send_message.assert_called_once()

    def test_send_email_report_error_handling(self):
        """Test that email sending handles errors gracefully."""
        smtp_config = {
            "host": "smtp.gmail.com",
            "port": 587,
            "username": "test@example.com",
            "password": "password",
            "from_email": "test@example.com",
            "to_emails": ["recipient@example.com"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(
                reports_dir=str(tmpdir), smtp_config=smtp_config
            )

            with patch("smtplib.SMTP", side_effect=Exception("SMTP error")):
                # Should not raise exception
                reporter._send_email_report("Test report", "2025-10-27")

    def test_generate_report_with_email(self):
        """Test generating and emailing report."""
        smtp_config = {
            "host": "smtp.gmail.com",
            "port": 587,
            "username": "test@example.com",
            "password": "password",
            "from_email": "test@example.com",
            "to_emails": ["recipient@example.com"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(
                reports_dir=str(tmpdir), smtp_config=smtp_config
            )

            with patch.object(reporter.health_checker, "check_all"):
                with patch("smtplib.SMTP") as mock_smtp:
                    mock_server = Mock()
                    mock_smtp.return_value.__enter__.return_value = mock_server

                    report_path = reporter.generate_daily_report(send_email=True)

                    # Report should be generated
                    assert report_path.exists()

                    # Email should be sent
                    mock_server.send_message.assert_called_once()


class TestIntegration:
    """Integration tests for performance reporting."""

    def test_complete_report_generation(self):
        """Test generating complete report with all components."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = PerformanceReporter(reports_dir=str(tmpdir))

            with patch.object(reporter.health_checker, "check_all") as mock_health:
                from src.monitoring.health_checker import (
                    HealthCheckResult,
                    HealthStatus,
                )

                mock_health.return_value = [
                    HealthCheckResult(
                        component="data_pipeline",
                        status=HealthStatus.HEALTHY,
                        message="Operational",
                    ),
                    HealthCheckResult(
                        component="risk_management",
                        status=HealthStatus.HEALTHY,
                        message="Operational",
                    ),
                ]

                # Mock risk manager
                mock_risk = Mock()
                mock_risk.calculate_risk_metrics.return_value = Mock(
                    total_exposure=100000.0,
                    net_exposure=50000.0,
                    gross_exposure=150000.0,
                    var_95=5000.0,
                    var_99=7500.0,
                    max_drawdown=0.05,
                    sharpe_ratio=1.5,
                )
                mock_risk.portfolio_tracker.portfolio_value = 1000000.0

                mock_pos = Mock(
                    symbol="AAPL",
                    asset_class="equities",
                    quantity=100,
                    average_price=150.0,
                    unrealized_pnl=500.0,
                )
                mock_risk.get_all_positions.return_value = [mock_pos]

                # Mock execution metrics
                mock_exec = Mock()
                mock_exec.get_daily_summary.return_value = {
                    "total_orders": 100,
                    "filled_orders": 95,
                    "cancelled_orders": 3,
                    "rejected_orders": 2,
                    "fill_rate_pct": 95.0,
                    "avg_fill_time_ms": 150.0,
                    "avg_slippage_bps": 2.5,
                    "total_commissions": 50.0,
                }

                report_path = reporter.generate_daily_report(
                    risk_manager=mock_risk, exec_metrics=mock_exec
                )

                # Verify report exists
                assert report_path.exists()

                content = report_path.read_text()

                # Verify all sections present
                assert "SolaraAlpha Daily Report" in content
                assert "P&L Overview" in content
                assert "Risk Metrics" in content
                assert "Execution Quality" in content
                assert "Top 5 Positions" in content
                assert "System Health" in content
