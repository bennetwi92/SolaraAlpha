"""Walk-forward backtesting framework for ETF arbitrage strategy.

Implements rigorous out-of-sample validation with rolling spread statistics
re-estimation to avoid overfitting. Includes transaction cost modeling
and realistic execution simulation.
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from .spread import SpreadCalculator
from .signals import SignalGenerator, SignalConfig, SignalType
from .position_sizing import PositionSizer, PositionSizingConfig

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Backtesting configuration."""
    train_pct: float = 0.6  # 60% training data
    lookback_window: int = 60  # Rolling window for spread statistics
    reestimate_every: int = 20  # Re-estimate spread stats every 20 days
    commission_pct: float = 0.001  # 0.1% commission
    slippage_pct: float = 0.0005  # 0.05% slippage
    initial_capital: float = 100000.0  # $100k starting capital


@dataclass
class Trade:
    """Individual trade record."""
    timestamp: pd.Timestamp
    symbol1: str
    symbol2: str
    quantity1: int
    quantity2: int
    price1: float
    price2: float
    signal_type: str  # 'enter_long_spread', 'enter_short_spread', 'exit', 'stop_loss'
    z_score: float
    pnl: float = 0.0
    costs: float = 0.0


@dataclass
class BacktestResult:
    """Comprehensive backtest results."""
    trades: List[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    returns: pd.Series = field(default_factory=pd.Series)
    metrics: Dict[str, float] = field(default_factory=dict)
    spread_history: pd.DataFrame = field(default_factory=pd.DataFrame)


class ETFArbBacktester:
    """Walk-forward backtesting engine for ETF arbitrage.

    Methodology:
    1. Split data into train/test sets (60/40)
    2. Estimate spread statistics on training data
    3. Test on out-of-sample data
    4. Re-estimate spread statistics on rolling windows (every 20 days)
    5. Calculate performance with transaction costs
    """

    def __init__(
        self,
        config: Optional[BacktestConfig] = None,
        signal_config: Optional[SignalConfig] = None,
        position_config: Optional[PositionSizingConfig] = None
    ):
        """Initialize backtester.

        Args:
            config: Backtesting configuration
            signal_config: Signal generation configuration
            position_config: Position sizing configuration
        """
        self.config = config or BacktestConfig()
        self.signal_config = signal_config or SignalConfig()
        self.position_config = position_config or PositionSizingConfig()

        # Initialize components
        self.spread_calc = SpreadCalculator(
            lookback_window=self.config.lookback_window
        )
        self.signal_gen = SignalGenerator(config=self.signal_config)
        self.position_sizer = PositionSizer(
            portfolio_value=self.config.initial_capital,
            config=self.position_config
        )

    def split_train_test(
        self,
        data: pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Split data into training and testing sets.

        Args:
            data: Full dataset with time index

        Returns:
            Tuple of (train_data, test_data)
        """
        split_idx = int(len(data) * self.config.train_pct)
        train = data.iloc[:split_idx]
        test = data.iloc[split_idx:]

        logger.info(
            f"Data split | Train: {len(train)} ({train.index[0]} to {train.index[-1]}), "
            f"Test: {len(test)} ({test.index[0]} to {test.index[-1]})"
        )

        return train, test

    def simulate_trade_costs(self, trade_value: float) -> float:
        """Calculate transaction costs for a trade.

        Args:
            trade_value: Absolute dollar value of trade

        Returns:
            Total costs (commission + slippage)
        """
        commission = trade_value * self.config.commission_pct
        slippage = trade_value * self.config.slippage_pct
        return commission + slippage

    def backtest_pair(
        self,
        series1: pd.Series,
        series2: pd.Series,
        symbol1: str,
        symbol2: str
    ) -> BacktestResult:
        """Backtest ETF arbitrage strategy on ETF pair.

        Args:
            series1: First ETF price series
            series2: Second ETF price series
            symbol1: First ETF symbol
            symbol2: Second ETF symbol

        Returns:
            BacktestResult with trades, equity curve, and metrics
        """
        logger.info(f"Starting backtest for {symbol1}/{symbol2}")

        # Combine into DataFrame
        data = pd.DataFrame({'price1': series1, 'price2': series2})

        # Split train/test
        train_data, test_data = self.split_train_test(data)

        # Calculate spread history on full test period
        spread_df = self.spread_calc.get_spread_history(
            test_data['price1'],
            test_data['price2'],
            method='ratio'
        )

        # Initialize tracking
        trades = []
        equity = [self.config.initial_capital]
        current_position = None
        position = None
        days_since_reestimate = 0
        win_count = 0
        loss_count = 0
        total_wins = 0.0
        total_losses = 0.0

        # Walk forward through test data
        for i in range(self.config.lookback_window, len(test_data)):
            current_date = test_data.index[i]
            price1 = test_data['price1'].iloc[i]
            price2 = test_data['price2'].iloc[i]

            # Re-estimate spread statistics periodically
            if days_since_reestimate >= self.config.reestimate_every or days_since_reestimate == 0:
                # Use rolling window for re-estimation
                window_start = max(0, i - self.config.lookback_window)
                window_data = test_data.iloc[window_start:i+1]

                spread_df = self.spread_calc.get_spread_history(
                    window_data['price1'],
                    window_data['price2'],
                    method='ratio'
                )
                days_since_reestimate = 0

                # Debug logging for first iteration
                if i == self.config.lookback_window:
                    logger.info(f"First iteration: i={i}, window_data len={len(window_data)}, spread_df len={len(spread_df)}")
                    logger.info(f"Window dates: {window_data.index[0]} to {window_data.index[-1]}")
                    if len(spread_df) > 0:
                        logger.info(f"Last z-score: {spread_df['z_score'].iloc[-1]}")

            # Get current spread metrics (use last value from spread_df)
            if len(spread_df) > 0 and not spread_df.empty:
                z_score = spread_df['z_score'].iloc[-1]
                spread = spread_df['spread'].iloc[-1]
                rolling_std = spread_df['rolling_std'].iloc[-1]
            else:
                z_score = np.nan
                spread = np.nan
                rolling_std = np.nan

            # Skip if invalid metrics
            if np.isnan(z_score) or np.isnan(spread) or np.isnan(rolling_std):
                days_since_reestimate += 1
                equity.append(equity[-1])
                if i == self.config.lookback_window:
                    logger.info(f"Skipping first iteration due to invalid metrics: z={z_score}, spread={spread}, std={rolling_std}")
                continue

            # Calculate half-life for mean reversion check
            recent_spread = spread_df['spread'].dropna().tail(min(60, len(spread_df)))
            half_life = self.spread_calc.calculate_half_life(recent_spread) if len(recent_spread) > 1 else np.nan

            # Generate signal
            signal = self.signal_gen.generate_signal(
                z_score=z_score,
                spread=spread,
                half_life=half_life,
                timestamp=current_date,
                current_position=current_position
            )

            # Log signal if not NO_SIGNAL
            if signal.signal_type != SignalType.NO_SIGNAL:
                logger.info(f"{current_date.date()}: {signal.signal_type.value}, z={z_score:.2f}, reason={signal.reason}")

            # Process signal
            if signal.signal_type == SignalType.ENTER_LONG_SPREAD:
                # Enter long spread position
                win_rate = win_count / (win_count + loss_count) if (win_count + loss_count) > 0 else 0.5
                avg_win = total_wins / win_count if win_count > 0 else 0.01
                avg_loss = total_losses / loss_count if loss_count > 0 else 0.01

                position = self.position_sizer.calculate_position_size(
                    symbol1=symbol1,
                    symbol2=symbol2,
                    price1=price1,
                    price2=price2,
                    z_score=z_score,
                    spread_volatility=rolling_std,
                    win_rate=win_rate,
                    avg_win=avg_win,
                    avg_loss=avg_loss
                )

                # Calculate costs
                costs = self.simulate_trade_costs(position.total_capital)

                trade = Trade(
                    timestamp=current_date,
                    symbol1=symbol1,
                    symbol2=symbol2,
                    quantity1=position.quantity1,
                    quantity2=position.quantity2,
                    price1=price1,
                    price2=price2,
                    signal_type='enter_long_spread',
                    z_score=z_score,
                    costs=costs
                )
                trades.append(trade)
                current_position = 'long_spread'

                # Deduct costs from equity
                equity.append(equity[-1] - costs)

            elif signal.signal_type == SignalType.ENTER_SHORT_SPREAD:
                # Enter short spread position
                win_rate = win_count / (win_count + loss_count) if (win_count + loss_count) > 0 else 0.5
                avg_win = total_wins / win_count if win_count > 0 else 0.01
                avg_loss = total_losses / loss_count if loss_count > 0 else 0.01

                position = self.position_sizer.calculate_position_size(
                    symbol1=symbol1,
                    symbol2=symbol2,
                    price1=price1,
                    price2=price2,
                    z_score=z_score,
                    spread_volatility=rolling_std,
                    win_rate=win_rate,
                    avg_win=avg_win,
                    avg_loss=avg_loss
                )

                # Calculate costs
                costs = self.simulate_trade_costs(position.total_capital)

                trade = Trade(
                    timestamp=current_date,
                    symbol1=symbol1,
                    symbol2=symbol2,
                    quantity1=position.quantity1,
                    quantity2=position.quantity2,
                    price1=price1,
                    price2=price2,
                    signal_type='enter_short_spread',
                    z_score=z_score,
                    costs=costs
                )
                trades.append(trade)
                current_position = 'short_spread'

                # Deduct costs from equity
                equity.append(equity[-1] - costs)

            elif signal.signal_type in [SignalType.EXIT_POSITION, SignalType.STOP_LOSS]:
                # Exit position
                if position is not None:
                    # Calculate P&L
                    pnl1 = position.quantity1 * (price1 - position.price1)
                    pnl2 = position.quantity2 * (price2 - position.price2)
                    pnl = pnl1 + pnl2

                    # Calculate costs
                    costs = self.simulate_trade_costs(position.total_capital)

                    # Net P&L after costs
                    net_pnl = pnl - costs

                    # Track win/loss statistics
                    if net_pnl > 0:
                        win_count += 1
                        total_wins += net_pnl / position.total_capital
                    else:
                        loss_count += 1
                        total_losses += abs(net_pnl / position.total_capital)

                    trade = Trade(
                        timestamp=current_date,
                        symbol1=symbol1,
                        symbol2=symbol2,
                        quantity1=-position.quantity1,
                        quantity2=-position.quantity2,
                        price1=price1,
                        price2=price2,
                        signal_type='exit' if signal.signal_type == SignalType.EXIT_POSITION else 'stop_loss',
                        z_score=z_score,
                        pnl=net_pnl,
                        costs=costs
                    )
                    trades.append(trade)

                    # Update equity
                    equity.append(equity[-1] + net_pnl)

                    # Clear position
                    position = None
                    current_position = None
                else:
                    equity.append(equity[-1])
            else:
                # No signal, maintain equity
                equity.append(equity[-1])

            # Update position sizer with current equity
            self.position_sizer.update_portfolio_value(equity[-1])

            days_since_reestimate += 1

        # Create equity curve
        equity_curve = pd.Series(equity, index=[test_data.index[0]] + list(test_data.index[self.config.lookback_window:]))

        # Calculate returns
        returns = equity_curve.pct_change().dropna()

        # Calculate metrics
        metrics = self._calculate_metrics(equity_curve, returns, trades)

        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            returns=returns,
            metrics=metrics,
            spread_history=spread_df
        )

    def _calculate_metrics(
        self,
        equity_curve: pd.Series,
        returns: pd.Series,
        trades: List[Trade]
    ) -> Dict[str, float]:
        """Calculate performance metrics.

        Args:
            equity_curve: Portfolio value over time
            returns: Daily returns
            trades: List of executed trades

        Returns:
            Dictionary of performance metrics
        """
        total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1

        # Annualized metrics
        trading_days = len(returns)
        years = trading_days / 252

        if years > 0:
            annualized_return = (1 + total_return) ** (1 / years) - 1
        else:
            annualized_return = 0

        # Sharpe ratio (assuming 0% risk-free rate)
        if len(returns) > 1 and returns.std() > 0:
            sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(252)
        else:
            sharpe_ratio = 0

        # Max drawdown
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()

        # Trade statistics
        num_trades = len(trades)
        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl < 0]

        win_rate = len(winning_trades) / num_trades if num_trades > 0 else 0

        avg_win = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t.pnl for t in losing_trades]) if losing_trades else 0

        profit_factor = abs(sum([t.pnl for t in winning_trades]) / sum([t.pnl for t in losing_trades])) if losing_trades and sum([t.pnl for t in losing_trades]) != 0 else 0

        total_costs = sum([t.costs for t in trades])

        return {
            'total_return': total_return,
            'annualized_return': annualized_return,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'num_trades': num_trades,
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'total_costs': total_costs,
            'final_equity': equity_curve.iloc[-1]
        }
