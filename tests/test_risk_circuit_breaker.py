"""Unit tests for circuit breaker."""
import json
import tempfile
from pathlib import Path

import pytest
import yaml

from src.risk.circuit_breaker import CircuitBreaker
from src.risk.config.risk_config import RiskConfigManager


@pytest.fixture
def temp_config():
    """Create temporary risk configuration."""
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

        yield config_path, temp_dir


@pytest.fixture
def breaker(temp_config):
    """Create circuit breaker with test config."""
    config_path, temp_dir = temp_config
    config = RiskConfigManager(str(config_path))
    return CircuitBreaker(config, initial_value=100000.0)


def test_circuit_breaker_initialization(breaker):
    """Test circuit breaker initialization."""
    assert breaker.high_water_mark == 100000.0
    assert breaker.current_value == 100000.0
    assert breaker.max_drawdown_pct == 10.0
    assert not breaker.is_active


def test_high_water_mark_update(breaker):
    """Test HWM updates when portfolio increases."""
    initial_hwm = breaker.high_water_mark

    # Portfolio increases
    breaker.check_drawdown(110000.0)

    assert breaker.high_water_mark == 110000.0
    assert breaker.high_water_mark > initial_hwm
    assert not breaker.is_active


def test_drawdown_below_threshold(breaker):
    """Test drawdown below threshold does not trigger."""
    # 5% drawdown (below 10% threshold)
    triggered = breaker.check_drawdown(95000.0)

    assert not triggered
    assert not breaker.is_active
    assert breaker.current_drawdown_pct == pytest.approx(5.0, rel=0.01)


def test_drawdown_at_threshold_triggers(breaker):
    """Test drawdown at exactly threshold triggers circuit breaker."""
    # Exactly 10% drawdown
    triggered = breaker.check_drawdown(90000.0)

    assert triggered
    assert breaker.is_active

    state = breaker.get_state()
    assert state.is_active
    assert state.drawdown_pct == pytest.approx(10.0, rel=0.01)
    assert "Maximum drawdown exceeded" in state.trigger_reason


def test_drawdown_exceeds_threshold_triggers(breaker):
    """Test drawdown exceeding threshold triggers circuit breaker."""
    # 15% drawdown (exceeds 10% threshold)
    triggered = breaker.check_drawdown(85000.0)

    assert triggered
    assert breaker.is_active

    state = breaker.get_state()
    assert state.drawdown_pct == pytest.approx(15.0, rel=0.01)


def test_circuit_breaker_only_triggers_once(breaker):
    """Test circuit breaker only triggers on first breach."""
    # First breach
    first_trigger = breaker.check_drawdown(89000.0)
    assert first_trigger

    # Further drawdown should not re-trigger
    second_trigger = breaker.check_drawdown(85000.0)
    assert not second_trigger

    # But breaker remains active
    assert breaker.is_active


def test_manual_trigger(breaker):
    """Test manual circuit breaker trigger (IRM authority)."""
    reason = "Suspicious market activity detected"

    breaker.manual_trigger(reason)

    assert breaker.is_active
    state = breaker.get_state()
    assert reason in state.trigger_reason


def test_reset_circuit_breaker(breaker):
    """Test resetting circuit breaker with justification."""
    # Trigger breaker
    breaker.check_drawdown(85000.0)
    assert breaker.is_active

    # Reset with justification
    justification = "Risk reviewed and approved by IRM"
    breaker.reset(justification)

    assert not breaker.is_active
    state = breaker.get_state()
    assert not state.is_active


def test_reset_requires_justification(breaker):
    """Test that reset requires justification when configured."""
    breaker.check_drawdown(85000.0)

    # Try to reset without justification
    with pytest.raises(ValueError, match="Justification required"):
        breaker.reset("")


def test_hwm_persistence(temp_config):
    """Test high-water mark persists across instances."""
    config_path, temp_dir = temp_config
    config = RiskConfigManager(str(config_path))

    # Create first instance and update HWM
    breaker1 = CircuitBreaker(config, initial_value=100000.0)
    breaker1.check_drawdown(120000.0)  # New HWM
    assert breaker1.high_water_mark == 120000.0

    # Create second instance - should load persisted HWM
    breaker2 = CircuitBreaker(config, initial_value=None)
    assert breaker2.high_water_mark == 120000.0


def test_hwm_storage_file_created(temp_config):
    """Test HWM storage file is created."""
    config_path, temp_dir = temp_config
    config = RiskConfigManager(str(config_path))

    breaker = CircuitBreaker(config, initial_value=100000.0)

    hwm_path = Path(temp_dir) / "hwm.json"
    assert hwm_path.exists()

    # Verify file content
    with open(hwm_path, "r") as f:
        data = json.load(f)
        assert data["high_water_mark"] == 100000.0
        assert "last_updated" in data


def test_calculate_drawdown_pct(breaker):
    """Test drawdown percentage calculation."""
    # HWM: $100,000

    # No drawdown
    breaker.check_drawdown(100000.0)
    assert breaker.current_drawdown_pct == 0.0

    # 5% drawdown
    breaker.check_drawdown(95000.0)
    assert breaker.current_drawdown_pct == pytest.approx(5.0, rel=0.01)

    # 20% drawdown
    breaker.check_drawdown(80000.0)
    assert breaker.current_drawdown_pct == pytest.approx(20.0, rel=0.01)


def test_get_distance_to_trigger(breaker):
    """Test calculating distance to circuit breaker trigger."""
    # HWM: $100,000, Max DD: 10%
    # Trigger value: $90,000

    distance = breaker.get_distance_to_trigger()

    assert distance["trigger_value"] == 90000.0
    assert distance["distance_dollars"] == 10000.0  # $100k - $90k
    assert distance["buffer_pct"] == pytest.approx(10.0, rel=0.01)

    # After 5% drawdown
    breaker.check_drawdown(95000.0)
    distance = breaker.get_distance_to_trigger()

    assert distance["distance_dollars"] == pytest.approx(5000.0, rel=0.01)
    assert distance["buffer_pct"] == pytest.approx(5.0, rel=0.01)


def test_hwm_updates_with_recovery(breaker):
    """Test HWM updates when portfolio recovers and exceeds previous high."""
    # Initial HWM: $100,000
    assert breaker.high_water_mark == 100000.0

    # Drawdown
    breaker.check_drawdown(95000.0)
    assert breaker.high_water_mark == 100000.0  # Unchanged

    # Recovery to new high
    breaker.check_drawdown(105000.0)
    assert breaker.high_water_mark == 105000.0  # Updated

    # Circuit breaker not triggered
    assert not breaker.is_active


def test_circuit_breaker_state_snapshot(breaker):
    """Test getting complete circuit breaker state."""
    breaker.check_drawdown(92000.0)  # 8% drawdown

    state = breaker.get_state()

    assert not state.is_active
    assert state.high_water_mark == 100000.0
    assert state.current_value == 92000.0
    assert state.drawdown_pct == pytest.approx(8.0, rel=0.01)
    assert state.trigger_timestamp is None


def test_zero_hwm_edge_case(temp_config):
    """Test edge case with zero HWM."""
    config_path, temp_dir = temp_config
    config = RiskConfigManager(str(config_path))

    breaker = CircuitBreaker(config, initial_value=0.0)

    # Drawdown calculation should handle zero gracefully
    assert breaker._calculate_drawdown_pct(0.0) == 0.0


def test_negative_portfolio_value(breaker):
    """Test circuit breaker with negative portfolio value."""
    # Extreme loss scenario
    breaker.check_drawdown(-10000.0)

    # Should trigger (100% loss > 10% threshold)
    assert breaker.is_active
    assert breaker.current_drawdown_pct > 100.0
