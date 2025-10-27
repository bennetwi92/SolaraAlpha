"""Tests for AuditLogger."""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile

from src.monitoring.audit_logger import AuditLogger, OverrideAction


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def audit_logger(temp_db):
    """Create AuditLogger instance with temporary database."""
    return AuditLogger(db_path=temp_db)


def test_audit_logger_initialization(audit_logger):
    """Test AuditLogger initialization creates schema."""
    assert audit_logger.db_path.exists()
    assert audit_logger.get_total_count() == 0


def test_log_override_success(audit_logger):
    """Test logging successful override action."""
    log_id = audit_logger.log_override(
        action_type=OverrideAction.CIRCUIT_BREAKER_TRIGGER,
        operator="user123",
        reason="Emergency market conditions",
        parameters={"threshold": 0.1},
        previous_state={"is_active": False},
        new_state={"is_active": True},
        success=True
    )

    assert log_id > 0
    assert audit_logger.get_total_count() == 1


def test_log_override_failure(audit_logger):
    """Test logging failed override action."""
    log_id = audit_logger.log_override(
        action_type=OverrideAction.CIRCUIT_BREAKER_RESET,
        operator="user123",
        reason="Test reset",
        success=False,
        error_message="Insufficient permissions"
    )

    assert log_id > 0

    # Retrieve and verify
    logs = audit_logger.get_recent_overrides(limit=1)
    assert len(logs) == 1
    assert logs[0]["success"] is False
    assert logs[0]["error_message"] == "Insufficient permissions"


def test_get_recent_overrides(audit_logger):
    """Test retrieving recent override logs."""
    # Log multiple overrides
    for i in range(5):
        audit_logger.log_override(
            action_type=OverrideAction.CIRCUIT_BREAKER_TRIGGER,
            operator=f"user{i}",
            reason=f"Reason {i}",
            success=True
        )

    # Get recent 3
    logs = audit_logger.get_recent_overrides(limit=3)
    assert len(logs) == 3
    assert logs[0]["operator"] == "user4"  # Most recent first


def test_filter_by_action_type(audit_logger):
    """Test filtering by action type."""
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_TRIGGER, "user1", "reason1", success=True)
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_RESET, "user2", "reason2", success=True)
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_TRIGGER, "user3", "reason3", success=True)

    trigger_logs = audit_logger.get_recent_overrides(action_type=OverrideAction.CIRCUIT_BREAKER_TRIGGER)
    assert len(trigger_logs) == 2
    assert all(log["action_type"] == OverrideAction.CIRCUIT_BREAKER_TRIGGER.value for log in trigger_logs)


def test_filter_by_operator(audit_logger):
    """Test filtering by operator."""
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_TRIGGER, "alice", "reason1", success=True)
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_TRIGGER, "bob", "reason2", success=True)
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_TRIGGER, "alice", "reason3", success=True)

    alice_logs = audit_logger.get_recent_overrides(operator="alice")
    assert len(alice_logs) == 2
    assert all(log["operator"] == "alice" for log in alice_logs)


def test_filter_by_hours(audit_logger):
    """Test filtering by time window."""
    # Log old override
    old_timestamp = datetime.now() - timedelta(hours=25)
    audit_logger.log_override(
        OverrideAction.CIRCUIT_BREAKER_TRIGGER,
        "user1",
        "old",
        success=True,
        timestamp=old_timestamp
    )

    # Log recent override
    audit_logger.log_override(
        OverrideAction.CIRCUIT_BREAKER_TRIGGER,
        "user2",
        "recent",
        success=True
    )

    # Get last 24 hours
    logs = audit_logger.get_recent_overrides(hours=24)
    assert len(logs) == 1
    assert logs[0]["reason"] == "recent"


def test_get_override_counts_by_type(audit_logger):
    """Test getting override counts grouped by action type."""
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_TRIGGER, "user", "reason", success=True)
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_TRIGGER, "user", "reason", success=True)
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_RESET, "user", "reason", success=True)
    audit_logger.log_override(OverrideAction.LIMIT_ADJUSTMENT, "user", "reason", success=True)

    counts = audit_logger.get_override_counts_by_type(hours=24)
    assert counts[OverrideAction.CIRCUIT_BREAKER_TRIGGER.value] == 2
    assert counts[OverrideAction.CIRCUIT_BREAKER_RESET.value] == 1
    assert counts[OverrideAction.LIMIT_ADJUSTMENT.value] == 1


def test_get_override_counts_by_operator(audit_logger):
    """Test getting override counts grouped by operator."""
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_TRIGGER, "alice", "reason", success=True)
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_TRIGGER, "alice", "reason", success=True)
    audit_logger.log_override(OverrideAction.CIRCUIT_BREAKER_TRIGGER, "bob", "reason", success=True)

    counts = audit_logger.get_override_counts_by_operator(hours=24)
    assert counts["alice"] == 2
    assert counts["bob"] == 1


def test_purge_old_logs(audit_logger):
    """Test purging old audit logs."""
    # Log old overrides
    old_timestamp = datetime.now() - timedelta(days=400)
    for i in range(3):
        audit_logger.log_override(
            OverrideAction.CIRCUIT_BREAKER_TRIGGER,
            "user",
            f"old_{i}",
            success=True,
            timestamp=old_timestamp
        )

    # Log recent override
    audit_logger.log_override(
        OverrideAction.CIRCUIT_BREAKER_TRIGGER,
        "user",
        "recent",
        success=True
    )

    # Purge logs older than 365 days (default retention)
    deleted = audit_logger.purge_old_logs(days=365)
    assert deleted == 3
    assert audit_logger.get_total_count() == 1


def test_state_serialization(audit_logger):
    """Test state parameters are properly serialized/deserialized."""
    previous_state = {"is_active": False, "value": 100000.0}
    new_state = {"is_active": True, "value": 100000.0}
    parameters = {"manual": True, "threshold": 0.1}

    audit_logger.log_override(
        action_type=OverrideAction.CIRCUIT_BREAKER_TRIGGER,
        operator="user",
        reason="test",
        parameters=parameters,
        previous_state=previous_state,
        new_state=new_state,
        success=True
    )

    logs = audit_logger.get_recent_overrides(limit=1)
    assert logs[0]["parameters"] == parameters
    assert logs[0]["previous_state"] == previous_state
    assert logs[0]["new_state"] == new_state


def test_all_override_action_types(audit_logger):
    """Test logging all override action types."""
    actions = [
        OverrideAction.CIRCUIT_BREAKER_TRIGGER,
        OverrideAction.CIRCUIT_BREAKER_RESET,
        OverrideAction.LIMIT_ADJUSTMENT,
        OverrideAction.EMERGENCY_KILL_SWITCH,
        OverrideAction.POSITION_FORCE_CLOSE
    ]

    for action in actions:
        audit_logger.log_override(action, "user", "test", success=True)

    assert audit_logger.get_total_count() == len(actions)

    logs = audit_logger.get_recent_overrides(limit=10)
    logged_actions = {log["action_type"] for log in logs}
    assert logged_actions == {action.value for action in actions}
