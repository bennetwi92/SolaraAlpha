"""Tests for position sizing module."""
import pytest
import numpy as np

from src.strategies.etf_arbitrage.position_sizing import (
    PositionSizer, PositionSizingConfig, SpreadPosition
)


class TestPositionSizer:
    """Test cases for PositionSizer."""

    @pytest.fixture
    def sizer(self):
        """Create PositionSizer instance."""
        return PositionSizer(portfolio_value=100000.0)

    @pytest.fixture
    def kelly_sizer(self):
        """Create PositionSizer with Kelly criterion."""
        config = PositionSizingConfig(
            method='kelly',
            max_position_pct=0.20,
            kelly_fraction=0.5
        )
        return PositionSizer(portfolio_value=100000.0, config=config)

    @pytest.fixture
    def fixed_sizer(self):
        """Create PositionSizer with fixed fractional."""
        config = PositionSizingConfig(
            method='fixed_fractional',
            max_position_pct=0.20,
            fixed_fraction=0.10
        )
        return PositionSizer(portfolio_value=100000.0, config=config)

    def test_kelly_criterion_calculation(self, kelly_sizer):
        """Test Kelly criterion position sizing."""
        position = kelly_sizer.calculate_position_size(
            symbol1='SPY',
            symbol2='IVV',
            price1=450.0,
            price2=450.0,
            z_score=-2.0,
            spread_volatility=0.01,
            win_rate=0.6,
            avg_win=0.02,
            avg_loss=0.01
        )

        assert isinstance(position, SpreadPosition)
        assert position.symbol1 == 'SPY'
        assert position.symbol2 == 'IVV'
        assert position.quantity1 > 0  # Long SPY
        assert position.quantity2 < 0  # Short IVV
        assert position.allocation_pct > 0
        assert position.allocation_pct <= 0.20  # Respects max

    def test_fixed_fractional_sizing(self, fixed_sizer):
        """Test fixed fractional position sizing."""
        position = fixed_sizer.calculate_position_size(
            symbol1='SPY',
            symbol2='IVV',
            price1=450.0,
            price2=450.0,
            z_score=-2.0,
            spread_volatility=0.01
        )

        assert position.allocation_pct == 0.10  # Fixed fraction

    def test_market_neutral_quantities(self, sizer):
        """Test market-neutral position quantities."""
        position = sizer.calculate_position_size(
            symbol1='SPY',
            symbol2='IVV',
            price1=450.0,
            price2=450.0,
            z_score=-2.0,
            spread_volatility=0.01
        )

        # Positions should be roughly equal dollar value
        exposure1 = abs(position.quantity1 * position.price1)
        exposure2 = abs(position.quantity2 * position.price2)

        assert abs(exposure1 - exposure2) / max(exposure1, exposure2) < 0.1  # Within 10%

    def test_long_spread_direction(self, sizer):
        """Test long spread position direction."""
        position = sizer.calculate_position_size(
            symbol1='SPY',
            symbol2='IVV',
            price1=450.0,
            price2=450.0,
            z_score=-2.0,  # Negative z-score -> long spread
            spread_volatility=0.01
        )

        assert position.quantity1 > 0  # Long
        assert position.quantity2 < 0  # Short

    def test_short_spread_direction(self, sizer):
        """Test short spread position direction."""
        position = sizer.calculate_position_size(
            symbol1='SPY',
            symbol2='IVV',
            price1=450.0,
            price2=450.0,
            z_score=2.0,  # Positive z-score -> short spread
            spread_volatility=0.01
        )

        assert position.quantity1 < 0  # Short
        assert position.quantity2 > 0  # Long

    def test_max_position_constraint(self, sizer):
        """Test maximum position constraint."""
        # Even with high win rate, should cap at max
        position = sizer.calculate_position_size(
            symbol1='SPY',
            symbol2='IVV',
            price1=450.0,
            price2=450.0,
            z_score=-2.0,
            spread_volatility=0.01,
            win_rate=0.9,
            avg_win=0.1,
            avg_loss=0.01
        )

        assert position.allocation_pct <= 0.20  # Max 20%

    def test_minimum_capital_threshold(self):
        """Test minimum capital requirement."""
        config = PositionSizingConfig(min_capital_per_trade=5000.0)
        sizer = PositionSizer(portfolio_value=100000.0, config=config)

        # Small fixed fraction below minimum
        sizer.config.fixed_fraction = 0.01  # 1% = $1000

        position = sizer.calculate_position_size(
            symbol1='SPY',
            symbol2='IVV',
            price1=450.0,
            price2=450.0,
            z_score=-2.0,
            spread_volatility=0.01
        )

        # Should reject due to min capital
        assert position.allocation_pct == 0
        assert position.quantity1 == 0
        assert position.quantity2 == 0

    def test_update_portfolio_value(self, sizer):
        """Test updating portfolio value."""
        original_max = sizer.get_max_position_capital()

        sizer.update_portfolio_value(150000.0)

        new_max = sizer.get_max_position_capital()
        assert new_max > original_max
        assert new_max == 150000.0 * 0.20  # 20% of new value

    def test_kelly_with_zero_avg_loss(self, kelly_sizer):
        """Test Kelly criterion with zero average loss."""
        position = kelly_sizer.calculate_position_size(
            symbol1='SPY',
            symbol2='IVV',
            price1=450.0,
            price2=450.0,
            z_score=-2.0,
            spread_volatility=0.01,
            win_rate=0.6,
            avg_win=0.02,
            avg_loss=0.0  # Zero loss
        )

        # Should fall back to fixed fractional when Kelly calculation fails
        assert position.allocation_pct > 0

    def test_dollar_exposures(self, sizer):
        """Test dollar exposure calculations."""
        position = sizer.calculate_position_size(
            symbol1='SPY',
            symbol2='IVV',
            price1=450.0,
            price2=450.0,
            z_score=-2.0,
            spread_volatility=0.01
        )

        assert position.dollar_exposure1 == position.quantity1 * position.price1
        assert position.dollar_exposure2 == position.quantity2 * position.price2
        assert position.total_capital == abs(position.dollar_exposure1) + abs(position.dollar_exposure2)
