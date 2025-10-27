"""Backtesting framework for statistical arbitrage strategy.

Implements walk-forward validation with rolling factor model re-estimation.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .factor_model import FactorModel
from .portfolio import PortfolioConstructor, PortfolioPosition
from .position_sizing import PositionSizer, SizedPosition
from .signals import SignalGenerator, SignalType, PortfolioSignal

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Configuration for statistical arbitrage backtest."""

    # Portfolio parameters
    portfolio_value: float = 100_000.0
    min_positions: int = 20
    max_positions: int = 50
    target_positions: int = 30

    # Factor model parameters
    n_factors: int = 5
    factor_estimation_window: int = 60
    reestimation_frequency: int = 20  # Re-estimate factors every N days

    # Signal parameters
    signal_lookback: int = 20
    entry_threshold: float = 2.0
    exit_threshold: float = 0.5
    stop_threshold: float = 3.5

    # Position sizing parameters
    target_leverage: float = 1.0
    volatility_lookback: int = 20
    max_position_weight: float = 0.05

    # Transaction costs
    commission_rate: float = 0.001  # 0.1% per trade
    slippage_rate: float = 0.0005  # 0.05% slippage


@dataclass
class Trade:
    """Represents a trade execution."""

    timestamp: pd.Timestamp
    symbol: str
    signal_type: SignalType
    quantity: int
    price: float
    dollar_value: float
    commission: float
    slippage: float


@dataclass
class BacktestResult:
    """Results from statistical arbitrage backtest."""

    trades: List[Trade]
    equity_curve: pd.Series
    returns: pd.Series
    metrics: Dict[str, float]
    portfolio_stats: pd.DataFrame = field(default_factory=pd.DataFrame)


class StatArbBacktester:
    """Backtest engine for statistical arbitrage strategy.

    Implements walk-forward validation:
    1. Estimate factor model on rolling window
    2. Generate signals from residuals
    3. Construct portfolio and size positions
    4. Execute trades with transaction costs
    5. Track P&L and portfolio evolution
    """

    def __init__(self, config: BacktestConfig = BacktestConfig()):
        """Initialize backtester.

        Args:
            config: Backtest configuration
        """
        self.config = config

        # Initialize strategy components
        self.factor_model = FactorModel(
            n_factors=config.n_factors,
            estimation_window=config.factor_estimation_window,
        )
        self.signal_generator = SignalGenerator(
            lookback=config.signal_lookback,
            entry_threshold=config.entry_threshold,
            exit_threshold=config.exit_threshold,
            stop_threshold=config.stop_threshold,
        )
        self.portfolio_constructor = PortfolioConstructor(
            min_positions=config.min_positions,
            max_positions=config.max_positions,
            target_positions=config.target_positions,
        )
        self.position_sizer = PositionSizer(
            portfolio_value=config.portfolio_value,
            target_leverage=config.target_leverage,
            volatility_lookback=config.volatility_lookback,
            max_position_weight=config.max_position_weight,
        )

        # Backtest state
        self.trades: List[Trade] = []
        self.current_positions: Dict[str, SizedPosition] = {}
        self.position_entry_prices: Dict[str, float] = {}  # Track entry prices for P&L
        self.equity: List[float] = []  # Equity curve (realized P&L)

    def backtest(
        self,
        prices: pd.DataFrame,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> BacktestResult:
        """Run backtest on price data.

        Args:
            prices: DataFrame with prices (time × stocks)
            start_date: Start date for backtest (default: after warmup period)
            end_date: End date for backtest (default: last date)

        Returns:
            BacktestResult with trades, equity curve, and metrics

        Raises:
            ValueError: If insufficient data
        """
        # Calculate returns
        returns = prices.pct_change().dropna()

        # Determine backtest period
        min_start = (
            self.config.factor_estimation_window
            + self.config.signal_lookback
        )
        if len(returns) < min_start:
            raise ValueError(
                f"Insufficient data: need {min_start} periods, got {len(returns)}"
            )

        if start_date is None:
            start_idx = min_start
        else:
            start_idx = max(min_start, returns.index.get_loc(start_date))

        if end_date is None:
            end_idx = len(returns)
        else:
            end_idx = min(len(returns), returns.index.get_loc(end_date) + 1)

        # Reset state
        self._reset_state()

        # Initial factor model estimation
        try:
            self.factor_model.fit(returns.iloc[:start_idx])
            logger.info("Initial factor model fitted successfully")
        except ValueError as e:
            logger.error(f"Initial factor model estimation failed: {e}")
            raise ValueError(
                f"Cannot run backtest: initial factor model estimation failed: {e}"
            )

        # Walk-forward backtest
        last_reestimation = start_idx

        for i in range(start_idx, end_idx):
            current_date = returns.index[i]

            # Check if we need to re-estimate factor model
            if i - last_reestimation >= self.config.reestimation_frequency:
                self._reestimate_factors(returns.iloc[:i])
                last_reestimation = i

            # Run iteration
            self._run_iteration(
                current_date=current_date,
                returns_history=returns.iloc[: i + 1],
                prices_history=prices.iloc[: i + 1],
            )

        # Calculate results
        result = self._calculate_results()

        return result

    def _reset_state(self) -> None:
        """Reset backtest state."""
        self.trades.clear()
        self.current_positions.clear()
        self.position_entry_prices.clear()
        self.equity = [self.config.portfolio_value]  # Start with initial capital
        self.signal_generator.reset_positions()

    def _reestimate_factors(self, returns: pd.DataFrame) -> None:
        """Re-estimate factor model on recent data.

        Args:
            returns: Historical returns up to current point
        """
        try:
            self.factor_model.fit(returns)
            logger.debug("Factor model re-estimated successfully")
        except ValueError as e:
            # If factor estimation fails, keep using previous model
            logger.warning(f"Factor re-estimation failed: {e}")

    def _run_iteration(
        self,
        current_date: pd.Timestamp,
        returns_history: pd.DataFrame,
        prices_history: pd.DataFrame,
    ) -> None:
        """Run single backtest iteration.

        Args:
            current_date: Current date
            returns_history: Returns up to current date
            prices_history: Prices up to current date
        """
        # Calculate residuals from factor model
        try:
            residuals = self.factor_model.transform(returns_history)
        except ValueError as e:
            # If transform fails, skip this iteration
            logger.warning(f"Factor transform failed: {e}")
            self.equity.append(self.equity[-1])  # Carry forward equity
            return

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

        # Check for exit signals first
        self._process_exits(portfolio_signal, prices_history.iloc[[-1]])

        # Construct new portfolio from entry signals
        try:
            ideal_portfolio = self.portfolio_constructor.construct_portfolio(
                portfolio_signal
            )
        except ValueError as e:
            # If portfolio construction fails, skip new entries
            logger.debug(f"Portfolio construction failed: {e}")
            self.equity.append(self.equity[-1])  # Carry forward equity
            return

        # Size positions
        try:
            sized_positions = self.position_sizer.calculate_positions(
                ideal_portfolio, prices_history, returns_history
            )
        except ValueError as e:
            # If position sizing fails, skip new entries
            logger.warning(f"Position sizing failed: {e}")
            self.equity.append(self.equity[-1])  # Carry forward equity
            return

        # Execute trades for new positions
        self._process_entries(sized_positions, prices_history.iloc[[-1]], current_date)

        # If no trades executed, carry forward equity
        if len(self.equity) == 0 or (
            len(self.trades) > 0 and self.trades[-1].timestamp != current_date
        ):
            self.equity.append(self.equity[-1])

    def _process_exits(
        self, portfolio_signal: PortfolioSignal, current_prices: pd.DataFrame
    ) -> None:
        """Process exit signals for current positions.

        Args:
            portfolio_signal: Current portfolio signal
            current_prices: Current prices
        """
        exit_signals = self.signal_generator.get_exit_signals(portfolio_signal)

        for symbol, signal in exit_signals.items():
            if symbol in self.current_positions:
                position = self.current_positions[symbol]
                price = current_prices[symbol].iloc[0]

                # Calculate P&L
                entry_price = self.position_entry_prices[symbol]
                if position.signal_type == SignalType.LONG:
                    pnl = (price - entry_price) * position.quantity
                else:  # SHORT
                    pnl = (entry_price - price) * abs(position.quantity)

                # Calculate transaction costs
                dollar_value = abs(position.quantity * price)
                commission = dollar_value * self.config.commission_rate
                slippage = dollar_value * self.config.slippage_rate
                total_costs = commission + slippage

                # Net P&L
                net_pnl = pnl - total_costs

                # Create exit trade
                trade = Trade(
                    timestamp=portfolio_signal.timestamp,
                    symbol=symbol,
                    signal_type=signal,
                    quantity=-position.quantity,
                    price=price,
                    dollar_value=dollar_value,
                    commission=commission,
                    slippage=slippage,
                )

                self.trades.append(trade)

                # Update equity
                self.equity.append(self.equity[-1] + net_pnl)

                # Remove from current positions
                del self.current_positions[symbol]
                del self.position_entry_prices[symbol]

                # Update signal generator state
                self.signal_generator.update_positions({symbol: SignalType.NO_POSITION})

                logger.debug(
                    f"Closed {symbol}: P&L=${net_pnl:.2f} (entry=${entry_price:.2f}, "
                    f"exit=${price:.2f})"
                )

    def _process_entries(
        self,
        sized_positions: Dict[str, SizedPosition],
        current_prices: pd.DataFrame,
        current_date: pd.Timestamp,
    ) -> None:
        """Process entry signals for new positions.

        Args:
            sized_positions: Sized positions to enter
            current_prices: Current prices
            current_date: Current timestamp
        """
        for symbol, position in sized_positions.items():
            # Skip if already in position
            if symbol in self.current_positions:
                continue

            price = current_prices[symbol].iloc[0]

            # Calculate transaction costs
            dollar_value = abs(position.quantity * price)
            commission = dollar_value * self.config.commission_rate
            slippage = dollar_value * self.config.slippage_rate
            total_costs = commission + slippage

            # Create entry trade
            trade = Trade(
                timestamp=current_date,
                symbol=symbol,
                signal_type=position.signal_type,
                quantity=position.quantity,
                price=price,
                dollar_value=dollar_value,
                commission=commission,
                slippage=slippage,
            )

            self.trades.append(trade)

            # Update equity (subtract entry costs)
            self.equity.append(self.equity[-1] - total_costs)

            # Add to current positions
            self.current_positions[symbol] = position
            self.position_entry_prices[symbol] = price

            # Update signal generator state
            self.signal_generator.update_positions({symbol: position.signal_type})

            logger.debug(
                f"Opened {symbol} ({position.signal_type.value}): qty={position.quantity}, "
                f"px=${price:.2f}"
            )


    def _calculate_results(self) -> BacktestResult:
        """Calculate backtest metrics and results.

        Returns:
            BacktestResult with all backtest outputs
        """
        # Build equity curve from equity list
        # Note: equity list has N+1 elements (initial + N iterations)
        # But we want to align with returns index which has N elements

        # Get returns index from backtest period
        returns_data = self.trades[0].timestamp if self.trades else None

        # Create equity series - use last N elements to match backtest period
        equity_series = pd.Series(self.equity)

        # Trim to match actual backtest dates if needed
        if len(equity_series) > 1:
            # Get date range from trades or use sequential
            if self.trades:
                dates = sorted(set(t.timestamp for t in self.trades))
                # Equity has one entry per iteration, align with dates
                # For now, use simple index
                equity_curve = equity_series
            else:
                equity_curve = equity_series
        else:
            equity_curve = equity_series

        # Returns
        returns = equity_curve.pct_change().dropna()

        # Calculate metrics
        metrics = self._calculate_metrics(equity_curve, returns)

        result = BacktestResult(
            trades=self.trades,
            equity_curve=equity_curve,
            returns=returns,
            metrics=metrics,
            portfolio_stats=pd.DataFrame(),
        )

        return result

    def _calculate_metrics(
        self, equity_curve: pd.Series, returns: pd.Series
    ) -> Dict[str, float]:
        """Calculate backtest performance metrics.

        Args:
            equity_curve: Portfolio value over time
            returns: Portfolio returns

        Returns:
            Dict with performance metrics
        """
        if len(returns) == 0:
            return {
                "total_return": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "num_trades": 0,
            }

        # Total return
        total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1

        # Sharpe ratio (annualized)
        if returns.std() > 0:
            sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(252)
        else:
            sharpe_ratio = 0.0

        # Max drawdown
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()

        # Win rate (from trades)
        profitable_trades = sum(
            1
            for trade in self.trades
            if trade.signal_type in [SignalType.EXIT_LONG, SignalType.EXIT_SHORT]
        )
        total_closed_trades = len(
            [
                t
                for t in self.trades
                if t.signal_type in [SignalType.EXIT_LONG, SignalType.EXIT_SHORT]
            ]
        )
        win_rate = (
            profitable_trades / total_closed_trades if total_closed_trades > 0 else 0.0
        )

        metrics = {
            "total_return": total_return,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "num_trades": len(self.trades),
            "avg_daily_return": returns.mean(),
            "volatility": returns.std() * np.sqrt(252),
        }

        return metrics
