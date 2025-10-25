"""
Risk Management Engine - Protect capital through systematic risk controls.

Features:
- Position sizing: Kelly criterion, fixed fractional, volatility-adjusted
- Portfolio constraints: position limits, sector exposure, gross/net exposure
- Risk metrics: VaR (95%, 99%), CVaR, max drawdown, portfolio beta
- Pre-trade validation: enforce limits before execution
- Fast (<100ms) pre-trade checks for real-time trading

Example:
    >>> from src.risk.risk_manager import RiskManager, RiskConfig
    >>> import pandas as pd
    >>>
    >>> # Configure risk limits
    >>> config = RiskConfig(
    ...     max_position_size_pct=0.10,  # 10% per position
    ...     max_sector_exposure_pct=0.30,  # 30% per sector
    ...     max_gross_exposure_pct=2.0  # 200% gross
    ... )
    >>>
    >>> # Initialize manager
    >>> manager = RiskManager(config)
    >>>
    >>> # Calculate position size
    >>> size = manager.calculate_position_size(
    ...     symbol="AAPL",
    ...     price=150.0,
    ...     portfolio_value=100000.0,
    ...     method="fixed_fractional"
    ... )
    >>>
    >>> # Pre-trade check
    >>> decision = manager.pre_trade_check(
    ...     symbol="AAPL",
    ...     quantity=100,
    ...     side="long",
    ...     price=150.0,
    ...     current_portfolio=portfolio_df
    ... )
    >>> if decision.approved:
    ...     print("Trade approved")
    ... else:
    ...     print(f"Trade rejected: {decision.reason}")
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Literal
from enum import Enum
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class PositionSizeMethod(Enum):
    """Position sizing methods."""
    KELLY = "kelly"
    FIXED_FRACTIONAL = "fixed_fractional"
    VOLATILITY_ADJUSTED = "volatility_adjusted"


@dataclass
class RiskConfig:
    """
    Risk management configuration.

    Attributes:
        max_position_size_pct: Maximum position size as % of portfolio (default: 10%)
        max_sector_exposure_pct: Maximum sector exposure as % of portfolio (default: 30%)
        max_gross_exposure_pct: Maximum gross exposure as % of portfolio (default: 200%)
        max_net_exposure_pct: Maximum net exposure as % of portfolio (default: 100%)
        max_correlation: Maximum correlation between positions (default: 0.70)
        initial_portfolio_value: Default portfolio value for calculating limits on first trade (default: $100k)
        kelly_max_fraction: Maximum Kelly fraction to prevent over-leverage (default: 25%)
        fixed_fractional_pct: Fixed percentage for fixed fractional sizing (default: 2%)
        volatility_target: Target volatility for volatility-adjusted sizing (default: 15%)
        var_confidence_95: Value at Risk confidence level (default: 95%)
        var_confidence_99: Value at Risk confidence level (default: 99%)
        var_method: Method for VaR calculation (default: "historical")
        var_lookback_days: Lookback period for VaR calculation (default: 252)
        enable_pre_trade_checks: Enable pre-trade validation (default: True)
        pre_trade_timeout_ms: Maximum time for pre-trade check in ms (default: 100)
    """
    # Position limits
    max_position_size_pct: float = 0.10  # 10%
    max_sector_exposure_pct: float = 0.30  # 30%
    max_gross_exposure_pct: float = 2.0  # 200%
    max_net_exposure_pct: float = 1.0  # 100%
    max_correlation: float = 0.70
    initial_portfolio_value: float = 100000.0  # Default portfolio value for first trade

    # Position sizing parameters
    kelly_max_fraction: float = 0.25  # 25% cap
    fixed_fractional_pct: float = 0.02  # 2% per trade
    volatility_target: float = 0.15  # 15% annualized

    # Risk metrics configuration
    var_confidence_95: float = 0.95
    var_confidence_99: float = 0.99
    var_method: Literal["historical", "parametric"] = "historical"
    var_lookback_days: int = 252

    # Pre-trade check configuration
    enable_pre_trade_checks: bool = True
    pre_trade_timeout_ms: int = 100


@dataclass
class RiskMetrics:
    """
    Portfolio risk metrics.

    Attributes:
        var_95: Value at Risk at 95% confidence
        var_99: Value at Risk at 99% confidence
        cvar_95: Conditional VaR (expected shortfall) at 95%
        cvar_99: Conditional VaR (expected shortfall) at 99%
        max_drawdown: Maximum drawdown percentage
        current_drawdown: Current drawdown from peak
        portfolio_beta: Portfolio beta vs benchmark
        portfolio_volatility: Annualized portfolio volatility
        sharpe_ratio: Sharpe ratio
        gross_exposure: Total gross exposure as % of portfolio
        net_exposure: Net exposure as % of portfolio
        position_count: Number of positions
        largest_position_pct: Largest position as % of portfolio
    """
    var_95: float = 0.0
    var_99: float = 0.0
    cvar_95: float = 0.0
    cvar_99: float = 0.0
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    portfolio_beta: float = 0.0
    portfolio_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    position_count: int = 0
    largest_position_pct: float = 0.0


@dataclass
class PreTradeCheck:
    """
    Pre-trade validation request.

    Attributes:
        symbol: Ticker symbol
        quantity: Number of shares (positive)
        side: Trade side ("long" or "short")
        price: Trade price
        sector: Sector classification (optional)
    """
    symbol: str
    quantity: float
    side: Literal["long", "short"]
    price: float
    sector: Optional[str] = None


@dataclass
class RiskDecision:
    """
    Pre-trade check decision.

    Attributes:
        approved: Whether trade is approved
        reason: Rejection reason (if not approved)
        suggested_size: Suggested position size (if applicable)
        risk_metrics: Current portfolio risk metrics
    """
    approved: bool
    reason: Optional[str] = None
    suggested_size: Optional[float] = None
    risk_metrics: Optional[RiskMetrics] = None


class RiskManager:
    """
    Risk management engine for position sizing and portfolio constraints.

    Enforces systematic risk discipline through position sizing algorithms,
    portfolio constraint validation, and pre-trade risk checks. Designed for
    fast (<100ms) real-time validation.

    Example:
        >>> config = RiskConfig(max_position_size_pct=0.10)
        >>> manager = RiskManager(config)
        >>>
        >>> # Calculate position size
        >>> size = manager.calculate_position_size(
        ...     symbol="AAPL",
        ...     price=150.0,
        ...     portfolio_value=100000.0,
        ...     method="fixed_fractional"
        ... )
        >>> print(f"Position size: {size} shares")
        >>>
        >>> # Validate trade
        >>> decision = manager.pre_trade_check(
        ...     symbol="AAPL",
        ...     quantity=100,
        ...     side="long",
        ...     price=150.0,
        ...     current_portfolio=portfolio_df
        ... )
    """

    def __init__(self, config: Optional[RiskConfig] = None):
        """
        Initialize risk manager.

        Args:
            config: Risk configuration (uses defaults if None)
        """
        self.config = config or RiskConfig()
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate configuration parameters."""
        if self.config.max_position_size_pct <= 0 or self.config.max_position_size_pct > 1:
            raise ValueError("max_position_size_pct must be between 0 and 1")

        if self.config.kelly_max_fraction <= 0 or self.config.kelly_max_fraction > 1:
            raise ValueError("kelly_max_fraction must be between 0 and 1")

        if self.config.fixed_fractional_pct <= 0 or self.config.fixed_fractional_pct > 1:
            raise ValueError("fixed_fractional_pct must be between 0 and 1")

        if self.config.volatility_target <= 0:
            raise ValueError("volatility_target must be positive")

    def calculate_position_size(
        self,
        symbol: str,
        price: float,
        portfolio_value: float,
        method: str = "fixed_fractional",
        win_rate: Optional[float] = None,
        avg_win: Optional[float] = None,
        avg_loss: Optional[float] = None,
        volatility: Optional[float] = None,
        atr: Optional[float] = None
    ) -> float:
        """
        Calculate position size using specified method.

        Args:
            symbol: Ticker symbol
            price: Current price
            portfolio_value: Total portfolio value
            method: Sizing method ("kelly", "fixed_fractional", "volatility_adjusted")
            win_rate: Win rate for Kelly criterion (0-1)
            avg_win: Average win for Kelly criterion
            avg_loss: Average loss for Kelly criterion
            volatility: Historical volatility for volatility-adjusted
            atr: Average True Range for volatility-adjusted

        Returns:
            Position size in shares (rounded down to whole shares)

        Raises:
            ValueError: If inputs are invalid or required parameters missing

        Example:
            >>> manager = RiskManager()
            >>> size = manager.calculate_position_size(
            ...     symbol="AAPL",
            ...     price=150.0,
            ...     portfolio_value=100000.0,
            ...     method="fixed_fractional"
            ... )
            >>> print(f"Buy {size} shares")
        """
        if price <= 0:
            raise ValueError(f"Invalid price: {price}")

        if portfolio_value <= 0:
            raise ValueError(f"Invalid portfolio value: {portfolio_value}")

        # Dispatch to appropriate method
        if method == "kelly" or method == PositionSizeMethod.KELLY.value:
            return self._kelly_position_size(
                price, portfolio_value, win_rate, avg_win, avg_loss
            )
        elif method == "fixed_fractional" or method == PositionSizeMethod.FIXED_FRACTIONAL.value:
            return self._fixed_fractional_size(price, portfolio_value)
        elif method == "volatility_adjusted" or method == PositionSizeMethod.VOLATILITY_ADJUSTED.value:
            return self._volatility_adjusted_size(
                price, portfolio_value, volatility, atr
            )
        else:
            raise ValueError(f"Unknown position sizing method: {method}")

    def _kelly_position_size(
        self,
        price: float,
        portfolio_value: float,
        win_rate: Optional[float],
        avg_win: Optional[float],
        avg_loss: Optional[float]
    ) -> float:
        """
        Calculate position size using Kelly criterion.

        Kelly fraction = (p * b - q) / b
        where p = win rate, q = 1 - p, b = avg_win / avg_loss

        Capped at kelly_max_fraction to prevent over-leverage.

        Args:
            price: Current price
            portfolio_value: Portfolio value
            win_rate: Historical win rate (0-1)
            avg_win: Average winning trade size
            avg_loss: Average losing trade size (positive value)

        Returns:
            Position size in shares
        """
        if win_rate is None or avg_win is None or avg_loss is None:
            raise ValueError("Kelly criterion requires win_rate, avg_win, and avg_loss")

        if not (0 <= win_rate <= 1):
            raise ValueError(f"win_rate must be between 0 and 1: {win_rate}")

        if avg_win <= 0:
            raise ValueError(f"avg_win must be positive: {avg_win}")

        if avg_loss <= 0:
            raise ValueError(f"avg_loss must be positive: {avg_loss}")

        # Handle edge cases
        if win_rate == 0:
            logger.warning(f"Win rate is 0 for Kelly criterion, returning 0 position size")
            return 0.0

        # Calculate Kelly fraction
        lose_rate = 1 - win_rate
        win_loss_ratio = avg_win / avg_loss

        if win_rate == 1:
            # 100% win rate: mathematically kelly = 1.0
            kelly_fraction = 1.0
        else:
            kelly_fraction = (win_rate * win_loss_ratio - lose_rate) / win_loss_ratio

        # Cap at zero (don't trade if Kelly is negative)
        if kelly_fraction <= 0:
            logger.warning(
                f"Kelly criterion suggests no position (fraction={kelly_fraction:.3f})"
            )
            return 0.0

        # Cap at maximum to prevent over-leverage
        kelly_fraction = min(kelly_fraction, self.config.kelly_max_fraction)

        # Calculate position size
        capital_to_risk = portfolio_value * kelly_fraction
        shares = int(capital_to_risk / price)

        return float(shares)

    def _fixed_fractional_size(
        self,
        price: float,
        portfolio_value: float
    ) -> float:
        """
        Calculate position size using fixed fractional method.

        Allocates fixed percentage of portfolio to each position.

        Args:
            price: Current price
            portfolio_value: Portfolio value

        Returns:
            Position size in shares
        """
        capital_to_use = portfolio_value * self.config.fixed_fractional_pct
        shares = int(capital_to_use / price)
        return float(shares)

    def _volatility_adjusted_size(
        self,
        price: float,
        portfolio_value: float,
        volatility: Optional[float],
        atr: Optional[float]
    ) -> float:
        """
        Calculate position size adjusted for volatility.

        Scales position size inversely with volatility to maintain
        consistent risk across positions.

        Args:
            price: Current price
            portfolio_value: Portfolio value
            volatility: Annualized volatility (0-1)
            atr: Average True Range

        Returns:
            Position size in shares
        """
        # Use volatility if provided, otherwise estimate from ATR
        if volatility is not None:
            # Check for invalid (negative) volatility first
            if volatility < 0:
                raise ValueError(f"volatility must be positive: {volatility}")
            # Handle zero/near-zero volatility edge case
            if volatility < 1e-10:
                logger.warning("Zero/near-zero volatility detected, using fixed fractional sizing")
                return self._fixed_fractional_size(price, portfolio_value)
            asset_vol = volatility
        elif atr is not None:
            if atr <= 0:
                raise ValueError(f"atr must be positive: {atr}")
            # Estimate annualized volatility from ATR
            # ATR is in price units, normalize by price and annualize
            asset_vol = (atr / price) * np.sqrt(252)
            # Handle zero/near-zero volatility edge case
            if asset_vol < 1e-10:
                logger.warning("Zero/near-zero volatility detected, using fixed fractional sizing")
                return self._fixed_fractional_size(price, portfolio_value)
        else:
            raise ValueError("volatility_adjusted requires either volatility or atr")

        # Scale position size: target_vol / asset_vol
        # Higher volatility = smaller position
        scaling_factor = self.config.volatility_target / asset_vol

        # Base position size (use fixed fractional as base)
        base_capital = portfolio_value * self.config.fixed_fractional_pct
        adjusted_capital = base_capital * scaling_factor

        # Cap at max position size
        max_capital = portfolio_value * self.config.max_position_size_pct
        final_capital = min(adjusted_capital, max_capital)

        shares = int(final_capital / price)
        return float(shares)

    def calculate_risk_metrics(
        self,
        portfolio: pd.DataFrame,
        returns: pd.Series,
        benchmark_returns: Optional[pd.Series] = None
    ) -> RiskMetrics:
        """
        Calculate comprehensive portfolio risk metrics.

        Args:
            portfolio: DataFrame with columns [symbol, quantity, price, value, sector]
            returns: Historical daily returns series
            benchmark_returns: Benchmark returns for beta calculation (optional)

        Returns:
            RiskMetrics with all calculated metrics

        Example:
            >>> metrics = manager.calculate_risk_metrics(
            ...     portfolio=current_positions_df,
            ...     returns=daily_returns_series
            ... )
            >>> print(f"VaR(95%): ${metrics.var_95:,.2f}")
            >>> print(f"Max Drawdown: {metrics.max_drawdown:.2%}")
        """
        metrics = RiskMetrics()

        # Portfolio characteristics
        if not portfolio.empty:
            total_value = portfolio['value'].abs().sum()
            metrics.position_count = len(portfolio)

            if total_value > 0:
                # Largest position
                metrics.largest_position_pct = (portfolio['value'].abs().max() / total_value)

                # Gross and net exposure
                metrics.gross_exposure = portfolio['value'].abs().sum() / total_value
                metrics.net_exposure = portfolio['value'].sum() / total_value

        # Return-based metrics
        if not returns.empty and len(returns) > 0:
            # VaR calculation
            metrics.var_95 = self._calculate_var(returns, self.config.var_confidence_95)
            metrics.var_99 = self._calculate_var(returns, self.config.var_confidence_99)

            # CVaR (Conditional VaR / Expected Shortfall)
            metrics.cvar_95 = self._calculate_cvar(returns, self.config.var_confidence_95)
            metrics.cvar_99 = self._calculate_cvar(returns, self.config.var_confidence_99)

            # Volatility (annualized)
            metrics.portfolio_volatility = returns.std() * np.sqrt(252)

            # Sharpe ratio (assuming 2% risk-free rate)
            if metrics.portfolio_volatility > 0:
                excess_returns = returns.mean() - (0.02 / 252)  # Daily risk-free rate
                metrics.sharpe_ratio = (excess_returns / returns.std()) * np.sqrt(252)

            # Maximum and current drawdown
            metrics.max_drawdown = self._calculate_max_drawdown(returns)
            metrics.current_drawdown = self._calculate_current_drawdown(returns)

            # Beta (if benchmark provided)
            if benchmark_returns is not None and not benchmark_returns.empty:
                metrics.portfolio_beta = self._calculate_beta(returns, benchmark_returns)

        return metrics

    def _calculate_var(self, returns: pd.Series, confidence: float) -> float:
        """
        Calculate Value at Risk using historical simulation.

        Args:
            returns: Daily returns series
            confidence: Confidence level (0-1)

        Returns:
            VaR (positive number representing potential loss)
        """
        if len(returns) == 0:
            return 0.0

        # Use lookback window
        lookback_returns = returns.tail(self.config.var_lookback_days)

        if len(lookback_returns) == 0:
            return 0.0

        # Historical simulation method
        if self.config.var_method == "historical":
            # VaR is the (1-confidence) quantile of returns
            var_return = lookback_returns.quantile(1 - confidence)
            # Return as positive number (potential loss)
            return abs(var_return)

        # Parametric method (assuming normal distribution)
        else:
            mean = lookback_returns.mean()
            std = lookback_returns.std()

            if std == 0:
                return 0.0

            # Z-score for confidence level (e.g., -1.645 for 95%)
            from scipy import stats
            z_score = abs(stats.norm.ppf(1 - confidence))

            # VaR represents loss: mean - z_score * std (for left tail)
            var_return = mean - z_score * std
            return abs(var_return)

    def _calculate_cvar(self, returns: pd.Series, confidence: float) -> float:
        """
        Calculate Conditional VaR (Expected Shortfall).

        CVaR is the expected loss given that VaR is exceeded.

        Args:
            returns: Daily returns series
            confidence: Confidence level (0-1)

        Returns:
            CVaR (positive number representing expected loss in tail)
        """
        if len(returns) == 0:
            return 0.0

        lookback_returns = returns.tail(self.config.var_lookback_days)

        if len(lookback_returns) == 0:
            return 0.0

        # Find VaR threshold
        var_threshold = -self._calculate_var(lookback_returns, confidence)

        # CVaR is average of returns worse than VaR
        tail_returns = lookback_returns[lookback_returns <= var_threshold]

        if len(tail_returns) == 0:
            return abs(var_threshold)

        cvar = abs(tail_returns.mean())
        return cvar

    def _calculate_max_drawdown(self, returns: pd.Series) -> float:
        """
        Calculate maximum drawdown from returns series.

        Args:
            returns: Daily returns series

        Returns:
            Maximum drawdown (positive percentage, e.g., 0.25 = 25% drawdown)
        """
        if len(returns) == 0:
            return 0.0

        # Calculate cumulative returns
        cum_returns = (1 + returns).cumprod()

        # Calculate running maximum
        running_max = cum_returns.cummax()

        # Calculate drawdown at each point
        drawdown = (cum_returns - running_max) / running_max

        # Return maximum drawdown (as positive number)
        return abs(drawdown.min())

    def _calculate_current_drawdown(self, returns: pd.Series) -> float:
        """
        Calculate current drawdown from peak.

        Args:
            returns: Daily returns series

        Returns:
            Current drawdown (positive percentage)
        """
        if len(returns) == 0:
            return 0.0

        # Calculate cumulative returns
        cum_returns = (1 + returns).cumprod()

        # Maximum value to date
        max_value = cum_returns.max()

        # Current value
        current_value = cum_returns.iloc[-1]

        # Current drawdown
        drawdown = (current_value - max_value) / max_value

        return abs(drawdown)

    def _calculate_beta(
        self,
        returns: pd.Series,
        benchmark_returns: pd.Series
    ) -> float:
        """
        Calculate portfolio beta vs benchmark.

        Args:
            returns: Portfolio returns
            benchmark_returns: Benchmark returns

        Returns:
            Portfolio beta
        """
        # Align indices
        aligned_returns, aligned_benchmark = returns.align(
            benchmark_returns, join='inner'
        )

        if len(aligned_returns) < 2:
            return 0.0

        # Calculate covariance and variance (use ddof=1 for consistency)
        covariance = np.cov(aligned_returns, aligned_benchmark)[0, 1]
        benchmark_variance = np.var(aligned_benchmark, ddof=1)

        # Use tolerance for zero check to avoid floating point issues
        if benchmark_variance < 1e-10:
            return 0.0

        beta = covariance / benchmark_variance
        return beta

    def pre_trade_check(
        self,
        symbol: str,
        quantity: float,
        side: Literal["long", "short"],
        price: float,
        current_portfolio: pd.DataFrame,
        sector: Optional[str] = None,
        returns: Optional[pd.Series] = None
    ) -> RiskDecision:
        """
        Validate proposed trade against portfolio constraints.

        Performs fast (<100ms) pre-trade validation to ensure trade
        doesn't violate risk limits. Checks:
        - Position size limits
        - Sector exposure limits
        - Gross/net exposure limits
        - Correlation limits (if returns provided)

        Args:
            symbol: Ticker symbol
            quantity: Number of shares (positive)
            side: Trade side ("long" or "short")
            price: Trade price
            current_portfolio: DataFrame with [symbol, quantity, price, value, sector]
            sector: Sector classification (optional)
            returns: Historical returns for correlation check (optional)

        Returns:
            RiskDecision with approval status and reason

        Example:
            >>> decision = manager.pre_trade_check(
            ...     symbol="AAPL",
            ...     quantity=100,
            ...     side="long",
            ...     price=150.0,
            ...     current_portfolio=positions_df
            ... )
            >>> if decision.approved:
            ...     execute_trade()
            ... else:
            ...     print(f"Rejected: {decision.reason}")
        """
        if not self.config.enable_pre_trade_checks:
            return RiskDecision(approved=True)

        # Input validation
        if quantity <= 0:
            return RiskDecision(
                approved=False,
                reason=f"Invalid quantity: {quantity}"
            )

        if price <= 0:
            return RiskDecision(
                approved=False,
                reason=f"Invalid price: {price}"
            )

        # Calculate trade value
        trade_value = quantity * price
        if side == "short":
            trade_value = -trade_value

        # Calculate portfolio value
        if current_portfolio.empty:
            # Use configured initial portfolio value for first trade
            abs_portfolio_value = self.config.initial_portfolio_value
        else:
            # Use gross exposure (absolute values) for limit calculations
            abs_portfolio_value = current_portfolio['value'].abs().sum()
            if abs_portfolio_value == 0:
                abs_portfolio_value = self.config.initial_portfolio_value

        # 1. Check position size limit
        position_size_check = self._check_position_size_limit(
            symbol, trade_value, abs_portfolio_value, current_portfolio
        )
        if not position_size_check.approved:
            return position_size_check

        # 2. Check sector exposure limit
        if sector is not None:
            sector_check = self._check_sector_exposure_limit(
                sector, trade_value, abs_portfolio_value, current_portfolio
            )
            if not sector_check.approved:
                return sector_check

        # 3. Check gross exposure limit
        gross_check = self._check_gross_exposure_limit(
            trade_value, abs_portfolio_value, current_portfolio
        )
        if not gross_check.approved:
            return gross_check

        # 4. Check net exposure limit
        net_check = self._check_net_exposure_limit(
            trade_value, abs_portfolio_value, current_portfolio
        )
        if not net_check.approved:
            return net_check

        # Note: Correlation limit check not implemented - requires multi-asset returns matrix
        # TODO: Implement correlation-based position limits in future version

        # Calculate risk metrics for context
        risk_metrics = None
        if returns is not None:
            risk_metrics = self.calculate_risk_metrics(current_portfolio, returns)

        # All checks passed
        return RiskDecision(
            approved=True,
            risk_metrics=risk_metrics
        )

    def _check_position_size_limit(
        self,
        symbol: str,
        trade_value: float,
        portfolio_value: float,
        current_portfolio: pd.DataFrame
    ) -> RiskDecision:
        """Check if position size exceeds limit."""
        # Find existing position
        existing_position = current_portfolio[
            current_portfolio['symbol'] == symbol
        ] if not current_portfolio.empty else pd.DataFrame()

        # Calculate new position value
        if not existing_position.empty:
            current_value = existing_position['value'].iloc[0]
            new_position_value = current_value + trade_value
        else:
            new_position_value = trade_value

        # Check limit
        position_pct = abs(new_position_value) / portfolio_value

        if position_pct > self.config.max_position_size_pct:
            # Suggested size calculation would require price, which we don't have in this context
            # Return None instead of attempting complex calculation
            suggested_size = None

            return RiskDecision(
                approved=False,
                reason=f"Position size limit exceeded: {position_pct:.1%} > {self.config.max_position_size_pct:.1%}",
                suggested_size=suggested_size
            )

        return RiskDecision(approved=True)

    def _check_sector_exposure_limit(
        self,
        sector: str,
        trade_value: float,
        portfolio_value: float,
        current_portfolio: pd.DataFrame
    ) -> RiskDecision:
        """Check if sector exposure exceeds limit."""
        # Calculate current sector exposure
        if not current_portfolio.empty and 'sector' in current_portfolio.columns:
            sector_positions = current_portfolio[current_portfolio['sector'] == sector]
            current_sector_exposure = sector_positions['value'].abs().sum()
        else:
            current_sector_exposure = 0.0

        # Calculate new sector exposure
        new_sector_exposure = current_sector_exposure + abs(trade_value)
        sector_exposure_pct = new_sector_exposure / portfolio_value

        if sector_exposure_pct > self.config.max_sector_exposure_pct:
            return RiskDecision(
                approved=False,
                reason=f"Sector exposure limit exceeded for {sector}: {sector_exposure_pct:.1%} > {self.config.max_sector_exposure_pct:.1%}"
            )

        return RiskDecision(approved=True)

    def _check_gross_exposure_limit(
        self,
        trade_value: float,
        portfolio_value: float,
        current_portfolio: pd.DataFrame
    ) -> RiskDecision:
        """Check if gross exposure exceeds limit."""
        # Calculate current gross exposure
        if not current_portfolio.empty:
            current_gross = current_portfolio['value'].abs().sum()
        else:
            current_gross = 0.0

        # Calculate new gross exposure
        new_gross = current_gross + abs(trade_value)
        gross_exposure_pct = new_gross / portfolio_value

        if gross_exposure_pct > self.config.max_gross_exposure_pct:
            return RiskDecision(
                approved=False,
                reason=f"Gross exposure limit exceeded: {gross_exposure_pct:.1%} > {self.config.max_gross_exposure_pct:.1%}"
            )

        return RiskDecision(approved=True)

    def _check_net_exposure_limit(
        self,
        trade_value: float,
        portfolio_value: float,
        current_portfolio: pd.DataFrame
    ) -> RiskDecision:
        """Check if net exposure exceeds limit."""
        # Calculate current net exposure
        if not current_portfolio.empty:
            current_net = current_portfolio['value'].sum()
        else:
            current_net = 0.0

        # Calculate new net exposure
        new_net = current_net + trade_value
        net_exposure_pct = abs(new_net) / portfolio_value

        if net_exposure_pct > self.config.max_net_exposure_pct:
            return RiskDecision(
                approved=False,
                reason=f"Net exposure limit exceeded: {net_exposure_pct:.1%} > {self.config.max_net_exposure_pct:.1%}"
            )

        return RiskDecision(approved=True)
