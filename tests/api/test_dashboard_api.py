"""Tests for Dashboard API endpoints."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch
from datetime import datetime

from src.api.dashboard_api import app, initialize_components
from src.monitoring.alerting import AlertSeverity


@pytest.fixture
def mock_components():
    """Create mock components for API testing."""
    # Mock RiskManager
    mock_rm = Mock()
    mock_rm.manual_circuit_breaker_trigger = Mock()
    mock_rm.circuit_breaker = Mock()
    mock_rm.circuit_breaker.reset = Mock()

    # Mock HealthChecker
    mock_hc = Mock()

    # Mock AlertManager
    mock_am = Mock()

    return mock_rm, mock_hc, mock_am


@pytest.fixture
def client(mock_components):
    """Create test client with initialized components."""
    mock_rm, mock_hc, mock_am = mock_components
    initialize_components(mock_rm, mock_hc, mock_am)
    return TestClient(app)


def test_health_endpoint(client):
    """Test API health check endpoint."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data


@patch("src.api.dashboard_api.data_provider")
def test_get_system_status(mock_data_provider, client):
    """Test system status endpoint."""
    mock_data_provider.get_system_status.return_value = {
        "timestamp": datetime.now().isoformat(),
        "circuit_breaker": {"is_active": False},
        "positions": [],
        "exposure": {},
        "health": {"overall": "healthy"},
        "recent_alerts": {},
        "total_positions": 0
    }

    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "circuit_breaker" in data
    assert "positions" in data


@patch("src.api.dashboard_api.data_provider")
def test_get_risk_metrics(mock_data_provider, client):
    """Test risk metrics endpoint."""
    mock_data_provider.get_risk_metrics.return_value = {
        "status": "available",
        "var_95": -2500.0,
        "cvar_95": -3200.0,
        "sharpe_30d": 1.5
    }

    response = client.get("/api/risk/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "available"
    assert data["var_95"] == -2500.0


@patch("src.api.dashboard_api.data_provider")
def test_get_circuit_breaker_state(mock_data_provider, client):
    """Test circuit breaker state endpoint."""
    mock_data_provider.get_circuit_breaker_state.return_value = {
        "is_active": False,
        "high_water_mark": 100000.0,
        "current_value": 100000.0,
        "drawdown_pct": 0.0,
        "distance_to_trigger": 0.05
    }

    response = client.get("/api/circuit-breaker")
    assert response.status_code == 200
    data = response.json()
    assert data["is_active"] is False
    assert data["distance_to_trigger"] == 0.05


@patch("src.api.dashboard_api.data_provider")
def test_get_positions(mock_data_provider, client):
    """Test positions endpoint."""
    mock_data_provider.get_positions.return_value = [
        {
            "symbol": "AAPL",
            "quantity": 100.0,
            "unrealized_pnl": 500.0
        }
    ]

    response = client.get("/api/positions")
    assert response.status_code == 200
    positions = response.json()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"


@patch("src.api.dashboard_api.data_provider")
def test_get_exposure(mock_data_provider, client):
    """Test exposure endpoint."""
    mock_data_provider.get_exposure_summary.return_value = {
        "gross_exposure": 50000.0,
        "net_exposure": 5000.0
    }

    response = client.get("/api/exposure")
    assert response.status_code == 200
    data = response.json()
    assert data["gross_exposure"] == 50000.0


@patch("src.api.dashboard_api.data_provider")
def test_get_limit_utilization(mock_data_provider, client):
    """Test limit utilization endpoint."""
    mock_data_provider.get_limit_utilization.return_value = {
        "gross_exposure": {"utilization_pct": 25.0},
        "net_exposure": {"utilization_pct": 10.0}
    }

    response = client.get("/api/limits")
    assert response.status_code == 200
    data = response.json()
    assert "gross_exposure" in data
    assert "net_exposure" in data


@patch("src.api.dashboard_api.data_provider")
def test_get_recent_alerts(mock_data_provider, client):
    """Test recent alerts endpoint."""
    mock_data_provider.get_recent_alerts.return_value = [
        {
            "component": "test",
            "severity": "warning",
            "message": "Test alert"
        }
    ]

    response = client.get("/api/alerts/recent?limit=10")
    assert response.status_code == 200
    alerts = response.json()
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "warning"


@patch("src.api.dashboard_api.data_provider")
def test_get_alert_summary(mock_data_provider, client):
    """Test alert summary endpoint."""
    mock_data_provider.get_alert_summary.return_value = {
        "by_severity": {"warning": 2},
        "by_component": {"comp1": 1}
    }

    response = client.get("/api/alerts/summary?hours=24")
    assert response.status_code == 200
    data = response.json()
    assert "by_severity" in data
    assert "by_component" in data


@patch("src.api.dashboard_api.data_provider")
def test_get_pnl_history(mock_data_provider, client):
    """Test P&L history endpoint."""
    mock_data_provider.get_pnl_history.return_value = {
        "time_series": [],
        "time_window_hours": 24
    }

    response = client.get("/api/performance/pnl?hours=24")
    assert response.status_code == 200
    data = response.json()
    assert "time_series" in data


@patch("src.api.dashboard_api.audit_logger")
def test_get_recent_overrides(mock_audit_logger, client):
    """Test recent overrides endpoint."""
    mock_audit_logger.get_recent_overrides.return_value = [
        {
            "action_type": "circuit_breaker_trigger",
            "operator": "user1",
            "success": True
        }
    ]

    response = client.get("/api/audit/recent?limit=50")
    assert response.status_code == 200
    overrides = response.json()
    assert len(overrides) == 1


@patch("src.api.dashboard_api.audit_logger")
@patch("src.api.dashboard_api.data_provider")
@patch("src.api.dashboard_api.risk_manager")
def test_trigger_circuit_breaker(mock_rm, mock_dp, mock_audit, client):
    """Test manual circuit breaker trigger endpoint."""
    mock_dp.get_circuit_breaker_state.return_value = {
        "is_active": False,
        "high_water_mark": 100000.0
    }

    payload = {
        "operator": "user123",
        "reason": "Emergency market conditions"
    }

    response = client.post("/api/override/circuit-breaker/trigger", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "Circuit breaker triggered" in data["message"]

    # Verify audit log was called
    mock_audit.log_override.assert_called_once()


@patch("src.api.dashboard_api.audit_logger")
@patch("src.api.dashboard_api.data_provider")
@patch("src.api.dashboard_api.risk_manager")
def test_reset_circuit_breaker(mock_rm, mock_dp, mock_audit, client):
    """Test circuit breaker reset endpoint."""
    mock_dp.get_circuit_breaker_state.return_value = {
        "is_active": True,
        "high_water_mark": 100000.0
    }

    payload = {
        "operator": "user123",
        "justification": "Market stabilized"
    }

    response = client.post("/api/override/circuit-breaker/reset", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "reset successfully" in data["message"]

    # Verify audit log was called
    mock_audit.log_override.assert_called_once()


@patch("src.api.dashboard_api.risk_manager")
def test_trigger_circuit_breaker_error(mock_rm, client):
    """Test circuit breaker trigger with error."""
    mock_rm.manual_circuit_breaker_trigger.side_effect = Exception("Test error")

    payload = {
        "operator": "user123",
        "reason": "Emergency"
    }

    response = client.post("/api/override/circuit-breaker/trigger", json=payload)
    assert response.status_code == 500
    assert "Failed to trigger" in response.json()["detail"]


def test_override_validation_missing_fields(client):
    """Test override endpoint validation."""
    # Missing required fields
    response = client.post("/api/override/circuit-breaker/trigger", json={})
    assert response.status_code == 422  # Validation error


def test_serve_dashboard_html(client):
    """Test serving dashboard HTML."""
    response = client.get("/")
    assert response.status_code == 200
    # Should return HTML content (either from file or fallback)
    assert "text/html" in response.headers["content-type"]
