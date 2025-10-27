"""Walk-forward backtesting framework for pairs trading.

Implements rigorous out-of-sample validation with rolling parameter
re-estimation to avoid overfitting. Includes transaction cost modeling
and realistic execution simulation.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..pairs_trading.cointegration import CointegrationTester
from ..pairs_trading.position_sizing import PairPosition, PositionSizer
from ..pairs_trading.signals import SignalGenerator, SignalType

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Backtesting configuration.

    Attributes:
        train_pct: Training data percentage (default 0.6 = 60%).
        lookback_window: Rolling window for cointegration (default 60 days).
        reestimate_every: Days between parameter re-estimation (default 20).
        commission_pct: Commission per trade as % of value (default 0.001 = 0.1%).
        slippage_pct: Slippage per trade as % of value (default 0.0005 = 0.05%).
        initial_capital: Starting portfolio value (default $100,000).
    """

    train_pct: float = 0.6
    lookback_window: int = 60
    reestimate_every: int = 20
    commission_pct: float = 0.001
    slippage_pct: float = 0.0005
    initial_capital: float = 100000.0


@dataclass
class Trade:
    """Individual trade record.

    Attributes:
        timestamp: Trade execution time.
        symbol1: First asset symbol.
        symbol2: Second asset symbol.
        quantity1: Units traded for asset1.
        quantity2: Units traded for asset2.
        price1: Execution price for asset1.
        price2: Execution price for asset2.
        signal: Trade signal (1=long spread, -1=short spread).
        pnl: Realized P&L for trade.
        costs: Transaction costs (commission + slippage).
    """

    timestamp: pd.Timestamp
    symbol1: str
    symbol2: str
    quantity1: float
    quantity2: float
    price1: float
    price2: float
    signal: int
    pnl: float = 0.0
    costs: float = 0.0


@dataclass
class BacktestResult:
    """Comprehensive backtest results.

    Attributes:
        trades: List of executed trades.
        equity_curve: Portfolio value over time.
        returns: Daily returns series.
        metrics: Performance metrics dictionary.
        parameter_history: Rolling parameter estimates.
    """

    trades: List[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    returns: pd.Series = field(default_factory=pd.Series)
    metrics: Dict[str, float] = field(default_factory=dict)
    parameter_history: pd.DataFrame = field(default_factory=pd.DataFrame)


class PairsBacktester:
    """Walk-forward backtesting engine for pairs trading.

    Implements rigorous out-of-sample testing methodology:
    1. Split data into train/test sets
    2. Estimate parameters on training data
    3. Test on out-of-sample data
    4. Re-estimate parameters on rolling windows
    5. Calculate performance with transaction costs

    Example:
        >>> backtester = PairsBacktester(config=BacktestConfig())
        >>> result = backtester.backtest_pair(
        ...     prices_a, prices_b,
        ...     symbol1='AAPL', symbol2='MSFT'
        ... )
        >>> print(f"Sharpe: {result.metrics['sharpe_ratio']:.2f}")
    """

    def __init__(self, config: BacktestConfig = BacktestConfig()) -> None:
        """Initialize backtester.

        Args:
            config: Backtesting configuration.
        """
        self.config = config

        # Initialize components
        self.coint_tester = CointegrationTester()
        self.signal_generator = SignalGenerator(
            lookback_window=config.lookback_window
        )
        self.position_sizer = PositionSizer(
            portfolio_value=config.initial_capital
        )

    def split_train_test(
        self, data: pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Split data into training and testing sets.

        Args:
            data: Full dataset with time index.

        Returns:
            Tuple of (train_data, test_data).
        """
        split_idx = int(len(data) * self.config.train_pct)

        train = data.iloc[:split_idx]
        test = data.iloc[split_idx:]

        logger.info(
            f"Data split | "
            f"Train: {len(train)} ({train.index[0]} to {train.index[-1]}), "
            f"Test: {len(test)} ({test.index[0]} to {test.index[-1]})"
        )

        return train, test

    def estimate_parameters(
        self, series1: pd.Series, series2: pd.Series
    ) -> Optional[Dict]:
        """Estimate cointegration parameters on training data.

        Args:
            series1: First asset prices.
            series2: Second asset prices.

        Returns:
            Dictionary with hedge_ratio, p_value, is_cointegrated, or None if test fails.
        """
        try:
            result = self.coint_tester.test_pair(series1, series2)

            if not result.is_cointegrated:
                logger.warning(
                    f"Pair not cointegrated (p={result.p_value:.4f})"
                )
                return None

            # Validate spread stationarity
            spread = series1 - result.hedge_ratio * series2
            is_stationary, adf_pval = (
                self.coint_tester.test_spread_stationarity(spread)
            )

            if not is_stationary:
                logger.warning(
                    f"Spread not stationary (ADF p={adf_pval:.4f})"
                )
                return None

            return {
                "hedge_ratio": result.hedge_ratio,
                "p_value": result.p_value,
                "is_cointegrated": True,
                "adf_pvalue": adf_pval,
            }

        except Exception as e:
            logger.error(f"Parameter estimation failed: {e}")
            return None

    def simulate_trade_costs(
        self, trade_value: float
    ) -> float:
        """Calculate transaction costs for a trade.

        Args:
            trade_value: Absolute dollar value of trade.

        Returns:
            Total costs (commission + slippage).
        """
        commission = trade_value * self.config.commission_pct
        slippage = trade_value * self.config.slippage_pct
        return commission + slippage

    def backtest_pair(
        self,
        series1: pd.Series,
        series2: pd.Series,
        symbol1: str,
        symbol2: str,
    ) -> BacktestResult:
        """Backtest pairs trading strategy on single pair.

        Args:
            series1: First asset price series.
            series2: Second asset price series.
            symbol1: First asset symbol.
            symbol2: Second asset symbol.

        Returns:
            BacktestResult with trades, equity curve, and metrics.
        """
        logger.info(f"Starting backtest for {symbol1}/{symbol2}")

        # Combine into DataFrame
        data = pd.DataFrame({"series1": series1, "series2": series2})

        # Split train/test
        train_data, test_data = self.split_train_test(data)

        # Estimate initial parameters on training data
        params = self.estimate_parameters(
            train_data["series1"], train_data["series2"]
        )

        if params is None:
            logger.error("Initial parameter estimation failed - aborting")
            return BacktestResult()

        hedge_ratio = params["hedge_ratio"]

        # Initialize tracking
        trades = []
        equity = [self.config.initial_capital]
        current_position = SignalType.NO_POSITION
        position_entry_prices = None
        parameter_history = []

        # Walk-forward through test data
        for i in range(len(test_data)):
            current_idx = i + len(train_data)
            current_date = data.index[current_idx]

            # Re-estimate parameters periodically
            if i > 0 and i % self.config.reestimate_every == 0:
                # Use rolling window ending at current point
                window_start = max(
                    0, current_idx - self.config.lookback_window
                )
                window_data = data.iloc[window_start:current_idx]

                new_params = self.estimate_parameters(
                    window_data["series1"], window_data["series2"]
                )

                if new_params is not None:
                    hedge_ratio = new_params["hedge_ratio"]
                    logger.info(
                        f"Parameters updated at {current_date} | "
                        f"Hedge ratio: {hedge_ratio:.4f}"
                    )

                parameter_history.append(
                    {
                        "date": current_date,
                        "hedge_ratio": hedge_ratio,
                        "p_value": (
                            new_params["p_value"] if new_params else None
                        ),
                    }
                )

            # Generate signal using data up to current point
            lookback_start = max(
                0, current_idx - self.config.lookback_window
            )
            history_s1 = data["series1"].iloc[lookback_start : current_idx + 1]
            history_s2 = data["series2"].iloc[lookback_start : current_idx + 1]

            signals_df = self.signal_generator.generate_signals(
                history_s1, history_s2, hedge_ratio, current_position
            )

            current_signal = signals_df.iloc[-1]["signal"]
            current_price1 = data["series1"].iloc[current_idx]
            current_price2 = data["series2"].iloc[current_idx]

            # Execute trade if signal present
            if current_signal != 0:
                # Calculate spread volatility for position sizing
                spread = history_s1 - hedge_ratio * history_s2
                spread_vol = self.signal_generator.calculate_spread_volatility(
                    spread
                )

                # Size position
                position = self.position_sizer.calculate_position_size(
                    symbol1,
                    symbol2,
                    current_price1,
                    current_price2,
                    hedge_ratio,
                    spread_vol,
                )

                # Apply signal direction
                if current_signal < 0:
                    # Short spread: invert quantities
                    position.quantity1 = -position.quantity1
                    position.quantity2 = -position.quantity2

                # Calculate trade costs
                trade_value = (
                    position.dollar_exposure1 + position.dollar_exposure2
                )
                costs = self.simulate_trade_costs(trade_value)

                # Record trade
                trade = Trade(
                    timestamp=current_date,
                    symbol1=symbol1,
                    symbol2=symbol2,
                    quantity1=position.quantity1,
                    quantity2=position.quantity2,
                    price1=current_price1,
                    price2=current_price2,
                    signal=current_signal,
                    costs=costs,
                )

                # Calculate P&L if closing position
                if (
                    current_position != SignalType.NO_POSITION
                    and position_entry_prices is not None
                ):
                    # P&L = (current_prices - entry_prices) * quantities
                    pnl1 = (
                        current_price1 - position_entry_prices[0]
                    ) * position.quantity1
                    pnl2 = (
                        current_price2 - position_entry_prices[1]
                    ) * position.quantity2
                    trade.pnl = pnl1 + pnl2 - costs

                    # Update equity
                    equity.append(equity[-1] + trade.pnl)

                    # Close position
                    current_position = SignalType.NO_POSITION
                    position_entry_prices = None

                else:
                    # Opening new position
                    if current_signal > 0:
                        current_position = SignalType.LONG_SPREAD
                    else:
                        current_position = SignalType.SHORT_SPREAD

                    position_entry_prices = (current_price1, current_price2)

                    # Entry costs reduce equity
                    equity.append(equity[-1] - costs)

                trades.append(trade)

            else:
                # No trade, carry forward equity
                equity.append(equity[-1])

        # Build equity curve
        test_dates = test_data.index
        equity_curve = pd.Series(equity[: len(test_dates)], index=test_dates)

        # Calculate returns
        returns = equity_curve.pct_change().dropna()

        # Calculate metrics
        metrics = self._calculate_metrics(equity_curve, returns, trades)

        # Parameter history DataFrame
        param_df = pd.DataFrame(parameter_history)

        result = BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            returns=returns,
            metrics=metrics,
            parameter_history=param_df,
        )

        logger.info(
            f"Backtest complete | "
            f"Trades: {len(trades)}, "
            f"Sharpe: {metrics.get('sharpe_ratio', 0):.2f}, "
            f"Max DD: {metrics.get('max_drawdown', 0):.2%}"
        )

        return result

    def _calculate_metrics(
        self,
        equity_curve: pd.Series,
        returns: pd.Series,
        trades: List[Trade],
    ) -> Dict[str, float]:
        """Calculate comprehensive performance metrics.

        Args:
            equity_curve: Portfolio value over time.
            returns: Daily returns series.
            trades: List of executed trades.

        Returns:
            Dictionary of performance metrics.
        """
        # Total return
        total_return = (
            (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
            if len(equity_curve) > 0
            else 0.0
        )

        # Sharpe ratio
        sharpe_ratio = (
            (returns.mean() / returns.std() * np.sqrt(252))
            if returns.std() > 0 and len(returns) > 0
            else 0.0
        )

        # Max drawdown
        cumulative = equity_curve / equity_curve.iloc[0]
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min() if len(drawdown) > 0 else 0.0

        # Win rate
        winning_trades = sum(1 for t in trades if t.pnl > 0)
        total_trades = len([t for t in trades if t.pnl != 0])
        win_rate = (
            winning_trades / total_trades if total_trades > 0 else 0.0
        )

        # Average trade P&L
        trade_pnls = [t.pnl for t in trades if t.pnl != 0]
        avg_trade = np.mean(trade_pnls) if trade_pnls else 0.0

        # Profit factor
        gross_profit = sum(pnl for pnl in trade_pnls if pnl > 0)
        gross_loss = abs(sum(pnl for pnl in trade_pnls if pnl < 0))
        profit_factor = (
            gross_profit / gross_loss if gross_loss > 0 else 0.0
        )

        return {
            "total_return": total_return,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "total_trades": total_trades,
            "avg_trade": avg_trade,
            "profit_factor": profit_factor,
            "final_equity": equity_curve.iloc[-1] if len(equity_curve) > 0 else 0.0,
        }

    def backtest_multiple_pairs(
        self, pairs: List[Tuple[pd.Series, pd.Series, str, str]]
    ) -> Dict[str, BacktestResult]:
        """Backtest multiple pairs independently.

        Args:
            pairs: List of (series1, series2, symbol1, symbol2) tuples.

        Returns:
            Dictionary mapping pair name to BacktestResult.
        """
        results = {}

        for series1, series2, symbol1, symbol2 in pairs:
            pair_name = f"{symbol1}/{symbol2}"
            logger.info(f"Backtesting pair: {pair_name}")

            result = self.backtest_pair(series1, series2, symbol1, symbol2)
            results[pair_name] = result

        # Summary statistics
        successful = sum(
            1
            for r in results.values()
            if r.metrics.get("sharpe_ratio", 0) > 1.0
        )
        logger.info(
            f"Backtest summary | "
            f"{successful}/{len(pairs)} pairs exceed Sharpe > 1.0"
        )

        return results
