"""Unit tests for limit enforcer."""
import tempfile
from pathlib import Path

import pytest
import yaml

from src.risk.config.risk_config import RiskConfigManager
from src.risk.limit_enforcer import LimitEnforcer, LimitType
from src.risk.position_tracker import PositionTracker


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

        yield config_path


@pytest.fixture
def tracker():
    """Create fresh position tracker."""
    return PositionTracker()


@pytest.fixture
def enforcer(tracker, temp_config):
    """Create limit enforcer with test config."""
    config = RiskConfigManager(str(temp_config))
    portfolio_value = 100000.0
    return LimitEnforcer(tracker, config, portfolio_value)


def test_enforcer_initialization(enforcer):
    """Test limit enforcer initialization."""
    assert enforcer.portfolio_value == 100000.0
    assert enforcer.per_strategy_limit_pct == 5.0
    assert enforcer.per_asset_limit_pct == 10.0
    assert enforcer.gross_exposure_limit_pct == 200.0
    assert enforcer.net_exposure_limit_pct == 20.0


def test_update_portfolio_value(enforcer):
    """Test updating portfolio value."""
    enforcer.update_portfolio_value(150000.0)
    assert enforcer.portfolio_value == 150000.0


def test_check_trade_empty_portfolio(enforcer):
    """Test trade validation with no existing positions."""
    # Portfolio value: $100,000
    # Per-strategy limit: 5% = $5,000
    # This trade is $4,500 (30 shares @ $150), which should pass
    result = enforcer.check_trade(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="strat1",
        quantity=30,
        price=150.0,
    )

    assert result.passed
    assert result.message == "Trade passes all limit checks"


def test_per_strategy_limit_violation(enforcer):
    """Test per-strategy limit enforcement."""
    # Portfolio value: $100,000
    # Per-strategy limit: 5% = $5,000

    # Add position bringing strategy to edge of limit
    enforcer.tracker.add_position(
        "AAPL", "equities", "strat1", 33, 150.0, 150.0
    )  # $4,950

    # Try to add trade that would breach limit
    result = enforcer.check_trade(
        symbol="MSFT",
        asset_class="equities",
        strategy_id="strat1",
        quantity=10,
        price=300.0,  # $3,000 additional
    )

    assert not result.passed
    assert result.violated_limit == LimitType.PER_STRATEGY
    assert "per-strategy limit" in result.message.lower()


def test_per_asset_limit_violation(enforcer):
    """Test per-asset limit enforcement."""
    # Portfolio value: $100,000
    # Per-asset limit: 10% = $10,000

    # Strategy 1 has $7,500 of AAPL
    enforcer.tracker.add_position(
        "AAPL", "equities", "strat1", 50, 150.0, 150.0
    )

    # Strategy 2 tries to add $5,000 more (total would be $12,500)
    result = enforcer.check_trade(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="strat2",
        quantity=33,
        price=150.0,
    )

    assert not result.passed
    assert result.violated_limit == LimitType.PER_ASSET
    assert "per-asset limit" in result.message.lower()


def test_gross_exposure_limit_violation(enforcer):
    """Test gross exposure limit enforcement."""
    # Portfolio value: $100,000
    # Per-strategy limit: 5% = $5,000
    # Gross exposure limit: 200% = $200,000

    # Add positions totaling $196,500 gross (each strategy under 5% limit)
    enforcer.tracker.add_position(
        "AAPL", "equities", "strat1", 656, 150.0, 150.0
    )  # $98,400
    enforcer.tracker.add_position(
        "MSFT", "equities", "strat2", -327, 300.0, 300.0
    )  # $98,100

    # Try to add $4,500 more (strat3 gets 4.5% which is under 5% per-strategy limit,
    # but gross would be $201k > $200k limit)
    result = enforcer.check_trade(
        symbol="GOOG",
        asset_class="equities",
        strategy_id="strat3",
        quantity=2,
        price=2250.0,
    )

    assert not result.passed
    assert result.violated_limit == LimitType.GROSS_EXPOSURE


def test_net_exposure_limit_violation(enforcer):
    """Test net exposure limit (market-neutral constraint)."""
    # Portfolio value: $100,000
    # Net exposure limit: 20% = $20,000

    # Add positions with $18,000 net long
    enforcer.tracker.add_position(
        "AAPL", "equities", "strat1", 200, 150.0, 150.0
    )  # $30,000 long
    enforcer.tracker.add_position(
        "MSFT", "equities", "strat2", -40, 300.0, 300.0
    )  # -$12,000 short
    # Net: $18,000

    # Try to add $5,000 more long (net would be $23,000 > $20,000)
    result = enforcer.check_trade(
        symbol="GOOG",
        asset_class="equities",
        strategy_id="strat3",
        quantity=3,
        price=1600.0,
    )

    assert not result.passed
    assert result.violated_limit == LimitType.NET_EXPOSURE
    assert "market-neutral" in result.message.lower()


def test_multiple_limit_checks_pass(enforcer):
    """Test trade that passes all limits."""
    # Add some existing positions
    enforcer.tracker.add_position(
        "AAPL", "equities", "strat1", 10, 150.0, 150.0
    )
    enforcer.tracker.add_position(
        "MSFT", "equities", "strat2", -5, 300.0, 300.0
    )

    # Add modest trade that should pass
    result = enforcer.check_trade(
        symbol="GOOG",
        asset_class="equities",
        strategy_id="strat3",
        quantity=2,
        price=1500.0,
    )

    assert result.passed


def test_get_available_capacity(enforcer):
    """Test calculating available trading capacity."""
    # Add position: $7,500 in strat1 for AAPL
    enforcer.tracker.add_position(
        "AAPL", "equities", "strat1", 50, 150.0, 150.0
    )

    capacity = enforcer.get_available_capacity("strat1", "AAPL")

    # Strategy capacity: $5,000 limit - $7,500 used = -$2,500 (already over for adds)
    # But can still reduce
    assert capacity["strategy_capacity"] >= 0  # Max with 0

    # Asset capacity: $10,000 limit - $7,500 used = $2,500
    assert capacity["asset_capacity"] == pytest.approx(2500.0, rel=0.01)

    # Min capacity is the binding constraint
    assert capacity["min_capacity"] <= capacity["strategy_capacity"]
    assert capacity["min_capacity"] <= capacity["asset_capacity"]


def test_check_trade_reduce_position(enforcer):
    """Test that reducing position always passes limits."""
    # Add large position
    enforcer.tracker.add_position(
        "AAPL", "equities", "strat1", 100, 150.0, 150.0
    )

    # Sell some shares (negative quantity)
    result = enforcer.check_trade(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="strat1",
        quantity=-50,
        price=155.0,
    )

    # Should pass (reducing exposure)
    assert result.passed


def test_edge_case_zero_quantity_trade(enforcer):
    """Test edge case with zero quantity trade."""
    result = enforcer.check_trade(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="strat1",
        quantity=0,
        price=150.0,
    )

    # Zero quantity should pass (no impact)
    assert result.passed


def test_per_strategy_limit_percentage_calculation(enforcer):
    """Test correct percentage calculation for strategy limit."""
    # Portfolio: $100,000
    # Strategy limit: 5% = $5,000

    # Add position worth exactly 5%
    enforcer.tracker.add_position(
        "AAPL", "equities", "strat1", 33.33, 150.0, 150.0
    )  # ~$5,000

    result = enforcer.check_trade(
        symbol="MSFT",
        asset_class="equities",
        strategy_id="strat1",
        quantity=1,
        price=100.0,  # Small additional trade
    )

    # Should fail due to exceeding 5%
    assert not result.passed
    assert result.violated_limit == LimitType.PER_STRATEGY


def test_short_position_gross_exposure(enforcer):
    """Test that short positions count toward gross exposure."""
    # Add short position
    enforcer.tracker.add_position(
        "AAPL", "equities", "strat1", -100, 150.0, 150.0
    )  # -$15,000

    exposure = enforcer.tracker.get_portfolio_exposure()

    # Gross should be absolute value
    assert exposure["gross"] == 15000.0
    assert exposure["net"] == -15000.0
