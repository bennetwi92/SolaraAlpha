"""Tests for FX carry position sizing."""
import pytest

from src.strategies.fx_carry.position_sizing import FXCarryPositionSizer
from src.strategies.fx_carry.signals import CarrySignal


@pytest.fixture
def position_sizer():
    """Create position sizer instance."""
    return FXCarryPositionSizer(
        vol_target=10.0,
        max_position_pct=0.20,
        max_leverage=1.0,
    )


@pytest.fixture
def sample_signals():
    """Create sample carry signals."""
    import pandas as pd

    return [
        CarrySignal(
            pair="AUD-USD",
            timestamp=pd.Timestamp.now(),
            rate_differential=3.0,
            trend_score=0.6,
            volatility=8.0,
            signal=0.8,
            position_size=0.8,
        ),
        CarrySignal(
            pair="NZD-USD",
            timestamp=pd.Timestamp.now(),
            rate_differential=2.5,
            trend_score=0.5,
            volatility=10.0,
            signal=0.6,
            position_size=0.6,
        ),
        CarrySignal(
            pair="EUR-JPY",
            timestamp=pd.Timestamp.now(),
            rate_differential=2.0,
            trend_score=0.4,
            volatility=12.0,
            signal=0.5,
            position_size=0.5,
        ),
    ]


class TestFXCarryPositionSizer:
    """Test suite for position sizing."""

    def test_initialization(self):
        """Test position sizer initialization."""
        sizer = FXCarryPositionSizer(
            vol_target=12.0,
            max_position_pct=0.25,
            max_leverage=1.5,
        )

        assert sizer.vol_target == 12.0
        assert sizer.max_position_pct == 0.25
        assert sizer.max_leverage == 1.5

    def test_calculate_position_sizes_empty(self, position_sizer):
        """Test position sizing with no signals."""
        positions = position_sizer.calculate_position_sizes([], 100_000)

        assert positions == {}

    def test_calculate_position_sizes_basic(
        self, position_sizer, sample_signals
    ):
        """Test basic position sizing."""
        portfolio_value = 100_000

        positions = position_sizer.calculate_position_sizes(
            sample_signals, portfolio_value
        )

        # Should have positions for all signals
        assert len(positions) == 3
        assert "AUD-USD" in positions
        assert "NZD-USD" in positions
        assert "EUR-JPY" in positions

        # All positions should be non-zero
        for pair, size in positions.items():
            assert size != 0

        # Check position sizes are reasonable
        total_exposure = sum(abs(p) for p in positions.values())
        assert total_exposure <= portfolio_value * position_sizer.max_leverage

    def test_calculate_position_sizes_respects_max_position(
        self, position_sizer, sample_signals
    ):
        """Test that position sizing respects maximum position limit."""
        portfolio_value = 100_000

        positions = position_sizer.calculate_position_sizes(
            sample_signals, portfolio_value
        )

        # No position should exceed max_position_pct
        max_allowed = portfolio_value * position_sizer.max_position_pct

        for pair, size in positions.items():
            assert abs(size) <= max_allowed * 1.01  # Allow 1% tolerance

    def test_calculate_position_sizes_respects_leverage(
        self, sample_signals
    ):
        """Test that position sizing respects leverage limit."""
        sizer = FXCarryPositionSizer(
            vol_target=10.0,
            max_position_pct=0.30,
            max_leverage=0.5,  # Low leverage
        )

        portfolio_value = 100_000

        positions = sizer.calculate_position_sizes(
            sample_signals, portfolio_value
        )

        # Total exposure should not exceed leverage limit
        total_exposure = sum(abs(p) for p in positions.values())
        assert total_exposure <= portfolio_value * sizer.max_leverage * 1.01

    def test_calculate_position_sizes_inverse_volatility(
        self, position_sizer
    ):
        """Test that lower volatility leads to larger positions."""
        import pandas as pd

        low_vol_signal = CarrySignal(
            pair="PAIR-A",
            timestamp=pd.Timestamp.now(),
            rate_differential=2.0,
            trend_score=0.5,
            volatility=5.0,  # Low vol
            signal=0.6,
            position_size=0.6,
        )

        high_vol_signal = CarrySignal(
            pair="PAIR-B",
            timestamp=pd.Timestamp.now(),
            rate_differential=2.0,
            trend_score=0.5,
            volatility=15.0,  # High vol
            signal=0.6,
            position_size=0.6,
        )

        positions = position_sizer.calculate_position_sizes(
            [low_vol_signal, high_vol_signal], 100_000
        )

        # Lower volatility should result in larger position
        assert abs(positions["PAIR-A"]) > abs(positions["PAIR-B"])

    def test_calculate_position_sizes_signal_direction(
        self, position_sizer
    ):
        """Test that position signs match signal directions."""
        import pandas as pd

        positive_signal = CarrySignal(
            pair="AUD-USD",
            timestamp=pd.Timestamp.now(),
            rate_differential=2.0,
            trend_score=0.5,
            volatility=10.0,
            signal=0.6,  # Positive
            position_size=0.6,
        )

        negative_signal = CarrySignal(
            pair="USD-JPY",
            timestamp=pd.Timestamp.now(),
            rate_differential=-2.0,
            trend_score=-0.5,
            volatility=10.0,
            signal=-0.6,  # Negative
            position_size=0.6,
        )

        positions = position_sizer.calculate_position_sizes(
            [positive_signal, negative_signal], 100_000
        )

        assert positions["AUD-USD"] > 0  # Long
        assert positions["USD-JPY"] < 0  # Short

    def test_calculate_portfolio_volatility(
        self, position_sizer, sample_signals
    ):
        """Test portfolio volatility calculation."""
        portfolio_value = 100_000

        positions = position_sizer.calculate_position_sizes(
            sample_signals, portfolio_value
        )

        volatilities = {s.pair: s.volatility for s in sample_signals}

        portfolio_vol = position_sizer.calculate_portfolio_volatility(
            positions, volatilities, portfolio_value
        )

        assert isinstance(portfolio_vol, float)
        assert portfolio_vol > 0
        # Portfolio vol should be near target (but not exact due to constraints)
        assert 5.0 < portfolio_vol < 20.0

    def test_calculate_portfolio_volatility_empty(self, position_sizer):
        """Test portfolio volatility with no positions."""
        vol = position_sizer.calculate_portfolio_volatility(
            {}, {}, 100_000
        )

        assert vol == 0.0

    def test_calculate_rebalance_no_drift(self, position_sizer):
        """Test rebalancing when positions are aligned."""
        current = {"AUD-USD": 10_000, "EUR-USD": 15_000}
        target = {"AUD-USD": 10_500, "EUR-USD": 15_200}

        trades = position_sizer.calculate_rebalance(
            current, target, rebalance_threshold=0.10
        )

        # Small drift should not trigger rebalance
        assert len(trades) == 0 or all(
            abs(t) < 1000 for t in trades.values()
        )

    def test_calculate_rebalance_with_drift(self, position_sizer):
        """Test rebalancing with significant drift."""
        current = {"AUD-USD": 10_000, "EUR-USD": 15_000}
        target = {"AUD-USD": 15_000, "EUR-USD": 10_000}  # Swapped

        trades = position_sizer.calculate_rebalance(
            current, target, rebalance_threshold=0.10
        )

        # Significant drift should trigger rebalance
        assert len(trades) == 2
        assert "AUD-USD" in trades
        assert "EUR-USD" in trades
        assert trades["AUD-USD"] > 0  # Increase
        assert trades["EUR-USD"] < 0  # Decrease

    def test_calculate_rebalance_new_position(self, position_sizer):
        """Test rebalancing with new positions."""
        current = {"AUD-USD": 10_000}
        target = {"AUD-USD": 10_000, "EUR-USD": 15_000}

        trades = position_sizer.calculate_rebalance(
            current, target, rebalance_threshold=0.10
        )

        # New position should be added
        assert "EUR-USD" in trades
        assert trades["EUR-USD"] == 15_000

    def test_calculate_rebalance_close_position(self, position_sizer):
        """Test rebalancing with position closure."""
        current = {"AUD-USD": 10_000, "EUR-USD": 15_000}
        target = {"AUD-USD": 10_000}  # Close EUR-USD

        trades = position_sizer.calculate_rebalance(
            current, target, rebalance_threshold=0.10
        )

        # Position should be closed
        assert "EUR-USD" in trades
        assert trades["EUR-USD"] == -15_000

    def test_calculate_risk_metrics(
        self, position_sizer, sample_signals
    ):
        """Test risk metrics calculation."""
        portfolio_value = 100_000

        positions = position_sizer.calculate_position_sizes(
            sample_signals, portfolio_value
        )

        metrics = position_sizer.calculate_risk_metrics(
            positions, sample_signals, portfolio_value
        )

        # Check all expected metrics present
        assert "gross_exposure" in metrics
        assert "net_exposure" in metrics
        assert "leverage" in metrics
        assert "portfolio_vol" in metrics
        assert "num_positions" in metrics

        # Validate metric values
        assert metrics["gross_exposure"] > 0
        assert metrics["leverage"] <= position_sizer.max_leverage * 1.01
        assert metrics["num_positions"] == len(positions)
        assert metrics["portfolio_vol"] > 0

    def test_calculate_risk_metrics_empty(self, position_sizer):
        """Test risk metrics with no positions."""
        metrics = position_sizer.calculate_risk_metrics(
            {}, [], 100_000
        )

        assert metrics["gross_exposure"] == 0.0
        assert metrics["net_exposure"] == 0.0
        assert metrics["leverage"] == 0.0
        assert metrics["portfolio_vol"] == 0.0
        assert metrics["num_positions"] == 0
