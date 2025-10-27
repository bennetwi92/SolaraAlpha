"""Tests for position sizing module."""
import pytest

from src.strategies.pairs_trading.position_sizing import (
    PairPosition,
    PositionSizer,
)


class TestPositionSizer:
    """Test suite for PositionSizer."""

    def test_initialization(self):
        """Test position sizer initialization."""
        sizer = PositionSizer(
            portfolio_value=100000,
            max_position_pct=0.05,
            target_volatility=0.10,
            min_capital_per_pair=1000,
        )

        assert sizer.portfolio_value == 100000
        assert sizer.max_position_pct == 0.05
        assert sizer.target_volatility == 0.10
        assert sizer.min_capital_per_pair == 1000

    def test_calculate_position_size(self):
        """Test basic position size calculation."""
        sizer = PositionSizer(portfolio_value=100000)

        position = sizer.calculate_position_size(
            symbol1="AAPL",
            symbol2="MSFT",
            price1=150.0,
            price2=300.0,
            hedge_ratio=0.5,
            spread_volatility=0.15,
        )

        assert isinstance(position, PairPosition)
        assert position.symbol1 == "AAPL"
        assert position.symbol2 == "MSFT"
        assert position.quantity1 > 0  # Long side
        assert position.quantity2 < 0  # Short side (hedge)
        assert position.total_capital > 0
        assert position.hedge_ratio == 0.5

    def test_volatility_scaling(self):
        """Test that higher volatility reduces position size."""
        sizer = PositionSizer(portfolio_value=100000, target_volatility=0.10)

        # Low volatility position
        pos_low_vol = sizer.calculate_position_size(
            "AAPL",
            "MSFT",
            150.0,
            300.0,
            0.5,
            spread_volatility=0.05,
        )

        # High volatility position
        pos_high_vol = sizer.calculate_position_size(
            "AAPL",
            "MSFT",
            150.0,
            300.0,
            0.5,
            spread_volatility=0.20,
        )

        # Lower vol should have larger capital allocation
        assert pos_low_vol.total_capital > pos_high_vol.total_capital

    def test_max_position_limit(self):
        """Test that position respects max position percentage."""
        sizer = PositionSizer(
            portfolio_value=100000, max_position_pct=0.05
        )

        position = sizer.calculate_position_size(
            "AAPL",
            "MSFT",
            150.0,
            300.0,
            0.5,
            spread_volatility=0.01,  # Very low vol (would size large)
        )

        max_capital = 100000 * 0.05
        # Should not exceed max
        assert position.total_capital <= max_capital

    def test_min_position_limit(self):
        """Test that position respects minimum capital."""
        sizer = PositionSizer(
            portfolio_value=100000, min_capital_per_pair=1000
        )

        position = sizer.calculate_position_size(
            "AAPL",
            "MSFT",
            150.0,
            300.0,
            0.5,
            spread_volatility=2.0,  # Very high vol (would size tiny)
        )

        # Should not go below minimum
        assert position.total_capital >= 1000

    def test_invalid_prices(self):
        """Test that invalid prices raise error."""
        sizer = PositionSizer(portfolio_value=100000)

        with pytest.raises(ValueError, match="Invalid prices"):
            sizer.calculate_position_size(
                "AAPL", "MSFT", -150.0, 300.0, 0.5, 0.10
            )

        with pytest.raises(ValueError, match="Invalid prices"):
            sizer.calculate_position_size(
                "AAPL", "MSFT", 150.0, 0.0, 0.5, 0.10
            )

    def test_invalid_volatility(self):
        """Test that invalid volatility raises error."""
        sizer = PositionSizer(portfolio_value=100000)

        with pytest.raises(ValueError, match="Invalid spread volatility"):
            sizer.calculate_position_size(
                "AAPL", "MSFT", 150.0, 300.0, 0.5, 0.0
            )

        with pytest.raises(ValueError, match="Invalid spread volatility"):
            sizer.calculate_position_size(
                "AAPL", "MSFT", 150.0, 300.0, 0.5, -0.1
            )

    def test_hedge_ratio_application(self):
        """Test that hedge ratio is correctly applied."""
        sizer = PositionSizer(portfolio_value=100000)

        hedge_ratio = 2.0
        position = sizer.calculate_position_size(
            "AAPL",
            "MSFT",
            150.0,
            300.0,
            hedge_ratio,
            spread_volatility=0.10,
        )

        # quantity2 should be hedge_ratio * quantity1 (in magnitude)
        expected_q2 = -hedge_ratio * position.quantity1
        assert abs(position.quantity2 - expected_q2) < 0.01

    def test_calculate_kelly_fraction(self):
        """Test Kelly criterion calculation."""
        sizer = PositionSizer(portfolio_value=100000)

        # Positive expectancy scenario
        kelly = sizer.calculate_kelly_fraction(
            win_rate=0.6, avg_win=0.02, avg_loss=0.01
        )

        assert isinstance(kelly, float)
        assert 0 <= kelly <= 0.5  # Capped at half Kelly

        # Negative expectancy (should return 0)
        kelly_neg = sizer.calculate_kelly_fraction(
            win_rate=0.3, avg_win=0.01, avg_loss=0.02
        )

        assert kelly_neg == 0.0

    def test_kelly_zero_loss(self):
        """Test Kelly with zero average loss."""
        sizer = PositionSizer(portfolio_value=100000)

        kelly = sizer.calculate_kelly_fraction(
            win_rate=0.6, avg_win=0.02, avg_loss=0.0
        )

        assert kelly == 0.0

    def test_adjust_for_risk_limits_no_adjustment(self):
        """Test adjustment when within all limits."""
        sizer = PositionSizer(portfolio_value=100000)

        position = PairPosition(
            symbol1="AAPL",
            symbol2="MSFT",
            quantity1=10,
            quantity2=-5,
            dollar_exposure1=1500,
            dollar_exposure2=1500,
            total_capital=3000,
            hedge_ratio=0.5,
        )

        adjusted = sizer.adjust_for_risk_limits(
            position,
            current_strategy_exposure=0,
            current_symbol1_exposure=0,
            current_symbol2_exposure=0,
            per_strategy_limit=10000,
            per_asset_limit=10000,
        )

        # Should not be adjusted
        assert adjusted.total_capital == position.total_capital
        assert adjusted.quantity1 == position.quantity1

    def test_adjust_for_risk_limits_strategy_breach(self):
        """Test adjustment when strategy limit breached."""
        sizer = PositionSizer(portfolio_value=100000)

        position = PairPosition(
            symbol1="AAPL",
            symbol2="MSFT",
            quantity1=10,
            quantity2=-5,
            dollar_exposure1=1500,
            dollar_exposure2=1500,
            total_capital=3000,
            hedge_ratio=0.5,
        )

        # Strategy already near limit
        adjusted = sizer.adjust_for_risk_limits(
            position,
            current_strategy_exposure=8000,
            current_symbol1_exposure=0,
            current_symbol2_exposure=0,
            per_strategy_limit=10000,  # Only 2000 remaining
            per_asset_limit=20000,
        )

        # Should be scaled down
        assert adjusted.total_capital < position.total_capital
        assert adjusted.total_capital <= 2000

    def test_adjust_for_risk_limits_asset_breach(self):
        """Test adjustment when per-asset limit breached."""
        sizer = PositionSizer(portfolio_value=100000)

        position = PairPosition(
            symbol1="AAPL",
            symbol2="MSFT",
            quantity1=10,
            quantity2=-5,
            dollar_exposure1=2000,
            dollar_exposure2=1000,
            total_capital=3000,
            hedge_ratio=0.5,
        )

        # Asset1 near limit
        adjusted = sizer.adjust_for_risk_limits(
            position,
            current_strategy_exposure=0,
            current_symbol1_exposure=8000,
            current_symbol2_exposure=0,
            per_strategy_limit=20000,
            per_asset_limit=9000,  # Only 1000 remaining for asset1
        )

        # Should be scaled down
        assert adjusted.dollar_exposure1 < position.dollar_exposure1
        assert adjusted.dollar_exposure1 <= 1000

    def test_update_portfolio_value(self):
        """Test portfolio value update."""
        sizer = PositionSizer(portfolio_value=100000)

        sizer.update_portfolio_value(120000)

        assert sizer.portfolio_value == 120000

    def test_get_sizing_metrics(self):
        """Test sizing metrics retrieval."""
        sizer = PositionSizer(
            portfolio_value=100000,
            max_position_pct=0.05,
            target_volatility=0.10,
        )

        metrics = sizer.get_sizing_metrics()

        assert isinstance(metrics, dict)
        assert metrics["portfolio_value"] == 100000
        assert metrics["max_position_pct"] == 0.05
        assert metrics["max_position_capital"] == 5000
        assert metrics["target_volatility"] == 0.10

    def test_dollar_exposure_calculation(self):
        """Test that dollar exposures are correctly calculated."""
        sizer = PositionSizer(portfolio_value=100000)

        price1, price2 = 150.0, 300.0
        position = sizer.calculate_position_size(
            "AAPL", "MSFT", price1, price2, 0.5, 0.10
        )

        # Verify dollar exposures match quantities * prices
        assert abs(
            position.dollar_exposure1 - abs(position.quantity1 * price1)
        ) < 0.01
        assert abs(
            position.dollar_exposure2 - abs(position.quantity2 * price2)
        ) < 0.01
