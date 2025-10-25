"""
Strategy Framework - Base Classes

Provides abstract base classes for all trading strategies with lifecycle
management, position tracking, and integration hooks.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum
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
        - on_risk_rejection() - Risk rejection handling

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
        Calculate hedge ratio with caching for performance.

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
