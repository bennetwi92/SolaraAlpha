"""
Strategy Framework - Backtesting Integration

Runs strategy backtests using the existing BacktestEngine.
"""

from src.backtest.engine import BacktestEngine, BacktestConfig, BacktestResult
from src.strategy.base import BaseStrategy, Signal, SignalType
from src.data.market_client import MarketDataClient
import pandas as pd
from typing import Dict
import logging

logger = logging.getLogger(__name__)


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
        Convert signals to pandas Series with validation.

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

        # Calculate returns from combined equity
        combined_returns = combined_equity.pct_change().fillna(0)

        # Recalculate metrics on combined equity
        # Use first result as template and update metrics
        if results:
            aggregated = list(results.values())[0]
            aggregated.equity_curve = combined_equity
            aggregated.returns = combined_returns
            aggregated.trades = all_trades
            aggregated.total_trades = len(all_trades)

            # Recalculate performance metrics
            from src.backtest.engine import BacktestEngine
            aggregated = BacktestEngine._calculate_metrics(aggregated, combined_equity, combined_returns, all_trades)

        return aggregated
