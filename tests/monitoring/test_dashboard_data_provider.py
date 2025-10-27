"""Tests for DashboardDataProvider."""

import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime
import tempfile
from pathlib import Path

from src.monitoring.dashboard_data_provider import DashboardDataProvider
from src.monitoring.alert_store import AlertStore
from src.monitoring.pnl_store import PnLStore
from src.monitoring.alerting import Alert, AlertSeverity
from src.risk.circuit_breaker import CircuitBreakerState
from src.risk.position_tracker import Position
from src.risk.metrics_calculator import RiskMetrics
from src.monitoring.health_checker import HealthCheckResult, HealthStatus


@pytest.fixture
def temp_dbs():
    """Create temporary databases for testing."""
    with tempfile.NamedTemporaryFile(suffix="_alert.db", delete=False) as f1, \
         tempfile.NamedTemporaryFile(suffix="_pnl.db", delete=False) as f2:
        alert_db = f1.name
        pnl_db = f2.name

    yield alert_db, pnl_db

    Path(alert_db).unlink(missing_ok=True)
    Path(pnl_db).unlink(missing_ok=True)


@pytest.fixture
def mock_risk_manager():
    """Create mock RiskManager."""
    mock_rm = Mock()
    mock_rm.get_system_status.return_value = {
        "circuit_breaker": {
            "is_active": False,
            "high_water_mark": 100000.0,
            "current_value": 100000.0,
            "drawdown_pct": 0.0
        },
        "positions": [],
        "exposure": {
            "gross": 50000.0,
            "net": 5000.0,
            "long": 27500.0,
            "short": -22500.0,
            "total_pnl": 2500.0
        }
    }

    # Mock circuit breaker
    mock_cb_state = CircuitBreakerState(
        is_active=False,
        high_water_mark=100000.0,
        current_value=100000.0,
        drawdown_pct=0.0,
        trigger_timestamp=None,
        trigger_reason=""
    )
    mock_rm.get_circuit_breaker_state.return_value = mock_cb_state

    mock_cb = Mock()
    mock_cb.get_distance_to_trigger.return_value = 0.05
    mock_cb.current_value = 100000.0
    mock_rm.circuit_breaker = mock_cb

    # Mock position tracker
    mock_position_tracker = Mock()
    mock_position_tracker.get_all_positions.return_value = []
    mock_rm.position_tracker = mock_position_tracker

    # Mock config
    mock_config = Mock()
    mock_config.get_per_strategy_limit_pct.return_value = 5.0
    mock_config.get_per_asset_limit_pct.return_value = 10.0
    mock_config.get_gross_exposure_limit_pct.return_value = 200.0
    mock_config.get_net_exposure_limit_pct.return_value = 20.0
    mock_rm.config = mock_config

    # Mock portfolio exposure
    mock_rm.get_portfolio_exposure.return_value = {
        "gross": 50000.0,
        "net": 5000.0,
        "long": 27500.0,
        "short": -22500.0,
        "total_pnl": 2500.0
    }

    return mock_rm


@pytest.fixture
def mock_health_checker():
    """Create mock HealthChecker."""
    mock_hc = Mock()
    mock_hc.check_all.return_value = [
        HealthCheckResult(
            component="test_component",
            status=HealthStatus.HEALTHY,
            message="All good",
            metrics=None,
            timestamp=datetime.now()
        )
    ]
    return mock_hc


@pytest.fixture
def data_provider(temp_dbs, mock_risk_manager, mock_health_checker):
    """Create DashboardDataProvider with mocks."""
    alert_db, pnl_db = temp_dbs
    alert_store = AlertStore(db_path=alert_db)
    pnl_store = PnLStore(db_path=pnl_db)

    return DashboardDataProvider(
        risk_manager=mock_risk_manager,
        health_checker=mock_health_checker,
        alert_store=alert_store,
        pnl_store=pnl_store
    )


def test_get_system_status(data_provider):
    """Test getting system status."""
    status = data_provider.get_system_status()

    assert "timestamp" in status
    assert "circuit_breaker" in status
    assert "positions" in status
    assert "exposure" in status
    assert "health" in status
    assert status["health"]["overall"] == "healthy"


def test_get_circuit_breaker_state(data_provider):
    """Test getting circuit breaker state."""
    cb_state = data_provider.get_circuit_breaker_state()

    assert cb_state["is_active"] is False
    assert cb_state["high_water_mark"] == 100000.0
    assert cb_state["current_value"] == 100000.0
    assert cb_state["drawdown_pct"] == 0.0
    assert cb_state["distance_to_trigger"] == 0.05


def test_get_risk_metrics_insufficient_data(data_provider, mock_risk_manager):
    """Test risk metrics with insufficient data."""
    mock_risk_manager.calculate_risk_metrics.return_value = None

    metrics = data_provider.get_risk_metrics()

    assert metrics["status"] == "insufficient_data"
    assert "message" in metrics


def test_get_risk_metrics_available(data_provider, mock_risk_manager):
    """Test risk metrics when available."""
    mock_metrics = RiskMetrics(
        timestamp=datetime.now(),
        var_95=-2500.0,
        cvar_95=-3200.0,
        sharpe_30d=1.5,
        sharpe_90d=1.3,
        sharpe_365d=1.2,
        beta=0.8,
        volatility=0.15,
        max_drawdown=0.08
    )
    mock_risk_manager.calculate_risk_metrics.return_value = mock_metrics

    metrics = data_provider.get_risk_metrics()

    assert metrics["status"] == "available"
    assert metrics["var_95"] == -2500.0
    assert metrics["sharpe_30d"] == 1.5


def test_get_risk_metrics_error(data_provider, mock_risk_manager):
    """Test risk metrics with error."""
    mock_risk_manager.calculate_risk_metrics.side_effect = Exception("Test error")

    metrics = data_provider.get_risk_metrics()

    assert metrics["status"] == "error"
    assert "Test error" in metrics["message"]


def test_get_positions(data_provider, mock_risk_manager):
    """Test getting positions."""
    mock_position = Position(
        symbol="AAPL",
        asset_class="equity",
        strategy_id="pairs_trading",
        quantity=100.0,
        entry_price=150.0,
        current_price=155.0,
        timestamp=datetime.now()
    )
    mock_risk_manager.position_tracker.get_all_positions.return_value = [mock_position]

    positions = data_provider.get_positions()

    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["quantity"] == 100.0
    assert positions[0]["unrealized_pnl"] == 500.0  # (155-150)*100


def test_get_exposure_summary(data_provider):
    """Test getting exposure summary."""
    exposure = data_provider.get_exposure_summary()

    assert exposure["gross_exposure"] == 50000.0
    assert exposure["net_exposure"] == 5000.0
    assert exposure["long_exposure"] == 27500.0
    assert exposure["short_exposure"] == -22500.0
    assert exposure["total_pnl"] == 2500.0


def test_get_limit_utilization_no_positions(data_provider):
    """Test limit utilization with no positions."""
    limits = data_provider.get_limit_utilization()

    assert limits["gross_exposure"]["utilization_pct"] >= 0
    assert limits["net_exposure"]["utilization_pct"] >= 0


def test_get_recent_alerts(data_provider):
    """Test getting recent alerts."""
    # Store some alerts
    alert = Alert("test", AlertSeverity.WARNING, "Test alert", timestamp=datetime.now())
    data_provider.alert_store.store_alert(alert)

    alerts = data_provider.get_recent_alerts(limit=10)

    assert len(alerts) == 1
    assert alerts[0]["component"] == "test"
    assert alerts[0]["severity"] == "warning"


def test_get_alert_summary(data_provider):
    """Test getting alert summary."""
    # Store alerts
    data_provider.alert_store.store_alert(Alert("c1", AlertSeverity.INFO, "msg", timestamp=datetime.now()))
    data_provider.alert_store.store_alert(Alert("c2", AlertSeverity.CRITICAL, "msg", timestamp=datetime.now()))

    summary = data_provider.get_alert_summary(hours=24)

    assert "by_severity" in summary
    assert "by_component" in summary
    assert summary["time_window_hours"] == 24


def test_get_pnl_history(data_provider):
    """Test getting P&L history."""
    # Store P&L snapshot
    data_provider.pnl_store.store_portfolio_snapshot(100000.0, 100000.0, 0.0)

    pnl_history = data_provider.get_pnl_history(hours=24)

    assert "time_series" in pnl_history
    assert pnl_history["time_window_hours"] == 24
    assert len(pnl_history["time_series"]) == 1


def test_get_risk_metrics_history(data_provider):
    """Test getting risk metrics history."""
    # Store metrics snapshot
    data_provider.pnl_store.store_risk_metrics(var_95=-2500.0, sharpe_30d=1.5)

    metrics_history = data_provider.get_risk_metrics_history(hours=24)

    assert "time_series" in metrics_history
    assert metrics_history["time_window_hours"] == 24
    assert len(metrics_history["time_series"]) == 1


def test_health_status_degraded(data_provider, mock_health_checker):
    """Test system status when health is degraded."""
    mock_health_checker.check_all.return_value = [
        HealthCheckResult("comp1", HealthStatus.HEALTHY, "OK", None, datetime.now()),
        HealthCheckResult("comp2", HealthStatus.DEGRADED, "Slow", None, datetime.now())
    ]

    status = data_provider.get_system_status()

    assert status["health"]["overall"] == "degraded"
    assert len(status["health"]["components"]) == 2
