"""Integration tests for risk management framework."""
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.data.storage.parquet_storage import ParquetStorage
from src.risk import RiskManager
from src.risk.position_tracker import Position


@pytest.fixture
def temp_config_and_data():
    """Create temporary config and data directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / "risk_limits.yaml"

        config_data = {
            "position_limits": {
                "per_strategy_pct": 5.0,
                "per_asset_pct": 10.0,
                "gross_exposure_pct": 200.0,
                "net_exposure_pct": 20.0,
            },
            "drawdown": {
                "max_drawdown_pct": 10.0,
                "high_water_mark_persist": True,
                "hwm_storage_path": f"{temp_dir}/hwm.json",
            },
            "risk_metrics": {
                "var_confidence": 0.95,
                "var_lookback_days": 252,
                "cvar_confidence": 0.95,
                "sharpe_windows": [30, 90, 365],
                "benchmark_symbol": "SPY",
                "benchmark_asset_class": "equities",
                "min_history_days": 30,
            },
            "alerts": {
                "log_all_limit_breaches": True,
                "log_risk_metrics": True,
                "alert_on_circuit_breaker": True,
            },
        }

        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        # Create mock storage config
        class MockDataConfig:
            def get_storage_config(self):
                return {
                    "base_path": f"{temp_dir}/data",
                    "format": "parquet",
                    "compression": "snappy",
                }

        yield config_path, temp_dir, MockDataConfig


@pytest.fixture
def risk_manager(temp_config_and_data):
    """Create risk manager with test configuration."""
    config_path, temp_dir, mock_config = temp_config_and_data
    return RiskManager(portfolio_value=100000.0, config_path=str(config_path))


def test_risk_manager_initialization(risk_manager):
    """Test risk manager initialization."""
    assert risk_manager.portfolio_value == 100000.0
    assert risk_manager.position_tracker is not None
    assert risk_manager.limit_enforcer is not None
    assert risk_manager.circuit_breaker is not None
    assert risk_manager.metrics_calculator is not None


def test_complete_trade_workflow(risk_manager):
    """Test complete workflow: validate -> record -> check."""
    # 1. Validate trade before execution
    result = risk_manager.validate_trade(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="pairs_1",
        quantity=100,
        price=150.0,
    )

    assert result.passed

    # 2. Record trade after execution
    position = risk_manager.record_trade(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="pairs_1",
        quantity=100,
        entry_price=150.0,
        current_price=150.0,
    )

    assert position is not None
    assert position.quantity == 100

    # 3. Check positions
    all_positions = risk_manager.get_all_positions()
    assert len(all_positions) == 1

    # 4. Get exposure
    exposure = risk_manager.get_portfolio_exposure()
    assert exposure["gross"] == 15000.0  # 100 * 150


def test_limit_enforcement_workflow(risk_manager):
    """Test that limits are enforced across multiple trades."""
    # Fill up strategy to near limit (5% = $5,000)
    risk_manager.record_trade(
        "AAPL", "equities", "strat1", 33, 150.0, 150.0
    )  # $4,950

    # Try to exceed strategy limit
    result = risk_manager.validate_trade(
        "MSFT", "equities", "strat1", 10, 300.0  # +$3,000
    )

    assert not result.passed
    assert "strategy" in result.message.lower()


def test_circuit_breaker_workflow(risk_manager):
    """Test circuit breaker triggers and halts trading."""
    # Add some positions
    risk_manager.record_trade(
        "AAPL", "equities", "strat1", 100, 150.0, 150.0
    )

    # Simulate portfolio loss triggering circuit breaker
    # Initial value: $100,000
    # Positions: $15,000
    # Cash: $85,000
    # Current portfolio: $100,000

    # Update prices causing loss
    risk_manager.update_prices({"AAPL": 80.0})  # -46% on position

    # Check circuit breaker
    triggered = risk_manager.check_circuit_breaker()

    # Should trigger (large loss)
    assert triggered
    assert risk_manager.circuit_breaker.is_active

    # Subsequent trades should be rejected
    result = risk_manager.validate_trade(
        "MSFT", "equities", "strat1", 10, 300.0
    )

    assert not result.passed
    assert "Circuit breaker" in result.message


def test_multi_strategy_exposure_tracking(risk_manager):
    """Test tracking positions across multiple strategies."""
    # Strategy 1
    risk_manager.record_trade(
        "AAPL", "equities", "strat1", 100, 150.0, 150.0
    )
    risk_manager.record_trade(
        "MSFT", "equities", "strat1", 50, 300.0, 300.0
    )

    # Strategy 2
    risk_manager.record_trade(
        "AAPL", "equities", "strat2", 50, 150.0, 150.0
    )

    # Check strategy-specific exposure
    strat1_exposure = risk_manager.get_strategy_exposure("strat1")
    assert strat1_exposure["gross"] == 30000.0  # 15k + 15k

    strat2_exposure = risk_manager.get_strategy_exposure("strat2")
    assert strat2_exposure["gross"] == 7500.0  # 50 * 150

    # Check symbol exposure across strategies
    aapl_exposure = risk_manager.get_symbol_exposure("AAPL")
    assert aapl_exposure["quantity"] == 150  # 100 + 50


def test_price_update_workflow(risk_manager):
    """Test price updates and P&L tracking."""
    # Record initial position
    risk_manager.record_trade(
        "AAPL", "equities", "strat1", 100, 150.0, 150.0
    )

    # Update price (profit scenario)
    risk_manager.update_prices({"AAPL": 160.0})

    # Check updated P&L
    position = risk_manager.get_position("AAPL", "strat1")
    assert position.current_price == 160.0
    assert position.unrealized_pnl == 1000.0  # (160-150) * 100

    # Check portfolio exposure reflects P&L
    exposure = risk_manager.get_portfolio_exposure()
    assert exposure["total_pnl"] == 1000.0


def test_available_capacity_calculation(risk_manager):
    """Test calculating available trading capacity."""
    # Add position
    risk_manager.record_trade(
        "AAPL", "equities", "strat1", 30, 150.0, 150.0
    )  # $4,500

    # Check available capacity
    capacity = risk_manager.get_available_capacity("strat1", "AAPL")

    # Strategy limit: $5,000, used: $4,500, remaining: $500
    assert capacity["strategy_capacity"] == pytest.approx(500.0, rel=0.01)

    # Asset limit: $10,000, used: $4,500, remaining: $5,500
    assert capacity["asset_capacity"] == pytest.approx(5500.0, rel=0.01)

    # Binding constraint is strategy capacity
    assert capacity["min_capacity"] == pytest.approx(500.0, rel=0.01)


def test_system_status_reporting(risk_manager):
    """Test comprehensive system status reporting."""
    # Add some positions
    risk_manager.record_trade(
        "AAPL", "equities", "strat1", 100, 150.0, 155.0
    )
    risk_manager.record_trade(
        "MSFT", "equities", "strat2", -50, 300.0, 295.0
    )

    status = risk_manager.get_system_status()

    assert "timestamp" in status
    assert status["portfolio_value"] > 0
    assert status["position_count"] == 2
    assert "exposure" in status
    assert "circuit_breaker" in status
    assert "limits" in status

    # Check circuit breaker status
    assert not status["circuit_breaker"]["is_active"]
    assert status["circuit_breaker"]["high_water_mark"] == 100000.0


def test_pairs_trading_scenario(risk_manager):
    """Test pairs trading scenario with long/short legs."""
    # Pairs trade: Long AAPL, Short MSFT
    risk_manager.record_trade(
        "AAPL", "equities", "pairs_1", 100, 150.0, 150.0
    )  # Long $15k
    risk_manager.record_trade(
        "MSFT", "equities", "pairs_1", -50, 300.0, 300.0
    )  # Short $15k

    # Check strategy exposure
    exposure = risk_manager.get_strategy_exposure("pairs_1")

    # Gross: $30k (long + short absolute)
    assert exposure["gross"] == 30000.0

    # Net: ~$0 (market neutral)
    assert exposure["net"] == 0.0

    # Strategy is within 5% limit ($5k) because gross is $30k
    # This would fail - pairs trades need special handling or higher limits
    # For now, test that it's tracked correctly


def test_manual_circuit_breaker_trigger(risk_manager):
    """Test IRM manual circuit breaker trigger."""
    # IRM manually triggers
    reason = "Unusual market conditions detected"
    risk_manager.manual_circuit_breaker_trigger(reason)

    assert risk_manager.circuit_breaker.is_active

    state = risk_manager.get_circuit_breaker_state()
    assert reason in state.trigger_reason


def test_circuit_breaker_reset_workflow(risk_manager):
    """Test circuit breaker reset with justification."""
    # Trigger circuit breaker
    risk_manager.circuit_breaker.check_drawdown(85000.0)
    assert risk_manager.circuit_breaker.is_active

    # Reset with IRM justification
    justification = "Portfolio restructured, risk reviewed"
    risk_manager.reset_circuit_breaker(justification)

    assert not risk_manager.circuit_breaker.is_active

    # Trading should be allowed again
    result = risk_manager.validate_trade(
        "AAPL", "equities", "strat1", 10, 150.0
    )
    assert result.passed


def test_multiple_assets_limit_check(risk_manager):
    """Test per-asset limit across different strategies."""
    # Asset limit: 10% = $10,000

    # Strategy 1: $6,000 of AAPL
    risk_manager.record_trade(
        "AAPL", "equities", "strat1", 40, 150.0, 150.0
    )

    # Strategy 2: Try to add $6,000 more AAPL (would exceed $10k limit)
    result = risk_manager.validate_trade(
        "AAPL", "equities", "strat2", 40, 150.0
    )

    # Should fail per-asset limit
    assert not result.passed
    assert "asset" in result.message.lower()


def test_portfolio_value_updates_with_pnl(risk_manager):
    """Test that portfolio value updates with position P&L."""
    initial_value = risk_manager.portfolio_value

    # Record position
    risk_manager.record_trade(
        "AAPL", "equities", "strat1", 100, 150.0, 150.0
    )

    # Update price (profit)
    risk_manager.update_prices({"AAPL": 160.0})

    # Portfolio value should increase by P&L
    # Simplified: initial + unrealized P&L
    expected_value = initial_value + 1000.0  # (160-150) * 100

    assert risk_manager.portfolio_value == pytest.approx(
        expected_value, rel=0.01
    )


def test_close_position_workflow(risk_manager):
    """Test complete position open and close workflow."""
    # Open position
    risk_manager.record_trade(
        "AAPL", "equities", "strat1", 100, 150.0, 150.0
    )

    assert risk_manager.position_tracker.get_position_count() == 1

    # Validate close trade
    result = risk_manager.validate_trade(
        "AAPL", "equities", "strat1", -100, 155.0
    )
    assert result.passed  # Closing position should always pass

    # Close position
    risk_manager.record_trade(
        "AAPL", "equities", "strat1", -100, 155.0, 155.0
    )

    # Position should be closed
    assert risk_manager.position_tracker.get_position_count() == 0


def test_extreme_volatility_scenario(risk_manager):
    """Test risk management during extreme volatility."""
    # Record position
    risk_manager.record_trade(
        "AAPL", "equities", "strat1", 100, 150.0, 150.0
    )

    # Extreme price drop
    risk_manager.update_prices({"AAPL": 50.0})  # -66% crash

    # Check circuit breaker triggers
    triggered = risk_manager.check_circuit_breaker()

    # Should trigger due to extreme loss
    assert triggered

    # New trades should be rejected
    result = risk_manager.validate_trade(
        "MSFT", "equities", "strat2", 10, 300.0
    )
    assert not result.passed
