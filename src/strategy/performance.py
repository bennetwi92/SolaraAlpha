"""
Strategy Framework - Performance Tracking

Tracks strategy performance in real-time with attribution analysis.
"""

from dataclasses import dataclass
from typing import Dict, List
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


@dataclass
class StrategyPerformance:
    """
    Strategy-level performance metrics.

    Attributes:
        total_pnl: Total profit/loss
        realized_pnl: Realized P&L
        unrealized_pnl: Unrealized P&L
        num_trades: Total trades
        win_rate: Percentage of winning trades
        sharpe_ratio: Sharpe ratio
        max_drawdown: Maximum drawdown
        avg_hold_time: Average holding period
    """
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    num_trades: int = 0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    avg_hold_time: float = 0.0  # in hours


class PerformanceTracker:
    """
    Tracks strategy performance in real-time.

    Provides attribution analysis, metric calculation, and diagnostics.
    """

    def __init__(self, strategy_name: str):
        """
        Initialize performance tracker.

        Args:
            strategy_name: Strategy name for tracking
        """
        self.strategy_name = strategy_name
        self.pnl_history: List[float] = []
        self.trade_history: List[Dict] = []
        self.equity_curve: pd.Series = pd.Series(dtype=float)

    def record_trade(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        entry_time: pd.Timestamp,
        exit_time: pd.Timestamp,
        pnl: float
    ) -> None:
        """Record completed trade."""
        hold_time = (exit_time - entry_time).total_seconds() / 3600  # hours

        trade_record = {
            'symbol': symbol,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'quantity': quantity,
            'entry_time': entry_time,
            'exit_time': exit_time,
            'pnl': pnl,
            'hold_time_hours': hold_time
        }

        self.trade_history.append(trade_record)

    def calculate_performance(self) -> StrategyPerformance:
        """Calculate current performance metrics."""
        if not self.trade_history:
            return StrategyPerformance()

        df = pd.DataFrame(self.trade_history)

        # Win rate
        winning_trades = len(df[df['pnl'] > 0])
        win_rate = winning_trades / len(df) if len(df) > 0 else 0.0

        # P&L
        total_pnl = df['pnl'].sum()

        # Sharpe ratio (simplified)
        returns = df['pnl']
        if returns.std() > 0:
            sharpe = (returns.mean() / returns.std()) * np.sqrt(252)
        else:
            sharpe = 0.0

        # Average hold time
        avg_hold_time = df['hold_time_hours'].mean()

        return StrategyPerformance(
            total_pnl=total_pnl,
            realized_pnl=total_pnl,
            num_trades=len(df),
            win_rate=win_rate,
            sharpe_ratio=sharpe,
            avg_hold_time=avg_hold_time
        )
