"""Paper trading engine for pairs trading strategy.

Real-time signal generation and order simulation without capital deployment.
Validates strategy execution logic and risk integration before live trading.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from ...data.storage.parquet_storage import ParquetStorage
from ...risk.risk_manager import RiskManager
from ..pairs_trading.cointegration import CointegrationTester
from ..pairs_trading.position_sizing import PairPosition, PositionSizer
from ..pairs_trading.signals import SignalGenerator, SignalType, TradingSignal

logger = logging.getLogger(__name__)


@dataclass
class PaperTrade:
    """Paper trade record.

    Attributes:
        timestamp: Trade execution timestamp.
        pair_name: Pair identifier (e.g., 'AAPL/MSFT').
        symbol1: First asset symbol.
        symbol2: Second asset symbol.
        signal: Trade signal (1=long spread, -1=short spread, 0=close).
        quantity1: Units of asset1.
        quantity2: Units of asset2.
        price1: Execution price asset1.
        price2: Execution price asset2.
        hedge_ratio: Hedge ratio used.
        z_score: Spread z-score at execution.
        fill_status: Fill status ('filled', 'partial', 'rejected').
        rejection_reason: Reason if rejected by risk manager.
    """

    timestamp: pd.Timestamp
    pair_name: str
    symbol1: str
    symbol2: str
    signal: int
    quantity1: float
    quantity2: float
    price1: float
    price2: float
    hedge_ratio: float
    z_score: float
    fill_status: str = "filled"
    rejection_reason: Optional[str] = None


@dataclass
class PaperPosition:
    """Current paper trading position for a pair.

    Attributes:
        pair_name: Pair identifier.
        symbol1: First asset symbol.
        symbol2: Second asset symbol.
        quantity1: Current quantity asset1.
        quantity2: Current quantity asset2.
        entry_price1: Entry price asset1.
        entry_price2: Entry price asset2.
        current_price1: Latest price asset1.
        current_price2: Latest price asset2.
        hedge_ratio: Hedge ratio.
        unrealized_pnl: Current unrealized P&L.
        position_type: Position type (LONG_SPREAD, SHORT_SPREAD).
    """

    pair_name: str
    symbol1: str
    symbol2: str
    quantity1: float
    quantity2: float
    entry_price1: float
    entry_price2: float
    current_price1: float
    current_price2: float
    hedge_ratio: float
    unrealized_pnl: float = 0.0
    position_type: SignalType = SignalType.NO_POSITION


class PaperTradingEngine:
    """Paper trading engine for pairs strategy.

    Simulates real-time trading with:
    - Live data ingestion
    - Signal generation
    - Risk validation
    - Order simulation with realistic fills
    - Position tracking and P&L calculation

    Example:
        >>> engine = PaperTradingEngine(
        ...     portfolio_value=100000,
        ...     strategy_id='pairs_paper_1'
        ... )
        >>> engine.add_pair('AAPL', 'MSFT', 'equities')
        >>> engine.run_iteration()  # Call periodically
    """

    def __init__(
        self,
        portfolio_value: float,
        strategy_id: str = "pairs_paper_1",
        lookback_window: int = 60,
    ) -> None:
        """Initialize paper trading engine.

        Args:
            portfolio_value: Initial portfolio value.
            strategy_id: Unique strategy identifier for risk tracking.
            lookback_window: Historical window for signals (default 60 days).
        """
        self.portfolio_value = portfolio_value
        self.strategy_id = strategy_id
        self.lookback_window = lookback_window

        # Initialize components
        self.storage = ParquetStorage()
        self.risk_manager = RiskManager(portfolio_value=portfolio_value)
        self.coint_tester = CointegrationTester()
        self.signal_generator = SignalGenerator(
            lookback_window=lookback_window
        )
        self.position_sizer = PositionSizer(
            portfolio_value=portfolio_value
        )

        # State tracking
        self.active_pairs: Dict[str, Dict] = {}
        self.positions: Dict[str, PaperPosition] = {}
        self.trade_history: List[PaperTrade] = []

        logger.info(
            f"Paper trading engine initialized | "
            f"Portfolio: ${portfolio_value:,.2f}, "
            f"Strategy: {strategy_id}"
        )

    def add_pair(
        self,
        symbol1: str,
        symbol2: str,
        asset_class: str,
        hedge_ratio: Optional[float] = None,
    ) -> bool:
        """Add pair to paper trading watchlist.

        If hedge_ratio not provided, estimates from historical data.

        Args:
            symbol1: First asset symbol.
            symbol2: Second asset symbol.
            asset_class: Asset class for both symbols.
            hedge_ratio: Pre-computed hedge ratio (optional).

        Returns:
            True if pair added successfully, False otherwise.
        """
        pair_name = f"{symbol1}/{symbol2}"

        try:
            # Load historical data for cointegration test
            prices1 = self._load_prices(symbol1, asset_class)
            prices2 = self._load_prices(symbol2, asset_class)

            if prices1 is None or prices2 is None:
                logger.error(
                    f"Failed to load prices for {pair_name}"
                )
                return False

            # Estimate hedge ratio if not provided
            if hedge_ratio is None:
                result = self.coint_tester.test_pair(prices1, prices2)

                if not result.is_cointegrated:
                    logger.warning(
                        f"{pair_name} not cointegrated (p={result.p_value:.4f})"
                    )
                    return False

                hedge_ratio = result.hedge_ratio

            # Store pair configuration
            self.active_pairs[pair_name] = {
                "symbol1": symbol1,
                "symbol2": symbol2,
                "asset_class": asset_class,
                "hedge_ratio": hedge_ratio,
                "current_position": SignalType.NO_POSITION,
            }

            logger.info(
                f"Pair added: {pair_name} | Hedge ratio: {hedge_ratio:.4f}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to add pair {pair_name}: {e}")
            return False

    def run_iteration(self) -> Dict[str, List[PaperTrade]]:
        """Run single iteration of paper trading.

        Fetches latest prices, generates signals, validates with risk manager,
        simulates order fills, and updates positions.

        Returns:
            Dictionary mapping pair_name to list of executed trades.
        """
        iteration_trades = {}

        for pair_name, pair_config in self.active_pairs.items():
            try:
                trades = self._process_pair(pair_name, pair_config)
                if trades:
                    iteration_trades[pair_name] = trades

            except Exception as e:
                logger.error(
                    f"Error processing {pair_name}: {e}"
                )
                continue

        # Update portfolio value
        self._update_portfolio_value()

        # Check circuit breaker
        if self.risk_manager.check_circuit_breaker():
            logger.critical(
                "🚨 CIRCUIT BREAKER ACTIVE - Paper trading halted"
            )

        return iteration_trades

    def _process_pair(
        self, pair_name: str, pair_config: Dict
    ) -> List[PaperTrade]:
        """Process single pair for signal generation and execution.

        Args:
            pair_name: Pair identifier.
            pair_config: Pair configuration dictionary.

        Returns:
            List of executed paper trades.
        """
        symbol1 = pair_config["symbol1"]
        symbol2 = pair_config["symbol2"]
        asset_class = pair_config["asset_class"]
        hedge_ratio = pair_config["hedge_ratio"]
        current_position = pair_config["current_position"]

        # Load recent price history
        prices1 = self._load_prices(symbol1, asset_class)
        prices2 = self._load_prices(symbol2, asset_class)

        if prices1 is None or prices2 is None:
            logger.warning(f"Price data unavailable for {pair_name}")
            return []

        # Generate signal
        signal = self.signal_generator.generate_signal_at_point(
            prices1, prices2, hedge_ratio, current_position
        )

        # No trade signal
        if signal.signal == current_position:
            return []

        # Calculate position size
        spread = prices1 - hedge_ratio * prices2
        spread_vol = self.signal_generator.calculate_spread_volatility(
            spread
        )

        current_price1 = prices1.iloc[-1]
        current_price2 = prices2.iloc[-1]

        position = self.position_sizer.calculate_position_size(
            symbol1,
            symbol2,
            current_price1,
            current_price2,
            hedge_ratio,
            spread_vol,
        )

        # Apply signal direction
        if signal.signal == SignalType.SHORT_SPREAD:
            position.quantity1 = -position.quantity1
            position.quantity2 = -position.quantity2
        elif signal.signal == SignalType.NO_POSITION:
            # Closing position - use existing position quantities
            if pair_name in self.positions:
                existing = self.positions[pair_name]
                position.quantity1 = -existing.quantity1
                position.quantity2 = -existing.quantity2

        # Validate with risk manager
        # Validate leg 1
        result1 = self.risk_manager.validate_trade(
            symbol1,
            asset_class,
            self.strategy_id,
            position.quantity1,
            current_price1,
        )

        # Validate leg 2
        result2 = self.risk_manager.validate_trade(
            symbol2,
            asset_class,
            self.strategy_id,
            position.quantity2,
            current_price2,
        )

        # Check if trade rejected
        if not result1.passed or not result2.passed:
            rejection_reason = result1.message if not result1.passed else result2.message

            logger.warning(
                f"Trade rejected for {pair_name}: {rejection_reason}"
            )

            # Record rejected trade
            paper_trade = PaperTrade(
                timestamp=signal.timestamp,
                pair_name=pair_name,
                symbol1=symbol1,
                symbol2=symbol2,
                signal=signal.signal.value,
                quantity1=position.quantity1,
                quantity2=position.quantity2,
                price1=current_price1,
                price2=current_price2,
                hedge_ratio=hedge_ratio,
                z_score=signal.z_score,
                fill_status="rejected",
                rejection_reason=rejection_reason,
            )

            self.trade_history.append(paper_trade)
            return [paper_trade]

        # Simulate order fill (assume 100% fill for paper trading)
        paper_trade = PaperTrade(
            timestamp=signal.timestamp,
            pair_name=pair_name,
            symbol1=symbol1,
            symbol2=symbol2,
            signal=signal.signal.value,
            quantity1=position.quantity1,
            quantity2=position.quantity2,
            price1=current_price1,
            price2=current_price2,
            hedge_ratio=hedge_ratio,
            z_score=signal.z_score,
            fill_status="filled",
        )

        # Record trades with risk manager
        self.risk_manager.record_trade(
            symbol1,
            asset_class,
            self.strategy_id,
            position.quantity1,
            current_price1,
            current_price1,
        )

        self.risk_manager.record_trade(
            symbol2,
            asset_class,
            self.strategy_id,
            position.quantity2,
            current_price2,
            current_price2,
        )

        # Update position tracking
        if signal.signal == SignalType.NO_POSITION:
            # Close position
            if pair_name in self.positions:
                del self.positions[pair_name]
            pair_config["current_position"] = SignalType.NO_POSITION
        else:
            # Open or update position
            self.positions[pair_name] = PaperPosition(
                pair_name=pair_name,
                symbol1=symbol1,
                symbol2=symbol2,
                quantity1=position.quantity1,
                quantity2=position.quantity2,
                entry_price1=current_price1,
                entry_price2=current_price2,
                current_price1=current_price1,
                current_price2=current_price2,
                hedge_ratio=hedge_ratio,
                position_type=signal.signal,
            )
            pair_config["current_position"] = signal.signal

        self.trade_history.append(paper_trade)

        logger.info(
            f"Paper trade executed | "
            f"{pair_name} | Signal: {signal.signal.name}, "
            f"Q1: {position.quantity1:.2f}, Q2: {position.quantity2:.2f}"
        )

        return [paper_trade]

    def _load_prices(
        self, symbol: str, asset_class: str
    ) -> Optional[pd.Series]:
        """Load historical prices from storage.

        Args:
            symbol: Asset symbol.
            asset_class: Asset class.

        Returns:
            Price series (Close prices) or None if unavailable.
        """
        try:
            data = self.storage.read(asset_class, symbol, frequency="1d")

            if data is None or len(data) < self.lookback_window:
                return None

            # Return last N days (lookback + buffer)
            return data["Close"].tail(self.lookback_window + 20)

        except Exception as e:
            logger.error(f"Failed to load prices for {symbol}: {e}")
            return None

    def _update_portfolio_value(self) -> None:
        """Update portfolio value based on current positions."""
        total_unrealized_pnl = 0.0

        for pair_name, position in self.positions.items():
            # Update current prices
            pair_config = self.active_pairs[pair_name]
            asset_class = pair_config["asset_class"]

            prices1 = self._load_prices(position.symbol1, asset_class)
            prices2 = self._load_prices(position.symbol2, asset_class)

            if prices1 is not None and prices2 is not None:
                position.current_price1 = prices1.iloc[-1]
                position.current_price2 = prices2.iloc[-1]

                # Calculate unrealized P&L
                pnl1 = (
                    position.current_price1 - position.entry_price1
                ) * position.quantity1
                pnl2 = (
                    position.current_price2 - position.entry_price2
                ) * position.quantity2

                position.unrealized_pnl = pnl1 + pnl2
                total_unrealized_pnl += position.unrealized_pnl

        # Update portfolio value
        new_value = self.portfolio_value + total_unrealized_pnl
        self.position_sizer.update_portfolio_value(new_value)

    def get_status(self) -> Dict:
        """Get current paper trading status.

        Returns:
            Dictionary with positions, P&L, and system metrics.
        """
        exposure = self.risk_manager.get_portfolio_exposure()
        cb_state = self.risk_manager.get_circuit_breaker_state()

        return {
            "timestamp": datetime.now().isoformat(),
            "portfolio_value": self.portfolio_value,
            "active_pairs": len(self.active_pairs),
            "open_positions": len(self.positions),
            "total_trades": len(self.trade_history),
            "exposure": exposure,
            "circuit_breaker_active": cb_state.is_active,
            "positions": [
                {
                    "pair": pos.pair_name,
                    "type": pos.position_type.name,
                    "unrealized_pnl": pos.unrealized_pnl,
                }
                for pos in self.positions.values()
            ],
        }

    def export_trades(self) -> pd.DataFrame:
        """Export trade history as DataFrame.

        Returns:
            DataFrame with all paper trades.
        """
        if not self.trade_history:
            return pd.DataFrame()

        records = [
            {
                "timestamp": t.timestamp,
                "pair_name": t.pair_name,
                "symbol1": t.symbol1,
                "symbol2": t.symbol2,
                "signal": t.signal,
                "quantity1": t.quantity1,
                "quantity2": t.quantity2,
                "price1": t.price1,
                "price2": t.price2,
                "hedge_ratio": t.hedge_ratio,
                "z_score": t.z_score,
                "fill_status": t.fill_status,
                "rejection_reason": t.rejection_reason,
            }
            for t in self.trade_history
        ]

        return pd.DataFrame(records)
