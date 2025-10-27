"""Tests for AlertStore persistence layer."""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile

from src.monitoring.alert_store import AlertStore
from src.monitoring.alerting import Alert, AlertSeverity


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def alert_store(temp_db):
    """Create AlertStore instance with temporary database."""
    return AlertStore(db_path=temp_db)


def test_alert_store_initialization(alert_store):
    """Test AlertStore initialization creates schema."""
    assert alert_store.db_path.exists()
    assert alert_store.get_total_count() == 0


def test_store_alert(alert_store):
    """Test storing alert."""
    alert = Alert(
        component="test_component",
        severity=AlertSeverity.WARNING,
        message="Test alert",
        context={"key": "value"},
        timestamp=datetime.now()
    )

    alert_id = alert_store.store_alert(alert)
    assert alert_id > 0
    assert alert_store.get_total_count() == 1


def test_get_recent_alerts(alert_store):
    """Test retrieving recent alerts."""
    # Store multiple alerts
    for i in range(5):
        alert = Alert(
            component=f"component_{i}",
            severity=AlertSeverity.INFO,
            message=f"Alert {i}",
            timestamp=datetime.now()
        )
        alert_store.store_alert(alert)

    # Retrieve alerts
    alerts = alert_store.get_recent_alerts(limit=3)
    assert len(alerts) == 3
    assert alerts[0].component == "component_4"  # Most recent first


def test_filter_by_component(alert_store):
    """Test filtering alerts by component."""
    alert_store.store_alert(Alert("comp1", AlertSeverity.INFO, "msg1", timestamp=datetime.now()))
    alert_store.store_alert(Alert("comp2", AlertSeverity.INFO, "msg2", timestamp=datetime.now()))
    alert_store.store_alert(Alert("comp1", AlertSeverity.INFO, "msg3", timestamp=datetime.now()))

    comp1_alerts = alert_store.get_recent_alerts(component="comp1")
    assert len(comp1_alerts) == 2
    assert all(a.component == "comp1" for a in comp1_alerts)


def test_filter_by_severity(alert_store):
    """Test filtering alerts by severity."""
    alert_store.store_alert(Alert("comp", AlertSeverity.INFO, "msg1", timestamp=datetime.now()))
    alert_store.store_alert(Alert("comp", AlertSeverity.CRITICAL, "msg2", timestamp=datetime.now()))
    alert_store.store_alert(Alert("comp", AlertSeverity.INFO, "msg3", timestamp=datetime.now()))

    critical_alerts = alert_store.get_recent_alerts(severity=AlertSeverity.CRITICAL)
    assert len(critical_alerts) == 1
    assert critical_alerts[0].severity == AlertSeverity.CRITICAL


def test_filter_by_hours(alert_store):
    """Test filtering alerts by time window."""
    # Store old alert
    old_alert = Alert("comp", AlertSeverity.INFO, "old", timestamp=datetime.now() - timedelta(hours=25))
    alert_store.store_alert(old_alert)

    # Store recent alert
    recent_alert = Alert("comp", AlertSeverity.INFO, "recent", timestamp=datetime.now())
    alert_store.store_alert(recent_alert)

    # Get alerts from last 24 hours
    alerts = alert_store.get_recent_alerts(hours=24)
    assert len(alerts) == 1
    assert alerts[0].message == "recent"


def test_get_alert_counts_by_severity(alert_store):
    """Test getting alert counts grouped by severity."""
    alert_store.store_alert(Alert("comp", AlertSeverity.INFO, "msg", timestamp=datetime.now()))
    alert_store.store_alert(Alert("comp", AlertSeverity.INFO, "msg", timestamp=datetime.now()))
    alert_store.store_alert(Alert("comp", AlertSeverity.WARNING, "msg", timestamp=datetime.now()))
    alert_store.store_alert(Alert("comp", AlertSeverity.CRITICAL, "msg", timestamp=datetime.now()))

    counts = alert_store.get_alert_counts_by_severity(hours=24)
    assert counts["info"] == 2
    assert counts["warning"] == 1
    assert counts["critical"] == 1


def test_get_alert_counts_by_component(alert_store):
    """Test getting alert counts grouped by component."""
    alert_store.store_alert(Alert("comp1", AlertSeverity.INFO, "msg", timestamp=datetime.now()))
    alert_store.store_alert(Alert("comp1", AlertSeverity.INFO, "msg", timestamp=datetime.now()))
    alert_store.store_alert(Alert("comp2", AlertSeverity.INFO, "msg", timestamp=datetime.now()))

    counts = alert_store.get_alert_counts_by_component(hours=24)
    assert counts["comp1"] == 2
    assert counts["comp2"] == 1


def test_purge_old_alerts(alert_store):
    """Test purging old alerts."""
    # Store old alerts
    for i in range(3):
        alert = Alert(
            component="comp",
            severity=AlertSeverity.INFO,
            message=f"old_{i}",
            timestamp=datetime.now() - timedelta(days=31)
        )
        alert_store.store_alert(alert)

    # Store recent alert
    alert_store.store_alert(Alert("comp", AlertSeverity.INFO, "recent", timestamp=datetime.now()))

    # Purge alerts older than 30 days
    deleted = alert_store.purge_old_alerts(days=30)
    assert deleted == 3
    assert alert_store.get_total_count() == 1


def test_alert_context_serialization(alert_store):
    """Test alert context is properly serialized/deserialized."""
    context = {"key1": "value1", "key2": 123, "nested": {"key": "value"}}
    alert = Alert("comp", AlertSeverity.INFO, "msg", context=context, timestamp=datetime.now())

    alert_store.store_alert(alert)

    retrieved = alert_store.get_recent_alerts(limit=1)[0]
    assert retrieved.context == context
