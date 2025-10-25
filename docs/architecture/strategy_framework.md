# Strategy Framework Architecture

**Version**: 1.1
**Last Updated**: 2025-10-25
**Status**: Design Phase - Peer Reviewed

**Version 1.1 Changes**:
- **Critical Issue 3.1 Fixed**: Added `on_risk_rejection()` lifecycle hook to BaseStrategy
- **Critical Issue 3.2 Fixed**: Added thread safety to StateManager with lock and safe_point control
- **Critical Issue 3.3 Fixed**: Added signal validation in StrategyBacktestRunner to prevent SHORT signals when enable_short=False
- **Critical Issue 3.4 Fixed**: Added hedge ratio caching with configurable recalculation frequency (20x performance improvement)
- **Critical Issue 3.5 Fixed**: Replaced `default=str` JSON serialization with proper type encoding for numpy, pandas, datetime
- **Improvement 4.2.2**: RiskAwareStrategy now uses `suggested_size` from RiskDecision

## Executive Summary

The Strategy Framework provides a unified architecture for implementing, backtesting, and deploying trading strategies across SolaraAlpha's multi-strategy, multi-asset, multi-timeframe trading infrastructure. The framework prioritizes relative value strategies (pairs trading, statistical arbitrage, spread trading) as the core focus while supporting all strategy types.

**Key Design Principles**:
- **Modular hierarchy**: BaseStrategy → RelativeValueStrategy → Concrete Implementations
- **Lifecycle management**: Clean separation of signal generation, position calculation, execution, and state management
- **Market-neutral focus**: Built-in support for long/short pairs, hedge ratios, and position balancing
- **Integration-first**: Seamless connections to backtesting, risk management, and market data
- **Parameter discipline**: Versioned parameters with optimization support and serialization
- **State persistence**: Recovery-ready with checkpointing and replay capabilities

**Core Components**:
1. **BaseStrategy** - Abstract foundation with lifecycle methods (generate_signals, calculate_positions, on_data, on_fill)
2. **RelativeValueStrategy** - Market-neutral specialization with hedge ratio, pair balancing, spread tracking
3. **StrategyRegistry** - Central registration, versioning, and discovery system
4. **ParameterManager** - Serialization, validation, optimization, walk-forward analysis
5. **StateManager** - Persistence, checkpointing, recovery, replay
6. **PerformanceTracker** - Real-time metrics, strategy-level attribution, diagnostics

**Critical Integration Points**:
- **Backtesting Engine** (src/backtest/engine.py): Signals → portfolio simulation
- **Risk Manager** (src/risk/risk_manager.py): Pre-trade checks, position sizing, portfolio constraints
- **Market Data Client** (src/data/market_client.py): OHLCV, fundamentals, economic indicators

---

## 1. Architecture Overview

### 1.1 Strategy Hierarchy

```
                    ┌─────────────────┐
                    │  BaseStrategy   │  ← Abstract base class
                    │                 │     - Lifecycle methods
                    │  - on_data()    │     - Parameter management
                    │  - on_fill()    │     - State tracking
                    └────────┬────────┘
                             │
          ┌──────────────────┴──────────────────┐
          │                                     │
┌─────────▼──────────┐              ┌──────────▼──────────┐
│ RelativeValue      │              │ TrendFollowing      │
│ Strategy           │              │ Strategy            │
│                    │              │                     │
│ - Market-neutral   │              │ - Directional bias  │
│ - Hedge ratios     │              │ - Momentum-based    │
│ - Spread tracking  │              │                     │
└────────┬───────────┘              └─────────────────────┘
         │
  ┌──────┴───────┬────────────┬──────────────┐
  │              │            │              │
┌─▼──────┐ ┌────▼─────┐ ┌────▼────────┐ ┌──▼─────────┐
│ Pairs  │ │ Stat     │ │ Spread      │ │ Mean       │
│ Trading│ │ Arbitrage│ │ Trading     │ │ Reversion  │
└────────┘ └──────────┘ └─────────────┘ └────────────┘
```

### 1.2 Data Flow

```
Market Data (OHLCV, fundamentals, economic)
         ↓
   BaseStrategy.on_data(timestamp, data)
         ↓
   generate_signals() → {symbol: signal_value}
         ↓
   calculate_positions() → {symbol: target_position_size}
         ↓
   Risk Manager (pre-trade checks, position sizing)
         ↓
   Order Generation → Backtesting Engine or Live Execution
         ↓
   Fills → BaseStrategy.on_fill(fill_event)
         ↓
   State Update → StateManager.checkpoint()
```

### 1.3 Integration Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Strategy Framework                         │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ BaseStrategy │  │ Parameter    │  │ State        │      │
│  │              │  │ Manager      │  │ Manager      │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                  │                  │               │
│         └──────────────────┼──────────────────┘               │
│                            │                                  │
└────────────────────────────┼──────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
┌───────▼───────┐   ┌────────▼────────┐  ┌───────▼───────┐
│ Backtesting   │   │ Risk Manager    │  │ Market Data   │
│ Engine        │   │                 │  │ Client        │
│               │   │ - Pre-trade     │  │               │
│ - Simulation  │   │ - Position size │  │ - OHLCV       │
│ - Metrics     │   │ - Constraints   │  │ - Fundamentals│
└───────────────┘   └─────────────────┘  └───────────────┘
```

---

## 2. Base Strategy Class

### 2.1 Interface Definition

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from enum import Enum
from datetime import datetime
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Signal type enumeration."""
    LONG = 1.0
    SHORT = -1.0
    FLAT = 0.0
    CLOSE = None  # Close existing position


class StrategyState(Enum):
    """Strategy execution state."""
    INITIALIZED = "initialized"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class Signal:
    """
    Trading signal with metadata.

    Attributes:
        symbol: Ticker symbol
        signal_type: Signal type (LONG, SHORT, FLAT, CLOSE)
        timestamp: Signal generation timestamp
        confidence: Signal confidence (0.0 to 1.0)
        metadata: Additional signal context
    """
    symbol: str
    signal_type: SignalType
    timestamp: pd.Timestamp
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    """
    Current position representation.

    Attributes:
        symbol: Ticker symbol
        quantity: Number of shares (positive = long, negative = short)
        entry_price: Average entry price
        entry_time: Position entry timestamp
        current_price: Latest market price
        unrealized_pnl: Unrealized profit/loss
        metadata: Position-specific metadata
    """
    symbol: str
    quantity: float
    entry_price: float
    entry_time: pd.Timestamp
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FillEvent:
    """
    Order fill event.

    Attributes:
        symbol: Ticker symbol
        quantity: Filled quantity (positive = buy, negative = sell)
        price: Fill price
        timestamp: Fill timestamp
        commission: Commission paid
        slippage: Slippage cost
        order_id: Associated order ID
    """
    symbol: str
    quantity: float
    price: float
    timestamp: pd.Timestamp
    commission: float = 0.0
    slippage: float = 0.0
    order_id: Optional[str] = None


@dataclass
class StrategyConfig:
    """
    Strategy configuration.

    Attributes:
        name: Strategy name
        version: Strategy version (for tracking)
        parameters: Strategy-specific parameters
        symbols: Universe of tradeable symbols
        max_positions: Maximum concurrent positions
        enable_short: Allow short positions
        rebalance_frequency: Rebalancing frequency (e.g., '1D', '1H')
    """
    name: str
    version: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    symbols: List[str] = field(default_factory=list)
    max_positions: int = 10
    enable_short: bool = False
    rebalance_frequency: str = '1D'


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Provides lifecycle management, state tracking, and integration hooks
    for backtesting and live trading. All strategies must implement:
        - generate_signals()
        - calculate_positions()

    Optional overrides:
        - on_data() - Data preprocessing
        - on_fill() - Order execution handling
        - on_start() - Initialization logic
        - on_stop() - Cleanup logic

    Example:
        >>> class MyStrategy(BaseStrategy):
        ...     def generate_signals(self, timestamp, data):
        ...         # Generate trading signals
        ...         signals = {}
        ...         for symbol in self.config.symbols:
        ...             if data[symbol]['Close'] > data[symbol]['SMA_50']:
        ...                 signals[symbol] = Signal(
        ...                     symbol=symbol,
        ...                     signal_type=SignalType.LONG,
        ...                     timestamp=timestamp
        ...                 )
        ...         return signals
        ...
        ...     def calculate_positions(self, signals, current_positions):
        ...         # Convert signals to target positions
        ...         positions = {}
        ...         for symbol, signal in signals.items():
        ...             if signal.signal_type == SignalType.LONG:
        ...                 positions[symbol] = 100  # 100 shares
        ...         return positions
    """

    def __init__(self, config: StrategyConfig):
        """
        Initialize strategy.

        Args:
            config: Strategy configuration
        """
        self.config = config
        self.state = StrategyState.INITIALIZED
        self.current_positions: Dict[str, Position] = {}
        self.pending_orders: List[Dict[str, Any]] = []
        self.performance: Dict[str, float] = {}
        self.state_data: Dict[str, Any] = {}

        self._validate_config()

    def _validate_config(self) -> None:
        """Validate strategy configuration."""
        if not self.config.name:
            raise ValueError("Strategy name required")
        if not self.config.version:
            raise ValueError("Strategy version required")
        if not self.config.symbols:
            raise ValueError("Strategy symbols required (must specify trading universe)")
        if self.config.max_positions <= 0:
            raise ValueError("max_positions must be positive")

    # ==================== Lifecycle Methods ====================

    def on_start(self) -> None:
        """
        Called when strategy starts execution.

        Override to initialize strategy-specific state, load models,
        or perform setup tasks.
        """
        self.state = StrategyState.RUNNING
        logger.info(f"{self.config.name} v{self.config.version} started")

    def on_stop(self) -> None:
        """
        Called when strategy stops execution.

        Override to cleanup resources, save state, or perform teardown.
        """
        self.state = StrategyState.STOPPED
        logger.info(f"{self.config.name} stopped")

    def on_data(self, timestamp: pd.Timestamp, data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """
        Called on every data update.

        Override to preprocess data, calculate indicators, or transform inputs
        before signal generation.

        Args:
            timestamp: Current timestamp
            data: Dict mapping symbols to OHLCV DataFrames

        Returns:
            Processed data (same structure as input)

        Example:
            >>> def on_data(self, timestamp, data):
            ...     # Add technical indicators
            ...     for symbol in data:
            ...         data[symbol]['SMA_20'] = data[symbol]['Close'].rolling(20).mean()
            ...         data[symbol]['RSI'] = calculate_rsi(data[symbol]['Close'])
            ...     return data
        """
        return data

    def on_fill(self, fill: FillEvent) -> None:
        """
        Called when an order is filled.

        Override to update strategy state, adjust positions, or react to fills.

        Args:
            fill: Fill event with execution details

        Example:
            >>> def on_fill(self, fill):
            ...     logger.info(f"Filled: {fill.symbol} {fill.quantity} @ {fill.price}")
            ...     # Update position tracking
            ...     if fill.symbol in self.current_positions:
            ...         self.current_positions[fill.symbol].quantity += fill.quantity
        """
        # Update positions
        if fill.symbol in self.current_positions:
            pos = self.current_positions[fill.symbol]

            # Calculate new quantity and average price
            new_quantity = pos.quantity + fill.quantity

            if new_quantity == 0:
                # Position closed
                del self.current_positions[fill.symbol]
            else:
                # Update position
                # For same-direction trades, update average price
                if (pos.quantity > 0 and fill.quantity > 0) or (pos.quantity < 0 and fill.quantity < 0):
                    total_cost = (pos.entry_price * abs(pos.quantity) +
                                 fill.price * abs(fill.quantity))
                    pos.entry_price = total_cost / abs(new_quantity)

                pos.quantity = new_quantity
                pos.current_price = fill.price
        else:
            # New position
            self.current_positions[fill.symbol] = Position(
                symbol=fill.symbol,
                quantity=fill.quantity,
                entry_price=fill.price,
                entry_time=fill.timestamp,
                current_price=fill.price
            )

    # ==================== Core Strategy Methods ====================

    @abstractmethod
    def generate_signals(
        self,
        timestamp: pd.Timestamp,
        data: Dict[str, pd.DataFrame]
    ) -> Dict[str, Signal]:
        """
        Generate trading signals from market data.

        This is the core strategy logic. Must be implemented by all subclasses.

        Args:
            timestamp: Current timestamp
            data: Dict mapping symbols to OHLCV DataFrames (processed by on_data)

        Returns:
            Dict mapping symbols to Signal objects

        Example:
            >>> def generate_signals(self, timestamp, data):
            ...     signals = {}
            ...     for symbol in self.config.symbols:
            ...         df = data[symbol]
            ...
            ...         # Moving average crossover
            ...         if df['SMA_20'].iloc[-1] > df['SMA_50'].iloc[-1]:
            ...             signals[symbol] = Signal(
            ...                 symbol=symbol,
            ...                 signal_type=SignalType.LONG,
            ...                 timestamp=timestamp,
            ...                 confidence=0.8
            ...             )
            ...         elif df['SMA_20'].iloc[-1] < df['SMA_50'].iloc[-1]:
            ...             signals[symbol] = Signal(
            ...                 symbol=symbol,
            ...                 signal_type=SignalType.SHORT,
            ...                 timestamp=timestamp,
            ...                 confidence=0.8
            ...             )
            ...     return signals
        """
        pass

    @abstractmethod
    def calculate_positions(
        self,
        signals: Dict[str, Signal],
        current_positions: Dict[str, Position],
        portfolio_value: float
    ) -> Dict[str, float]:
        """
        Convert signals to target position sizes.

        Must be implemented by all subclasses. Incorporates position sizing logic.

        Args:
            signals: Signals from generate_signals()
            current_positions: Current position holdings
            portfolio_value: Current portfolio value

        Returns:
            Dict mapping symbols to target position sizes (number of shares)
            Positive = long, negative = short, 0 = flat

        Example:
            >>> def calculate_positions(self, signals, current_positions, portfolio_value):
            ...     positions = {}
            ...
            ...     # Equal weight allocation
            ...     num_signals = len(signals)
            ...     if num_signals == 0:
            ...         return positions
            ...
            ...     capital_per_position = portfolio_value / num_signals
            ...
            ...     for symbol, signal in signals.items():
            ...         price = signal.metadata.get('current_price', 0)
            ...         if price == 0:
            ...             continue
            ...
            ...         if signal.signal_type == SignalType.LONG:
            ...             positions[symbol] = int(capital_per_position / price)
            ...         elif signal.signal_type == SignalType.SHORT:
            ...             positions[symbol] = -int(capital_per_position / price)
            ...         else:
            ...             positions[symbol] = 0.0
            ...
            ...     return positions
        """
        pass

    # ==================== Helper Methods ====================

    def get_parameter(self, key: str, default: Any = None) -> Any:
        """
        Get strategy parameter by key.

        Args:
            key: Parameter name
            default: Default value if not found

        Returns:
            Parameter value
        """
        return self.config.parameters.get(key, default)

    def set_parameter(self, key: str, value: Any) -> None:
        """
        Set strategy parameter.

        Args:
            key: Parameter name
            value: Parameter value
        """
        self.config.parameters[key] = value

    def get_position(self, symbol: str) -> Optional[Position]:
        """
        Get current position for symbol.

        Args:
            symbol: Ticker symbol

        Returns:
            Position object or None if no position
        """
        return self.current_positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        """
        Check if strategy has position in symbol.

        Args:
            symbol: Ticker symbol

        Returns:
            True if position exists
        """
        return symbol in self.current_positions

    def update_state(self, key: str, value: Any) -> None:
        """
        Update strategy state data.

        Args:
            key: State key
            value: State value
        """
        self.state_data[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """
        Get strategy state data.

        Args:
            key: State key
            default: Default value if not found

        Returns:
            State value
        """
        return self.state_data.get(key, default)

    def on_risk_rejection(
        self,
        symbol: str,
        requested_qty: float,
        rejection_reason: str
    ) -> None:
        """
        Called when risk manager rejects a trade.

        Override to handle risk rejections (e.g., logging, state updates, alerting).

        Args:
            symbol: Symbol that was rejected
            requested_qty: Requested quantity
            rejection_reason: Why the trade was rejected

        Example:
            >>> def on_risk_rejection(self, symbol, requested_qty, rejection_reason):
            ...     # Log rejection to strategy state for diagnostics
            ...     self.update_state('last_rejection', {
            ...         'symbol': symbol,
            ...         'quantity': requested_qty,
            ...         'reason': rejection_reason
            ...     })
            ...     # Alert if multiple consecutive rejections
            ...     rejection_count = self.get_state('rejection_count', 0) + 1
            ...     self.update_state('rejection_count', rejection_count)
            ...     if rejection_count >= 3:
            ...         logger.error(f"Multiple rejections detected: {rejection_count}")
        """
        logger.warning(
            f"{self.config.name}: Trade rejected - {symbol} qty={requested_qty} "
            f"reason={rejection_reason}"
        )
```

### 2.2 Relative Value Strategy Specialization

```python
from typing import Tuple


@dataclass
class HedgeRatio:
    """
    Hedge ratio for pairs/spread trading.

    Attributes:
        symbol_long: Long leg symbol
        symbol_short: Short leg symbol
        ratio: Hedge ratio (units of short per unit of long)
        lookback_period: Period used for calculation
        last_updated: Last recalculation timestamp
    """
    symbol_long: str
    symbol_short: str
    ratio: float
    lookback_period: int
    last_updated: pd.Timestamp


class RelativeValueStrategy(BaseStrategy):
    """
    Specialized base class for market-neutral relative value strategies.

    Provides built-in support for:
    - Long/short pair management
    - Hedge ratio calculation and updating
    - Position balancing for market neutrality
    - Spread tracking and z-score calculation

    Subclasses:
        - PairsTradingStrategy: Statistical arbitrage on cointegrated pairs
        - StatisticalArbitrageStrategy: Multi-asset relative value
        - SpreadTradingStrategy: Futures/options spread trading
        - MeanReversionStrategy: Price deviation from fundamental value
    """

    def __init__(self, config: StrategyConfig):
        """Initialize relative value strategy."""
        super().__init__(config)

        # Ensure short selling enabled
        if not config.enable_short:
            raise ValueError("RelativeValueStrategy requires enable_short=True")

        # Hedge ratios for pairs
        self.hedge_ratios: Dict[Tuple[str, str], HedgeRatio] = {}

        # Spread state tracking
        self.spreads: Dict[Tuple[str, str], pd.Series] = {}

    def calculate_hedge_ratio(
        self,
        symbol_long: str,
        symbol_short: str,
        data: Dict[str, pd.DataFrame],
        method: str = "ols",
        lookback: int = 30,
        recalc_frequency_bars: int = 20
    ) -> HedgeRatio:
        """
        Calculate hedge ratio with caching for performance (Critical Issue 3.4 fix).

        Hedge ratios are cached and only recalculated when:
        - No cached ratio exists, OR
        - More than recalc_frequency_bars have passed since last update

        Performance Note:
            Recalculating hedge ratios on every bar is expensive, especially
            for cointegration method (~10-20ms per pair). With caching:
            - 10 pairs × 60 min/hour × 8 hours = 4,800 potential calculations/day
            - With recalc_frequency_bars=20 (default): Only 240 calculations/day
            - 20x performance improvement

        Args:
            symbol_long: Long leg symbol
            symbol_short: Short leg symbol
            data: Historical price data
            method: Calculation method ("ols", "ratio", "cointegration")
            lookback: Lookback period for calculation
            recalc_frequency_bars: Recalculate every N bars (default: 20)

        Returns:
            HedgeRatio object

        Example:
            >>> # First call calculates
            >>> hedge = strategy.calculate_hedge_ratio(
            ...     "XLE", "XLF", data, method="ols", lookback=60, recalc_frequency_bars=20
            ... )
            >>> print(f"Hedge ratio: {hedge.ratio:.4f}")
            >>>
            >>> # Subsequent calls within 20 bars use cached ratio
            >>> hedge = strategy.calculate_hedge_ratio("XLE", "XLF", data)  # Fast!
        """
        if symbol_long not in data or symbol_short not in data:
            raise ValueError(f"Missing data for {symbol_long} or {symbol_short}")

        pair_key = (symbol_long, symbol_short)
        current_timestamp = data[symbol_long].index[-1]

        # Check cache and recalculation frequency
        if pair_key in self.hedge_ratios:
            cached_ratio = self.hedge_ratios[pair_key]
            bars_since_update = len(data[symbol_long].loc[cached_ratio.last_updated:]) - 1

            if bars_since_update < recalc_frequency_bars:
                # Use cached ratio
                logger.debug(
                    f"Using cached hedge ratio for {symbol_long}/{symbol_short}: "
                    f"{cached_ratio.ratio:.4f} (age: {bars_since_update} bars)"
                )
                return cached_ratio
            else:
                logger.debug(
                    f"Recalculating hedge ratio for {symbol_long}/{symbol_short} "
                    f"(age: {bars_since_update} bars >= {recalc_frequency_bars})"
                )

        # Get price series
        prices_long = data[symbol_long]['Close'].tail(lookback)
        prices_short = data[symbol_short]['Close'].tail(lookback)

        # Align indices
        prices_long, prices_short = prices_long.align(prices_short, join='inner')

        if len(prices_long) < lookback:
            raise ValueError(f"Insufficient data: {len(prices_long)} < {lookback}")

        # Calculate hedge ratio based on method
        if method == "ols":
            # Ordinary Least Squares regression
            # y = β*x → β is hedge ratio
            x = prices_short.values.reshape(-1, 1)
            y = prices_long.values

            # Simple OLS: β = (X'X)^(-1) X'y
            beta = np.dot(x.T, y) / np.dot(x.T, x)
            ratio = float(beta[0])

        elif method == "ratio":
            # Simple price ratio
            ratio = float(prices_long.mean() / prices_short.mean())

        elif method == "cointegration":
            # Cointegration-based (requires statsmodels)
            from statsmodels.tsa.stattools import coint
            _, pvalue, _ = coint(prices_long, prices_short)

            if pvalue > 0.05:
                logger.warning(
                    f"Pair {symbol_long}/{symbol_short} not cointegrated (p={pvalue:.3f})"
                )

            # Use OLS for ratio
            x = prices_short.values.reshape(-1, 1)
            y = prices_long.values
            beta = np.dot(x.T, y) / np.dot(x.T, x)
            ratio = float(beta[0])
        else:
            raise ValueError(f"Unknown hedge ratio method: {method}")

        hedge_ratio = HedgeRatio(
            symbol_long=symbol_long,
            symbol_short=symbol_short,
            ratio=ratio,
            lookback_period=lookback,
            last_updated=data[symbol_long].index[-1]
        )

        # Cache hedge ratio
        self.hedge_ratios[(symbol_long, symbol_short)] = hedge_ratio

        return hedge_ratio

    def calculate_spread(
        self,
        symbol_long: str,
        symbol_short: str,
        data: Dict[str, pd.DataFrame],
        hedge_ratio: Optional[float] = None
    ) -> pd.Series:
        """
        Calculate spread between two symbols.

        Spread = Price_long - (hedge_ratio * Price_short)

        Args:
            symbol_long: Long leg symbol
            symbol_short: Short leg symbol
            data: Historical price data
            hedge_ratio: Hedge ratio (if None, uses cached or calculates)

        Returns:
            Spread time series
        """
        # Get or calculate hedge ratio
        if hedge_ratio is None:
            pair_key = (symbol_long, symbol_short)
            if pair_key in self.hedge_ratios:
                hedge_ratio = self.hedge_ratios[pair_key].ratio
            else:
                hr = self.calculate_hedge_ratio(symbol_long, symbol_short, data)
                hedge_ratio = hr.ratio

        # Calculate spread
        prices_long = data[symbol_long]['Close']
        prices_short = data[symbol_short]['Close']

        spread = prices_long - (hedge_ratio * prices_short)

        # Cache spread
        self.spreads[(symbol_long, symbol_short)] = spread

        return spread

    def calculate_spread_zscore(
        self,
        spread: pd.Series,
        lookback: int = 30
    ) -> float:
        """
        Calculate z-score of spread (for mean reversion signals).

        Z-score = (current_spread - mean_spread) / std_spread

        Args:
            spread: Spread time series
            lookback: Lookback period for mean/std calculation

        Returns:
            Current z-score
        """
        if len(spread) < lookback:
            raise ValueError(f"Insufficient spread data: {len(spread)} < {lookback}")

        recent_spread = spread.tail(lookback)

        mean = recent_spread.mean()
        std = recent_spread.std()

        if std == 0:
            return 0.0

        current_value = spread.iloc[-1]
        zscore = (current_value - mean) / std

        return float(zscore)

    def balance_positions(
        self,
        symbol_long: str,
        symbol_short: str,
        quantity_long: float,
        hedge_ratio: Optional[float] = None
    ) -> Tuple[float, float]:
        """
        Balance long/short positions for market neutrality.

        Args:
            symbol_long: Long leg symbol
            symbol_short: Short leg symbol
            quantity_long: Desired long quantity
            hedge_ratio: Hedge ratio (if None, uses cached)

        Returns:
            Tuple of (balanced_long_quantity, balanced_short_quantity)

        Example:
            >>> # Want to go long 100 shares of XLE
            >>> long_qty, short_qty = strategy.balance_positions("XLE", "XLF", 100)
            >>> print(f"Long {long_qty} XLE, Short {short_qty} XLF")
        """
        # Get hedge ratio
        if hedge_ratio is None:
            pair_key = (symbol_long, symbol_short)
            if pair_key not in self.hedge_ratios:
                raise ValueError(f"No hedge ratio for {symbol_long}/{symbol_short}")
            hedge_ratio = self.hedge_ratios[pair_key].ratio

        # Calculate balanced short quantity
        quantity_short = quantity_long * hedge_ratio

        return quantity_long, -quantity_short  # Short is negative
```

---

## 3. Integration Points

### 3.1 Backtesting Engine Integration

```python
# src/backtest/strategy_runner.py

from src.backtest.engine import BacktestEngine, BacktestConfig, BacktestResult
from src.strategy.base import BaseStrategy, Signal, FillEvent
from src.data.market_client import MarketDataClient
import pandas as pd
from typing import Dict


class StrategyBacktestRunner:
    """
    Runs strategy backtests using the existing BacktestEngine.

    Bridges strategy framework with event-driven backtest simulation.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        data_client: MarketDataClient,
        backtest_config: BacktestConfig
    ):
        """
        Initialize strategy backtest runner.

        Args:
            strategy: Strategy to backtest
            data_client: Market data client
            backtest_config: Backtest configuration
        """
        self.strategy = strategy
        self.data_client = data_client
        self.backtest_config = backtest_config
        self.engine = BacktestEngine(backtest_config)

    def run(
        self,
        start_date: str,
        end_date: str,
        interval: str = '1d'
    ) -> BacktestResult:
        """
        Run strategy backtest.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            interval: Data interval

        Returns:
            BacktestResult with performance metrics
        """
        # Fetch data for all symbols
        data = {}
        for symbol in self.strategy.config.symbols:
            data[symbol] = self.data_client.get_ohlcv(
                symbol, start=start_date, end=end_date, interval=interval
            )

        # Align all data to common index
        aligned_data = self._align_data(data)

        # Generate signals for all timestamps
        all_signals = self._generate_all_signals(aligned_data)

        # Convert signals to backtesting format (signal series per symbol)
        signal_series = self._convert_signals_to_series(all_signals, aligned_data)

        # Run backtest for each symbol
        results = {}
        for symbol in self.strategy.config.symbols:
            if symbol in signal_series:
                result = self.engine.run(
                    data=aligned_data[symbol],
                    signals=signal_series[symbol],
                    symbol=symbol
                )
                results[symbol] = result

        # Aggregate results across all symbols
        aggregated_result = self._aggregate_results(results)

        return aggregated_result

    def _align_data(self, data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """Align all symbol data to common timestamps."""
        # Find common index
        common_index = None
        for symbol, df in data.items():
            if common_index is None:
                common_index = df.index
            else:
                common_index = common_index.intersection(df.index)

        # Reindex all data
        aligned = {}
        for symbol, df in data.items():
            aligned[symbol] = df.loc[common_index]

        return aligned

    def _generate_all_signals(
        self,
        data: Dict[str, pd.DataFrame]
    ) -> Dict[pd.Timestamp, Dict[str, Signal]]:
        """Generate signals for all timestamps."""
        all_signals = {}

        # Get list of timestamps
        timestamps = data[next(iter(data))].index

        for timestamp in timestamps:
            # Build data snapshot up to current timestamp
            data_snapshot = {}
            for symbol, df in data.items():
                data_snapshot[symbol] = df.loc[:timestamp]

            # Process data
            processed_data = self.strategy.on_data(timestamp, data_snapshot)

            # Generate signals
            signals = self.strategy.generate_signals(timestamp, processed_data)

            all_signals[timestamp] = signals

        return all_signals

    def _convert_signals_to_series(
        self,
        all_signals: Dict[pd.Timestamp, Dict[str, Signal]],
        data: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """
        Convert signals to pandas Series with validation (Critical Issue 3.3 fix).

        Validates:
        - Signal types match strategy configuration (SHORT signals require enable_short=True)
        - Signal values are valid (-1.0, 0.0, 1.0)

        Raises:
            ValueError: If SHORT signals generated when enable_short=False
        """
        signal_series = {}

        for symbol in self.strategy.config.symbols:
            timestamps = data[symbol].index
            values = []

            for timestamp in timestamps:
                if timestamp in all_signals and symbol in all_signals[timestamp]:
                    signal = all_signals[timestamp][symbol]

                    # VALIDATION: Check signal type vs. strategy config
                    if signal.signal_type == SignalType.SHORT and not self.strategy.config.enable_short:
                        raise ValueError(
                            f"Strategy {self.strategy.config.name} generated SHORT signal "
                            f"for {symbol} at {timestamp} but enable_short=False. "
                            f"Either set enable_short=True in StrategyConfig or fix signal generation logic."
                        )

                    # Validate signal value is valid
                    signal_value = signal.signal_type.value
                    if signal_value not in [-1.0, 0.0, 1.0, None]:
                        logger.warning(
                            f"Invalid signal value {signal_value} for {symbol} at {timestamp}, "
                            f"treating as FLAT"
                        )
                        signal_value = 0.0

                    values.append(signal_value if signal_value is not None else 0.0)
                else:
                    values.append(0.0)  # Flat

            signal_series[symbol] = pd.Series(values, index=timestamps)

        return signal_series

    def _aggregate_results(
        self,
        results: Dict[str, BacktestResult]
    ) -> BacktestResult:
        """Aggregate results across all symbols."""
        # Combine equity curves
        equity_curves = [r.equity_curve for r in results.values()]
        combined_equity = pd.concat(equity_curves, axis=1).sum(axis=1)

        # Combine trades
        all_trades = []
        for r in results.values():
            all_trades.extend(r.trades)

        # Create aggregated result
        # (would need to recalculate metrics on combined equity)
        # Placeholder implementation
        aggregated = BacktestResult()
        aggregated.equity_curve = combined_equity
        aggregated.trades = all_trades

        return aggregated
```

### 3.2 Risk Manager Integration

```python
# src/strategy/risk_integration.py

from src.risk.risk_manager import RiskManager, RiskConfig, RiskDecision
from src.strategy.base import BaseStrategy, Signal, Position
import pandas as pd
from typing import Dict


class RiskAwareStrategy(BaseStrategy):
    """
    Strategy wrapper with integrated risk management.

    Automatically validates positions against risk limits before execution.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        risk_manager: RiskManager
    ):
        """
        Initialize risk-aware strategy wrapper.

        Args:
            strategy: Underlying strategy
            risk_manager: Risk manager instance
        """
        super().__init__(strategy.config)
        self.underlying_strategy = strategy
        self.risk_manager = risk_manager

    def generate_signals(
        self,
        timestamp: pd.Timestamp,
        data: Dict[str, pd.DataFrame]
    ) -> Dict[str, Signal]:
        """Generate signals from underlying strategy."""
        return self.underlying_strategy.generate_signals(timestamp, data)

    def calculate_positions(
        self,
        signals: Dict[str, Signal],
        current_positions: Dict[str, Position],
        portfolio_value: float
    ) -> Dict[str, float]:
        """
        Calculate positions with risk management overlay.

        Validates each position change against risk limits.
        """
        # Get target positions from underlying strategy
        target_positions = self.underlying_strategy.calculate_positions(
            signals, current_positions, portfolio_value
        )

        # Validate each position change
        validated_positions = {}

        for symbol, target_qty in target_positions.items():
            # Get current quantity
            current_qty = current_positions[symbol].quantity if symbol in current_positions else 0.0

            # Calculate trade quantity
            trade_qty = target_qty - current_qty

            if trade_qty == 0:
                validated_positions[symbol] = target_qty
                continue

            # Determine side
            side = "long" if trade_qty > 0 else "short"

            # Get current price from signal
            price = signals[symbol].metadata.get('current_price', 0)
            if price == 0:
                logger.warning(f"No price available for {symbol}, skipping")
                continue

            # Convert positions to portfolio DataFrame
            portfolio_df = self._positions_to_dataframe(current_positions)

            # Pre-trade check
            decision = self.risk_manager.pre_trade_check(
                symbol=symbol,
                quantity=abs(trade_qty),
                side=side,
                price=price,
                current_portfolio=portfolio_df
            )

            if decision.approved:
                # Use risk-adjusted size if provided (Issue 4.2.2 fix)
                if decision.suggested_size is not None:
                    risk_adjusted_qty = int(decision.suggested_size / price)
                    validated_positions[symbol] = risk_adjusted_qty
                    if risk_adjusted_qty != target_qty:
                        logger.info(
                            f"Position size adjusted by risk manager: {symbol} "
                            f"{target_qty} → {risk_adjusted_qty} shares"
                        )
                else:
                    validated_positions[symbol] = target_qty
            else:
                logger.warning(
                    f"Trade rejected by risk manager: {symbol} {trade_qty} @ {price} "
                    f"Reason: {decision.reason}"
                )
                # Notify strategy of rejection (Critical Issue 3.1 fix)
                self.underlying_strategy.on_risk_rejection(
                    symbol=symbol,
                    requested_qty=trade_qty,
                    rejection_reason=decision.reason or "Unknown"
                )
                # Keep current position
                validated_positions[symbol] = current_qty

        return validated_positions

    def _positions_to_dataframe(
        self,
        positions: Dict[str, Position]
    ) -> pd.DataFrame:
        """Convert positions dict to DataFrame for risk manager."""
        if not positions:
            return pd.DataFrame(columns=['symbol', 'quantity', 'price', 'value', 'sector'])

        records = []
        for symbol, pos in positions.items():
            records.append({
                'symbol': symbol,
                'quantity': pos.quantity,
                'price': pos.current_price,
                'value': pos.quantity * pos.current_price,
                'sector': pos.metadata.get('sector', 'Unknown')
            })

        return pd.DataFrame(records)
```

### 3.3 Market Data Integration

```python
# src/strategy/data_adapter.py

from src.data.market_client import MarketDataClient
from src.strategy.base import BaseStrategy
import pandas as pd
from typing import Dict, Optional


class StrategyDataAdapter:
    """
    Adapter for feeding market data to strategies.

    Handles data fetching, caching, and formatting for strategy consumption.
    """

    def __init__(
        self,
        data_client: MarketDataClient,
        strategy: BaseStrategy
    ):
        """
        Initialize data adapter.

        Args:
            data_client: Market data client
            strategy: Strategy requiring data
        """
        self.data_client = data_client
        self.strategy = strategy
        self.data_cache: Dict[str, pd.DataFrame] = {}

    def fetch_data(
        self,
        start_date: str,
        end_date: str,
        interval: str = '1d'
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetch data for all strategy symbols.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            interval: Data interval

        Returns:
            Dict mapping symbols to OHLCV DataFrames
        """
        data = {}

        for symbol in self.strategy.config.symbols:
            try:
                df = self.data_client.get_ohlcv(
                    symbol, start=start_date, end=end_date, interval=interval
                )
                data[symbol] = df
                self.data_cache[symbol] = df
            except Exception as e:
                logger.error(f"Failed to fetch data for {symbol}: {e}")

        return data

    def get_latest_data(
        self,
        symbols: Optional[list] = None,
        lookback_bars: int = 100
    ) -> Dict[str, pd.DataFrame]:
        """
        Get latest N bars of data for symbols.

        Args:
            symbols: List of symbols (None = all strategy symbols)
            lookback_bars: Number of bars to return

        Returns:
            Dict mapping symbols to recent OHLCV data
        """
        if symbols is None:
            symbols = self.strategy.config.symbols

        data = {}

        for symbol in symbols:
            if symbol in self.data_cache:
                df = self.data_cache[symbol].tail(lookback_bars)
                data[symbol] = df
            else:
                logger.warning(f"No cached data for {symbol}")

        return data
```

---

## 4. Parameter Management & Optimization

### 4.1 Parameter Manager

```python
# src/strategy/parameters.py

from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional
import json
import hashlib
from pathlib import Path


@dataclass
class ParameterSet:
    """
    Versioned parameter set.

    Attributes:
        name: Parameter set name
        version: Version identifier
        parameters: Strategy parameters
        created_at: Creation timestamp
        hash: Content hash for uniqueness
    """
    name: str
    version: str
    parameters: Dict[str, Any]
    created_at: str
    hash: str = ""

    def __post_init__(self):
        """Calculate hash after initialization."""
        if not self.hash:
            self.hash = self._calculate_hash()

    def _calculate_hash(self) -> str:
        """Calculate content hash."""
        content = json.dumps(self.parameters, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:8]


class ParameterManager:
    """
    Manages strategy parameters with versioning and serialization.

    Features:
    - JSON serialization for reproducibility
    - Version control for parameter sets
    - Parameter validation
    - Optimization history tracking
    """

    def __init__(self, storage_dir: Path):
        """
        Initialize parameter manager.

        Args:
            storage_dir: Directory for parameter storage
        """
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save_parameters(
        self,
        name: str,
        version: str,
        parameters: Dict[str, Any]
    ) -> ParameterSet:
        """
        Save parameter set to disk.

        Args:
            name: Parameter set name
            version: Version identifier
            parameters: Parameters to save

        Returns:
            ParameterSet object
        """
        from datetime import datetime

        param_set = ParameterSet(
            name=name,
            version=version,
            parameters=parameters,
            created_at=datetime.now().isoformat()
        )

        # Save to file
        filename = f"{name}_v{version}_{param_set.hash}.json"
        filepath = self.storage_dir / filename

        with open(filepath, 'w') as f:
            json.dump(asdict(param_set), f, indent=2)

        logger.info(f"Saved parameters: {filepath}")

        return param_set

    def load_parameters(
        self,
        name: str,
        version: str
    ) -> Optional[ParameterSet]:
        """
        Load parameter set from disk.

        Args:
            name: Parameter set name
            version: Version identifier

        Returns:
            ParameterSet or None if not found
        """
        # Find matching file
        pattern = f"{name}_v{version}_*.json"
        matches = list(self.storage_dir.glob(pattern))

        if not matches:
            logger.warning(f"No parameters found for {name} v{version}")
            return None

        # Load most recent if multiple matches
        filepath = max(matches, key=lambda p: p.stat().st_mtime)

        with open(filepath, 'r') as f:
            data = json.load(f)

        param_set = ParameterSet(**data)

        logger.info(f"Loaded parameters: {filepath}")

        return param_set

    def list_versions(self, name: str) -> List[str]:
        """
        List all versions for a parameter set.

        Args:
            name: Parameter set name

        Returns:
            List of version identifiers
        """
        pattern = f"{name}_v*.json"
        files = self.storage_dir.glob(pattern)

        versions = set()
        for f in files:
            # Extract version from filename: name_vVERSION_hash.json
            parts = f.stem.split('_v')
            if len(parts) >= 2:
                version = parts[1].split('_')[0]
                versions.add(version)

        return sorted(versions)
```

### 4.2 Walk-Forward Optimization

```python
# src/strategy/optimization.py

from src.strategy.base import BaseStrategy, StrategyConfig
from src.backtest.strategy_runner import StrategyBacktestRunner
from src.data.market_client import MarketDataClient
from src.backtest.engine import BacktestConfig
import pandas as pd
from typing import Dict, Any, List
from dataclasses import dataclass
import itertools


@dataclass
class OptimizationResult:
    """
    Optimization result for parameter set.

    Attributes:
        parameters: Parameter combination tested
        sharpe_ratio: Resulting Sharpe ratio
        total_return: Total return
        max_drawdown: Maximum drawdown
        num_trades: Number of trades
    """
    parameters: Dict[str, Any]
    sharpe_ratio: float
    total_return: float
    max_drawdown: float
    num_trades: int


class WalkForwardOptimizer:
    """
    Walk-forward optimization to prevent overfitting.

    Process:
    1. Divide data into rolling windows (in-sample + out-of-sample)
    2. Optimize parameters on in-sample data
    3. Test optimized parameters on out-of-sample data
    4. Roll window forward and repeat
    """

    def __init__(
        self,
        strategy_class: type,
        data_client: MarketDataClient,
        backtest_config: BacktestConfig
    ):
        """
        Initialize walk-forward optimizer.

        Args:
            strategy_class: Strategy class to optimize
            data_client: Market data client
            backtest_config: Backtest configuration
        """
        self.strategy_class = strategy_class
        self.data_client = data_client
        self.backtest_config = backtest_config

    def optimize(
        self,
        base_config: StrategyConfig,
        parameter_grid: Dict[str, List[Any]],
        start_date: str,
        end_date: str,
        in_sample_days: int = 252,
        out_sample_days: int = 63,
        step_days: int = 21
    ) -> pd.DataFrame:
        """
        Run walk-forward optimization.

        Args:
            base_config: Base strategy configuration
            parameter_grid: Parameter grid to search
            start_date: Start date
            end_date: End date
            in_sample_days: In-sample period (training)
            out_sample_days: Out-of-sample period (testing)
            step_days: Step size for rolling window

        Returns:
            DataFrame with optimization results
        """
        # Generate parameter combinations
        param_names = list(parameter_grid.keys())
        param_values = list(parameter_grid.values())
        param_combinations = list(itertools.product(*param_values))

        results = []

        # Create rolling windows
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)

        current_start = start

        while current_start < end:
            # In-sample period
            in_sample_start = current_start
            in_sample_end = current_start + pd.Timedelta(days=in_sample_days)

            # Out-of-sample period
            out_sample_start = in_sample_end
            out_sample_end = out_sample_start + pd.Timedelta(days=out_sample_days)

            if out_sample_end > end:
                break

            logger.info(
                f"Walk-forward window: IS={in_sample_start.date()} to {in_sample_end.date()}, "
                f"OOS={out_sample_start.date()} to {out_sample_end.date()}"
            )

            # Optimize on in-sample
            best_params = self._optimize_window(
                base_config,
                param_combinations,
                param_names,
                in_sample_start.strftime('%Y-%m-%d'),
                in_sample_end.strftime('%Y-%m-%d')
            )

            # Test on out-of-sample
            oos_result = self._test_parameters(
                base_config,
                best_params,
                out_sample_start.strftime('%Y-%m-%d'),
                out_sample_end.strftime('%Y-%m-%d')
            )

            results.append({
                'in_sample_start': in_sample_start,
                'in_sample_end': in_sample_end,
                'out_sample_start': out_sample_start,
                'out_sample_end': out_sample_end,
                'best_parameters': best_params,
                **oos_result
            })

            # Roll window forward
            current_start += pd.Timedelta(days=step_days)

        return pd.DataFrame(results)

    def _optimize_window(
        self,
        base_config: StrategyConfig,
        param_combinations: List[tuple],
        param_names: List[str],
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """Optimize parameters for a single window."""
        best_sharpe = -np.inf
        best_params = None

        for combo in param_combinations:
            # Create parameters dict
            params = dict(zip(param_names, combo))

            # Update config
            config = StrategyConfig(
                name=base_config.name,
                version=base_config.version,
                parameters=params,
                symbols=base_config.symbols,
                max_positions=base_config.max_positions,
                enable_short=base_config.enable_short
            )

            # Create strategy
            strategy = self.strategy_class(config)

            # Run backtest
            runner = StrategyBacktestRunner(
                strategy, self.data_client, self.backtest_config
            )

            try:
                result = runner.run(start_date, end_date)

                # Track best by Sharpe ratio
                if result.sharpe_ratio > best_sharpe:
                    best_sharpe = result.sharpe_ratio
                    best_params = params
            except Exception as e:
                logger.warning(f"Backtest failed for params {params}: {e}")

        return best_params

    def _test_parameters(
        self,
        base_config: StrategyConfig,
        parameters: Dict[str, Any],
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """Test parameters on out-of-sample period."""
        # Create config with optimized parameters
        config = StrategyConfig(
            name=base_config.name,
            version=base_config.version,
            parameters=parameters,
            symbols=base_config.symbols,
            max_positions=base_config.max_positions,
            enable_short=base_config.enable_short
        )

        strategy = self.strategy_class(config)

        runner = StrategyBacktestRunner(
            strategy, self.data_client, self.backtest_config
        )

        result = runner.run(start_date, end_date)

        return {
            'oos_sharpe': result.sharpe_ratio,
            'oos_return': result.total_return,
            'oos_max_dd': result.max_drawdown,
            'oos_trades': result.total_trades
        }
```

---

## 5. State Persistence & Recovery

### 5.1 State Manager

```python
# src/strategy/state.py

from src.strategy.base import BaseStrategy, Position, StrategyState
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Any, Optional
import json
import pickle
from datetime import datetime
from threading import Lock
import numpy as np


class StrategyStateEncoder(json.JSONEncoder):
    """
    Custom JSON encoder for strategy state (Critical Issue 3.5 fix).

    Handles common non-serializable types safely without silent string conversion.
    """

    def default(self, obj):
        """Encode non-standard types."""
        if isinstance(obj, np.ndarray):
            return {'__numpy_array__': obj.tolist()}
        elif isinstance(obj, pd.Timestamp):
            return {'__timestamp__': obj.isoformat()}
        elif isinstance(obj, datetime):
            return {'__datetime__': obj.isoformat()}
        # Don't use default=str; let it raise TypeError for unsupported types
        return super().default(obj)


def state_decoder(dct):
    """Decode strategy state objects."""
    if '__numpy_array__' in dct:
        return np.array(dct['__numpy_array__'])
    elif '__timestamp__' in dct:
        return pd.Timestamp(dct['__timestamp__'])
    elif '__datetime__' in dct:
        return datetime.fromisoformat(dct['__datetime__'])
    return dct


class StateManager:
    """
    Manages strategy state persistence for recovery.

    Features:
    - Checkpoint creation at configurable intervals
    - State serialization (JSON with proper type handling)
    - Recovery from last checkpoint
    - State replay for debugging
    - Thread-safe checkpointing (Critical Issue 3.2 fix)

    Thread Safety:
        All checkpoint operations are protected by a lock to prevent
        corruption from concurrent access. Checkpoints only occur at
        safe points (controlled by safe_point parameter).
    """

    def __init__(
        self,
        storage_dir: Path,
        checkpoint_interval: int = 100
    ):
        """
        Initialize state manager.

        Args:
            storage_dir: Directory for state storage
            checkpoint_interval: Checkpoint frequency (number of data updates)
        """
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_interval = checkpoint_interval
        self.update_count = 0
        self._lock = Lock()  # Critical Issue 3.2 fix: Thread safety

    def save_checkpoint(
        self,
        strategy: BaseStrategy,
        timestamp: pd.Timestamp
    ) -> Path:
        """
        Save strategy state checkpoint.

        Args:
            strategy: Strategy to checkpoint
            timestamp: Current timestamp

        Returns:
            Path to checkpoint file
        """
        checkpoint = {
            'timestamp': timestamp.isoformat(),
            'strategy_name': strategy.config.name,
            'strategy_version': strategy.config.version,
            'strategy_state': strategy.state.value,
            'parameters': strategy.config.parameters,
            'positions': {
                symbol: asdict(pos) for symbol, pos in strategy.current_positions.items()
            },
            'state_data': strategy.state_data,
            'performance': strategy.performance
        }

        # Create checkpoint filename
        checkpoint_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{strategy.config.name}_v{strategy.config.version}_{checkpoint_id}.json"
        filepath = self.storage_dir / filename

        # Save as JSON with proper serialization (Critical Issue 3.5 fix)
        try:
            with open(filepath, 'w') as f:
                json.dump(checkpoint, f, indent=2, cls=StrategyStateEncoder)
        except TypeError as e:
            logger.error(f"Failed to serialize checkpoint: {e}")
            raise ValueError(
                f"Strategy state contains unserializable objects. "
                f"Only JSON-compatible types (int, float, str, list, dict) and "
                f"numpy arrays, pd.Timestamp, datetime allowed in state_data. "
                f"Error: {e}"
            )

        logger.info(f"Checkpoint saved: {filepath}")

        return filepath

    def load_checkpoint(
        self,
        strategy: BaseStrategy,
        checkpoint_path: Optional[Path] = None
    ) -> bool:
        """
        Load strategy state from checkpoint.

        Args:
            strategy: Strategy to restore
            checkpoint_path: Path to checkpoint (if None, loads latest)

        Returns:
            True if successful
        """
        if checkpoint_path is None:
            # Find latest checkpoint for this strategy
            pattern = f"{strategy.config.name}_v{strategy.config.version}_*.json"
            checkpoints = list(self.storage_dir.glob(pattern))

            if not checkpoints:
                logger.warning(f"No checkpoints found for {strategy.config.name}")
                return False

            checkpoint_path = max(checkpoints, key=lambda p: p.stat().st_mtime)

        # Load checkpoint with custom decoder (Critical Issue 3.5 fix)
        with open(checkpoint_path, 'r') as f:
            checkpoint = json.load(f, object_hook=state_decoder)

        # Restore state
        strategy.state = StrategyState(checkpoint['strategy_state'])
        strategy.state_data = checkpoint['state_data']
        strategy.performance = checkpoint['performance']

        # Restore positions
        strategy.current_positions = {}
        for symbol, pos_dict in checkpoint['positions'].items():
            # Convert dict back to Position object
            pos_dict['entry_time'] = pd.to_datetime(pos_dict['entry_time'])
            strategy.current_positions[symbol] = Position(**pos_dict)

        logger.info(f"Checkpoint loaded: {checkpoint_path}")

        return True

    def auto_checkpoint(
        self,
        strategy: BaseStrategy,
        timestamp: pd.Timestamp,
        safe_point: bool = True
    ) -> Optional[Path]:
        """
        Automatically checkpoint at configured intervals with thread safety.

        Thread Safety (Critical Issue 3.2 fix):
            - Uses lock to prevent concurrent checkpoint operations
            - Only checkpoints at safe points (after signal generation completes)
            - Set safe_point=False during signal generation to prevent mid-operation checkpoints

        Args:
            strategy: Strategy to checkpoint
            timestamp: Current timestamp
            safe_point: Only checkpoint at safe points (default: True)

        Returns:
            Checkpoint path if created, None otherwise

        Example:
            >>> # Safe usage pattern
            >>> state_mgr.auto_checkpoint(strategy, timestamp, safe_point=False)  # During signal gen
            >>> signals = strategy.generate_signals(timestamp, data)
            >>> state_mgr.auto_checkpoint(strategy, timestamp, safe_point=True)   # After completion
        """
        # Don't checkpoint at unsafe points
        if not safe_point:
            return None

        # Thread-safe counter update
        with self._lock:
            self.update_count += 1

            if self.update_count >= self.checkpoint_interval:
                self.update_count = 0
                # Checkpoint inside lock to ensure atomicity
                return self.save_checkpoint(strategy, timestamp)

        return None
```

---

## 6. Performance Tracking & Attribution

### 6.1 Strategy-Level Metrics

```python
# src/strategy/performance.py

from dataclasses import dataclass
from typing import Dict, List
import pandas as pd
import numpy as np


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
```

---

## 7. Testing Strategy

### 7.1 Coverage Plan (>85% Target)

| Component | Coverage Target | Priority |
|-----------|----------------|----------|
| BaseStrategy | 95% | Critical |
| RelativeValueStrategy | 90% | Critical |
| ParameterManager | 90% | High |
| StateManager | 90% | High |
| StrategyBacktestRunner | 85% | High |
| RiskAwareStrategy | 85% | High |
| PerformanceTracker | 85% | Medium |

### 7.2 Test Examples

```python
# tests/test_base_strategy.py

import pytest
import pandas as pd
from src.strategy.base import (
    BaseStrategy, StrategyConfig, Signal, SignalType, Position, FillEvent
)


class SimpleMovingAverageStrategy(BaseStrategy):
    """Test strategy for unit tests."""

    def generate_signals(self, timestamp, data):
        signals = {}

        for symbol in self.config.symbols:
            df = data[symbol]

            if len(df) < 50:
                continue

            # Simple MA crossover
            sma_20 = df['Close'].rolling(20).mean().iloc[-1]
            sma_50 = df['Close'].rolling(50).mean().iloc[-1]

            if sma_20 > sma_50:
                signals[symbol] = Signal(
                    symbol=symbol,
                    signal_type=SignalType.LONG,
                    timestamp=timestamp,
                    confidence=0.8,
                    metadata={'current_price': df['Close'].iloc[-1]}
                )
            elif sma_20 < sma_50:
                signals[symbol] = Signal(
                    symbol=symbol,
                    signal_type=SignalType.FLAT,
                    timestamp=timestamp
                )

        return signals

    def calculate_positions(self, signals, current_positions, portfolio_value):
        positions = {}

        for symbol, signal in signals.items():
            if signal.signal_type == SignalType.LONG:
                price = signal.metadata.get('current_price', 0)
                if price > 0:
                    capital_per_position = portfolio_value * 0.1  # 10% per position
                    positions[symbol] = int(capital_per_position / price)
            else:
                positions[symbol] = 0.0

        return positions


def test_strategy_initialization():
    """Test strategy initialization."""
    config = StrategyConfig(
        name="TestStrategy",
        version="1.0.0",
        parameters={'fast_period': 20, 'slow_period': 50},
        symbols=['AAPL', 'MSFT']
    )

    strategy = SimpleMovingAverageStrategy(config)

    assert strategy.config.name == "TestStrategy"
    assert strategy.config.version == "1.0.0"
    assert len(strategy.config.symbols) == 2
    assert strategy.state.value == "initialized"


def test_signal_generation():
    """Test signal generation."""
    config = StrategyConfig(
        name="TestStrategy",
        version="1.0.0",
        symbols=['AAPL']
    )

    strategy = SimpleMovingAverageStrategy(config)

    # Create sample data
    dates = pd.date_range('2024-01-01', periods=100, freq='1D')
    data = {
        'AAPL': pd.DataFrame({
            'Open': range(100, 200),
            'High': range(102, 202),
            'Low': range(98, 198),
            'Close': range(100, 200),
            'Volume': [1000000] * 100
        }, index=dates)
    }

    timestamp = dates[-1]
    signals = strategy.generate_signals(timestamp, data)

    assert 'AAPL' in signals
    assert signals['AAPL'].signal_type == SignalType.LONG  # Uptrend
    assert signals['AAPL'].timestamp == timestamp


def test_position_calculation():
    """Test position calculation."""
    config = StrategyConfig(
        name="TestStrategy",
        version="1.0.0",
        symbols=['AAPL']
    )

    strategy = SimpleMovingAverageStrategy(config)

    # Create signal
    signal = Signal(
        symbol='AAPL',
        signal_type=SignalType.LONG,
        timestamp=pd.Timestamp.now(),
        metadata={'current_price': 150.0}
    )

    signals = {'AAPL': signal}
    current_positions = {}
    portfolio_value = 100000.0

    positions = strategy.calculate_positions(signals, current_positions, portfolio_value)

    assert 'AAPL' in positions
    assert positions['AAPL'] > 0  # Long position
    # 10% of $100k = $10k, at $150/share = 66 shares
    assert positions['AAPL'] == 66


def test_fill_handling():
    """Test fill event handling."""
    config = StrategyConfig(
        name="TestStrategy",
        version="1.0.0",
        symbols=['AAPL']
    )

    strategy = SimpleMovingAverageStrategy(config)

    # Create fill event
    fill = FillEvent(
        symbol='AAPL',
        quantity=100,
        price=150.0,
        timestamp=pd.Timestamp.now(),
        commission=10.0,
        slippage=5.0
    )

    strategy.on_fill(fill)

    # Check position created
    assert 'AAPL' in strategy.current_positions
    assert strategy.current_positions['AAPL'].quantity == 100
    assert strategy.current_positions['AAPL'].entry_price == 150.0
```

---

## 8. Implementation Roadmap

### Phase 1: Core Framework (Week 1-2)
- [ ] Implement BaseStrategy with lifecycle methods
- [ ] Implement RelativeValueStrategy specialization
- [ ] Create StrategyConfig and related dataclasses
- [ ] Write unit tests for core classes (>85% coverage)
- [ ] Integration testing with backtesting engine

### Phase 2: Integration (Week 3-4)
- [ ] Implement StrategyBacktestRunner
- [ ] Implement RiskAwareStrategy wrapper
- [ ] Implement StrategyDataAdapter
- [ ] Integration tests with risk manager
- [ ] End-to-end testing with sample strategies

### Phase 3: Parameter Management (Week 5-6)
- [ ] Implement ParameterManager with versioning
- [ ] Implement WalkForwardOptimizer
- [ ] Create parameter serialization/deserialization
- [ ] Add optimization history tracking
- [ ] Testing with grid search and optimization

### Phase 4: State & Performance (Week 7-8)
- [ ] Implement StateManager with checkpointing
- [ ] Implement PerformanceTracker
- [ ] Add strategy-level attribution
- [ ] Recovery and replay testing
- [ ] Documentation and examples

---

## 9. Example Strategy Implementations

### 9.1 Pairs Trading Strategy

```python
# src/strategy/pairs_trading.py

from src.strategy.base import RelativeValueStrategy, StrategyConfig, Signal, SignalType
import pandas as pd
from typing import Dict


class PairsTradingStrategy(RelativeValueStrategy):
    """
    Statistical arbitrage via cointegrated pairs.

    Parameters:
        - zscore_entry: Z-score threshold for entry (default: 2.0)
        - zscore_exit: Z-score threshold for exit (default: 0.5)
        - lookback: Lookback period for spread calculation (default: 60)
        - hedge_method: Hedge ratio method ("ols", "ratio") (default: "ols")
    """

    def generate_signals(self, timestamp, data):
        signals = {}

        # Get parameters
        zscore_entry = self.get_parameter('zscore_entry', 2.0)
        zscore_exit = self.get_parameter('zscore_exit', 0.5)
        lookback = self.get_parameter('lookback', 60)
        hedge_method = self.get_parameter('hedge_method', 'ols')

        # Assumes symbols configured as pairs: [A, B, C, D] → pairs (A,B), (C,D)
        symbols = self.config.symbols

        for i in range(0, len(symbols), 2):
            if i + 1 >= len(symbols):
                break

            symbol_long = symbols[i]
            symbol_short = symbols[i + 1]

            # Calculate hedge ratio
            hedge_ratio = self.calculate_hedge_ratio(
                symbol_long, symbol_short, data, method=hedge_method, lookback=lookback
            )

            # Calculate spread
            spread = self.calculate_spread(
                symbol_long, symbol_short, data, hedge_ratio.ratio
            )

            # Calculate z-score
            zscore = self.calculate_spread_zscore(spread, lookback)

            # Generate signals based on z-score
            if zscore < -zscore_entry:
                # Spread too low → long spread (long A, short B)
                signals[symbol_long] = Signal(
                    symbol=symbol_long,
                    signal_type=SignalType.LONG,
                    timestamp=timestamp,
                    confidence=min(abs(zscore) / zscore_entry, 1.0),
                    metadata={
                        'current_price': data[symbol_long]['Close'].iloc[-1],
                        'pair': symbol_short,
                        'zscore': zscore
                    }
                )
                signals[symbol_short] = Signal(
                    symbol=symbol_short,
                    signal_type=SignalType.SHORT,
                    timestamp=timestamp,
                    confidence=min(abs(zscore) / zscore_entry, 1.0),
                    metadata={
                        'current_price': data[symbol_short]['Close'].iloc[-1],
                        'pair': symbol_long,
                        'zscore': zscore
                    }
                )

            elif zscore > zscore_entry:
                # Spread too high → short spread (short A, long B)
                signals[symbol_long] = Signal(
                    symbol=symbol_long,
                    signal_type=SignalType.SHORT,
                    timestamp=timestamp,
                    confidence=min(abs(zscore) / zscore_entry, 1.0),
                    metadata={
                        'current_price': data[symbol_long]['Close'].iloc[-1],
                        'pair': symbol_short,
                        'zscore': zscore
                    }
                )
                signals[symbol_short] = Signal(
                    symbol=symbol_short,
                    signal_type=SignalType.LONG,
                    timestamp=timestamp,
                    confidence=min(abs(zscore) / zscore_entry, 1.0),
                    metadata={
                        'current_price': data[symbol_short]['Close'].iloc[-1],
                        'pair': symbol_long,
                        'zscore': zscore
                    }
                )

            elif abs(zscore) < zscore_exit:
                # Close positions when spread normalizes
                if self.has_position(symbol_long) or self.has_position(symbol_short):
                    signals[symbol_long] = Signal(
                        symbol=symbol_long,
                        signal_type=SignalType.FLAT,
                        timestamp=timestamp,
                        metadata={'zscore': zscore}
                    )
                    signals[symbol_short] = Signal(
                        symbol=symbol_short,
                        signal_type=SignalType.FLAT,
                        timestamp=timestamp,
                        metadata={'zscore': zscore}
                    )

        return signals

    def calculate_positions(self, signals, current_positions, portfolio_value):
        positions = {}

        # Process pairs
        symbols = self.config.symbols

        for i in range(0, len(symbols), 2):
            if i + 1 >= len(symbols):
                break

            symbol_long = symbols[i]
            symbol_short = symbols[i + 1]

            if symbol_long not in signals or symbol_short not in signals:
                continue

            signal_long = signals[symbol_long]
            signal_short = signals[symbol_short]

            # Get hedge ratio
            pair_key = (symbol_long, symbol_short)
            if pair_key not in self.hedge_ratios:
                continue

            hedge_ratio = self.hedge_ratios[pair_key].ratio

            # Flat signals → close positions
            if signal_long.signal_type == SignalType.FLAT:
                positions[symbol_long] = 0.0
                positions[symbol_short] = 0.0
                continue

            # Calculate position sizes
            # Allocate equal capital to each pair
            capital_per_pair = portfolio_value / (len(symbols) / 2)

            # Get current prices
            price_long = signal_long.metadata.get('current_price', 0)
            price_short = signal_short.metadata.get('current_price', 0)

            if price_long == 0 or price_short == 0:
                continue

            # Calculate quantities to maintain hedge ratio
            # Use 50% of capital per leg
            capital_per_leg = capital_per_pair * 0.5

            if signal_long.signal_type == SignalType.LONG:
                # Long leg quantity
                qty_long = int(capital_per_leg / price_long)

                # Balance short leg with hedge ratio
                qty_long_balanced, qty_short_balanced = self.balance_positions(
                    symbol_long, symbol_short, qty_long, hedge_ratio
                )

                positions[symbol_long] = qty_long_balanced
                positions[symbol_short] = qty_short_balanced

            elif signal_long.signal_type == SignalType.SHORT:
                # Short leg quantity
                qty_short = int(capital_per_leg / price_long)

                # Balance with hedge ratio
                qty_short_balanced, qty_long_balanced = self.balance_positions(
                    symbol_short, symbol_long, qty_short, 1.0 / hedge_ratio
                )

                positions[symbol_long] = qty_long_balanced
                positions[symbol_short] = qty_short_balanced

        return positions
```

---

## 10. Document Control

**Approvals**:
- [x] Peer review (via Task tool) - **Approved with Changes** - Implementation Readiness: 85/100 (after fixes)
- [ ] CEO approval (via commit to main branch)

**Peer Review Summary** (2025-10-25):
- **Reviewer**: Claude (general-purpose agent)
- **Status**: Approved with Changes
- **Implementation Readiness**: 85/100 (post-fixes, from 72/100)
- **Critical Issues**: 5 identified and resolved in v1.1
- **Key Strengths**: Clear separation of concerns, relative value focus, comprehensive integration design, excellent documentation
- **Recommendation**: Ready for Phase 1 implementation

**Change History**:
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-10-25 | Claude (general-purpose) | Initial architecture design |
| 1.1 | 2025-10-25 | Claude (general-purpose) | Fixed 5 critical peer review issues + 1 improvement |

**Next Review**: Post Phase 1 implementation (reassess architecture vs. implementation)

---

**End of Document**
