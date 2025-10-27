"""Tests for PairsStrategy adapter."""
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from src.strategies.pairs_trading.paper_trading import PaperTrade
from src.strategies.pairs_trading.signals import SignalType
from src.strategies.pairs_trading.strategy_adapter import PairsStrategy


@pytest.fixture
def mock_engine():
    """Create mock PaperTradingEngine."""
    engine = Mock()
    engine.strategy_id = "test_pairs"
    engine.lookback_window = 60
    engine.active_pairs = {"AAPL/MSFT": {}, "GOOG/META": {}}
    engine.positions = {}
    engine.trade_history = []
    return engine


@pytest.fixture
def pairs_strategy(mock_engine):
    """Create PairsStrategy with mock engine."""
    return PairsStrategy(mock_engine)


class TestInitialization:
    """Test strategy initialization."""

    def test_init(self, mock_engine):
        """Test strategy initialization."""
        strategy = PairsStrategy(mock_engine)

        assert strategy.engine == mock_engine
        assert strategy.engine.strategy_id == "test_pairs"


class TestRunIteration:
    """Test strategy iteration execution."""

    def test_successful_iteration_no_trades(
        self, pairs_strategy, mock_engine
    ):
        """Test iteration with no trades."""
        mock_engine.run_iteration.return_value = {}

        result = pairs_strategy.run_iteration()

        assert result["trades"] == 0
        assert result["signals"] == 0
        assert result["errors"] == []
        assert result["pairs_processed"] == 2
        assert result["trades_rejected"] == 0

    def test_successful_iteration_with_trades(
        self, pairs_strategy, mock_engine
    ):
        """Test iteration with successful trades."""
        # Mock trades
        trade1 = PaperTrade(
            timestamp=pd.Timestamp("2025-10-27"),
            pair_name="AAPL/MSFT",
            symbol1="AAPL",
            symbol2="MSFT",
            signal=1,
            quantity1=100,
            quantity2=-50,
            price1=150.0,
            price2=300.0,
            hedge_ratio=0.5,
            z_score=2.1,
            fill_status="filled",
        )

        trade2 = PaperTrade(
            timestamp=pd.Timestamp("2025-10-27"),
            pair_name="GOOG/META",
            symbol1="GOOG",
            symbol2="META",
            signal=-1,
            quantity1=-50,
            quantity2=100,
            price1=140.0,
            price2=500.0,
            hedge_ratio=0.7,
            z_score=-2.3,
            fill_status="filled",
        )

        mock_engine.run_iteration.return_value = {
            "AAPL/MSFT": [trade1],
            "GOOG/META": [trade2],
        }

        result = pairs_strategy.run_iteration()

        assert result["trades"] == 2
        assert result["signals"] == 2
        assert result["errors"] == []
        assert result["pairs_processed"] == 2
        assert result["trades_rejected"] == 0

    def test_iteration_with_rejected_trades(
        self, pairs_strategy, mock_engine
    ):
        """Test iteration with rejected trades."""
        # Mock rejected trade
        rejected_trade = PaperTrade(
            timestamp=pd.Timestamp("2025-10-27"),
            pair_name="AAPL/MSFT",
            symbol1="AAPL",
            symbol2="MSFT",
            signal=1,
            quantity1=100,
            quantity2=-50,
            price1=150.0,
            price2=300.0,
            hedge_ratio=0.5,
            z_score=2.1,
            fill_status="rejected",
            rejection_reason="Position limit exceeded",
        )

        mock_engine.run_iteration.return_value = {
            "AAPL/MSFT": [rejected_trade],
        }

        result = pairs_strategy.run_iteration()

        assert result["trades"] == 0
        assert result["signals"] == 1
        assert result["trades_rejected"] == 1

    def test_iteration_failure(self, pairs_strategy, mock_engine):
        """Test iteration failure propagates exception."""
        mock_engine.run_iteration.side_effect = ValueError("Engine failure")

        with pytest.raises(ValueError, match="Engine failure"):
            pairs_strategy.run_iteration()


class TestGetStatus:
    """Test strategy status retrieval."""

    def test_get_status_success(self, pairs_strategy, mock_engine):
        """Test successful status retrieval."""
        mock_status = {
            "timestamp": "2025-10-27T12:00:00",
            "portfolio_value": 105000.0,
            "active_pairs": 2,
            "open_positions": 1,
            "total_trades": 10,
            "exposure": {},
            "circuit_breaker_active": False,
            "positions": [],
        }
        mock_engine.get_status.return_value = mock_status

        status = pairs_strategy.get_status()

        assert status["portfolio_value"] == 105000.0
        assert status["active_pairs"] == 2
        assert status["strategy_type"] == "pairs_trading"
        assert status["lookback_window"] == 60

    def test_get_status_failure(self, pairs_strategy, mock_engine):
        """Test status retrieval failure."""
        mock_engine.get_status.side_effect = RuntimeError("Status error")

        status = pairs_strategy.get_status()

        assert "error" in status
        assert status["strategy_type"] == "pairs_trading"


class TestShutdown:
    """Test strategy shutdown."""

    def test_shutdown_with_trades(self, pairs_strategy, mock_engine):
        """Test shutdown exports trade history."""
        # Mock trade history
        trades_df = pd.DataFrame(
            {
                "timestamp": [
                    pd.Timestamp("2025-10-27"),
                    pd.Timestamp("2025-10-28"),
                ],
                "pair_name": ["AAPL/MSFT", "GOOG/META"],
                "symbol1": ["AAPL", "GOOG"],
                "symbol2": ["MSFT", "META"],
                "signal": [1, -1],
                "quantity1": [100, -50],
                "quantity2": [-50, 100],
                "price1": [150.0, 140.0],
                "price2": [300.0, 500.0],
                "hedge_ratio": [0.5, 0.7],
                "z_score": [2.1, -2.3],
                "fill_status": ["filled", "filled"],
                "rejection_reason": [None, None],
            }
        )
        mock_engine.export_trades.return_value = trades_df

        mock_status = {
            "portfolio_value": 105000.0,
            "active_pairs": 2,
            "open_positions": 0,
        }
        mock_engine.get_status.return_value = mock_status

        # Mock the file operations to avoid actual I/O
        with patch("pandas.DataFrame.to_csv"):
            pairs_strategy.shutdown()

        # Verify calls were made
        mock_engine.export_trades.assert_called_once()
        mock_engine.get_status.assert_called_once()

    def test_shutdown_no_trades(self, pairs_strategy, mock_engine):
        """Test shutdown with no trade history."""
        mock_engine.export_trades.return_value = pd.DataFrame()
        mock_engine.get_status.return_value = {}

        # Should not raise exception
        pairs_strategy.shutdown()

        mock_engine.export_trades.assert_called_once()

    def test_shutdown_error_handling(self, pairs_strategy, mock_engine):
        """Test shutdown handles errors gracefully."""
        mock_engine.export_trades.side_effect = RuntimeError("Export error")

        # Should not raise exception
        pairs_strategy.shutdown()


class TestIntegration:
    """Integration tests with real PaperTradingEngine."""

    @pytest.mark.integration
    def test_real_engine_iteration(self):
        """Test with real PaperTradingEngine (mocked data)."""
        from unittest.mock import MagicMock

        from src.strategies.pairs_trading.paper_trading import PaperTradingEngine

        # Create engine with mocked dependencies
        with patch(
            "src.strategies.pairs_trading.paper_trading.ParquetStorage"
        ), patch(
            "src.strategies.pairs_trading.paper_trading.RiskManager"
        ):
            engine = PaperTradingEngine(
                portfolio_value=100000,
                strategy_id="test_integration",
            )

            # Mock _load_prices to return test data
            engine._load_prices = MagicMock(return_value=None)

            strategy = PairsStrategy(engine)

            # Should handle no data gracefully
            result = strategy.run_iteration()

            assert result["trades"] == 0
            assert result["errors"] == []
            assert "pairs_processed" in result
