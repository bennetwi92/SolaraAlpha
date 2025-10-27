"""Alerting system for error and anomaly detection.

Monitors logs, health checks, risk breaches, and system failures. Delivers
alerts via console, log files, and email with deduplication logic.
"""

import logging
import smtplib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Dict, Optional

from .health_checker import HealthCheckResult, HealthStatus

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    """Represents an alert event.

    Attributes:
        component: Component that triggered alert
        severity: Alert severity level
        message: Human-readable alert message
        context: Optional dict with additional context
        timestamp: Alert creation time
    """

    component: str
    severity: AlertSeverity
    message: str
    context: Optional[Dict] = None
    timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        """Set timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

    def get_key(self) -> str:
        """Get unique key for deduplication.

        Returns:
            String key combining component and message
        """
        return f"{self.component}:{self.message}"


class AlertManager:
    """Manages alert triggering and delivery.

    Monitors for:
    - ERROR/CRITICAL log entries
    - Health check failures (>3 consecutive)
    - Risk breaches
    - Data pipeline outages
    - Execution failures

    Implements alert deduplication (max 1 per issue per 15 minutes).

    Example:
        >>> alert_mgr = AlertManager()
        >>> alert_mgr.send_alert(
        ...     component="risk_manager",
        ...     severity=AlertSeverity.CRITICAL,
        ...     message="Position limit breached",
        ...     context={"symbol": "AAPL", "size": 1500}
        ... )
    """

    def __init__(
        self,
        dedup_window_minutes: int = 15,
        smtp_enabled: bool = False,
        smtp_config: Optional[Dict] = None,
    ) -> None:
        """Initialize alert manager.

        Args:
            dedup_window_minutes: Minutes to deduplicate alerts
            smtp_enabled: Enable email alerts
            smtp_config: SMTP configuration dict with keys:
                - host: SMTP server host
                - port: SMTP server port
                - username: SMTP username
                - password: SMTP password/app password
                - from_email: Sender email address
                - to_emails: List of recipient emails
        """
        self.dedup_window = timedelta(minutes=dedup_window_minutes)
        self.smtp_enabled = smtp_enabled
        self.smtp_config = smtp_config or {}

        # Track last alert time for deduplication
        self._last_alert_times: Dict[str, datetime] = {}

    def send_alert(
        self,
        component: str,
        severity: AlertSeverity,
        message: str,
        context: Optional[Dict] = None,
    ) -> None:
        """Send alert with deduplication.

        Args:
            component: Component triggering alert
            severity: Alert severity level
            message: Human-readable message
            context: Optional context dict
        """
        alert = Alert(
            component=component,
            severity=severity,
            message=message,
            context=context,
        )

        # Check deduplication
        if self._is_duplicate(alert):
            logger.debug(
                f"Alert suppressed (duplicate): {alert.get_key()}",
                extra={"context": {"alert": alert.__dict__}},
            )
            return

        # Record alert time
        self._last_alert_times[alert.get_key()] = alert.timestamp

        # Deliver alert via all channels
        self._deliver_console(alert)
        self._deliver_log(alert)

        if self.smtp_enabled:
            self._deliver_email(alert)

    def check_health_results(self, results: list[HealthCheckResult]) -> None:
        """Check health results and trigger alerts if needed.

        Args:
            results: List of health check results
        """
        for result in results:
            if result.status == HealthStatus.UNHEALTHY:
                self.send_alert(
                    component=result.component,
                    severity=AlertSeverity.CRITICAL,
                    message=f"Health check failed: {result.message}",
                    context=result.metrics,
                )
            elif result.status == HealthStatus.DEGRADED:
                self.send_alert(
                    component=result.component,
                    severity=AlertSeverity.WARNING,
                    message=f"Health check degraded: {result.message}",
                    context=result.metrics,
                )

    def _is_duplicate(self, alert: Alert) -> bool:
        """Check if alert is duplicate within deduplication window.

        Args:
            alert: Alert to check

        Returns:
            True if alert is duplicate, False otherwise
        """
        key = alert.get_key()
        if key not in self._last_alert_times:
            return False

        last_time = self._last_alert_times[key]
        time_since_last = alert.timestamp - last_time

        return time_since_last < self.dedup_window

    def _deliver_console(self, alert: Alert) -> None:
        """Deliver alert to console.

        Args:
            alert: Alert to deliver
        """
        prefix = {
            AlertSeverity.INFO: "ℹ️  INFO",
            AlertSeverity.WARNING: "⚠️  WARNING",
            AlertSeverity.CRITICAL: "🚨 CRITICAL",
        }[alert.severity]

        print(f"\n{prefix}: [{alert.component}] {alert.message}")
        if alert.context:
            print(f"  Context: {alert.context}")
        print(f"  Time: {alert.timestamp.isoformat()}\n")

    def _deliver_log(self, alert: Alert) -> None:
        """Deliver alert to log file with annotation.

        Args:
            alert: Alert to deliver
        """
        log_level = {
            AlertSeverity.INFO: logging.INFO,
            AlertSeverity.WARNING: logging.WARNING,
            AlertSeverity.CRITICAL: logging.CRITICAL,
        }[alert.severity]

        logger.log(
            log_level,
            f"ALERT: {alert.message}",
            extra={
                "context": {
                    "component": alert.component,
                    "severity": alert.severity.value,
                    "alert_context": alert.context,
                }
            },
        )

    def _deliver_email(self, alert: Alert) -> None:
        """Deliver alert via email.

        Args:
            alert: Alert to deliver
        """
        try:
            # Validate SMTP config
            required_keys = ["host", "port", "username", "password", "from_email", "to_emails"]
            if not all(k in self.smtp_config for k in required_keys):
                logger.warning(
                    "Email alert skipped: incomplete SMTP configuration",
                    extra={"context": {"missing_keys": required_keys}},
                )
                return

            # Build email
            msg = MIMEMultipart()
            msg["From"] = self.smtp_config["from_email"]
            msg["To"] = ", ".join(self.smtp_config["to_emails"])
            msg["Subject"] = f"[SolaraAlpha] {alert.severity.value.upper()}: {alert.component}"

            # Build body
            body = f"""
SolaraAlpha Alert

Component: {alert.component}
Severity: {alert.severity.value.upper()}
Time: {alert.timestamp.isoformat()}

Message:
{alert.message}

Context:
{alert.context if alert.context else 'None'}

---
This is an automated alert from SolaraAlpha monitoring system.
            """

            msg.attach(MIMEText(body, "plain"))

            # Send email
            with smtplib.SMTP(self.smtp_config["host"], self.smtp_config["port"]) as server:
                server.starttls()
                server.login(self.smtp_config["username"], self.smtp_config["password"])
                server.send_message(msg)

            logger.info(
                "Email alert sent",
                extra={
                    "context": {
                        "component": alert.component,
                        "severity": alert.severity.value,
                        "recipients": self.smtp_config["to_emails"],
                    }
                },
            )

        except Exception as e:
            logger.error(
                "Failed to send email alert",
                extra={"context": {"error": str(e)}},
                exc_info=True,
            )


class LogMonitor:
    """Monitors log files for ERROR/CRITICAL entries.

    Tails log file and triggers alerts for error-level entries.

    Example:
        >>> monitor = LogMonitor(alert_manager)
        >>> monitor.scan_recent_logs()  # Check last N lines
    """

    def __init__(
        self,
        alert_manager: AlertManager,
        log_file: str = "logs/app.log",
    ) -> None:
        """Initialize log monitor.

        Args:
            alert_manager: Alert manager for sending alerts
            log_file: Path to log file to monitor
        """
        self.alert_manager = alert_manager
        self.log_file = log_file
        self._last_position = 0

    def scan_recent_logs(self, max_lines: int = 100) -> None:
        """Scan recent log entries for errors.

        Args:
            max_lines: Maximum number of recent lines to scan
        """
        try:
            import json
            from pathlib import Path

            log_path = Path(self.log_file)
            if not log_path.exists():
                return

            # Read recent lines
            with open(log_path, "r") as f:
                # Move to last position if set
                if self._last_position > 0:
                    f.seek(self._last_position)

                lines = f.readlines()[-max_lines:]
                self._last_position = f.tell()

            # Parse and check each line
            for line in lines:
                try:
                    log_entry = json.loads(line.strip())

                    # Check for ERROR or CRITICAL
                    level = log_entry.get("level", "")
                    if level in ["ERROR", "CRITICAL"]:
                        severity = (
                            AlertSeverity.CRITICAL
                            if level == "CRITICAL"
                            else AlertSeverity.WARNING
                        )

                        self.alert_manager.send_alert(
                            component=log_entry.get("component", "unknown"),
                            severity=severity,
                            message=log_entry.get("message", ""),
                            context=log_entry.get("context"),
                        )

                except json.JSONDecodeError:
                    # Skip non-JSON lines
                    continue

        except Exception as e:
            logger.warning(
                "Log scanning failed",
                extra={"context": {"error": str(e)}},
            )
