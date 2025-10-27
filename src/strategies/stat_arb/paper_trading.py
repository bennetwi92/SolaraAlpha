"""Paper trading engine for statistical arbitrage strategy.

Real-time simulation for multi-stock portfolios with factor model updates and risk validation.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from ...data.storage.parquet_storage import ParquetStorage
from ...risk.risk_manager import RiskManager
from .factor_model import FactorModel
from .portfolio import PortfolioConstructor, PortfolioPosition
from .position_sizing import PositionSizer, SizedPosition
from .signals import SignalGenerator, SignalType, PortfolioSignal

logger = logging.getLogger(__name__)


@dataclass
class PaperTrade:
    """Paper trade record for stat arb portfolio."""

    timestamp: pd.Timestamp
    symbol: str
    signal_type: SignalType
    quantity: int
    price: float
    dollar_value: float
    z_score: float
    residual: float
    fill_status: str = "filled"
    rejection_reason: Optional[str] = None


@dataclass
class PaperPosition:
    """Current paper trading position."""

    symbol: str
    signal_type: SignalType
    quantity: int
    entry_price: float
    current_price: float
    unrealized_pnl: float
    dollar_exposure: float
    weight: float


class PaperTradingEngine:
    """Paper trading engine for statistical arbitrage strategy.

    Simulates real-time portfolio management with:
    - Rolling factor model estimation
    - Multi-stock signal generation
    - Risk-validated portfolio construction
    - Position tracking and P&L calculation
    """

    def __init__(
        self,
        universe: List[str],
        asset_class: str,
        portfolio_value: float,
        strategy_id: str,
        n_factors: int = 5,
        target_positions: int = 30,
        reestimation_frequency: int = 20,
    ):
        """Initialize paper trading engine.

        Args:
            universe: List of stock symbols to trade
            asset_class: Asset class (e.g., 'equities')
            portfolio_value: Initial portfolio value (USD)
            strategy_id: Strategy identifier for risk tracking
            n_factors: Number of PCA factors
            target_positions: Target number of positions (long + short)
            reestimation_frequency: Days between factor re-estimation
        """
        if len(universe) < target_positions:
            raise ValueError(
                f"Universe size ({len(universe)}) must be >= "
                f"target_positions ({target_positions})"
            )

        self.universe = universe
        self.asset_class = asset_class
        self.portfolio_value = portfolio_value
        self.initial_value = portfolio_value
        self.strategy_id = strategy_id
        self.reestimation_frequency = reestimation_frequency

        # Initialize components
        self.factor_model = FactorModel(
            n_factors=n_factors, estimation_window=60
        )
        self.signal_generator = SignalGenerator(
            lookback=20, entry_threshold=2.0, exit_threshold=0.5, stop_threshold=3.5
        )
        self.portfolio_constructor = PortfolioConstructor(
            min_positions=20,
            max_positions=50,
            target_positions=target_positions,
        )
        self.position_sizer = PositionSizer(
            portfolio_value=portfolio_value,
            target_leverage=1.0,
            volatility_lookback=20,
        )

        # Risk manager
        self.risk_manager = RiskManager(portfolio_value=portfolio_value)

        # Data storage
        self.storage = ParquetStorage()

        # State
        self.current_positions: Dict[str, PaperPosition] = {}
        self.trade_history: List[PaperTrade] = []
        self.days_since_reestimation = 0
        self.factor_model_fitted = False

        logger.info(
            f"Initialized StatArb paper trading: universe={len(universe)}, "
            f"target_positions={target_positions}, factors={n_factors}"
        )

    def run_iteration(self) -> Dict[str, List[PaperTrade]]:
        """Run one trading iteration.

        Returns:
            Dict mapping 'trades' to list of executed PaperTrade objects

        Raises:
            ValueError: If data unavailable or processing fails
        """
        logger.info(f"Running StatArb iteration for {len(self.universe)} stocks")

        # Fetch latest data
        prices, returns = self._fetch_data()

        if prices is None or returns is None:
            logger.warning("Data fetch failed, skipping iteration")
            return {"trades": []}

        # Check if factor model needs re-estimation
        if (
            not self.factor_model_fitted
            or self.days_since_reestimation >= self.reestimation_frequency
        ):
            self._reestimate_factors(returns)
            self.days_since_reestimation = 0
            self.factor_model_fitted = True
        else:
            self.days_since_reestimation += 1

        # Calculate residuals
        try:
            residuals = self.factor_model.transform(returns)
        except ValueError as e:
            logger.error(f"Factor model transform failed: {e}")
            return {"trades": []}

        # Update current prices for P&L calculation
        self._update_positions_mtm(prices.iloc[-1])

        # Generate signals
        current_position_types = {
            symbol: pos.signal_type for symbol, pos in self.current_positions.items()
        }
        portfolio_signal = self.signal_generator.generate_signals(
            residuals, current_position_types
        )

        # Filter by confidence
        portfolio_signal = self.signal_generator.filter_signals_by_confidence(
            portfolio_signal
        )

        # Process exits first
        exit_trades = self._process_exits(portfolio_signal, prices.iloc[-1])

        # Construct new portfolio
        try:
            ideal_portfolio = self.portfolio_constructor.construct_portfolio(
                portfolio_signal
            )
        except ValueError as e:
            logger.info(f"Portfolio construction skipped: {e}")
            return {"trades": exit_trades}

        # Size positions
        try:
            sized_positions = self.position_sizer.calculate_positions(
                ideal_portfolio, prices, returns
            )
        except ValueError as e:
            logger.error(f"Position sizing failed: {e}")
            return {"trades": exit_trades}

        # Process entries
        entry_trades = self._process_entries(
            sized_positions, prices.iloc[-1], portfolio_signal.timestamp
        )

        all_trades = exit_trades + entry_trades

        logger.info(
            f"Iteration complete: {len(exit_trades)} exits, {len(entry_trades)} entries"
        )

        return {"trades": all_trades}

    def _fetch_data(self) -> tuple:
        """Fetch latest price data for universe.

        Returns:
            Tuple of (prices DataFrame, returns DataFrame) or (None, None) on error
        """
        all_prices = []

        for symbol in self.universe:
            try:
                prices = self.storage.read(self.asset_class, symbol, "1d")
                if prices is not None and len(prices) > 0:
                    all_prices.append(prices["close"].rename(symbol))
            except Exception as e:
                logger.warning(f"Failed to fetch {symbol}: {e}")
                continue

        if not all_prices:
            logger.error("No price data available for any symbol")
            return None, None

        # Combine into DataFrame
        prices_df = pd.concat(all_prices, axis=1)
        prices_df = prices_df.dropna()  # Drop rows with missing data

        if len(prices_df) < 80:  # Need 60 for factor + 20 for signal
            logger.error(f"Insufficient data: {len(prices_df)} rows")
            return None, None

        # Calculate returns
        returns_df = prices_df.pct_change().dropna()

        return prices_df, returns_df

    def _reestimate_factors(self, returns: pd.DataFrame) -> None:
        """Re-estimate factor model.

        Args:
            returns: Historical returns
        """
        try:
            self.factor_model.fit(returns)
            logger.info("Factor model re-estimated successfully")
        except ValueError as e:
            logger.warning(f"Factor model estimation failed: {e}")

    def _update_positions_mtm(self, current_prices: pd.Series) -> None:
        """Update positions with current mark-to-market prices.

        Args:
            current_prices: Latest prices for all symbols
        """
        for symbol, position in self.current_positions.items():
            if symbol in current_prices:
                position.current_price = current_prices[symbol]

                # Calculate unrealized P&L
                if position.signal_type == SignalType.LONG:
                    position.unrealized_pnl = position.quantity * (
                        position.current_price - position.entry_price
                    )
                elif position.signal_type == SignalType.SHORT:
                    position.unrealized_pnl = -position.quantity * (
                        position.current_price - position.entry_price
                    )

    def _process_exits(
        self, portfolio_signal: PortfolioSignal, current_prices: pd.Series
    ) -> List[PaperTrade]:
        """Process exit signals.

        Args:
            portfolio_signal: Current signals
            current_prices: Current prices

        Returns:
            List of executed exit trades
        """
        trades = []
        exit_signals = self.signal_generator.get_exit_signals(portfolio_signal)

        for symbol, signal in exit_signals.items():
            if symbol not in self.current_positions:
                continue

            position = self.current_positions[symbol]
            price = current_prices.get(symbol)

            if price is None or pd.isna(price):
                continue

            # Execute exit
            trade = PaperTrade(
                timestamp=portfolio_signal.timestamp,
                symbol=symbol,
                signal_type=signal,
                quantity=-position.quantity,  # Reverse position
                price=price,
                dollar_value=abs(position.quantity * price),
                z_score=portfolio_signal.z_scores.get(symbol, 0.0),
                residual=portfolio_signal.residuals.get(symbol, 0.0),
                fill_status="filled",
            )

            trades.append(trade)
            self.trade_history.append(trade)

            # Update portfolio value
            self.portfolio_value += position.unrealized_pnl

            # Remove position
            del self.current_positions[symbol]

            logger.info(
                f"Closed {symbol} ({position.signal_type.value}): "
                f"P&L=${position.unrealized_pnl:.2f}"
            )

        # Update signal generator state
        for symbol in exit_signals.keys():
            self.signal_generator.update_positions({symbol: SignalType.NO_POSITION})

        return trades

    def _process_entries(
        self,
        sized_positions: Dict[str, SizedPosition],
        current_prices: pd.Series,
        timestamp: pd.Timestamp,
    ) -> List[PaperTrade]:
        """Process entry signals with risk validation.

        Args:
            sized_positions: Positions to enter
            current_prices: Current prices
            timestamp: Current timestamp

        Returns:
            List of executed entry trades
        """
        trades = []

        for symbol, position in sized_positions.items():
            # Skip if already in position
            if symbol in self.current_positions:
                continue

            price = current_prices.get(symbol)
            if price is None or pd.isna(price):
                continue

            # Risk validation
            validation = self.risk_manager.validate_trade(
                symbol=symbol,
                asset_class=self.asset_class,
                strategy_id=self.strategy_id,
                quantity=abs(position.quantity),
                price=price,
            )

            if not validation.passed:
                logger.warning(
                    f"Trade rejected for {symbol}: {validation.message}"
                )
                trade = PaperTrade(
                    timestamp=timestamp,
                    symbol=symbol,
                    signal_type=position.signal_type,
                    quantity=position.quantity,
                    price=price,
                    dollar_value=abs(position.quantity * price),
                    z_score=0.0,
                    residual=0.0,
                    fill_status="rejected",
                    rejection_reason=validation.message,
                )
                trades.append(trade)
                continue

            # Execute trade
            trade = PaperTrade(
                timestamp=timestamp,
                symbol=symbol,
                signal_type=position.signal_type,
                quantity=position.quantity,
                price=price,
                dollar_value=abs(position.quantity * price),
                z_score=0.0,  # Could extract from signal if needed
                residual=0.0,
                fill_status="filled",
            )

            trades.append(trade)
            self.trade_history.append(trade)

            # Record with risk manager
            self.risk_manager.record_trade(
                symbol=symbol,
                asset_class=self.asset_class,
                strategy_id=self.strategy_id,
                quantity=abs(position.quantity),
                entry_price=price,
                current_price=price,
            )

            # Create paper position
            paper_pos = PaperPosition(
                symbol=symbol,
                signal_type=position.signal_type,
                quantity=position.quantity,
                entry_price=price,
                current_price=price,
                unrealized_pnl=0.0,
                dollar_exposure=position.dollar_exposure,
                weight=position.weight,
            )

            self.current_positions[symbol] = paper_pos

            logger.info(
                f"Opened {symbol} ({position.signal_type.value}): "
                f"qty={position.quantity}, px=${price:.2f}"
            )

        # Update signal generator state
        for symbol, position in sized_positions.items():
            if symbol in self.current_positions:
                self.signal_generator.update_positions({symbol: position.signal_type})

        return trades

    def get_status(self) -> Dict:
        """Get current engine status.

        Returns:
            Dict with portfolio state and statistics
        """
        total_pnl = sum(pos.unrealized_pnl for pos in self.current_positions.values())
        total_return = (
            (self.portfolio_value + total_pnl - self.initial_value) / self.initial_value
            if self.initial_value > 0
            else 0.0
        )

        long_exposure = sum(
            pos.dollar_exposure
            for pos in self.current_positions.values()
            if pos.signal_type == SignalType.LONG
        )
        short_exposure = sum(
            abs(pos.dollar_exposure)
            for pos in self.current_positions.values()
            if pos.signal_type == SignalType.SHORT
        )

        return {
            "portfolio_value": self.portfolio_value + total_pnl,
            "cash": self.portfolio_value,
            "total_pnl": total_pnl,
            "total_return": total_return,
            "num_positions": len(self.current_positions),
            "long_exposure": long_exposure,
            "short_exposure": short_exposure,
            "gross_exposure": long_exposure + short_exposure,
            "net_exposure": long_exposure - short_exposure,
            "num_trades": len(self.trade_history),
            "factor_model_fitted": self.factor_model_fitted,
        }

    def shutdown(self) -> None:
        """Cleanup and export results."""
        logger.info(
            f"Shutting down StatArb engine: {len(self.trade_history)} total trades"
        )

        # Could export trades to file here if needed
        # For now, just log summary
        status = self.get_status()
        logger.info(f"Final portfolio value: ${status['portfolio_value']:.2f}")
        logger.info(f"Total return: {status['total_return']:.2%}")
