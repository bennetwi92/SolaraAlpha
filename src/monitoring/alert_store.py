"""
Alert History Persistence Layer

Stores alerts in SQLite database for dashboard querying and audit trail.
Provides time-series alert data with filtering by component, severity, and time range.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict

from src.monitoring.alerting import Alert, AlertSeverity


class AlertStore:
    """Persistent storage for alert history with query capabilities."""

    def __init__(self, db_path: str = "logs/alerts.db"):
        """Initialize alert store with SQLite database.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    component TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context TEXT,
                    timestamp TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indices for common queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON alerts(timestamp DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_component
                ON alerts(component)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_severity
                ON alerts(severity)
            """)

            conn.commit()

    def store_alert(self, alert: Alert) -> int:
        """Store alert in database.

        Args:
            alert: Alert to store

        Returns:
            Alert ID from database
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO alerts (component, severity, message, context, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    alert.component,
                    alert.severity.value,
                    alert.message,
                    json.dumps(alert.context) if alert.context else None,
                    alert.timestamp.isoformat()
                )
            )
            conn.commit()
            return cursor.lastrowid

    def get_recent_alerts(
        self,
        limit: int = 100,
        component: Optional[str] = None,
        severity: Optional[AlertSeverity] = None,
        hours: Optional[int] = None
    ) -> List[Alert]:
        """Query recent alerts with optional filtering.

        Args:
            limit: Maximum number of alerts to return
            component: Filter by component name
            severity: Filter by severity level
            hours: Only return alerts from last N hours

        Returns:
            List of alerts matching criteria
        """
        query = "SELECT component, severity, message, context, timestamp FROM alerts WHERE 1=1"
        params = []

        if component:
            query += " AND component = ?"
            params.append(component)

        if severity:
            query += " AND severity = ?"
            params.append(severity.value)

        if hours:
            cutoff = datetime.now() - timedelta(hours=hours)
            query += " AND timestamp >= ?"
            params.append(cutoff.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        alerts = []
        for row in rows:
            component, severity_str, message, context_json, timestamp_str = row
            alerts.append(Alert(
                component=component,
                severity=AlertSeverity(severity_str),
                message=message,
                context=json.loads(context_json) if context_json else None,
                timestamp=datetime.fromisoformat(timestamp_str)
            ))

        return alerts

    def get_alert_counts_by_severity(self, hours: int = 24) -> Dict[str, int]:
        """Get alert counts grouped by severity for last N hours.

        Args:
            hours: Time window in hours

        Returns:
            Dict mapping severity to count
        """
        cutoff = datetime.now() - timedelta(hours=hours)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT severity, COUNT(*)
                FROM alerts
                WHERE timestamp >= ?
                GROUP BY severity
                """,
                (cutoff.isoformat(),)
            )
            rows = cursor.fetchall()

        return {severity: count for severity, count in rows}

    def get_alert_counts_by_component(self, hours: int = 24) -> Dict[str, int]:
        """Get alert counts grouped by component for last N hours.

        Args:
            hours: Time window in hours

        Returns:
            Dict mapping component to count
        """
        cutoff = datetime.now() - timedelta(hours=hours)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT component, COUNT(*)
                FROM alerts
                WHERE timestamp >= ?
                GROUP BY component
                """,
                (cutoff.isoformat(),)
            )
            rows = cursor.fetchall()

        return {component: count for component, count in rows}

    def purge_old_alerts(self, days: int = 30) -> int:
        """Delete alerts older than N days.

        Args:
            days: Age threshold in days

        Returns:
            Number of alerts deleted
        """
        cutoff = datetime.now() - timedelta(days=days)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM alerts WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            conn.commit()
            return cursor.rowcount

    def get_total_count(self) -> int:
        """Get total number of alerts in database.

        Returns:
            Total alert count
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM alerts")
            return cursor.fetchone()[0]
