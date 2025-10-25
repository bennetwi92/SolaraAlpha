"""
Dummy Strategy Example

A simple example strategy demonstrating the BaseStrategy framework.
This strategy implements a basic moving average crossover for educational purposes.
"""

import pandas as pd
import numpy as np
from typing import Dict

from src.strategy.base import (
    BaseStrategy,
    StrategyConfig,
    Signal,
    SignalType,
    Position
)


class DummyMovingAverageCrossover(BaseStrategy):
    """
    Simple moving average crossover strategy.

    Generates LONG signals when fast MA crosses above slow MA,
    and FLAT signals when fast MA crosses below slow MA.

    This is a basic example to demonstrate the strategy framework.
    It is NOT intended for actual trading.

    Parameters:
        fast_period (int): Fast moving average period (default: 20)
        slow_period (int): Slow moving average period (default: 50)
        position_size_pct (float): Position size as % of portfolio (default: 0.1)

    Example:
        >>> config = StrategyConfig(
        ...     name="DummyMAStrategy",
        ...     version="1.0.0",
        ...     parameters={
        ...         'fast_period': 20,
        ...         'slow_period': 50,
        ...         'position_size_pct': 0.1
        ...     },
        ...     symbols=['AAPL', 'MSFT', 'GOOGL'],
        ...     max_positions=5
        ... )
        >>> strategy = DummyMovingAverageCrossover(config)
    """

    def __init__(self, config: StrategyConfig):
        """
        Initialize the dummy strategy.

        Args:
            config: Strategy configuration
        """
        super().__init__(config)

        # Validate parameters
        self._validate_parameters()

    def _validate_parameters(self) -> None:
        """Validate strategy-specific parameters."""
        fast_period = self.get_parameter('fast_period', 20)
        slow_period = self.get_parameter('slow_period', 50)
        position_size_pct = self.get_parameter('position_size_pct', 0.1)

        if fast_period >= slow_period:
            raise ValueError(
                f"fast_period ({fast_period}) must be less than "
                f"slow_period ({slow_period})"
            )

        if not 0.0 < position_size_pct <= 1.0:
            raise ValueError(
                f"position_size_pct must be between 0 and 1, got {position_size_pct}"
            )

    def on_data(self, timestamp: pd.Timestamp, data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """
        Calculate moving averages for all symbols.

        Args:
            timestamp: Current timestamp
            data: Market data

        Returns:
            Data with moving averages added
        """
        fast_period = self.get_parameter('fast_period', 20)
        slow_period = self.get_parameter('slow_period', 50)

        for symbol in self.config.symbols:
            if symbol not in data:
                continue

            df = data[symbol]

            # Calculate moving averages
            df[f'SMA_{fast_period}'] = df['Close'].rolling(fast_period).mean()
            df[f'SMA_{slow_period}'] = df['Close'].rolling(slow_period).mean()

        return data

    def generate_signals(
        self,
        timestamp: pd.Timestamp,
        data: Dict[str, pd.DataFrame]
    ) -> Dict[str, Signal]:
        """
        Generate trading signals based on MA crossover.

        Args:
            timestamp: Current timestamp
            data: Market data (with MAs from on_data)

        Returns:
            Dictionary of signals
        """
        signals = {}

        fast_period = self.get_parameter('fast_period', 20)
        slow_period = self.get_parameter('slow_period', 50)

        for symbol in self.config.symbols:
            if symbol not in data:
                continue

            df = data[symbol]

            # Need enough data for slow MA
            if len(df) < slow_period:
                continue

            # Get current and previous MA values
            fast_ma_key = f'SMA_{fast_period}'
            slow_ma_key = f'SMA_{slow_period}'

            if fast_ma_key not in df.columns or slow_ma_key not in df.columns:
                continue

            # Current values
            fast_ma_current = df[fast_ma_key].iloc[-1]
            slow_ma_current = df[slow_ma_key].iloc[-1]

            # Check for NaN values
            if pd.isna(fast_ma_current) or pd.isna(slow_ma_current):
                continue

            # Previous values (for crossover detection)
            if len(df) > 1:
                fast_ma_prev = df[fast_ma_key].iloc[-2]
                slow_ma_prev = df[slow_ma_key].iloc[-2]
            else:
                fast_ma_prev = fast_ma_current
                slow_ma_prev = slow_ma_current

            current_price = df['Close'].iloc[-1]

            # Detect crossover
            signal_type = SignalType.FLAT
            confidence = 0.0

            # Bullish crossover: fast crosses above slow
            if fast_ma_prev <= slow_ma_prev and fast_ma_current > slow_ma_current:
                signal_type = SignalType.LONG
                confidence = 0.7

            # Bearish crossover: fast crosses below slow
            elif fast_ma_prev >= slow_ma_prev and fast_ma_current < slow_ma_current:
                signal_type = SignalType.FLAT
                confidence = 0.7

            # Continue existing trend (no crossover)
            elif fast_ma_current > slow_ma_current:
                signal_type = SignalType.LONG
                confidence = 0.5
            else:
                signal_type = SignalType.FLAT
                confidence = 0.5

            signals[symbol] = Signal(
                symbol=symbol,
                signal_type=signal_type,
                timestamp=timestamp,
                confidence=confidence,
                metadata={
                    'current_price': current_price,
                    'fast_ma': fast_ma_current,
                    'slow_ma': slow_ma_current
                }
            )

        return signals

    def calculate_positions(
        self,
        signals: Dict[str, Signal],
        current_positions: Dict[str, Position],
        portfolio_value: float
    ) -> Dict[str, float]:
        """
        Convert signals to position sizes using equal weighting.

        Args:
            signals: Generated signals
            current_positions: Current holdings
            portfolio_value: Total portfolio value

        Returns:
            Target positions (symbol -> quantity)
        """
        positions = {}

        position_size_pct = self.get_parameter('position_size_pct', 0.1)

        # Count number of LONG signals
        long_signals = [s for s in signals.values() if s.signal_type == SignalType.LONG]
        num_longs = len(long_signals)

        if num_longs == 0:
            # No longs, close all positions
            for symbol in self.config.symbols:
                positions[symbol] = 0.0
            return positions

        # Respect max_positions limit
        num_longs = min(num_longs, self.config.max_positions)

        # Calculate capital per position
        capital_per_position = portfolio_value * position_size_pct

        for symbol, signal in signals.items():
            if signal.signal_type == SignalType.LONG:
                price = signal.metadata.get('current_price', 0)

                if price > 0:
                    # Calculate shares
                    quantity = int(capital_per_position / price)
                    positions[symbol] = quantity
                else:
                    positions[symbol] = 0.0
            else:
                # FLAT signal: close position
                positions[symbol] = 0.0

        return positions


# Example usage
if __name__ == "__main__":
    # Example configuration
    config = StrategyConfig(
        name="DummyMAStrategy",
        version="1.0.0",
        parameters={
            'fast_period': 20,
            'slow_period': 50,
            'position_size_pct': 0.1
        },
        symbols=['AAPL', 'MSFT', 'GOOGL'],
        max_positions=5,
        enable_short=False,
        rebalance_frequency='1D'
    )

    # Create strategy
    strategy = DummyMovingAverageCrossover(config)

    print(f"Strategy: {strategy.config.name} v{strategy.config.version}")
    print(f"Trading universe: {strategy.config.symbols}")
    print(f"Parameters: {strategy.config.parameters}")
    print(f"State: {strategy.state.value}")

    # Example data (you would normally fetch this from a data source)
    dates = pd.date_range('2024-01-01', periods=100, freq='1D')
    sample_data = {
        symbol: pd.DataFrame({
            'Open': np.random.uniform(100, 200, 100),
            'High': np.random.uniform(100, 200, 100),
            'Low': np.random.uniform(100, 200, 100),
            'Close': np.random.uniform(100, 200, 100),
            'Volume': np.random.randint(1000000, 10000000, 100)
        }, index=dates)
        for symbol in config.symbols
    }

    # Process data
    timestamp = dates[-1]
    processed_data = strategy.on_data(timestamp, sample_data)

    # Generate signals
    signals = strategy.generate_signals(timestamp, processed_data)

    print(f"\nSignals generated at {timestamp}:")
    for symbol, signal in signals.items():
        print(f"  {symbol}: {signal.signal_type.name} (confidence: {signal.confidence:.2f})")

    # Calculate positions
    portfolio_value = 100000.0
    target_positions = strategy.calculate_positions(signals, {}, portfolio_value)

    print(f"\nTarget positions (portfolio value: ${portfolio_value:,.2f}):")
    for symbol, quantity in target_positions.items():
        if quantity > 0:
            print(f"  {symbol}: {quantity} shares")
