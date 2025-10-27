"""Tests for alerting system."""

import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.monitoring.alerting import (
    Alert,
    AlertManager,
    AlertSeverity,
    LogMonitor,
)
from src.monitoring.health_checker import HealthCheckResult, HealthStatus


class TestAlertSeverity:
    """Test AlertSeverity enum."""

    def test_severity_values(self):
        """Test that severity enum has expected values."""
        assert AlertSeverity.INFO.value == "info"
        assert AlertSeverity.WARNING.value == "warning"
        assert AlertSeverity.CRITICAL.value == "critical"


class TestAlert:
    """Test Alert dataclass."""

    def test_basic_creation(self):
        """Test creating a basic alert."""
        alert = Alert(
            component="test_component",
            severity=AlertSeverity.WARNING,
            message="Test warning message",
        )

        assert alert.component == "test_component"
        assert alert.severity == AlertSeverity.WARNING
        assert alert.message == "Test warning message"
        assert alert.context is None
        assert alert.timestamp is not None

    def test_with_context(self):
        """Test alert with context dict."""
        context = {"error_code": 500, "user_id": 123}
        alert = Alert(
            component="test",
            severity=AlertSeverity.CRITICAL,
            message="Error occurred",
            context=context,
        )

        assert alert.context == context

    def test_get_key(self):
        """Test alert key generation for deduplication."""
        alert1 = Alert(
            component="component1",
            severity=AlertSeverity.WARNING,
            message="Message 1",
        )
        alert2 = Alert(
            component="component1",
            severity=AlertSeverity.CRITICAL,
            message="Message 1",
        )
        alert3 = Alert(
            component="component2",
            severity=AlertSeverity.WARNING,
            message="Message 1",
        )

        # Same component and message = same key (severity doesn't matter)
        assert alert1.get_key() == alert2.get_key()
        # Different component = different key
        assert alert1.get_key() != alert3.get_key()

    def test_timestamp_auto_set(self):
        """Test that timestamp is automatically set."""
        alert = Alert(
            component="test", severity=AlertSeverity.INFO, message="Test"
        )

        assert alert.timestamp is not None
        assert isinstance(alert.timestamp, datetime)


class TestAlertManager:
    """Test AlertManager class."""

    def test_initialization(self):
        """Test alert manager initialization."""
        manager = AlertManager(dedup_window_minutes=10)

        assert manager.dedup_window == timedelta(minutes=10)
        assert manager.smtp_enabled is False

    def test_initialization_with_smtp(self):
        """Test initialization with SMTP config."""
        smtp_config = {
            "host": "smtp.gmail.com",
            "port": 587,
            "username": "test@example.com",
            "password": "password",
            "from_email": "test@example.com",
            "to_emails": ["recipient@example.com"],
        }
        manager = AlertManager(smtp_enabled=True, smtp_config=smtp_config)

        assert manager.smtp_enabled is True
        assert manager.smtp_config == smtp_config

    def test_send_alert_basic(self, capsys):
        """Test sending a basic alert."""
        manager = AlertManager()

        manager.send_alert(
            component="test_component",
            severity=AlertSeverity.WARNING,
            message="Test alert message",
        )

        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "test_component" in captured.out
        assert "Test alert message" in captured.out

    def test_send_alert_with_context(self, capsys):
        """Test sending alert with context."""
        manager = AlertManager()

        manager.send_alert(
            component="test",
            severity=AlertSeverity.CRITICAL,
            message="Critical error",
            context={"error_code": 500},
        )

        captured = capsys.readouterr()
        assert "CRITICAL" in captured.out
        assert "error_code" in captured.out

    def test_alert_deduplication(self, capsys):
        """Test that duplicate alerts within window are suppressed."""
        manager = AlertManager(dedup_window_minutes=15)

        # Send first alert
        manager.send_alert(
            component="test", severity=AlertSeverity.WARNING, message="Test"
        )

        # Send duplicate alert immediately
        manager.send_alert(
            component="test", severity=AlertSeverity.WARNING, message="Test"
        )

        captured = capsys.readouterr()
        # Should only see one alert in output
        assert captured.out.count("WARNING") == 1

    def test_alert_deduplication_different_components(self, capsys):
        """Test that alerts from different components are not deduplicated."""
        manager = AlertManager()

        manager.send_alert(
            component="comp1", severity=AlertSeverity.WARNING, message="Test"
        )
        manager.send_alert(
            component="comp2", severity=AlertSeverity.WARNING, message="Test"
        )

        captured = capsys.readouterr()
        # Should see two alerts (different components)
        assert captured.out.count("WARNING") == 2

    def test_alert_deduplication_window_expiry(self, capsys):
        """Test that alerts are sent after deduplication window expires."""
        # Very short window for testing
        manager = AlertManager(dedup_window_minutes=0)

        manager.send_alert(
            component="test", severity=AlertSeverity.WARNING, message="Test"
        )

        # Wait for window to expire
        time.sleep(0.01)

        manager.send_alert(
            component="test", severity=AlertSeverity.WARNING, message="Test"
        )

        captured = capsys.readouterr()
        # Should see two alerts (window expired)
        assert captured.out.count("WARNING") == 2

    def test_check_health_results_healthy(self, capsys):
        """Test that healthy results don't trigger alerts."""
        manager = AlertManager()

        results = [
            HealthCheckResult(
                component="test", status=HealthStatus.HEALTHY, message="OK"
            )
        ]

        manager.check_health_results(results)

        captured = capsys.readouterr()
        # Should not trigger any alerts
        assert "ALERT" not in captured.out

    def test_check_health_results_degraded(self, capsys):
        """Test that degraded results trigger warning alerts."""
        manager = AlertManager()

        results = [
            HealthCheckResult(
                component="test",
                status=HealthStatus.DEGRADED,
                message="Performance degraded",
            )
        ]

        manager.check_health_results(results)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "degraded" in captured.out.lower()

    def test_check_health_results_unhealthy(self, capsys):
        """Test that unhealthy results trigger critical alerts."""
        manager = AlertManager()

        results = [
            HealthCheckResult(
                component="test", status=HealthStatus.UNHEALTHY, message="Failed"
            )
        ]

        manager.check_health_results(results)

        captured = capsys.readouterr()
        assert "CRITICAL" in captured.out
        assert "Failed" in captured.out

    def test_deliver_console(self, capsys):
        """Test console alert delivery."""
        manager = AlertManager()

        alert = Alert(
            component="test",
            severity=AlertSeverity.WARNING,
            message="Test message",
        )

        manager._deliver_console(alert)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "test" in captured.out
        assert "Test message" in captured.out

    def test_deliver_email_missing_config(self):
        """Test that email delivery skips when config incomplete."""
        manager = AlertManager(smtp_enabled=True, smtp_config={})

        alert = Alert(
            component="test", severity=AlertSeverity.CRITICAL, message="Test"
        )

        # Should not raise exception
        manager._deliver_email(alert)

    def test_deliver_email_with_complete_config(self):
        """Test email delivery with complete config."""
        smtp_config = {
            "host": "smtp.gmail.com",
            "port": 587,
            "username": "test@example.com",
            "password": "password",
            "from_email": "test@example.com",
            "to_emails": ["recipient@example.com"],
        }
        manager = AlertManager(smtp_enabled=True, smtp_config=smtp_config)

        alert = Alert(
            component="test", severity=AlertSeverity.CRITICAL, message="Test"
        )

        with patch("smtplib.SMTP") as mock_smtp:
            mock_server = Mock()
            mock_smtp.return_value.__enter__.return_value = mock_server

            manager._deliver_email(alert)

            # Verify SMTP calls
            mock_smtp.assert_called_once()
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once()
            mock_server.send_message.assert_called_once()

    def test_deliver_email_error_handling(self):
        """Test that email delivery handles errors gracefully."""
        smtp_config = {
            "host": "smtp.gmail.com",
            "port": 587,
            "username": "test@example.com",
            "password": "password",
            "from_email": "test@example.com",
            "to_emails": ["recipient@example.com"],
        }
        manager = AlertManager(smtp_enabled=True, smtp_config=smtp_config)

        alert = Alert(
            component="test", severity=AlertSeverity.CRITICAL, message="Test"
        )

        with patch("smtplib.SMTP", side_effect=Exception("SMTP error")):
            # Should not raise exception
            manager._deliver_email(alert)


class TestLogMonitor:
    """Test LogMonitor class."""

    def test_initialization(self):
        """Test log monitor initialization."""
        mock_alert_mgr = Mock()
        monitor = LogMonitor(alert_manager=mock_alert_mgr, log_file="test.log")

        assert monitor.alert_manager == mock_alert_mgr
        assert monitor.log_file == "test.log"

    def test_scan_recent_logs_no_file(self):
        """Test scanning when log file doesn't exist."""
        mock_alert_mgr = Mock()
        monitor = LogMonitor(
            alert_manager=mock_alert_mgr, log_file="nonexistent.log"
        )

        # Should not raise exception
        monitor.scan_recent_logs()

    def test_scan_recent_logs_error_entries(self):
        """Test scanning detects ERROR entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"

            # Write log entries
            log_entries = [
                {
                    "timestamp": "2025-10-27T10:00:00Z",
                    "level": "INFO",
                    "component": "test",
                    "message": "Normal message",
                },
                {
                    "timestamp": "2025-10-27T10:01:00Z",
                    "level": "ERROR",
                    "component": "test.error",
                    "message": "Error message",
                },
            ]

            with open(log_file, "w") as f:
                for entry in log_entries:
                    f.write(json.dumps(entry) + "\n")

            mock_alert_mgr = Mock()
            monitor = LogMonitor(
                alert_manager=mock_alert_mgr, log_file=str(log_file)
            )

            monitor.scan_recent_logs()

            # Should have sent alert for ERROR
            mock_alert_mgr.send_alert.assert_called_once()
            call_args = mock_alert_mgr.send_alert.call_args
            assert call_args[1]["severity"] == AlertSeverity.WARNING

    def test_scan_recent_logs_critical_entries(self):
        """Test scanning detects CRITICAL entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"

            log_entries = [
                {
                    "timestamp": "2025-10-27T10:00:00Z",
                    "level": "CRITICAL",
                    "component": "test.critical",
                    "message": "Critical failure",
                },
            ]

            with open(log_file, "w") as f:
                for entry in log_entries:
                    f.write(json.dumps(entry) + "\n")

            mock_alert_mgr = Mock()
            monitor = LogMonitor(
                alert_manager=mock_alert_mgr, log_file=str(log_file)
            )

            monitor.scan_recent_logs()

            # Should have sent alert with CRITICAL severity
            mock_alert_mgr.send_alert.assert_called_once()
            call_args = mock_alert_mgr.send_alert.call_args
            assert call_args[1]["severity"] == AlertSeverity.CRITICAL

    def test_scan_recent_logs_ignores_info(self):
        """Test scanning ignores INFO and WARNING entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"

            log_entries = [
                {
                    "timestamp": "2025-10-27T10:00:00Z",
                    "level": "INFO",
                    "component": "test",
                    "message": "Info message",
                },
                {
                    "timestamp": "2025-10-27T10:01:00Z",
                    "level": "WARNING",
                    "component": "test",
                    "message": "Warning message",
                },
            ]

            with open(log_file, "w") as f:
                for entry in log_entries:
                    f.write(json.dumps(entry) + "\n")

            mock_alert_mgr = Mock()
            monitor = LogMonitor(
                alert_manager=mock_alert_mgr, log_file=str(log_file)
            )

            monitor.scan_recent_logs()

            # Should not send any alerts
            mock_alert_mgr.send_alert.assert_not_called()

    def test_scan_recent_logs_handles_invalid_json(self):
        """Test scanning handles non-JSON lines gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"

            with open(log_file, "w") as f:
                f.write("Not a JSON line\n")
                f.write("Another invalid line\n")

            mock_alert_mgr = Mock()
            monitor = LogMonitor(
                alert_manager=mock_alert_mgr, log_file=str(log_file)
            )

            # Should not raise exception
            monitor.scan_recent_logs()

    def test_scan_recent_logs_tracks_position(self):
        """Test that log monitor tracks last read position."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"

            # Write initial entries
            with open(log_file, "w") as f:
                f.write(
                    json.dumps(
                        {
                            "level": "ERROR",
                            "component": "test",
                            "message": "Error 1",
                        }
                    )
                    + "\n"
                )

            mock_alert_mgr = Mock()
            monitor = LogMonitor(
                alert_manager=mock_alert_mgr, log_file=str(log_file)
            )

            # First scan
            monitor.scan_recent_logs()
            assert mock_alert_mgr.send_alert.call_count == 1

            # Append new entry
            with open(log_file, "a") as f:
                f.write(
                    json.dumps(
                        {
                            "level": "ERROR",
                            "component": "test",
                            "message": "Error 2",
                        }
                    )
                    + "\n"
                )

            # Second scan should only see new entry
            monitor.scan_recent_logs()
            assert mock_alert_mgr.send_alert.call_count == 2


class TestIntegration:
    """Integration tests for alerting system."""

    def test_end_to_end_alerting(self, capsys):
        """Test complete alerting flow."""
        manager = AlertManager()

        # Simulate health check failure
        results = [
            HealthCheckResult(
                component="data_pipeline",
                status=HealthStatus.UNHEALTHY,
                message="Connection failed",
                metrics={"latency_ms": 5000.0},
            )
        ]

        manager.check_health_results(results)

        captured = capsys.readouterr()
        assert "CRITICAL" in captured.out
        assert "data_pipeline" in captured.out
        assert "Connection failed" in captured.out
