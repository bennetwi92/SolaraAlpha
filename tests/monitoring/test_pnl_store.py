"""Tests for PnLStore time-series storage."""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile

from src.monitoring.pnl_store import PnLStore


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def pnl_store(temp_db):
    """Create PnLStore instance with temporary database."""
    return PnLStore(db_path=temp_db)


def test_pnl_store_initialization(pnl_store):
    """Test PnLStore initialization creates schema."""
    assert pnl_store.db_path.exists()
    assert pnl_store.get_latest_portfolio_value() is None


def test_store_portfolio_snapshot(pnl_store):
    """Test storing portfolio P&L snapshot."""
    snapshot_id = pnl_store.store_portfolio_snapshot(
        portfolio_value=100000.0,
        high_water_mark=105000.0,
        drawdown_pct=0.0476,
        daily_pnl=1500.0,
        daily_return_pct=0.015
    )

    assert snapshot_id > 0
    assert pnl_store.get_latest_portfolio_value() == 100000.0


def test_store_strategy_snapshot(pnl_store):
    """Test storing strategy P&L snapshot."""
    snapshot_id = pnl_store.store_strategy_snapshot(
        strategy_id="pairs_trading",
        strategy_value=25000.0,
        daily_pnl=500.0,
        daily_return_pct=0.02
    )

    assert snapshot_id > 0


def test_store_risk_metrics(pnl_store):
    """Test storing risk metrics snapshot."""
    snapshot_id = pnl_store.store_risk_metrics(
        var_95=-2500.0,
        cvar_95=-3200.0,
        sharpe_30d=1.5,
        sharpe_90d=1.3,
        sharpe_365d=1.2,
        beta=0.8,
        volatility=0.15,
        max_drawdown=0.08
    )

    assert snapshot_id > 0


def test_get_portfolio_pnl_history(pnl_store):
    """Test retrieving portfolio P&L history."""
    # Store multiple snapshots
    for i in range(5):
        timestamp = datetime.now() - timedelta(hours=i)
        pnl_store.store_portfolio_snapshot(
            portfolio_value=100000.0 + (i * 1000),
            high_water_mark=105000.0,
            drawdown_pct=0.0,
            timestamp=timestamp
        )

    # Get last 3 hours
    history = pnl_store.get_portfolio_pnl_history(hours=3)
    assert len(history) == 3

    # Check ordering (ascending by time)
    assert history[0]["portfolio_value"] > history[-1]["portfolio_value"]


def test_get_strategy_pnl_history(pnl_store):
    """Test retrieving strategy P&L history."""
    # Store snapshots for multiple strategies
    for i in range(3):
        timestamp = datetime.now() - timedelta(hours=i)
        pnl_store.store_strategy_snapshot(
            strategy_id="strategy1",
            strategy_value=25000.0 + (i * 100),
            timestamp=timestamp
        )
        pnl_store.store_strategy_snapshot(
            strategy_id="strategy2",
            strategy_value=30000.0 + (i * 200),
            timestamp=timestamp
        )

    # Get history for strategy1
    history = pnl_store.get_strategy_pnl_history("strategy1", hours=24)
    assert len(history) == 3
    assert all("strategy_value" in snap for snap in history)


def test_get_risk_metrics_history(pnl_store):
    """Test retrieving risk metrics history."""
    # Store multiple metric snapshots
    for i in range(4):
        timestamp = datetime.now() - timedelta(hours=i)
        pnl_store.store_risk_metrics(
            var_95=-2500.0 - (i * 100),
            sharpe_30d=1.5 - (i * 0.1),
            timestamp=timestamp
        )

    history = pnl_store.get_risk_metrics_history(hours=24)
    assert len(history) == 4
    assert all("var_95" in snap for snap in history)


def test_filter_by_time_window(pnl_store):
    """Test filtering snapshots by time window."""
    # Store old snapshot
    old_timestamp = datetime.now() - timedelta(hours=25)
    pnl_store.store_portfolio_snapshot(
        portfolio_value=95000.0,
        high_water_mark=100000.0,
        drawdown_pct=0.05,
        timestamp=old_timestamp
    )

    # Store recent snapshot
    recent_timestamp = datetime.now()
    pnl_store.store_portfolio_snapshot(
        portfolio_value=100000.0,
        high_water_mark=100000.0,
        drawdown_pct=0.0,
        timestamp=recent_timestamp
    )

    # Get last 24 hours
    history = pnl_store.get_portfolio_pnl_history(hours=24)
    assert len(history) == 1
    assert history[0]["portfolio_value"] == 100000.0


def test_purge_old_snapshots(pnl_store):
    """Test purging old snapshots."""
    # Store old snapshots
    old_timestamp = datetime.now() - timedelta(days=31)
    pnl_store.store_portfolio_snapshot(100000.0, 100000.0, 0.0, timestamp=old_timestamp)
    pnl_store.store_strategy_snapshot("s1", 25000.0, timestamp=old_timestamp)
    pnl_store.store_risk_metrics(var_95=-2500.0, timestamp=old_timestamp)

    # Store recent snapshots
    recent_timestamp = datetime.now()
    pnl_store.store_portfolio_snapshot(100000.0, 100000.0, 0.0, timestamp=recent_timestamp)
    pnl_store.store_strategy_snapshot("s1", 25000.0, timestamp=recent_timestamp)
    pnl_store.store_risk_metrics(var_95=-2500.0, timestamp=recent_timestamp)

    # Purge old snapshots
    portfolio_count, strategy_count, metrics_count = pnl_store.purge_old_snapshots(days=30)

    assert portfolio_count == 1
    assert strategy_count == 1
    assert metrics_count == 1

    # Verify only recent snapshots remain
    history = pnl_store.get_portfolio_pnl_history(hours=48)
    assert len(history) == 1


def test_snapshot_with_optional_fields(pnl_store):
    """Test storing snapshots with optional fields."""
    # Store without optional fields
    pnl_store.store_portfolio_snapshot(
        portfolio_value=100000.0,
        high_water_mark=100000.0,
        drawdown_pct=0.0
    )

    history = pnl_store.get_portfolio_pnl_history(hours=24)
    assert len(history) == 1
    assert history[0]["daily_pnl"] is None
    assert history[0]["daily_return_pct"] is None


def test_latest_portfolio_value(pnl_store):
    """Test getting latest portfolio value."""
    assert pnl_store.get_latest_portfolio_value() is None

    pnl_store.store_portfolio_snapshot(95000.0, 100000.0, 0.05)
    assert pnl_store.get_latest_portfolio_value() == 95000.0

    pnl_store.store_portfolio_snapshot(98000.0, 100000.0, 0.02)
    assert pnl_store.get_latest_portfolio_value() == 98000.0
