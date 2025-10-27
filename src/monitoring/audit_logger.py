"""
Audit Logger for IRM Override Actions

Provides comprehensive audit trail for all manual override actions
including circuit breaker triggers/resets, limit adjustments, and
emergency kill switches. Ensures full accountability and compliance.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from enum import Enum


class OverrideAction(Enum):
    """Types of manual override actions."""
    CIRCUIT_BREAKER_TRIGGER = "circuit_breaker_trigger"
    CIRCUIT_BREAKER_RESET = "circuit_breaker_reset"
    LIMIT_ADJUSTMENT = "limit_adjustment"
    EMERGENCY_KILL_SWITCH = "emergency_kill_switch"
    POSITION_FORCE_CLOSE = "position_force_close"


class AuditLogger:
    """Audit trail logger for manual override actions."""

    def __init__(self, db_path: str = "logs/audit.db"):
        """Initialize audit logger with SQLite database.

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
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    parameters TEXT,
                    previous_state TEXT,
                    new_state TEXT,
                    success BOOLEAN NOT NULL,
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indices
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_operator ON audit_log(operator)")

            conn.commit()

    def log_override(
        self,
        action_type: OverrideAction,
        operator: str,
        reason: str,
        parameters: Optional[Dict[str, Any]] = None,
        previous_state: Optional[Dict[str, Any]] = None,
        new_state: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ) -> int:
        """Log a manual override action.

        Args:
            action_type: Type of override action
            operator: Identifier of person/system performing override
            reason: Justification for override action
            parameters: Action parameters (e.g., new limit values)
            previous_state: State before override
            new_state: State after override
            success: Whether action succeeded
            error_message: Error message if action failed
            timestamp: Override timestamp (defaults to now)

        Returns:
            Audit log entry ID
        """
        if timestamp is None:
            timestamp = datetime.now()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO audit_log (
                    timestamp, action_type, operator, reason,
                    parameters, previous_state, new_state,
                    success, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp.isoformat(),
                    action_type.value,
                    operator,
                    reason,
                    json.dumps(parameters) if parameters else None,
                    json.dumps(previous_state) if previous_state else None,
                    json.dumps(new_state) if new_state else None,
                    success,
                    error_message
                )
            )
            conn.commit()
            return cursor.lastrowid

    def get_recent_overrides(
        self,
        limit: int = 100,
        action_type: Optional[OverrideAction] = None,
        operator: Optional[str] = None,
        hours: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Query recent override actions.

        Args:
            limit: Maximum number of entries to return
            action_type: Filter by action type
            operator: Filter by operator
            hours: Only return actions from last N hours

        Returns:
            List of audit log entries
        """
        query = """
            SELECT id, timestamp, action_type, operator, reason,
                   parameters, previous_state, new_state,
                   success, error_message
            FROM audit_log
            WHERE 1=1
        """
        params = []

        if action_type:
            query += " AND action_type = ?"
            params.append(action_type.value)

        if operator:
            query += " AND operator = ?"
            params.append(operator)

        if hours:
            cutoff = datetime.now() - timedelta(hours=hours)
            query += " AND timestamp >= ?"
            params.append(cutoff.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "timestamp": row[1],
                "action_type": row[2],
                "operator": row[3],
                "reason": row[4],
                "parameters": json.loads(row[5]) if row[5] else None,
                "previous_state": json.loads(row[6]) if row[6] else None,
                "new_state": json.loads(row[7]) if row[7] else None,
                "success": bool(row[8]),
                "error_message": row[9]
            }
            for row in rows
        ]

    def get_override_counts_by_type(self, hours: int = 24) -> Dict[str, int]:
        """Get override counts grouped by action type.

        Args:
            hours: Time window in hours

        Returns:
            Dict mapping action type to count
        """
        cutoff = datetime.now() - timedelta(hours=hours)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT action_type, COUNT(*)
                FROM audit_log
                WHERE timestamp >= ?
                GROUP BY action_type
                """,
                (cutoff.isoformat(),)
            )
            rows = cursor.fetchall()

        return {action_type: count for action_type, count in rows}

    def get_override_counts_by_operator(self, hours: int = 24) -> Dict[str, int]:
        """Get override counts grouped by operator.

        Args:
            hours: Time window in hours

        Returns:
            Dict mapping operator to count
        """
        cutoff = datetime.now() - timedelta(hours=hours)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT operator, COUNT(*)
                FROM audit_log
                WHERE timestamp >= ?
                GROUP BY operator
                """,
                (cutoff.isoformat(),)
            )
            rows = cursor.fetchall()

        return {operator: count for operator, count in rows}

    def purge_old_logs(self, days: int = 365) -> int:
        """Delete audit logs older than N days.

        Note: Default is 365 days for compliance requirements.

        Args:
            days: Age threshold in days

        Returns:
            Number of entries deleted
        """
        cutoff = datetime.now() - timedelta(days=days)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM audit_log WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            conn.commit()
            return cursor.rowcount

    def get_total_count(self) -> int:
        """Get total number of audit log entries.

        Returns:
            Total entry count
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM audit_log")
            return cursor.fetchone()[0]
