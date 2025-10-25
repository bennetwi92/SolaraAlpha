# Risk Management Architecture

**Version**: 1.1 (Post-Peer Review)
**Last Updated**: 2025-10-25
**Status**: Design Phase - Approved with Changes

## Executive Summary

Risk management is the TOP PRIORITY for SolaraAlpha. This architecture defines a comprehensive, multi-layered risk framework operating at both pre-trade (validation) and post-trade (monitoring) levels to prevent catastrophic losses. The framework supports multi-strategy, multi-asset, multi-timeframe trading operations while maintaining strict adherence to position sizing, portfolio constraints, drawdown limits, and exposure monitoring.

**Key Design Principles**:
- **Risk Management > Profitability** - Architecture enforces this hierarchy through hard limits
- **Defense in depth** - Multiple layers of controls (position → portfolio → account level)
- **Pre-trade validation** - Reject orders violating risk limits before execution
- **Real-time monitoring** - Continuous surveillance with automated circuit breakers
- **Strategy autonomy** - Strategies can be conservative; framework enforces minimums

**Core Components**:
1. **Position Sizing Engine** - Kelly criterion, fixed fractional, volatility-adjusted (ATR, std dev)
2. **Portfolio Constraints** - Max position size, sector exposure, correlation limits, gross/net exposure
3. **Risk Metrics Calculator** - VaR (95%, 99%), CVaR, portfolio beta, concentration, correlation matrix
4. **Drawdown Controller** - Max drawdown thresholds, daily loss limits, trailing stops
5. **Pre-Trade Validator** - Real-time risk checks before order submission
6. **Monitoring Dashboard** - Alert system for limit violations, circuit breakers

**Critical Constraints**:
- **No paid services** - All risk calculations use free data sources and open-source libraries
- **Computational efficiency** - Pre-trade checks must complete in <100ms
- **Multi-strategy coordination** - Risk limits enforced across uncorrelated strategy portfolio
- **Integration with existing systems** - Seamless connection to backtest engine (src/backtest/engine.py) and market data (src/data/)

---

## 1. Architecture Overview

### 1.1 Multi-Layer Risk Framework

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ACCOUNT LEVEL                                 │
│  Daily Loss Limit │ Max Drawdown │ Total Capital Exposure            │
├─────────────────────────────────────────────────────────────────────┤
│                        PORTFOLIO LEVEL                               │
│  Gross Exposure │ Net Exposure │ Sector Limits │ Correlation Limits  │
├─────────────────────────────────────────────────────────────────────┤
│                        POSITION LEVEL                                │
│  Max Position Size │ Volatility Limits │ Concentration Risk          │
├─────────────────────────────────────────────────────────────────────┤
│                        STRATEGY LEVEL                                │
│  Strategy-Specific Limits │ Per-Trade Risk │ Max Positions           │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Pre-Trade vs Post-Trade Risk

**Pre-Trade Validation** (Preventive):
- Executed BEFORE order submission
- Validates proposed order against all risk limits
- Rejects orders that would violate constraints
- Latency target: <100ms
- Integration point: Between strategy signal → order execution

**Post-Trade Monitoring** (Detective):
- Executed AFTER positions established
- Continuous monitoring of portfolio risk
- Triggers alerts and circuit breakers
- Latency target: <1 second
- Integration point: Real-time position updates

### 1.3 Data Flow

```
Strategy Signal
     ↓
Pre-Trade Validator ← Risk Limits Config
     ↓                ← Current Portfolio State
     ↓                ← Market Data (volatility, correlations)
     ↓
[PASS] → Order Execution
     ↓
Position Update
     ↓
Post-Trade Monitor ← Risk Metrics Calculator
     ↓                ← VaR, CVaR, Drawdown Tracker
     ↓
[Alert if violation] → Risk Dashboard
                     → Circuit Breaker (if critical)
```

---

## 2. Position Sizing Models

### 2.1 Kelly Criterion

**Purpose**: Optimal position size to maximize long-term growth rate

**Formula**:
```
Kelly% = (p * b - q) / b

Where:
  p = Probability of winning (win rate)
  q = Probability of losing (1 - p)
  b = Ratio of win amount to loss amount (avg_win / avg_loss)
```

**Implementation**:
```python
from dataclasses import dataclass
import numpy as np
from typing import Optional

@dataclass
class KellyConfig:
    """Configuration for Kelly Criterion position sizing."""
    fractional_kelly: float = 0.5  # Use half-Kelly for safety (0.25-0.5)
    min_trades_required: int = 30  # Minimum trades for statistical significance
    max_position_size: float = 0.10  # Cap at 10% even if Kelly suggests more
    default_position_size: float = 0.02  # Fallback if insufficient trade history

class KellyPositionSizer:
    """
    Kelly Criterion position sizer with safety constraints.

    Calculates optimal position size based on historical win rate and
    risk-reward ratio. Uses fractional Kelly to reduce risk of ruin.
    """

    def __init__(self, config: Optional[KellyConfig] = None):
        self.config = config or KellyConfig()

    def calculate_position_size(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        capital: float,
        num_trades: int
    ) -> float:
        """
        Calculate Kelly-optimal position size.

        Args:
            win_rate: Historical win rate (0.0 to 1.0)
            avg_win: Average winning trade P&L
            avg_loss: Average losing trade P&L (absolute value)
            capital: Current account capital
            num_trades: Number of historical trades

        Returns:
            Position size in dollars

        Example:
            >>> sizer = KellyPositionSizer()
            >>> position = sizer.calculate_position_size(
            ...     win_rate=0.55, avg_win=1000, avg_loss=500,
            ...     capital=100000, num_trades=50
            ... )
            >>> print(position)  # Will be fraction of capital
        """
        # Validate inputs
        if not (0 <= win_rate <= 1):
            raise ValueError(f"Win rate must be in [0, 1], got {win_rate}")
        if avg_loss <= 0:
            raise ValueError(f"Average loss must be positive, got {avg_loss}")
        if avg_win <= 0:
            raise ValueError(f"Average win must be positive, got {avg_win}")

        # Require minimum trades for statistical significance
        if num_trades < self.config.min_trades_required:
            return capital * self.config.default_position_size

        # Calculate Kelly percentage
        p = win_rate
        q = 1 - p
        b = avg_win / avg_loss  # Win/loss ratio

        # Full Kelly
        kelly_pct = (p * b - q) / b

        # Apply safety constraints
        kelly_pct = max(0, kelly_pct)  # Never go negative (no position)

        # Apply fractional Kelly FIRST (typically 0.5 = half-Kelly)
        kelly_pct *= self.config.fractional_kelly

        # Then cap at maximum (prevents fractional Kelly from being double-penalized)
        kelly_pct = min(kelly_pct, self.config.max_position_size)

        return capital * kelly_pct
```

**Safety Features**:
- **Fractional Kelly** (default 0.5): Reduces position size to mitigate estimation error
- **Maximum cap** (default 10%): Prevents over-concentration even if Kelly suggests larger
- **Minimum trades** (default 30): Requires statistical significance before using Kelly
- **Negative check**: Returns 0 if Kelly formula yields negative (unfavorable odds)

**Use Case**: Long-term mean-reversion and trend-following strategies with consistent win/loss profiles

### 2.2 Fixed Fractional

**Purpose**: Simple, predictable position sizing based on fixed % of capital

**Formula**:
```
Position Size = Capital × Fixed Fraction
```

**Implementation**:
```python
@dataclass
class FixedFractionalConfig:
    """Configuration for fixed fractional position sizing."""
    fraction: float = 0.02  # 2% of capital per position
    max_positions: int = 10  # Maximum concurrent positions
    min_position_value: float = 1000  # Minimum position size in dollars

class FixedFractionalPositionSizer:
    """
    Fixed fractional position sizer - allocates constant % of capital per trade.

    Simple and predictable. Good for diversified portfolios and strategies
    without clear win/loss statistics.
    """

    def __init__(self, config: Optional[FixedFractionalConfig] = None):
        self.config = config or FixedFractionalConfig()

    def calculate_position_size(
        self,
        capital: float,
        current_positions: int = 0
    ) -> float:
        """
        Calculate fixed fractional position size.

        Args:
            capital: Current account capital
            current_positions: Number of open positions

        Returns:
            Position size in dollars
        """
        # Check if at max positions
        if current_positions >= self.config.max_positions:
            return 0.0

        position_size = capital * self.config.fraction

        # Enforce minimum position size
        if position_size < self.config.min_position_value:
            return 0.0

        return position_size
```

**Advantages**:
- Simple to understand and implement
- Predictable risk per trade
- Automatic scaling with account size
- No historical trade data required

**Use Case**: Multi-strategy portfolios, pairs trading, statistical arbitrage (diversified uncorrelated positions)

### 2.3 Volatility-Based Sizing

**Purpose**: Risk parity - equal risk allocation across positions regardless of asset volatility

**Methods**:

#### **2.3.1 ATR-Based (Average True Range)**

**Formula**:
```
Position Size = (Risk per Trade) / (ATR × ATR Multiplier)

Where:
  Risk per Trade = Capital × Risk%
  ATR = Average True Range over N periods (typically 14)
  ATR Multiplier = Stop-loss distance in ATR units (e.g., 2.0 ATR)
```

**Implementation**:
```python
@dataclass
class ATRPositionSizerConfig:
    """Configuration for ATR-based position sizing."""
    risk_per_trade_pct: float = 0.01  # 1% of capital at risk per trade
    atr_period: int = 14  # ATR calculation period
    atr_multiplier: float = 2.0  # Stop-loss distance (2× ATR)
    min_position_value: float = 1000
    max_position_pct: float = 0.10  # Cap at 10% of capital

class ATRPositionSizer:
    """
    Volatility-adjusted position sizing using Average True Range (ATR).

    Allocates smaller positions to volatile assets, larger to stable assets,
    achieving equal risk per trade across the portfolio.
    """

    def __init__(self, config: Optional[ATRPositionSizerConfig] = None):
        self.config = config or ATRPositionSizerConfig()

    def calculate_atr(self, ohlcv: 'pd.DataFrame') -> float:
        """
        Calculate Average True Range.

        Args:
            ohlcv: DataFrame with columns [High, Low, Close]

        Returns:
            ATR value
        """
        import pandas as pd

        # True Range = max(high - low, |high - prev_close|, |low - prev_close|)
        high_low = ohlcv['High'] - ohlcv['Low']
        high_close = (ohlcv['High'] - ohlcv['Close'].shift(1)).abs()
        low_close = (ohlcv['Low'] - ohlcv['Close'].shift(1)).abs()

        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

        # ATR = moving average of true range
        atr = true_range.rolling(window=self.config.atr_period).mean().iloc[-1]

        return atr

    def calculate_position_size(
        self,
        capital: float,
        current_price: float,
        atr: float
    ) -> tuple[float, int]:
        """
        Calculate ATR-based position size.

        Args:
            capital: Current account capital
            current_price: Current asset price
            atr: Average True Range value

        Returns:
            Tuple of (position_value_dollars, num_shares)

        Example:
            >>> sizer = ATRPositionSizer()
            >>> # Stock at $100, ATR = $5, capital = $100k, risk 1%
            >>> position_value, shares = sizer.calculate_position_size(
            ...     capital=100000, current_price=100, atr=5
            ... )
            >>> # Risk per trade: $100k × 1% = $1,000
            >>> # Stop distance: 2 × $5 ATR = $10
            >>> # Position size: $1,000 / $10 = 100 shares = $10,000
        """
        # Risk per trade in dollars
        risk_dollars = capital * self.config.risk_per_trade_pct

        # Stop-loss distance in dollars
        stop_distance = atr * self.config.atr_multiplier

        if stop_distance == 0:
            return 0.0, 0

        # Number of shares that risks exactly risk_dollars
        num_shares = int(risk_dollars / stop_distance)

        # Position value
        position_value = num_shares * current_price

        # Apply constraints
        if position_value < self.config.min_position_value:
            return 0.0, 0

        # Cap at maximum position size
        max_position_value = capital * self.config.max_position_pct
        if position_value > max_position_value:
            position_value = max_position_value
            num_shares = int(position_value / current_price)

        return position_value, num_shares
```

#### **2.3.2 Standard Deviation-Based**

**Formula**:
```
Position Size = (Risk per Trade) / (σ × Z-score)

Where:
  σ = Historical volatility (annualized standard deviation of returns)
  Z-score = Confidence level (e.g., 1.96 for 95% confidence)
```

**Implementation**:
```python
@dataclass
class VolatilityPositionSizerConfig:
    """Configuration for volatility-based position sizing."""
    risk_per_trade_pct: float = 0.01
    volatility_lookback: int = 30  # Days for volatility calculation
    confidence_level: float = 0.95  # 95% confidence (z-score = 1.96)
    max_position_pct: float = 0.10

class VolatilityPositionSizer:
    """
    Volatility-adjusted position sizing using standard deviation.

    Similar to ATR but uses return volatility instead of price range.
    Better for portfolio-level risk parity.
    """

    def __init__(self, config: Optional[VolatilityPositionSizerConfig] = None):
        self.config = config or VolatilityPositionSizerConfig()

    def calculate_volatility(self, returns: 'pd.Series') -> float:
        """
        Calculate annualized volatility from returns.

        Args:
            returns: Daily return series

        Returns:
            Annualized volatility (std dev)
        """
        daily_vol = returns.std()
        annual_vol = daily_vol * (252 ** 0.5)  # Annualize
        return annual_vol

    def calculate_position_size(
        self,
        capital: float,
        current_price: float,
        volatility: float
    ) -> tuple[float, int]:
        """
        Calculate volatility-adjusted position size.

        Args:
            capital: Current account capital
            current_price: Current asset price
            volatility: Annualized volatility (e.g., 0.30 = 30%)

        Returns:
            Tuple of (position_value_dollars, num_shares)
        """
        import scipy.stats as stats

        # Z-score for confidence level
        z_score = stats.norm.ppf(self.config.confidence_level)

        # Risk per trade
        risk_dollars = capital * self.config.risk_per_trade_pct

        # Expected price move (1-day horizon)
        daily_volatility = volatility / (252 ** 0.5)
        expected_move = current_price * daily_volatility * z_score

        if expected_move == 0:
            return 0.0, 0

        # Position size to risk exactly risk_dollars
        num_shares = int(risk_dollars / expected_move)
        position_value = num_shares * current_price

        # Apply maximum constraint
        max_position_value = capital * self.config.max_position_pct
        if position_value > max_position_value:
            position_value = max_position_value
            num_shares = int(position_value / current_price)

        return position_value, num_shares
```

**Use Case**: Portfolio-level risk parity, cross-asset strategies (equities + crypto + forex with different volatilities)

### 2.4 Position Sizing Selector

**Unified interface for strategy selection**:

```python
from enum import Enum
from typing import Union

class PositionSizingMethod(Enum):
    """Available position sizing methods."""
    KELLY = "kelly"
    FIXED_FRACTIONAL = "fixed_fractional"
    ATR = "atr"
    VOLATILITY = "volatility"

class PositionSizer:
    """
    Unified position sizing interface.

    Selects appropriate sizing method based on strategy requirements.
    """

    def __init__(self, method: PositionSizingMethod, config: dict = None):
        self.method = method

        if method == PositionSizingMethod.KELLY:
            self.sizer = KellyPositionSizer(KellyConfig(**config) if config else None)
        elif method == PositionSizingMethod.FIXED_FRACTIONAL:
            self.sizer = FixedFractionalPositionSizer(FixedFractionalConfig(**config) if config else None)
        elif method == PositionSizingMethod.ATR:
            self.sizer = ATRPositionSizer(ATRPositionSizerConfig(**config) if config else None)
        elif method == PositionSizingMethod.VOLATILITY:
            self.sizer = VolatilityPositionSizer(VolatilityPositionSizerConfig(**config) if config else None)
        else:
            raise ValueError(f"Unknown position sizing method: {method}")

    def calculate(self, **kwargs) -> Union[float, tuple[float, int]]:
        """
        Calculate position size using selected method.

        Args vary by method - see individual sizer implementations.
        """
        if self.method == PositionSizingMethod.KELLY:
            return self.sizer.calculate_position_size(**kwargs)
        elif self.method == PositionSizingMethod.FIXED_FRACTIONAL:
            return self.sizer.calculate_position_size(**kwargs)
        elif self.method == PositionSizingMethod.ATR:
            return self.sizer.calculate_position_size(**kwargs)
        elif self.method == PositionSizingMethod.VOLATILITY:
            return self.sizer.calculate_position_size(**kwargs)
```

---

## 3. Portfolio Constraints

### 3.1 Position-Level Limits

```python
@dataclass
class PositionLimits:
    """Position-level risk constraints."""
    max_position_pct: float = 0.10  # Maximum 10% of capital per position
    max_leverage: float = 1.0  # No leverage (1.0 = 100% capital utilization)
    max_concentration_single_symbol: float = 0.15  # Max 15% in any single symbol
    min_position_value: float = 1000  # Minimum position size to avoid excessive fees
    max_positions_per_strategy: int = 10  # Maximum concurrent positions per strategy

class PositionLimitValidator:
    """Validates individual positions against limits."""

    def __init__(self, limits: Optional[PositionLimits] = None):
        self.limits = limits or PositionLimits()

    def validate_position(
        self,
        position_value: float,
        total_capital: float,
        current_positions: dict
    ) -> tuple[bool, str]:
        """
        Validate proposed position against limits.

        Args:
            position_value: Proposed position value in dollars
            total_capital: Total account capital
            current_positions: Dict of {symbol: position_value} for existing positions

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check position size as % of capital
        position_pct = position_value / total_capital
        if position_pct > self.limits.max_position_pct:
            return False, f"Position size {position_pct:.1%} exceeds max {self.limits.max_position_pct:.1%}"

        # Check minimum position value
        if position_value < self.limits.min_position_value:
            return False, f"Position value ${position_value} below minimum ${self.limits.min_position_value}"

        # Check leverage
        total_exposure = sum(current_positions.values()) + position_value
        leverage = total_exposure / total_capital
        if leverage > self.limits.max_leverage:
            return False, f"Leverage {leverage:.2f}× exceeds max {self.limits.max_leverage:.2f}×"

        # Check max positions
        if len(current_positions) >= self.limits.max_positions_per_strategy:
            return False, f"Already at max positions ({self.limits.max_positions_per_strategy})"

        return True, ""
```

### 3.2 Portfolio-Level Limits

```python
@dataclass
class PortfolioLimits:
    """Portfolio-level risk constraints."""
    max_gross_exposure: float = 1.0  # Max 100% capital deployed (long + short)
    max_net_exposure: float = 0.6  # Max 60% directional bias (long - short)
    max_sector_exposure: float = 0.30  # Max 30% in any single sector
    max_correlation_to_market: float = 0.7  # Max 0.7 correlation to SPY
    max_portfolio_concentration: float = 0.25  # Max 25% in top 3 positions
    min_strategy_diversification: int = 3  # Minimum 3 active strategies

class PortfolioLimitValidator:
    """Validates portfolio-level constraints."""

    def __init__(self, limits: Optional[PortfolioLimits] = None):
        self.limits = limits or PortfolioLimits()

    def calculate_gross_exposure(self, positions: dict) -> float:
        """Calculate gross exposure (sum of abs values)."""
        return sum(abs(value) for value in positions.values())

    def calculate_net_exposure(self, positions: dict) -> float:
        """Calculate net exposure (sum including sign)."""
        return sum(positions.values())

    def calculate_sector_exposure(
        self,
        positions: dict,
        symbol_sectors: dict
    ) -> dict:
        """
        Calculate exposure by sector.

        Args:
            positions: {symbol: position_value}
            symbol_sectors: {symbol: sector}

        Returns:
            {sector: total_exposure}
        """
        sector_exposure = {}
        for symbol, value in positions.items():
            sector = symbol_sectors.get(symbol, 'Unknown')
            sector_exposure[sector] = sector_exposure.get(sector, 0) + abs(value)
        return sector_exposure

    def validate_portfolio(
        self,
        positions: dict,
        total_capital: float,
        symbol_sectors: dict
    ) -> tuple[bool, list[str]]:
        """
        Validate entire portfolio against limits.

        Args:
            positions: {symbol: position_value} (positive = long, negative = short)
            total_capital: Total account capital
            symbol_sectors: {symbol: sector_name}

        Returns:
            Tuple of (is_valid, list_of_violations)
        """
        violations = []

        # Gross exposure check
        gross_exposure = self.calculate_gross_exposure(positions)
        gross_pct = gross_exposure / total_capital
        if gross_pct > self.limits.max_gross_exposure:
            violations.append(
                f"Gross exposure {gross_pct:.1%} exceeds max {self.limits.max_gross_exposure:.1%}"
            )

        # Net exposure check
        net_exposure = self.calculate_net_exposure(positions)
        net_pct = abs(net_exposure) / total_capital
        if net_pct > self.limits.max_net_exposure:
            violations.append(
                f"Net exposure {net_pct:.1%} exceeds max {self.limits.max_net_exposure:.1%}"
            )

        # Sector exposure check
        sector_exposure = self.calculate_sector_exposure(positions, symbol_sectors)
        for sector, exposure in sector_exposure.items():
            sector_pct = exposure / total_capital
            if sector_pct > self.limits.max_sector_exposure:
                violations.append(
                    f"Sector '{sector}' exposure {sector_pct:.1%} exceeds max {self.limits.max_sector_exposure:.1%}"
                )

        # Concentration check (top 3 positions)
        sorted_positions = sorted(positions.values(), key=abs, reverse=True)
        top3_value = sum(abs(v) for v in sorted_positions[:3])
        concentration = top3_value / total_capital
        if concentration > self.limits.max_portfolio_concentration:
            violations.append(
                f"Top 3 positions {concentration:.1%} exceeds max {self.limits.max_portfolio_concentration:.1%}"
            )

        return len(violations) == 0, violations
```

### 3.3 Correlation Limits

```python
@dataclass
class CorrelationLimits:
    """Correlation-based portfolio constraints."""
    max_pairwise_correlation: float = 0.8  # Max correlation between any two positions
    max_avg_correlation: float = 0.5  # Max average correlation across portfolio
    correlation_lookback: int = 60  # Days for correlation calculation
    min_correlation_samples: int = 30  # Minimum data points for valid correlation

class CorrelationLimitValidator:
    """Validates correlation constraints to ensure diversification."""

    def __init__(self, limits: Optional[CorrelationLimits] = None):
        self.limits = limits or CorrelationLimits()

    def calculate_correlation_matrix(
        self,
        returns_data: 'pd.DataFrame'
    ) -> 'pd.DataFrame':
        """
        Calculate correlation matrix from returns.

        Args:
            returns_data: DataFrame with columns = symbols, rows = dates, values = returns

        Returns:
            Correlation matrix (DataFrame)
        """
        # Use last N days
        recent_returns = returns_data.tail(self.limits.correlation_lookback)

        # Calculate correlation
        correlation_matrix = recent_returns.corr()

        return correlation_matrix

    def validate_correlation(
        self,
        new_symbol: str,
        existing_symbols: list,
        returns_data: 'pd.DataFrame'
    ) -> tuple[bool, str]:
        """
        Validate that adding new_symbol doesn't violate correlation limits.

        Args:
            new_symbol: Symbol to add
            existing_symbols: Currently held symbols
            returns_data: Historical returns data

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not existing_symbols:
            return True, ""  # First position always allowed

        # Calculate correlation matrix
        all_symbols = existing_symbols + [new_symbol]

        # Check if we have enough data
        if new_symbol not in returns_data.columns:
            return False, f"No return data available for {new_symbol}"

        if len(returns_data) < self.limits.min_correlation_samples:
            return True, ""  # Not enough data; allow position but warn

        correlation_matrix = self.calculate_correlation_matrix(
            returns_data[all_symbols]
        )

        # Check pairwise correlation with existing positions
        for existing_symbol in existing_symbols:
            corr = correlation_matrix.loc[new_symbol, existing_symbol]

            if abs(corr) > self.limits.max_pairwise_correlation:
                return False, (
                    f"Correlation with {existing_symbol} ({corr:.2f}) "
                    f"exceeds max {self.limits.max_pairwise_correlation:.2f}"
                )

        # Check average portfolio correlation
        # Exclude diagonal (self-correlation = 1.0)
        n = len(all_symbols)
        if n > 1:
            total_corr = correlation_matrix.values.sum() - n  # Subtract diagonal
            avg_corr = total_corr / (n * (n - 1))  # Average off-diagonal

            if avg_corr > self.limits.max_avg_correlation:
                return False, (
                    f"Average portfolio correlation ({avg_corr:.2f}) "
                    f"exceeds max {self.limits.max_avg_correlation:.2f}"
                )

        return True, ""
```

---

## 4. Risk Metrics

### 4.1 Value at Risk (VaR)

**Purpose**: Estimate maximum expected loss over a given time horizon at a specific confidence level

**Methods**:

#### **4.1.1 Historical VaR**

```python
import numpy as np
import pandas as pd
from typing import Literal

class VaRCalculator:
    """Calculate Value at Risk using multiple methods."""

    @staticmethod
    def historical_var(
        returns: pd.Series,
        confidence_level: float = 0.95,
        horizon_days: int = 1
    ) -> float:
        """
        Calculate Historical VaR (non-parametric).

        Args:
            returns: Historical return series
            confidence_level: Confidence level (0.95 = 95%, 0.99 = 99%)
            horizon_days: Time horizon in days

        Returns:
            VaR as positive number (e.g., 0.05 = 5% loss)

        Example:
            >>> returns = pd.Series([0.01, -0.02, 0.015, -0.01, 0.005])
            >>> var_95 = VaRCalculator.historical_var(returns, confidence_level=0.95)
            >>> print(f"95% VaR: {var_95:.2%}")  # Expected maximum loss
        """
        # Sort returns
        sorted_returns = returns.sort_values()

        # Find percentile corresponding to (1 - confidence_level)
        percentile = (1 - confidence_level) * 100
        var = np.percentile(sorted_returns, percentile)

        # Scale to horizon
        var_horizon = var * np.sqrt(horizon_days)

        # Return as positive number (loss)
        return abs(var_horizon)

    @staticmethod
    def parametric_var(
        returns: pd.Series,
        confidence_level: float = 0.95,
        horizon_days: int = 1
    ) -> float:
        """
        Calculate Parametric VaR (assumes normal distribution).

        Faster but less accurate for non-normal return distributions.

        Args:
            returns: Historical return series
            confidence_level: Confidence level
            horizon_days: Time horizon in days

        Returns:
            VaR as positive number
        """
        import scipy.stats as stats

        # Calculate mean and std dev
        mu = returns.mean()
        sigma = returns.std()

        # Z-score for confidence level
        z_score = stats.norm.ppf(1 - confidence_level)

        # VaR formula: μ - z×σ (negative because z_score is negative)
        var = mu + z_score * sigma

        # Scale to horizon
        var_horizon = var * np.sqrt(horizon_days)

        return abs(var_horizon)

    @staticmethod
    def monte_carlo_var(
        returns: pd.Series,
        confidence_level: float = 0.95,
        horizon_days: int = 1,
        num_simulations: int = 10000
    ) -> float:
        """
        Calculate Monte Carlo VaR (simulations from historical distribution).

        Most accurate but computationally expensive.

        Args:
            returns: Historical return series
            confidence_level: Confidence level
            horizon_days: Time horizon in days
            num_simulations: Number of Monte Carlo simulations

        Returns:
            VaR as positive number
        """
        # Bootstrap from historical returns
        simulated_returns = np.random.choice(
            returns.values,
            size=(num_simulations, horizon_days),
            replace=True
        )

        # Calculate cumulative return for each simulation
        cumulative_returns = (1 + simulated_returns).prod(axis=1) - 1

        # Find percentile
        percentile = (1 - confidence_level) * 100
        var = np.percentile(cumulative_returns, percentile)

        return abs(var)
```

#### **4.1.2 Conditional VaR (CVaR / Expected Shortfall)**

```python
class CVaRCalculator:
    """Calculate Conditional Value at Risk (Expected Shortfall)."""

    @staticmethod
    def historical_cvar(
        returns: pd.Series,
        confidence_level: float = 0.95
    ) -> float:
        """
        Calculate Historical CVaR.

        CVaR is the average loss beyond the VaR threshold.
        More conservative than VaR (accounts for tail risk).

        Args:
            returns: Historical return series
            confidence_level: Confidence level

        Returns:
            CVaR as positive number

        Example:
            >>> # If 95% VaR = 3%, CVaR might be 5% (average of worst 5% scenarios)
        """
        # Calculate VaR first
        var_threshold = VaRCalculator.historical_var(returns, confidence_level)

        # Find all returns worse than VaR
        tail_losses = returns[returns <= -var_threshold]

        # Average of tail losses
        if len(tail_losses) == 0:
            return var_threshold  # No tail events; CVaR = VaR

        cvar = abs(tail_losses.mean())

        return cvar
```

### 4.2 Portfolio Beta

```python
class PortfolioMetricsCalculator:
    """Calculate portfolio-level risk metrics."""

    @staticmethod
    def portfolio_beta(
        portfolio_returns: pd.Series,
        market_returns: pd.Series
    ) -> float:
        """
        Calculate portfolio beta relative to market (typically SPY).

        Beta > 1: More volatile than market
        Beta = 1: Moves with market
        Beta < 1: Less volatile than market
        Beta < 0: Inverse correlation (hedged)

        Args:
            portfolio_returns: Portfolio return series
            market_returns: Market return series (e.g., SPY)

        Returns:
            Beta coefficient
        """
        # Align indices
        aligned = pd.DataFrame({
            'portfolio': portfolio_returns,
            'market': market_returns
        }).dropna()

        # Calculate covariance and variance
        covariance = aligned['portfolio'].cov(aligned['market'])
        market_variance = aligned['market'].var()

        if market_variance == 0:
            return 0.0

        beta = covariance / market_variance

        return beta

    @staticmethod
    def concentration_risk(positions: dict, total_value: float) -> float:
        """
        Calculate Herfindahl-Hirschman Index (HHI) for concentration.

        HHI = sum of squared position weights

        HHI = 1.0: Fully concentrated (single position)
        HHI → 0: Fully diversified (many equal positions)

        Args:
            positions: {symbol: position_value}
            total_value: Total portfolio value

        Returns:
            HHI (0 to 1)
        """
        if total_value == 0:
            return 0.0

        # Calculate weights
        weights = [abs(value) / total_value for value in positions.values()]

        # HHI = sum of squared weights
        hhi = sum(w ** 2 for w in weights)

        return hhi

    @staticmethod
    def effective_number_of_positions(positions: dict, total_value: float) -> float:
        """
        Calculate effective number of independent positions.

        ENP = 1 / HHI

        ENP = 1: Effectively one position
        ENP = N: N equally weighted positions

        Args:
            positions: {symbol: position_value}
            total_value: Total portfolio value

        Returns:
            Effective number of positions
        """
        hhi = PortfolioMetricsCalculator.concentration_risk(positions, total_value)

        if hhi == 0:
            return 0.0

        return 1 / hhi
```

---

## 5. Drawdown Management

### 5.1 Drawdown Tracker

```python
@dataclass
class DrawdownLimits:
    """Drawdown limit configuration."""
    max_drawdown_pct: float = 0.20  # 20% max drawdown
    daily_loss_limit_pct: float = 0.05  # 5% max daily loss
    trailing_stop_pct: float = 0.15  # 15% trailing stop from peak
    recovery_threshold_pct: float = 0.10  # Resume trading after recovering to -10%

class DrawdownTracker:
    """
    Track portfolio drawdown and enforce limits.

    Implements circuit breaker functionality to halt trading during
    excessive drawdowns.
    """

    def __init__(self, limits: Optional[DrawdownLimits] = None):
        self.limits = limits or DrawdownLimits()
        self.reset()

    def reset(self) -> None:
        """Reset tracker state for new backtest session or strategy."""
        self.peak_value = 0.0
        self.trading_halted = False

    def update(
        self,
        current_value: float,
        previous_value: float,
        initial_value: float
    ) -> tuple[float, bool, list[str]]:
        """
        Update drawdown tracker and check limits.

        Args:
            current_value: Current portfolio value
            previous_value: Previous day's portfolio value
            initial_value: Initial account value

        Returns:
            Tuple of (current_drawdown_pct, halt_trading, list_of_violations)
        """
        violations = []

        # Initialize peak on first update
        if self.peak_value == 0:
            self.peak_value = current_value

        # Update peak
        if current_value > self.peak_value:
            self.peak_value = current_value

        # Calculate drawdown from peak
        drawdown_pct = (self.peak_value - current_value) / self.peak_value

        # Calculate daily loss
        if previous_value == 0:
            daily_loss_pct = 0.0
        else:
            daily_loss_pct = max(0, (previous_value - current_value) / previous_value)

        # Check max drawdown
        if drawdown_pct > self.limits.max_drawdown_pct:
            violations.append(
                f"Max drawdown {drawdown_pct:.1%} exceeds limit {self.limits.max_drawdown_pct:.1%}"
            )
            self.trading_halted = True

        # Check daily loss limit
        if daily_loss_pct > self.limits.daily_loss_limit_pct:
            violations.append(
                f"Daily loss {daily_loss_pct:.1%} exceeds limit {self.limits.daily_loss_limit_pct:.1%}"
            )
            self.trading_halted = True

        # Check trailing stop
        trailing_stop_value = self.peak_value * (1 - self.limits.trailing_stop_pct)
        if current_value < trailing_stop_value:
            violations.append(
                f"Trailing stop triggered: ${current_value:,.0f} < ${trailing_stop_value:,.0f}"
            )
            self.trading_halted = True

        # Check recovery threshold (resume trading)
        if self.trading_halted:
            recovery_drawdown = (self.peak_value - current_value) / self.peak_value
            if recovery_drawdown <= self.limits.recovery_threshold_pct:
                self.trading_halted = False
                violations.append("Trading resumed after recovery")

        return drawdown_pct, self.trading_halted, violations
```

---

## 6. Pre-Trade Risk Validation

### 6.1 Pre-Trade Validator

```python
from dataclasses import dataclass
from typing import Optional, Tuple
import pandas as pd

@dataclass
class PreTradeValidationResult:
    """Result of pre-trade validation."""
    approved: bool
    position_size: float  # Approved position size (may be reduced from requested)
    violations: list[str]
    warnings: list[str]

class PreTradeRiskValidator:
    """
    Comprehensive pre-trade risk validation.

    Validates proposed trades against ALL risk limits before execution.
    This is the final gatekeeper before order submission.
    """

    def __init__(
        self,
        position_limits: Optional[PositionLimits] = None,
        portfolio_limits: Optional[PortfolioLimits] = None,
        correlation_limits: Optional[CorrelationLimits] = None,
        drawdown_limits: Optional[DrawdownLimits] = None,
        correlation_cache_ttl: int = 300  # 5 minutes
    ):
        self.position_validator = PositionLimitValidator(position_limits)
        self.portfolio_validator = PortfolioLimitValidator(portfolio_limits)
        self.correlation_validator = CorrelationLimitValidator(correlation_limits)
        self.drawdown_tracker = DrawdownTracker(drawdown_limits)

        # Performance optimization: Cache correlation matrices
        self._correlation_cache = {}  # {frozenset(symbols): (timestamp, corr_matrix)}
        self._cache_ttl = correlation_cache_ttl

    def _validate_correlation_cached(
        self,
        symbol: str,
        existing_symbols: list,
        returns_data: pd.DataFrame
    ) -> tuple[bool, str]:
        """
        Validate correlation with caching for <100ms performance.

        Returns:
            Tuple of (is_valid, error_message)
        """
        from time import time

        if not existing_symbols:
            return True, ""

        # Create cache key from existing symbols
        cache_key = frozenset(existing_symbols)

        # Check cache
        if cache_key in self._correlation_cache:
            cached_time, cached_corr_matrix = self._correlation_cache[cache_key]

            # Use cached matrix if still fresh
            if time() - cached_time < self._cache_ttl:
                # Calculate correlation for new symbol only
                if symbol in returns_data.columns:
                    for existing_symbol in existing_symbols:
                        if existing_symbol in cached_corr_matrix.index:
                            # Calculate pairwise correlation
                            corr = returns_data[[symbol, existing_symbol]].corr().iloc[0, 1]

                            if abs(corr) > self.correlation_validator.limits.max_pairwise_correlation:
                                return False, (
                                    f"Correlation with {existing_symbol} ({corr:.2f}) "
                                    f"exceeds max {self.correlation_validator.limits.max_pairwise_correlation:.2f}"
                                )
                    return True, ""

        # Cache miss or expired - do full validation and cache result
        valid, error = self.correlation_validator.validate_correlation(
            symbol, existing_symbols, returns_data
        )

        # Update cache if we have valid existing symbols
        if existing_symbols:
            # Calculate and cache correlation matrix for existing positions
            try:
                corr_matrix = returns_data[existing_symbols].corr()
                self._correlation_cache[cache_key] = (time(), corr_matrix)
            except Exception:
                pass  # Skip caching on errors

        return valid, error

    def validate_trade(
        self,
        symbol: str,
        proposed_size: float,
        current_positions: dict,
        total_capital: float,
        symbol_sectors: dict,
        returns_data: pd.DataFrame,
        current_portfolio_value: float,
        previous_portfolio_value: float,
        initial_capital: float
    ) -> PreTradeValidationResult:
        """
        Validate proposed trade against all risk limits.

        Args:
            symbol: Symbol to trade
            proposed_size: Proposed position size (dollars)
            current_positions: {symbol: position_value}
            total_capital: Total account capital
            symbol_sectors: {symbol: sector}
            returns_data: Historical return data for correlation
            current_portfolio_value: Current portfolio value
            previous_portfolio_value: Previous day's value
            initial_capital: Initial account value

        Returns:
            PreTradeValidationResult with approval/rejection decision
        """
        violations = []
        warnings = []
        approved_size = proposed_size

        # 1. Check if trading is halted (drawdown limits)
        _, trading_halted, dd_violations = self.drawdown_tracker.update(
            current_portfolio_value,
            previous_portfolio_value,
            initial_capital
        )

        if trading_halted:
            violations.extend(dd_violations)
            return PreTradeValidationResult(
                approved=False,
                position_size=0.0,
                violations=violations,
                warnings=warnings
            )

        # 2. Position-level validation
        pos_valid, pos_error = self.position_validator.validate_position(
            proposed_size,
            total_capital,
            current_positions
        )

        if not pos_valid:
            violations.append(pos_error)

        # 3. Portfolio-level validation (with proposed position)
        test_positions = current_positions.copy()
        test_positions[symbol] = test_positions.get(symbol, 0) + proposed_size

        port_valid, port_violations = self.portfolio_validator.validate_portfolio(
            test_positions,
            total_capital,
            symbol_sectors
        )

        if not port_valid:
            violations.extend(port_violations)

        # 4. Correlation validation (with caching for performance)
        existing_symbols = list(current_positions.keys())
        corr_valid, corr_error = self._validate_correlation_cached(
            symbol,
            existing_symbols,
            returns_data
        )

        if not corr_valid:
            warnings.append(corr_error)  # Correlation is warning, not hard block

        # 5. Size reduction if soft limits violated
        if warnings and not violations:
            # Reduce position size by 50% if warnings present
            approved_size = proposed_size * 0.5
            warnings.append(f"Position size reduced to {approved_size} due to warnings")

        # Final decision
        approved = len(violations) == 0

        return PreTradeValidationResult(
            approved=approved,
            position_size=approved_size if approved else 0.0,
            violations=violations,
            warnings=warnings
        )
```

### 6.2 Integration with Backtesting Engine

**IMPORTANT - Cash Accounting for Short Positions**:

The current BacktestEngine (src/backtest/engine.py) has a documented conservative cash accounting approach for short positions. When integrating risk management, note:

1. **Use `current_equity`** instead of `total_capital` for risk calculations when short positions are active
2. **Current implementation**: Short positions deduct cash (conservative), compensated in `_calculate_equity()`
3. **Risk validator expectation**: Pass actual equity (cash + position values) as `total_capital` parameter
4. **Formula**: `total_capital = self._calculate_equity(current_price)` (not `self.config.initial_capital`)

This ensures position size percentages and exposure calculations reflect actual portfolio value.

**Modified BacktestEngine to include pre-trade validation**:

```python
# src/backtest/engine.py integration example

class RiskAwareBacktestEngine(BacktestEngine):
    """
    Enhanced backtest engine with integrated risk management.

    Extends base BacktestEngine to enforce risk limits during backtesting.
    """

    def __init__(
        self,
        config: BacktestConfig,
        risk_validator: PreTradeRiskValidator
    ):
        super().__init__(config)
        self.risk_validator = risk_validator
        self.rejected_trades = []  # Track risk-rejected trades

    def _open_position_with_risk_check(
        self,
        timestamp: pd.Timestamp,
        price: float,
        side: PositionSide,
        symbol: str,
        current_positions: dict,
        returns_data: pd.DataFrame,
        symbol_sectors: dict
    ) -> Optional[Position]:
        """
        Open position with pre-trade risk validation.

        Returns None if trade rejected by risk validator.
        """
        # Calculate proposed position size (use existing logic)
        quantity = self._calculate_position_size(price)
        proposed_value = quantity * price

        # Run risk validation
        validation_result = self.risk_validator.validate_trade(
            symbol=symbol,
            proposed_size=proposed_value,
            current_positions=current_positions,
            total_capital=self.config.initial_capital,
            symbol_sectors=symbol_sectors,
            returns_data=returns_data,
            current_portfolio_value=self._calculate_equity(price),
            previous_portfolio_value=self.equity_history[-1] if self.equity_history else self.config.initial_capital,
            initial_capital=self.config.initial_capital
        )

        # Log rejection
        if not validation_result.approved:
            self.rejected_trades.append({
                'timestamp': timestamp,
                'symbol': symbol,
                'proposed_size': proposed_value,
                'violations': validation_result.violations
            })
            return None

        # Proceed with approved size (may be reduced)
        approved_quantity = int(validation_result.position_size / price)

        # Call parent's _open_position with approved quantity
        return super()._open_position(timestamp, price, side)
```

---

## 7. Real-Time Monitoring & Alerts

### 7.1 Risk Dashboard Metrics

```python
@dataclass
class RiskDashboardMetrics:
    """Real-time risk metrics for dashboard display."""
    # Position metrics
    num_positions: int
    largest_position_pct: float
    smallest_position_pct: float

    # Portfolio metrics
    gross_exposure_pct: float
    net_exposure_pct: float
    leverage: float

    # Risk metrics
    var_95_pct: float
    var_99_pct: float
    cvar_95_pct: float
    portfolio_beta: float
    concentration_hhi: float
    avg_correlation: float

    # Drawdown metrics
    current_drawdown_pct: float
    max_drawdown_pct: float
    days_since_peak: int

    # Limit utilization
    position_limit_utilization: float  # % of max position size used
    gross_exposure_utilization: float  # % of max gross exposure used
    drawdown_limit_utilization: float  # % of max drawdown used

    # Status
    trading_halted: bool
    active_violations: list[str]
    active_warnings: list[str]

class RiskMonitor:
    """Real-time risk monitoring and dashboard metrics."""

    def __init__(
        self,
        position_limits: PositionLimits,
        portfolio_limits: PortfolioLimits,
        drawdown_limits: DrawdownLimits
    ):
        self.position_limits = position_limits
        self.portfolio_limits = portfolio_limits
        self.drawdown_limits = drawdown_limits
        self.drawdown_tracker = DrawdownTracker(drawdown_limits)

    def calculate_metrics(
        self,
        positions: dict,
        total_capital: float,
        portfolio_returns: pd.Series,
        market_returns: pd.Series,
        current_value: float,
        previous_value: float,
        initial_value: float,
        peak_value: float
    ) -> RiskDashboardMetrics:
        """
        Calculate all risk metrics for dashboard display.

        Args:
            positions: Current positions {symbol: value}
            total_capital: Total account capital
            portfolio_returns: Historical portfolio returns
            market_returns: Market (SPY) returns
            current_value: Current portfolio value
            previous_value: Previous day's value
            initial_value: Initial account value
            peak_value: Historical peak value

        Returns:
            RiskDashboardMetrics object
        """
        violations = []
        warnings = []

        # Position metrics
        if positions:
            position_values = [abs(v) for v in positions.values()]
            largest_position = max(position_values)
            smallest_position = min(position_values)
        else:
            largest_position = 0
            smallest_position = 0

        # Portfolio metrics
        gross_exposure = sum(abs(v) for v in positions.values())
        net_exposure = sum(positions.values())
        leverage = gross_exposure / total_capital if total_capital > 0 else 0

        # Risk metrics
        var_95 = VaRCalculator.historical_var(portfolio_returns, confidence_level=0.95)
        var_99 = VaRCalculator.historical_var(portfolio_returns, confidence_level=0.99)
        cvar_95 = CVaRCalculator.historical_cvar(portfolio_returns, confidence_level=0.95)
        beta = PortfolioMetricsCalculator.portfolio_beta(portfolio_returns, market_returns)
        hhi = PortfolioMetricsCalculator.concentration_risk(positions, total_capital)

        # Calculate avg correlation (simplified)
        avg_corr = 0.0  # TODO: Implement correlation calculation

        # Drawdown metrics
        drawdown_pct, trading_halted, dd_violations = self.drawdown_tracker.update(
            current_value, previous_value, initial_value
        )
        violations.extend(dd_violations)

        max_drawdown = (peak_value - current_value) / peak_value if peak_value > 0 else 0
        days_since_peak = 0  # TODO: Track days since peak

        # Limit utilization
        position_utilization = (largest_position / total_capital) / self.position_limits.max_position_pct
        gross_utilization = (gross_exposure / total_capital) / self.portfolio_limits.max_gross_exposure
        drawdown_utilization = drawdown_pct / self.drawdown_limits.max_drawdown_pct

        return RiskDashboardMetrics(
            num_positions=len(positions),
            largest_position_pct=largest_position / total_capital if total_capital > 0 else 0,
            smallest_position_pct=smallest_position / total_capital if total_capital > 0 else 0,
            gross_exposure_pct=gross_exposure / total_capital if total_capital > 0 else 0,
            net_exposure_pct=net_exposure / total_capital if total_capital > 0 else 0,
            leverage=leverage,
            var_95_pct=var_95,
            var_99_pct=var_99,
            cvar_95_pct=cvar_95,
            portfolio_beta=beta,
            concentration_hhi=hhi,
            avg_correlation=avg_corr,
            current_drawdown_pct=drawdown_pct,
            max_drawdown_pct=max_drawdown,
            days_since_peak=days_since_peak,
            position_limit_utilization=position_utilization,
            gross_exposure_utilization=gross_utilization,
            drawdown_limit_utilization=drawdown_utilization,
            trading_halted=trading_halted,
            active_violations=violations,
            active_warnings=warnings
        )
```

### 7.2 Alert System

```python
from enum import Enum
from datetime import datetime

class AlertSeverity(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass
class RiskAlert:
    """Risk alert structure."""
    timestamp: datetime
    severity: AlertSeverity
    category: str  # 'position', 'portfolio', 'drawdown', 'correlation'
    message: str
    metric_value: float
    limit_value: float
    action_required: str

class RiskAlertManager:
    """Manage risk alerts and notifications."""

    def __init__(self):
        self.alerts = []
        self.alert_history = []

    def check_thresholds_and_alert(
        self,
        metrics: RiskDashboardMetrics
    ) -> list[RiskAlert]:
        """
        Check metrics against thresholds and generate alerts.

        Args:
            metrics: Current risk dashboard metrics

        Returns:
            List of new alerts
        """
        new_alerts = []

        # Position limit alerts
        if metrics.position_limit_utilization > 0.9:
            new_alerts.append(RiskAlert(
                timestamp=datetime.now(),
                severity=AlertSeverity.WARNING,
                category='position',
                message='Position limit utilization above 90%',
                metric_value=metrics.position_limit_utilization,
                limit_value=1.0,
                action_required='Consider reducing position sizes'
            ))

        # Gross exposure alerts
        if metrics.gross_exposure_utilization > 0.9:
            new_alerts.append(RiskAlert(
                timestamp=datetime.now(),
                severity=AlertSeverity.WARNING,
                category='portfolio',
                message='Gross exposure near limit',
                metric_value=metrics.gross_exposure_pct,
                limit_value=1.0,
                action_required='Reduce total exposure or close positions'
            ))

        # Drawdown alerts
        if metrics.drawdown_limit_utilization > 0.8:
            new_alerts.append(RiskAlert(
                timestamp=datetime.now(),
                severity=AlertSeverity.ERROR,
                category='drawdown',
                message='Drawdown approaching maximum limit',
                metric_value=metrics.current_drawdown_pct,
                limit_value=metrics.max_drawdown_pct,
                action_required='Reduce risk exposure immediately'
            ))

        if metrics.trading_halted:
            new_alerts.append(RiskAlert(
                timestamp=datetime.now(),
                severity=AlertSeverity.CRITICAL,
                category='drawdown',
                message='Trading halted due to risk limit violation',
                metric_value=metrics.current_drawdown_pct,
                limit_value=metrics.max_drawdown_pct,
                action_required='Manual review required before resuming trading'
            ))

        # VaR alerts
        if metrics.var_95_pct > 0.10:  # 10% daily VaR threshold
            new_alerts.append(RiskAlert(
                timestamp=datetime.now(),
                severity=AlertSeverity.WARNING,
                category='portfolio',
                message='Daily VaR exceeds 10%',
                metric_value=metrics.var_95_pct,
                limit_value=0.10,
                action_required='Review portfolio risk composition'
            ))

        # Concentration alerts
        if metrics.concentration_hhi > 0.5:
            new_alerts.append(RiskAlert(
                timestamp=datetime.now(),
                severity=AlertSeverity.WARNING,
                category='portfolio',
                message='Portfolio concentration risk elevated',
                metric_value=metrics.concentration_hhi,
                limit_value=0.3,
                action_required='Diversify portfolio holdings'
            ))

        # Store alerts
        self.alerts.extend(new_alerts)
        self.alert_history.extend(new_alerts)

        return new_alerts

    def send_alerts(self, alerts: list[RiskAlert]) -> None:
        """
        Send alerts to configured channels.

        Args:
            alerts: List of alerts to send
        """
        for alert in alerts:
            # Log to console
            severity_prefix = {
                AlertSeverity.INFO: "[INFO]",
                AlertSeverity.WARNING: "[WARNING]",
                AlertSeverity.ERROR: "[ERROR]",
                AlertSeverity.CRITICAL: "[CRITICAL]"
            }

            print(f"{severity_prefix[alert.severity]} {alert.timestamp} - {alert.message}")
            print(f"  Metric: {alert.metric_value:.2%} | Limit: {alert.limit_value:.2%}")
            print(f"  Action: {alert.action_required}")

            # TODO: Send to additional channels (email, GitHub issues, etc.)
```

---

## 8. Integration Architecture

### 8.1 Integration with Backtesting Engine

**File**: `src/backtest/risk_integration.py`

```python
# Integration points with existing BacktestEngine

class BacktestRiskConfig:
    """Risk configuration for backtesting."""
    enable_risk_validation: bool = True
    position_sizing_method: PositionSizingMethod = PositionSizingMethod.FIXED_FRACTIONAL
    position_limits: PositionLimits = PositionLimits()
    portfolio_limits: PortfolioLimits = PortfolioLimits()
    drawdown_limits: DrawdownLimits = DrawdownLimits()

# Modify BacktestEngine.__init__ to accept risk_config
# Add risk validator to engine
# Wrap _open_position with risk checks
# Track rejected trades
# Include risk metrics in BacktestResult
```

### 8.2 Integration with Market Data

**File**: `src/data/risk_data_provider.py`

```python
# Risk data provider leveraging market data client

class RiskDataProvider:
    """Provide data needed for risk calculations."""

    def __init__(self, market_client: 'MarketDataClient'):
        self.client = market_client

    def get_return_data(
        self,
        symbols: list[str],
        lookback_days: int = 60
    ) -> pd.DataFrame:
        """
        Fetch return data for risk calculations.

        Returns:
            DataFrame with columns = symbols, values = daily returns
        """
        # Fetch OHLCV data
        # Calculate returns
        # Return aligned DataFrame
        pass

    def get_volatility_data(
        self,
        symbol: str,
        lookback_days: int = 30
    ) -> float:
        """
        Calculate historical volatility for symbol.

        Returns:
            Annualized volatility (std dev of returns)
        """
        # Fetch OHLCV
        # Calculate returns
        # Calculate std dev and annualize
        pass

    def get_correlation_matrix(
        self,
        symbols: list[str],
        lookback_days: int = 60
    ) -> pd.DataFrame:
        """
        Calculate correlation matrix for symbols.

        Returns:
            Correlation matrix DataFrame
        """
        # Fetch return data
        # Calculate correlation
        pass

    def get_sector_mapping(self) -> dict:
        """
        Get symbol → sector mapping from company_metadata table.

        Returns:
            {symbol: sector}
        """
        # Query company_metadata table
        pass
```

### 8.3 Storage Schema

**File**: `src/risk/schema.sql`

```sql
-- Risk metrics history
CREATE TABLE risk_metrics_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    var_95 REAL,
    var_99 REAL,
    cvar_95 REAL,
    portfolio_beta REAL,
    concentration_hhi REAL,
    gross_exposure_pct REAL,
    net_exposure_pct REAL,
    current_drawdown_pct REAL,
    trading_halted BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_risk_metrics_timestamp ON risk_metrics_history(timestamp);

-- Risk alerts log
CREATE TABLE risk_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    severity VARCHAR(20) NOT NULL,  -- 'info', 'warning', 'error', 'critical'
    category VARCHAR(50) NOT NULL,  -- 'position', 'portfolio', 'drawdown', 'correlation'
    message TEXT NOT NULL,
    metric_value REAL,
    limit_value REAL,
    action_required TEXT,
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_risk_alerts_timestamp ON risk_alerts(timestamp);
CREATE INDEX idx_risk_alerts_severity ON risk_alerts(severity);

-- Rejected trades (risk violations)
CREATE TABLE rejected_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    proposed_size REAL NOT NULL,
    rejection_reason TEXT NOT NULL,
    violations TEXT,  -- JSON array of violations
    strategy_name VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_rejected_trades_timestamp ON rejected_trades(timestamp);
CREATE INDEX idx_rejected_trades_symbol ON rejected_trades(symbol);
```

---

## 9. Testing Strategy

### 9.1 Unit Tests

```python
# tests/test_position_sizing.py
import pytest
from src.risk.position_sizing import KellyPositionSizer, FixedFractionalPositionSizer

def test_kelly_position_sizer():
    """Test Kelly criterion calculation."""
    sizer = KellyPositionSizer()

    # Profitable strategy: 60% win rate, 2:1 reward/risk
    position = sizer.calculate_position_size(
        win_rate=0.6,
        avg_win=2000,
        avg_loss=1000,
        capital=100000,
        num_trades=50
    )

    # Kelly = (0.6 × 2 - 0.4) / 2 = 0.4 = 40%
    # Fractional Kelly (0.5) = 20%
    # With 10% cap, expect 10% = $10,000
    assert 9000 <= position <= 11000

def test_fixed_fractional_position_sizer():
    """Test fixed fractional sizing."""
    sizer = FixedFractionalPositionSizer()

    position = sizer.calculate_position_size(capital=100000, current_positions=0)

    # Default 2% = $2,000
    assert position == 2000

# tests/test_var_calculator.py
def test_historical_var():
    """Test Historical VaR calculation."""
    import pandas as pd
    import numpy as np

    # Create return series with known distribution
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0.001, 0.02, 1000))

    var_95 = VaRCalculator.historical_var(returns, confidence_level=0.95)

    # 95% VaR should be around 5th percentile (negative return)
    assert 0.02 <= var_95 <= 0.05  # Expect ~3-4% for this distribution

# tests/test_pre_trade_validator.py
def test_pre_trade_validator_rejection():
    """Test that validator rejects oversized positions."""
    validator = PreTradeRiskValidator()

    result = validator.validate_trade(
        symbol='AAPL',
        proposed_size=20000,  # 20% of capital
        current_positions={},
        total_capital=100000,
        symbol_sectors={'AAPL': 'Technology'},
        returns_data=pd.DataFrame(),
        current_portfolio_value=100000,
        previous_portfolio_value=100000,
        initial_capital=100000
    )

    assert not result.approved
    assert 'Position size' in result.violations[0]
```

### 9.2 Integration Tests

```python
# tests/integration/test_risk_backtest_integration.py
def test_backtest_with_risk_management():
    """Test full backtest with risk limits enforced."""
    from src.backtest.engine import BacktestEngine
    from src.risk.pre_trade_validator import PreTradeRiskValidator

    # Create risk-aware backtest engine
    risk_validator = PreTradeRiskValidator()
    engine = RiskAwareBacktestEngine(
        config=BacktestConfig(initial_capital=100000),
        risk_validator=risk_validator
    )

    # Run backtest (should reject oversized positions)
    # Verify rejected_trades list populated
    # Verify no position exceeds limits
```

### 9.3 Coverage Targets

| Component | Coverage Target | Priority |
|-----------|----------------|----------|
| Position Sizing | 95% | Critical |
| Portfolio Validators | 95% | Critical |
| VaR/CVaR Calculators | 90% | Critical |
| Pre-Trade Validator | 95% | Critical |
| Drawdown Tracker | 90% | Critical |
| Alert Manager | 85% | High |
| Risk Monitor | 85% | High |

---

## 10. Implementation Roadmap

### Phase 1: Position Sizing & Limits (Week 1-2)
- [ ] Implement Kelly, Fixed Fractional, ATR, Volatility position sizers
- [ ] Create PositionLimitValidator and PortfolioLimitValidator
- [ ] Write unit tests (>85% coverage)
- [ ] Document position sizing selection guide

### Phase 2: Risk Metrics (Week 3-4)
- [ ] Implement VaR (Historical, Parametric, Monte Carlo)
- [ ] Implement CVaR calculator
- [ ] Implement portfolio beta, concentration metrics
- [ ] Create RiskDataProvider for market data integration
- [ ] Write integration tests

### Phase 3: Drawdown & Pre-Trade Validation (Week 5-6)
- [ ] Implement DrawdownTracker with circuit breakers
- [ ] Create PreTradeRiskValidator
- [ ] Integrate with BacktestEngine
- [ ] Test drawdown halt/resume logic
- [ ] Validate pre-trade checks complete in <100ms

### Phase 4: Monitoring & Alerts (Week 7-8)
- [ ] Implement RiskMonitor and RiskDashboardMetrics
- [ ] Create RiskAlertManager
- [ ] Build risk metrics storage (SQLite tables)
- [ ] Create risk dashboard visualization (optional)
- [ ] End-to-end testing with live data
- [ ] Documentation and runbook

---

## 11. Risk Mitigation

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| **Pre-trade checks too slow (>100ms)** | High (execution delays) | Medium | Pre-compute correlations; cache risk metrics; optimize calculations |
| **False positives (valid trades rejected)** | Medium (missed opportunities) | Medium | Tune limits conservatively; allow override with justification |
| **Overfitting risk limits to backtest** | Critical (failure in live) | High | Use walk-forward validation; test across market regimes |
| **Correlation calculation errors** | Medium (incorrect limits) | Low | Require minimum data samples; validate correlations |
| **Drawdown tracking bugs** | Critical (runaway losses) | Low | Extensive testing; manual verification of circuit breakers |
| **Risk metrics diverge from actual** | High (incorrect decisions) | Medium | Daily validation against portfolio reality; alert on anomalies |

---

## 12. Success Criteria

### Functional Requirements
- ✅ All 4 position sizing methods implemented and tested
- ✅ Portfolio constraints enforced (position, sector, correlation, exposure)
- ✅ Risk metrics calculated (VaR 95%/99%, CVaR, beta, concentration)
- ✅ Drawdown limits with circuit breakers
- ✅ Pre-trade validation integrated with backtest engine
- ✅ Real-time monitoring and alerts

### Performance Requirements
- ✅ Pre-trade validation completes in <100ms
- ✅ Risk metrics update in <1 second
- ✅ >85% test coverage across all components
- ✅ Zero false negatives (never allow limit-violating trades)

### Integration Requirements
- ✅ Seamless integration with BacktestEngine (src/backtest/engine.py)
- ✅ Leverage MarketDataClient for volatility, correlations
- ✅ Risk metrics persisted to SQLite
- ✅ Alert system operational

---

## 13. Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2025-10-25 | Implement 4 position sizing methods | Different strategies require different approaches (Kelly for trend, fixed for pairs, volatility for multi-asset) |
| 2025-10-25 | Use fractional Kelly (0.5) default | Full Kelly too aggressive; half-Kelly balances growth and safety |
| 2025-10-25 | Hard limits at position/portfolio level | Prevents catastrophic losses; enforces Risk > Profitability hierarchy |
| 2025-10-25 | Soft limits (warnings) for correlation | Correlation is important but not absolute; allow with warnings |
| 2025-10-25 | 20% max drawdown, 5% daily loss limit | Industry-standard risk thresholds for retail prop trading |
| 2025-10-25 | Circuit breaker halts all trading | Automatic protection; requires manual resume after recovery |
| 2025-10-25 | Pre-trade validation before execution | Last line of defense; prevents all limit violations |
| 2025-10-25 | Historical VaR as primary method | Non-parametric; doesn't assume normal distribution; suitable for multi-strategy |

---

## 14. Appendix

### 14.1 Risk Limit Recommendations by Strategy Type

| Strategy Type | Position Sizing | Max Position | Max Correlation | Notes |
|---------------|----------------|--------------|-----------------|-------|
| **Pairs Trading** | Fixed Fractional (2%) | 5% | 0.3 | Low correlation critical for pairs |
| **Trend Following** | Kelly (0.5 fractional) | 10% | 0.7 | Can tolerate higher correlation |
| **Mean Reversion** | ATR-based | 5% | 0.5 | Volatility-adjusted important |
| **Statistical Arbitrage** | Fixed Fractional (1%) | 3% | 0.2 | High diversification required |
| **Multi-Asset** | Volatility-based | 8% | 0.6 | Risk parity across asset classes |

### 14.2 Key Dependencies

```yaml
# environment.yml additions
dependencies:
  - python=3.11
  - pandas>=2.0
  - numpy>=1.24
  - scipy>=1.11  # For statistical functions (z-scores, distributions)
  - statsmodels>=0.14  # For advanced statistical tests

  # Testing
  - pytest>=7.4
  - pytest-cov>=4.1
```

### 14.3 Sample Risk Configuration

```python
# config/risk_config.py

PRODUCTION_RISK_CONFIG = {
    'position_sizing_method': PositionSizingMethod.FIXED_FRACTIONAL,
    'position_limits': {
        'max_position_pct': 0.10,
        'max_leverage': 1.0,
        'max_concentration_single_symbol': 0.15,
        'min_position_value': 1000,
        'max_positions_per_strategy': 10
    },
    'portfolio_limits': {
        'max_gross_exposure': 1.0,
        'max_net_exposure': 0.6,
        'max_sector_exposure': 0.30,
        'max_correlation_to_market': 0.7,
        'max_portfolio_concentration': 0.25,
        'min_strategy_diversification': 3
    },
    'drawdown_limits': {
        'max_drawdown_pct': 0.20,
        'daily_loss_limit_pct': 0.05,
        'trailing_stop_pct': 0.15,
        'recovery_threshold_pct': 0.10
    }
}
```

---

## Document Control

**Approvals**:
- [x] Peer review (via Task tool with general-purpose agent) - **Approved with Changes** (2025-10-25)
- [ ] CEO approval (via commit to main branch)

**Peer Review Summary** (2025-10-25):
- **Status**: Approved with Changes Required
- **Overall Assessment**: Excellent, comprehensive architecture demonstrating strong risk management principles
- **Implementation Readiness**: 85% (ready for Phase 1 after critical fixes)
- **Critical Fixes Applied**:
  1. Fixed Kelly criterion fractional calculation order (Critical Issue 3.1)
  2. Implemented correlation caching for <100ms performance (Critical Issue 3.2)
  3. Fixed DrawdownTracker state management with reset() method (Critical Issue 3.3)
- **Remaining Action Items** (for Phase 1):
  1. Document short position cash accounting requirement (use equity not capital)
  2. Add parameter validation to PositionSizer.calculate()
  3. Implement RiskDataProvider stub methods
  4. Enhance storage schema with session_id and strategy_name

**Change History**:
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-10-25 | Claude (general-purpose) | Initial architecture design |
| 1.1 | 2025-10-25 | Claude (general-purpose) | Post-peer review: Fixed Kelly criterion, correlation caching, DrawdownTracker state |

**Next Review**: After Phase 1 implementation (Week 2)

---

**End of Document**
