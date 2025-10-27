"""Risk metrics calculation for portfolio monitoring.

Calculates Value at Risk (VaR), Conditional VaR (CVaR), Sharpe ratio, and
portfolio beta using historical return data from the data infrastructure.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..data.storage.parquet_storage import ParquetStorage
from .config.risk_config import RiskConfigManager

logger = logging.getLogger(__name__)


@dataclass
class RiskMetrics:
    """Container for calculated risk metrics.

    Attributes:
        timestamp: Calculation timestamp.
        var_95: Value at Risk at 95% confidence (daily).
        cvar_95: Conditional VaR (Expected Shortfall) at 95%.
        sharpe_30d: 30-day Sharpe ratio.
        sharpe_90d: 90-day Sharpe ratio.
        sharpe_365d: 365-day Sharpe ratio.
        beta: Portfolio beta relative to benchmark.
        volatility: Annualized volatility (std dev of returns).
        max_drawdown: Maximum observed drawdown in period.
    """

    timestamp: datetime
    var_95: float
    cvar_95: float
    sharpe_30d: Optional[float] = None
    sharpe_90d: Optional[float] = None
    sharpe_365d: Optional[float] = None
    beta: Optional[float] = None
    volatility: float = 0.0
    max_drawdown: float = 0.0


class RiskMetricsCalculator:
    """Calculates risk metrics from portfolio return history.

    Leverages historical price data from data infrastructure to compute:
    - Value at Risk (VaR): 95% confidence daily loss estimate
    - Conditional VaR (CVaR): Expected loss beyond VaR
    - Sharpe Ratio: Risk-adjusted returns at multiple timeframes
    - Portfolio Beta: Systematic risk relative to benchmark
    - Volatility: Annualized standard deviation of returns

    Example:
        >>> calculator = RiskMetricsCalculator(config, storage)
        >>> returns = pd.Series([0.01, -0.02, 0.015, ...])
        >>> metrics = calculator.calculate_metrics(returns)
        >>> print(f"95% VaR: ${metrics.var_95:,.2f}")
    """

    def __init__(
        self,
        config: RiskConfigManager,
        storage: ParquetStorage,
    ) -> None:
        """Initialize risk metrics calculator.

        Args:
            config: Risk configuration manager.
            storage: Data storage for historical prices.
        """
        self.config = config
        self.storage = storage

        self.var_confidence = config.get_var_confidence()
        self.cvar_confidence = config.get_cvar_confidence()
        self.var_lookback_days = config.get_var_lookback_days()
        self.sharpe_windows = config.get_sharpe_windows()
        self.benchmark_symbol = config.get_benchmark_symbol()
        self.benchmark_asset_class = config.get_benchmark_asset_class()
        self.min_history_days = config.get_min_history_days()

        logger.info("Risk metrics calculator initialized")

    def calculate_metrics(
        self, portfolio_returns: pd.Series
    ) -> RiskMetrics:
        """Calculate all risk metrics from portfolio return series.

        Args:
            portfolio_returns: Series of daily portfolio returns (indexed by date).

        Returns:
            RiskMetrics object with calculated values.

        Raises:
            ValueError: If insufficient data for calculations.
        """
        if len(portfolio_returns) < self.min_history_days:
            raise ValueError(
                f"Insufficient data: {len(portfolio_returns)} days "
                f"(minimum: {self.min_history_days})"
            )

        # Calculate VaR and CVaR
        var_95 = self._calculate_var(portfolio_returns, self.var_confidence)
        cvar_95 = self._calculate_cvar(portfolio_returns, self.cvar_confidence)

        # Calculate Sharpe ratios at multiple windows
        sharpe_30d = self._calculate_sharpe(portfolio_returns, 30)
        sharpe_90d = self._calculate_sharpe(portfolio_returns, 90)
        sharpe_365d = self._calculate_sharpe(portfolio_returns, 365)

        # Calculate beta (requires benchmark data)
        beta = None
        try:
            beta = self._calculate_beta(portfolio_returns)
        except Exception as e:
            logger.warning(f"Beta calculation failed: {e}")

        # Calculate volatility (annualized)
        volatility = self._calculate_volatility(portfolio_returns)

        # Calculate max drawdown
        max_drawdown = self._calculate_max_drawdown(portfolio_returns)

        metrics = RiskMetrics(
            timestamp=datetime.now(),
            var_95=var_95,
            cvar_95=cvar_95,
            sharpe_30d=sharpe_30d,
            sharpe_90d=sharpe_90d,
            sharpe_365d=sharpe_365d,
            beta=beta,
            volatility=volatility,
            max_drawdown=max_drawdown,
        )

        if self.config.get_log_risk_metrics():
            logger.info(
                f"Risk Metrics | VaR95: {var_95:.4f}, CVaR95: {cvar_95:.4f}, "
                f"Sharpe30d: {sharpe_30d:.2f}, Vol: {volatility:.2%}, "
                f"MaxDD: {max_drawdown:.2%}"
            )

        return metrics

    def _calculate_var(
        self, returns: pd.Series, confidence: float
    ) -> float:
        """Calculate Value at Risk using historical simulation.

        Args:
            returns: Series of daily returns.
            confidence: Confidence level (e.g., 0.95 for 95%).

        Returns:
            VaR as negative return (loss) at specified confidence.
        """
        # Use lookback window
        recent_returns = returns.tail(self.var_lookback_days)

        # VaR is the (1-confidence) quantile of the return distribution
        var = recent_returns.quantile(1 - confidence)

        return abs(var)  # Return as positive loss value

    def _calculate_cvar(
        self, returns: pd.Series, confidence: float
    ) -> float:
        """Calculate Conditional VaR (Expected Shortfall).

        CVaR is the expected loss given that loss exceeds VaR.

        Args:
            returns: Series of daily returns.
            confidence: Confidence level.

        Returns:
            CVaR as expected loss beyond VaR threshold.
        """
        recent_returns = returns.tail(self.var_lookback_days)

        # Get VaR threshold
        var_threshold = recent_returns.quantile(1 - confidence)

        # CVaR is mean of returns below VaR threshold
        tail_returns = recent_returns[recent_returns <= var_threshold]

        if len(tail_returns) == 0:
            return abs(var_threshold)

        cvar = tail_returns.mean()

        return abs(cvar)

    def _calculate_sharpe(
        self, returns: pd.Series, window_days: int
    ) -> Optional[float]:
        """Calculate Sharpe ratio for a given window.

        Assumes risk-free rate = 0 for simplicity (can be enhanced).

        Args:
            returns: Series of daily returns.
            window_days: Lookback window in days.

        Returns:
            Annualized Sharpe ratio or None if insufficient data.
        """
        if len(returns) < window_days:
            return None

        recent_returns = returns.tail(window_days)

        mean_return = recent_returns.mean()
        std_return = recent_returns.std()

        if std_return == 0:
            return 0.0

        # Annualize (assuming 252 trading days)
        sharpe = (mean_return / std_return) * np.sqrt(252)

        return sharpe

    def _calculate_beta(self, portfolio_returns: pd.Series) -> float:
        """Calculate portfolio beta relative to benchmark.

        Args:
            portfolio_returns: Series of daily portfolio returns.

        Returns:
            Portfolio beta coefficient.

        Raises:
            Exception: If benchmark data unavailable or calculation fails.
        """
        # Get benchmark returns
        benchmark_data = self._load_benchmark_data()

        # Align dates
        aligned = pd.DataFrame(
            {
                "portfolio": portfolio_returns,
                "benchmark": benchmark_data,
            }
        ).dropna()

        if len(aligned) < self.min_history_days:
            raise ValueError(
                f"Insufficient aligned data for beta: {len(aligned)} days"
            )

        # Calculate covariance and variance
        covariance = aligned["portfolio"].cov(aligned["benchmark"])
        benchmark_variance = aligned["benchmark"].var()

        if benchmark_variance == 0:
            raise ValueError("Benchmark variance is zero")

        beta = covariance / benchmark_variance

        return beta

    def _calculate_volatility(self, returns: pd.Series) -> float:
        """Calculate annualized volatility.

        Args:
            returns: Series of daily returns.

        Returns:
            Annualized volatility (standard deviation).
        """
        std_dev = returns.std()
        annualized_vol = std_dev * np.sqrt(252)
        return annualized_vol

    def _calculate_max_drawdown(self, returns: pd.Series) -> float:
        """Calculate maximum drawdown from return series.

        Args:
            returns: Series of daily returns.

        Returns:
            Maximum drawdown as decimal (e.g., 0.15 for 15%).
        """
        # Calculate cumulative returns
        cumulative = (1 + returns).cumprod()

        # Calculate running maximum
        running_max = cumulative.expanding().max()

        # Calculate drawdown series
        drawdown = (cumulative - running_max) / running_max

        # Get maximum drawdown (most negative)
        max_dd = drawdown.min()

        return abs(max_dd)

    def _load_benchmark_data(self) -> pd.Series:
        """Load benchmark return data from storage.

        Returns:
            Series of daily benchmark returns.

        Raises:
            FileNotFoundError: If benchmark data not available.
        """
        # Load benchmark price data
        benchmark_prices = self.storage.read(
            self.benchmark_asset_class,
            self.benchmark_symbol,
            "1d",
        )

        # Calculate daily returns from close prices
        returns = benchmark_prices["Close"].pct_change().dropna()

        return returns

    def calculate_position_var(
        self, position_value: float, symbol: str, asset_class: str
    ) -> float:
        """Calculate VaR for a single position.

        Args:
            position_value: Current position value.
            symbol: Ticker symbol.
            asset_class: Asset class.

        Returns:
            Position VaR at 95% confidence.
        """
        try:
            # Load price data for symbol
            price_data = self.storage.read(asset_class, symbol, "1d")

            # Calculate returns
            returns = price_data["Close"].pct_change().dropna()

            # Calculate VaR
            var_pct = self._calculate_var(returns, self.var_confidence)

            # Convert to dollar VaR
            position_var = abs(position_value) * var_pct

            return position_var

        except Exception as e:
            logger.error(
                f"Failed to calculate position VaR for {symbol}: {e}"
            )
            return 0.0

    def portfolio_returns_from_positions(
        self,
        positions: List,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.Series:
        """Calculate historical portfolio returns from positions.

        This is a simplified version that assumes static positions.
        Production version would track actual position changes over time.

        Args:
            positions: List of Position objects.
            start_date: Start date for return calculation.
            end_date: End date for return calculation.

        Returns:
            Series of daily portfolio returns.
        """
        if not positions:
            return pd.Series(dtype=float)

        portfolio_values = None

        for position in positions:
            try:
                # Load price data
                price_data = self.storage.read(
                    position.asset_class,
                    position.symbol,
                    "1d",
                    start_date=start_date,
                    end_date=end_date,
                )

                # Calculate position value over time
                position_values = (
                    price_data["Close"] * position.quantity
                )

                if portfolio_values is None:
                    portfolio_values = position_values
                else:
                    # Align dates and sum
                    portfolio_values = portfolio_values.add(
                        position_values, fill_value=0
                    )

            except Exception as e:
                logger.warning(
                    f"Failed to load data for {position.symbol}: {e}"
                )
                continue

        if portfolio_values is None or len(portfolio_values) == 0:
            return pd.Series(dtype=float)

        # Calculate returns from portfolio values
        portfolio_returns = portfolio_values.pct_change().dropna()

        return portfolio_returns
