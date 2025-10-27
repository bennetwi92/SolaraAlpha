"""Unit tests for position tracker."""
import pytest
from datetime import datetime

from src.risk.position_tracker import Position, PositionTracker


@pytest.fixture
def tracker():
    """Create fresh position tracker."""
    return PositionTracker()


def test_position_properties():
    """Test Position dataclass calculated properties."""
    pos = Position(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="test_strat",
        quantity=100,
        entry_price=150.0,
        current_price=155.0,
    )

    assert pos.market_value == 15500.0  # 100 * 155
    assert pos.cost_basis == 15000.0  # 100 * 150
    assert pos.unrealized_pnl == 500.0
    assert pos.unrealized_pnl_pct == pytest.approx(3.333, rel=0.01)
    assert pos.is_long
    assert not pos.is_short


def test_position_short():
    """Test short position properties."""
    pos = Position(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="test_strat",
        quantity=-100,
        entry_price=150.0,
        current_price=145.0,
    )

    assert pos.market_value == -14500.0
    assert pos.cost_basis == -15000.0
    assert pos.unrealized_pnl == 500.0  # Profit on short
    assert not pos.is_long
    assert pos.is_short


def test_add_new_position(tracker):
    """Test adding a new position."""
    pos = tracker.add_position(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="strat1",
        quantity=100,
        entry_price=150.0,
        current_price=155.0,
    )

    assert pos is not None
    assert pos.quantity == 100
    assert pos.entry_price == 150.0
    assert tracker.get_position_count() == 1


def test_update_existing_position(tracker):
    """Test updating existing position with weighted average entry."""
    # First position
    tracker.add_position(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="strat1",
        quantity=100,
        entry_price=150.0,
        current_price=150.0,
    )

    # Add to position
    pos = tracker.add_position(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="strat1",
        quantity=50,
        entry_price=160.0,
        current_price=160.0,
    )

    # Check weighted average: (100*150 + 50*160) / 150 = 153.33
    assert pos.quantity == 150
    assert pos.entry_price == pytest.approx(153.333, rel=0.01)
    assert tracker.get_position_count() == 1


def test_close_position(tracker):
    """Test closing position reduces quantity to zero."""
    tracker.add_position(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="strat1",
        quantity=100,
        entry_price=150.0,
        current_price=150.0,
    )

    # Close position
    result = tracker.add_position(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="strat1",
        quantity=-100,
        entry_price=155.0,
        current_price=155.0,
    )

    assert result is None
    assert tracker.get_position_count() == 0


def test_update_price(tracker):
    """Test updating price for all positions of a symbol."""
    tracker.add_position(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="strat1",
        quantity=100,
        entry_price=150.0,
        current_price=150.0,
    )

    tracker.add_position(
        symbol="AAPL",
        asset_class="equities",
        strategy_id="strat2",
        quantity=50,
        entry_price=155.0,
        current_price=155.0,
    )

    # Update price
    tracker.update_price("AAPL", 160.0)

    pos1 = tracker.get_position("AAPL", "strat1")
    pos2 = tracker.get_position("AAPL", "strat2")

    assert pos1.current_price == 160.0
    assert pos2.current_price == 160.0


def test_get_positions_by_strategy(tracker):
    """Test filtering positions by strategy."""
    tracker.add_position("AAPL", "equities", "strat1", 100, 150.0, 150.0)
    tracker.add_position("MSFT", "equities", "strat1", 50, 300.0, 300.0)
    tracker.add_position("AAPL", "equities", "strat2", 75, 150.0, 150.0)

    strat1_positions = tracker.get_positions_by_strategy("strat1")
    assert len(strat1_positions) == 2

    strat2_positions = tracker.get_positions_by_strategy("strat2")
    assert len(strat2_positions) == 1


def test_get_positions_by_symbol(tracker):
    """Test filtering positions by symbol."""
    tracker.add_position("AAPL", "equities", "strat1", 100, 150.0, 150.0)
    tracker.add_position("AAPL", "equities", "strat2", 50, 155.0, 155.0)
    tracker.add_position("MSFT", "equities", "strat1", 75, 300.0, 300.0)

    aapl_positions = tracker.get_positions_by_symbol("AAPL")
    assert len(aapl_positions) == 2


def test_portfolio_exposure(tracker):
    """Test portfolio-level exposure calculation."""
    # Long position
    tracker.add_position("AAPL", "equities", "strat1", 100, 150.0, 155.0)
    # Short position
    tracker.add_position("MSFT", "equities", "strat1", -50, 300.0, 295.0)

    exposure = tracker.get_portfolio_exposure()

    # Gross: |100*155| + |-50*295| = 15500 + 14750 = 30250
    assert exposure["gross"] == 30250.0

    # Long: 100*155 = 15500
    assert exposure["long"] == 15500.0

    # Short: 50*295 = 14750
    assert exposure["short"] == 14750.0

    # Net: 15500 - 14750 = 750
    assert exposure["net"] == 750.0

    # Total P&L: (155-150)*100 + (300-295)*50 = 500 + 250 = 750
    assert exposure["total_pnl"] == 750.0


def test_strategy_exposure(tracker):
    """Test strategy-specific exposure calculation."""
    tracker.add_position("AAPL", "equities", "strat1", 100, 150.0, 155.0)
    tracker.add_position("MSFT", "equities", "strat1", 50, 300.0, 310.0)
    tracker.add_position("AAPL", "equities", "strat2", 75, 150.0, 155.0)

    strat1_exposure = tracker.get_strategy_exposure("strat1")

    # Gross: 100*155 + 50*310 = 15500 + 15500 = 31000
    assert strat1_exposure["gross"] == 31000.0

    # Total P&L: (155-150)*100 + (310-300)*50 = 500 + 500 = 1000
    assert strat1_exposure["total_pnl"] == 1000.0


def test_symbol_exposure(tracker):
    """Test symbol-specific exposure across strategies."""
    tracker.add_position("AAPL", "equities", "strat1", 100, 150.0, 155.0)
    tracker.add_position("AAPL", "equities", "strat2", 50, 152.0, 155.0)

    symbol_exposure = tracker.get_symbol_exposure("AAPL")

    # Total quantity: 100 + 50 = 150
    assert symbol_exposure["quantity"] == 150

    # Market value: 150 * 155 = 23250
    assert symbol_exposure["market_value"] == 23250.0

    # P&L: (155-150)*100 + (155-152)*50 = 500 + 150 = 650
    assert symbol_exposure["total_pnl"] == 650.0


def test_clear_all_positions(tracker):
    """Test clearing all positions."""
    tracker.add_position("AAPL", "equities", "strat1", 100, 150.0, 150.0)
    tracker.add_position("MSFT", "equities", "strat1", 50, 300.0, 300.0)

    assert tracker.get_position_count() == 2

    tracker.clear_all_positions()

    assert tracker.get_position_count() == 0
    assert len(tracker.get_all_positions()) == 0


def test_multiple_strategies_same_symbol(tracker):
    """Test tracking same symbol across multiple strategies."""
    tracker.add_position("AAPL", "equities", "strat1", 100, 150.0, 155.0)
    tracker.add_position("AAPL", "equities", "strat2", -50, 155.0, 155.0)

    # Should be 2 separate positions
    assert tracker.get_position_count() == 2

    # Strategy 1 should be long
    pos1 = tracker.get_position("AAPL", "strat1")
    assert pos1.is_long
    assert pos1.quantity == 100

    # Strategy 2 should be short
    pos2 = tracker.get_position("AAPL", "strat2")
    assert pos2.is_short
    assert pos2.quantity == -50


def test_partial_close(tracker):
    """Test partially closing a position."""
    tracker.add_position("AAPL", "equities", "strat1", 100, 150.0, 150.0)

    # Sell 30 shares
    pos = tracker.add_position(
        "AAPL", "equities", "strat1", -30, 155.0, 155.0
    )

    assert pos.quantity == 70
    assert pos.entry_price == 150.0  # Entry price unchanged
    assert tracker.get_position_count() == 1


def test_zero_quantity_edge_case(tracker):
    """Test edge case with zero quantity position."""
    pos = tracker.add_position(
        "AAPL", "equities", "strat1", 0, 150.0, 150.0
    )

    # Should create position even with zero quantity
    assert pos is not None
    assert pos.quantity == 0
