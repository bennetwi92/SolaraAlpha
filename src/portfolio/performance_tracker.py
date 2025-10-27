"""Strategy performance tracking for portfolio allocation decisions.

Tracks returns, Sharpe ratio, drawdown, volatility, and correlation metrics
for each strategy to inform capital allocation decisions.
"""
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class StrategyPerformance:
    """Performance metrics for a single strategy.

    Attributes:
        strategy_id: Unique strategy identifier
        returns: Series of daily returns
        total_return: Cumulative return
        sharpe_ratio: Sharpe ratio (annualized)
        sortino_ratio: Sortino ratio (annualized)
        max_drawdown: Maximum drawdown
        current_drawdown: Current drawdown from peak
        volatility: Annualized volatility
        win_rate: Percentage of profitable periods
        avg_win: Average winning return
        avg_loss: Average losing return
        profit_factor: Gross profit / gross loss
        last_updated: Last update timestamp
    """

    strategy_id: str
    returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    volatility: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    last_updated: datetime = field(default_factory=datetime.now)


class PerformanceTracker:
    """Tracks and calculates performance metrics for multiple strategies.

    Maintains historical returns and calculates risk-adjusted performance
    metrics used for capital allocation decisions.

    Example:
        >>> tracker = PerformanceTracker()
        >>> tracker.record_return('pairs_1', 0.02, datetime.now())
        >>> perf = tracker.get_performance('pairs_1')
        >>> print(f"Sharpe: {perf.sharpe_ratio:.2f}")
    """

    def __init__(
        self,
        lookback_days: int = 252,  # 1 year of trading days
        min_history_days: int = 30,
    ) -> None:
        """Initialize performance tracker.

        Args:
            lookback_days: Number of days of history to maintain
            min_history_days: Minimum days required for metric calculation
        """
        self.lookback_days = lookback_days
        self.min_history_days = min_history_days

        # Strategy performance data
        self._returns: Dict[str, deque] = {}  # strategy_id -> deque of (date, return)
        self._performance: Dict[str, StrategyPerformance] = {}

        logger.info(
            f"Performance tracker initialized | "
            f"Lookback: {lookback_days}d, Min history: {min_history_days}d"
        )

    def record_return(
        self,
        strategy_id: str,
        daily_return: float,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Record a daily return for a strategy.

        Args:
            strategy_id: Strategy identifier
            daily_return: Daily return as decimal (e.g., 0.02 for 2%)
            timestamp: Return timestamp (uses current time if None)
        """
        if timestamp is None:
            timestamp = datetime.now()

        # Initialize if new strategy
        if strategy_id not in self._returns:
            self._returns[strategy_id] = deque(maxlen=self.lookback_days)
            self._performance[strategy_id] = StrategyPerformance(
                strategy_id=strategy_id
            )

        # Record return
        self._returns[strategy_id].append((timestamp, daily_return))

        # Update performance metrics
        self._update_performance(strategy_id)

        logger.debug(
            f"Recorded return for {strategy_id}: {daily_return:.4f}",
            extra={"context": {"strategy_id": strategy_id, "return": daily_return}},
        )

    def get_performance(self, strategy_id: str) -> Optional[StrategyPerformance]:
        """Get current performance metrics for a strategy.

        Args:
            strategy_id: Strategy identifier

        Returns:
            StrategyPerformance object or None if not found
        """
        return self._performance.get(strategy_id)

    def get_all_performance(self) -> Dict[str, StrategyPerformance]:
        """Get performance metrics for all strategies.

        Returns:
            Dict mapping strategy_id to StrategyPerformance
        """
        return self._performance.copy()

    def get_correlation_matrix(
        self, strategy_ids: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """Calculate correlation matrix between strategies.

        Args:
            strategy_ids: List of strategies to include (all if None)

        Returns:
            DataFrame with correlation matrix
        """
        if strategy_ids is None:
            strategy_ids = list(self._returns.keys())

        # Build returns dataframe
        returns_dict = {}
        for sid in strategy_ids:
            if sid in self._returns and len(self._returns[sid]) >= self.min_history_days:
                returns_series = self._get_returns_series(sid)
                returns_dict[sid] = returns_series

        if len(returns_dict) < 2:
            # Return empty matrix if insufficient strategies
            return pd.DataFrame()

        returns_df = pd.DataFrame(returns_dict)
        correlation_matrix = returns_df.corr()

        return correlation_matrix

    def get_strategy_correlation(
        self, strategy_id_1: str, strategy_id_2: str
    ) -> Optional[float]:
        """Calculate correlation between two strategies.

        Args:
            strategy_id_1: First strategy identifier
            strategy_id_2: Second strategy identifier

        Returns:
            Correlation coefficient or None if insufficient data
        """
        if strategy_id_1 not in self._returns or strategy_id_2 not in self._returns:
            return None

        if (
            len(self._returns[strategy_id_1]) < self.min_history_days
            or len(self._returns[strategy_id_2]) < self.min_history_days
        ):
            return None

        returns_1 = self._get_returns_series(strategy_id_1)
        returns_2 = self._get_returns_series(strategy_id_2)

        # Align dates
        aligned = pd.DataFrame(
            {"s1": returns_1, "s2": returns_2}
        ).dropna()

        if len(aligned) < self.min_history_days:
            return None

        correlation = aligned["s1"].corr(aligned["s2"])
        return correlation

    def _update_performance(self, strategy_id: str) -> None:
        """Update performance metrics for a strategy.

        Args:
            strategy_id: Strategy identifier
        """
        returns_data = self._returns[strategy_id]

        if len(returns_data) < self.min_history_days:
            logger.debug(
                f"Insufficient data for {strategy_id}: "
                f"{len(returns_data)}/{self.min_history_days} days"
            )
            return

        # Convert to series
        returns_series = self._get_returns_series(strategy_id)

        # Calculate metrics
        perf = self._performance[strategy_id]
        perf.returns = returns_series
        perf.total_return = self._calculate_total_return(returns_series)
        perf.sharpe_ratio = self._calculate_sharpe(returns_series)
        perf.sortino_ratio = self._calculate_sortino(returns_series)
        perf.max_drawdown = self._calculate_max_drawdown(returns_series)
        perf.current_drawdown = self._calculate_current_drawdown(returns_series)
        perf.volatility = self._calculate_volatility(returns_series)
        perf.win_rate = self._calculate_win_rate(returns_series)
        perf.avg_win, perf.avg_loss = self._calculate_avg_win_loss(returns_series)
        perf.profit_factor = self._calculate_profit_factor(returns_series)
        perf.last_updated = datetime.now()

    def _get_returns_series(self, strategy_id: str) -> pd.Series:
        """Convert returns deque to pandas Series.

        Args:
            strategy_id: Strategy identifier

        Returns:
            Series indexed by date
        """
        returns_data = self._returns[strategy_id]
        dates = [item[0] for item in returns_data]
        returns = [item[1] for item in returns_data]
        return pd.Series(returns, index=pd.DatetimeIndex(dates))

    def _calculate_total_return(self, returns: pd.Series) -> float:
        """Calculate cumulative return.

        Args:
            returns: Series of daily returns

        Returns:
            Total cumulative return
        """
        return (1 + returns).prod() - 1

    def _calculate_sharpe(self, returns: pd.Series) -> float:
        """Calculate annualized Sharpe ratio.

        Args:
            returns: Series of daily returns

        Returns:
            Annualized Sharpe ratio
        """
        if len(returns) == 0:
            return 0.0

        mean_return = returns.mean()
        std_return = returns.std()

        if std_return < 1e-10:
            return 0.0

        # Annualize (252 trading days)
        sharpe = (mean_return / std_return) * np.sqrt(252)
        return sharpe

    def _calculate_sortino(self, returns: pd.Series) -> float:
        """Calculate annualized Sortino ratio.

        Uses downside deviation instead of total volatility.

        Args:
            returns: Series of daily returns

        Returns:
            Annualized Sortino ratio
        """
        if len(returns) == 0:
            return 0.0

        mean_return = returns.mean()

        # Calculate downside deviation (only negative returns)
        negative_returns = returns[returns < 0]
        if len(negative_returns) == 0:
            return np.inf if mean_return > 0 else 0.0

        downside_std = negative_returns.std()

        if downside_std < 1e-10:
            return 0.0

        # Annualize
        sortino = (mean_return / downside_std) * np.sqrt(252)
        return sortino

    def _calculate_max_drawdown(self, returns: pd.Series) -> float:
        """Calculate maximum drawdown.

        Args:
            returns: Series of daily returns

        Returns:
            Maximum drawdown as decimal (e.g., 0.15 for 15%)
        """
        if len(returns) == 0:
            return 0.0

        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        max_dd = drawdown.min()

        return abs(max_dd)

    def _calculate_current_drawdown(self, returns: pd.Series) -> float:
        """Calculate current drawdown from peak.

        Args:
            returns: Series of daily returns

        Returns:
            Current drawdown as decimal
        """
        if len(returns) == 0:
            return 0.0

        cumulative = (1 + returns).cumprod()
        running_max = cumulative.max()
        current_value = cumulative.iloc[-1]
        current_dd = (current_value - running_max) / running_max

        return abs(current_dd)

    def _calculate_volatility(self, returns: pd.Series) -> float:
        """Calculate annualized volatility.

        Args:
            returns: Series of daily returns

        Returns:
            Annualized volatility
        """
        if len(returns) == 0:
            return 0.0

        return returns.std() * np.sqrt(252)

    def _calculate_win_rate(self, returns: pd.Series) -> float:
        """Calculate win rate (percentage of positive returns).

        Args:
            returns: Series of daily returns

        Returns:
            Win rate as percentage (0-100)
        """
        if len(returns) == 0:
            return 0.0

        winning_days = (returns > 0).sum()
        total_days = len(returns)

        return (winning_days / total_days) * 100

    def _calculate_avg_win_loss(self, returns: pd.Series) -> tuple[float, float]:
        """Calculate average winning and losing returns.

        Args:
            returns: Series of daily returns

        Returns:
            Tuple of (avg_win, avg_loss)
        """
        if len(returns) == 0:
            return 0.0, 0.0

        wins = returns[returns > 0]
        losses = returns[returns < 0]

        avg_win = wins.mean() if len(wins) > 0 else 0.0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0

        return avg_win, avg_loss

    def _calculate_profit_factor(self, returns: pd.Series) -> float:
        """Calculate profit factor (gross profit / gross loss).

        Args:
            returns: Series of daily returns

        Returns:
            Profit factor
        """
        if len(returns) == 0:
            return 0.0

        wins = returns[returns > 0]
        losses = returns[returns < 0]

        gross_profit = wins.sum() if len(wins) > 0 else 0.0
        gross_loss = abs(losses.sum()) if len(losses) > 0 else 0.0

        if gross_loss < 1e-10:
            return np.inf if gross_profit > 0 else 0.0

        return gross_profit / gross_loss

    def clear_strategy(self, strategy_id: str) -> None:
        """Clear performance data for a strategy.

        Args:
            strategy_id: Strategy identifier
        """
        if strategy_id in self._returns:
            del self._returns[strategy_id]
        if strategy_id in self._performance:
            del self._performance[strategy_id]
        logger.info(f"Cleared performance data for {strategy_id}")
