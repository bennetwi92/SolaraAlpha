"""Backtesting framework for FX carry trade strategy."""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .position_sizing import FXCarryPositionSizer
from .signals import FXCarrySignals

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Configuration for FX carry backtest."""

    # Portfolio parameters
    portfolio_value: float = 100_000  # Initial capital
    max_positions: int = 10  # Maximum concurrent positions

    # Signal parameters
    trend_lookback: int = 60  # Days for trend calculation
    trend_threshold: float = 0.0  # Minimum trend score
    vol_lookback: int = 30  # Days for volatility
    vol_target: float = 10.0  # Target portfolio volatility (%)
    min_rate_diff: float = 1.0  # Minimum rate differential (%)

    # Position sizing parameters
    max_position_pct: float = 0.20  # Max 20% per position
    max_leverage: float = 1.0  # No leverage
    rebalance_threshold: float = 0.10  # 10% drift triggers rebalance

    # Transaction costs
    commission_pct: float = 0.001  # 0.1% commission (10 bps)
    slippage_pct: float = 0.0005  # 0.05% slippage (5 bps)

    # Rebalancing frequency
    rebalance_frequency_days: int = 7  # Weekly rebalancing


@dataclass
class BacktestResult:
    """Results from FX carry backtest."""

    config: BacktestConfig
    equity_curve: pd.Series
    trades: pd.DataFrame
    positions: pd.DataFrame
    metrics: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        """Calculate performance metrics."""
        if not self.equity_curve.empty:
            self.metrics = self._calculate_metrics()

    def _calculate_metrics(self) -> Dict[str, float]:
        """Calculate strategy performance metrics."""
        equity = self.equity_curve

        # Returns
        total_return = (equity.iloc[-1] / equity.iloc[0]) - 1
        returns = equity.pct_change().dropna()

        # Annualization factor (assuming daily data)
        days = (equity.index[-1] - equity.index[0]).days
        years = days / 365.25
        ann_factor = 252  # Trading days per year

        # Annualized return
        if years > 0:
            ann_return = (1 + total_return) ** (1 / years) - 1
        else:
            ann_return = 0.0

        # Volatility
        ann_volatility = returns.std() * np.sqrt(ann_factor)

        # Sharpe ratio (assuming 0% risk-free rate)
        if ann_volatility > 0:
            sharpe_ratio = ann_return / ann_volatility
        else:
            sharpe_ratio = 0.0

        # Drawdown
        cummax = equity.expanding().max()
        drawdown = (equity - cummax) / cummax
        max_drawdown = drawdown.min()

        # Win rate
        if len(self.trades) > 0:
            win_rate = (self.trades["pnl"] > 0).mean()
            avg_win = self.trades[self.trades["pnl"] > 0]["pnl"].mean() if (self.trades["pnl"] > 0).any() else 0.0
            avg_loss = self.trades[self.trades["pnl"] <= 0]["pnl"].mean() if (self.trades["pnl"] <= 0).any() else 0.0
        else:
            win_rate = 0.0
            avg_win = 0.0
            avg_loss = 0.0

        return {
            "total_return": total_return,
            "ann_return": ann_return,
            "ann_volatility": ann_volatility,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "num_trades": len(self.trades),
            "final_equity": equity.iloc[-1],
        }


class FXCarryBacktester:
    """Backtest FX carry trade strategy.

    Features:
    - Walk-forward backtesting with rolling rate differential updates
    - Transaction cost modeling (commission + slippage)
    - Realistic position sizing and rebalancing
    - Comprehensive performance metrics

    Example:
        >>> backtester = FXCarryBacktester()
        >>> result = backtester.backtest(prices_dict, rate_differentials)
        >>> print(f"Sharpe: {result.metrics['sharpe_ratio']:.2f}")
    """

    def __init__(self, config: Optional[BacktestConfig] = None) -> None:
        """Initialize backtester.

        Args:
            config: Backtest configuration (uses defaults if None)
        """
        self.config = config or BacktestConfig()

        # Initialize components
        self.signal_generator = FXCarrySignals(
            trend_lookback=self.config.trend_lookback,
            trend_threshold=self.config.trend_threshold,
            vol_lookback=self.config.vol_lookback,
            vol_target=self.config.vol_target,
            min_rate_diff=self.config.min_rate_diff,
        )

        self.position_sizer = FXCarryPositionSizer(
            vol_target=self.config.vol_target,
            max_position_pct=self.config.max_position_pct,
            max_leverage=self.config.max_leverage,
        )

        logger.info("Initialized FX carry backtester")

    def _calculate_transaction_costs(
        self, trade_size: float, price: float
    ) -> float:
        """Calculate transaction costs for a trade.

        Args:
            trade_size: Position size in dollars (absolute value)
            price: Execution price

        Returns:
            Total transaction cost ($)
        """
        commission = abs(trade_size) * self.config.commission_pct
        slippage = abs(trade_size) * self.config.slippage_pct
        return commission + slippage

    def _execute_rebalance(
        self,
        current_positions: Dict[str, Dict],
        target_positions: Dict[str, float],
        prices: Dict[str, float],
        timestamp: pd.Timestamp,
    ) -> tuple[Dict[str, Dict], list, float]:
        """Execute portfolio rebalance.

        Args:
            current_positions: Current positions {pair: {size, entry_price}}
            target_positions: Target position sizes {pair: size ($)}
            prices: Current prices {pair: price}
            timestamp: Rebalance timestamp

        Returns:
            Tuple of (new_positions, trades, total_costs)
        """
        trades = []
        total_costs = 0.0
        new_positions = current_positions.copy()

        # Calculate required trades
        current_sizes = {
            pair: pos["size"] for pair, pos in current_positions.items()
        }
        trades_needed = self.position_sizer.calculate_rebalance(
            current_sizes,
            target_positions,
            self.config.rebalance_threshold,
        )

        # Execute trades
        for pair, trade_size in trades_needed.items():
            if pair not in prices:
                logger.warning(f"Missing price for {pair} at {timestamp}")
                continue

            price = prices[pair]

            # Calculate transaction costs
            costs = self._calculate_transaction_costs(trade_size, price)
            total_costs += costs

            # Update position
            current_size = current_sizes.get(pair, 0.0)
            new_size = current_size + trade_size

            if abs(new_size) < 1.0:  # Close position if very small
                if pair in new_positions:
                    # Record closing trade
                    entry_price = new_positions[pair]["entry_price"]
                    pnl = (price - entry_price) / entry_price * current_size
                    trades.append(
                        {
                            "timestamp": timestamp,
                            "pair": pair,
                            "action": "close",
                            "size": -current_size,
                            "price": price,
                            "entry_price": entry_price,
                            "pnl": pnl - costs,
                            "costs": costs,
                        }
                    )
                    del new_positions[pair]
            else:
                # Update or create position
                if pair in new_positions:
                    # Existing position - update
                    old_entry = new_positions[pair]["entry_price"]
                    # Weighted average entry price
                    if abs(new_size) > abs(current_size):
                        # Increasing position
                        new_entry = (
                            old_entry * abs(current_size)
                            + price * abs(trade_size)
                        ) / abs(new_size)
                    else:
                        # Decreasing position - keep old entry
                        new_entry = old_entry

                    new_positions[pair] = {"size": new_size, "entry_price": new_entry}
                else:
                    # New position
                    new_positions[pair] = {"size": new_size, "entry_price": price}

                # Record trade
                trades.append(
                    {
                        "timestamp": timestamp,
                        "pair": pair,
                        "action": "rebalance",
                        "size": trade_size,
                        "price": price,
                        "costs": costs,
                        "pnl": 0.0,  # P&L calculated at close
                        "entry_price": new_positions[pair]["entry_price"],
                    }
                )

        return new_positions, trades, total_costs

    def backtest(
        self,
        prices_dict: Dict[str, pd.DataFrame],
        rate_differentials: Dict[str, float],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> BacktestResult:
        """Run backtest on historical data.

        Args:
            prices_dict: Dict mapping pair -> OHLCV DataFrame
            rate_differentials: Dict mapping pair -> rate differential (%)
            start_date: Backtest start date (uses data start if None)
            end_date: Backtest end date (uses data end if None)

        Returns:
            BacktestResult with equity curve, trades, and metrics
        """
        logger.info("Starting FX carry backtest")

        # Determine date range
        all_dates = pd.DatetimeIndex([])
        for df in prices_dict.values():
            all_dates = all_dates.union(df.index)

        all_dates = all_dates.sort_values()

        if start_date:
            all_dates = all_dates[all_dates >= pd.Timestamp(start_date)]
        if end_date:
            all_dates = all_dates[all_dates <= pd.Timestamp(end_date)]

        if len(all_dates) == 0:
            raise ValueError("No data in specified date range")

        # Initialize tracking
        equity = self.config.portfolio_value
        equity_curve = []
        positions = {}  # {pair: {size: $, entry_price: price}}
        all_trades = []
        position_history = []

        # Rebalance counter
        days_since_rebalance = self.config.rebalance_frequency_days

        logger.info(
            f"Backtesting from {all_dates[0].date()} to {all_dates[-1].date()} "
            f"({len(all_dates)} days, {len(prices_dict)} pairs)"
        )

        # Walk through each day
        for i, date in enumerate(all_dates):
            # Get current prices
            current_prices = {}
            for pair, df in prices_dict.items():
                if date in df.index:
                    current_prices[pair] = df.loc[date, "Close"]

            # Calculate mark-to-market P&L
            mtm_pnl = 0.0
            for pair, pos in positions.items():
                if pair in current_prices:
                    current_price = current_prices[pair]
                    entry_price = pos["entry_price"]
                    pnl = (current_price - entry_price) / entry_price * pos["size"]
                    mtm_pnl += pnl

            current_equity = self.config.portfolio_value + mtm_pnl

            # Check if rebalance needed
            days_since_rebalance += 1
            if days_since_rebalance >= self.config.rebalance_frequency_days:
                # Generate signals
                signals = self.signal_generator.generate_portfolio_signals(
                    prices_dict,
                    rate_differentials,
                    timestamp=date,
                    max_positions=self.config.max_positions,
                )

                # Calculate target positions
                target_positions = self.position_sizer.calculate_position_sizes(
                    signals, current_equity
                )

                # Execute rebalance
                positions, trades, costs = self._execute_rebalance(
                    positions, target_positions, current_prices, date
                )

                all_trades.extend(trades)
                current_equity -= costs  # Deduct transaction costs
                days_since_rebalance = 0

                logger.debug(
                    f"{date.date()}: Rebalanced to {len(positions)} positions, "
                    f"costs=${costs:.2f}"
                )

            # Record equity and positions
            equity_curve.append({"date": date, "equity": current_equity})
            for pair, pos in positions.items():
                position_history.append(
                    {
                        "date": date,
                        "pair": pair,
                        "size": pos["size"],
                        "entry_price": pos["entry_price"],
                    }
                )

        # Convert to DataFrames
        equity_df = pd.DataFrame(equity_curve).set_index("date")["equity"]
        trades_df = pd.DataFrame(all_trades) if all_trades else pd.DataFrame()
        positions_df = (
            pd.DataFrame(position_history) if position_history else pd.DataFrame()
        )

        result = BacktestResult(
            config=self.config,
            equity_curve=equity_df,
            trades=trades_df,
            positions=positions_df,
        )

        logger.info(
            f"Backtest complete: {result.metrics['num_trades']} trades, "
            f"Return: {result.metrics['total_return']:.2%}, "
            f"Sharpe: {result.metrics['sharpe_ratio']:.2f}"
        )

        return result
