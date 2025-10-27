"""ETF arbitrage strategy adapter for StrategyRunner integration.

Implements market-neutral relative value strategy exploiting pricing inefficiencies
between ETFs tracking the same index.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
import pandas as pd

from src.data.ingest.yfinance_ingestor import YFinanceIngestor
from src.execution.strategy_runner import Strategy
from src.risk.risk_manager import RiskManager

from .config import ETFArbConfig
from .spread import SpreadCalculator
from .signals import SignalGenerator, SignalConfig, SignalType
from .position_sizing import PositionSizer, PositionSizingConfig

logger = logging.getLogger(__name__)


class ETFArbStrategy(Strategy):
    """ETF arbitrage strategy with risk management integration.

    Implements market-neutral spread trading:
    1. Calculate spread z-scores between ETF pairs
    2. Generate entry/exit signals based on mean reversion
    3. Size positions using Kelly criterion
    4. Validate trades with RiskManager
    5. Execute market-neutral spread trades

    Example:
        >>> config = ETFArbConfig()
        >>> strategy = ETFArbStrategy(config)
        >>> runner = StrategyRunner()
        >>> runner.register_strategy(strategy, runner_config)
        >>> runner.start()
    """

    def __init__(
        self,
        config: Optional[ETFArbConfig] = None,
        risk_manager: Optional[RiskManager] = None
    ):
        """Initialize ETF arbitrage strategy.

        Args:
            config: Strategy configuration
            risk_manager: RiskManager instance (creates new if None)
        """
        self.config = config or ETFArbConfig()
        self.risk_manager = risk_manager or RiskManager(
            portfolio_value=100000.0  # Will be updated from actual portfolio
        )

        # Initialize data ingestor
        self.data_ingestor = YFinanceIngestor()

        # Initialize strategy components
        self.spread_calc = SpreadCalculator(
            lookback_window=self.config.lookback_window
        )

        signal_config = SignalConfig(
            entry_threshold=self.config.entry_threshold,
            exit_threshold=self.config.exit_threshold,
            stop_loss_threshold=self.config.stop_loss_threshold,
            min_half_life=self.config.min_half_life,
            max_half_life=self.config.max_half_life
        )
        self.signal_gen = SignalGenerator(config=signal_config)

        position_config = PositionSizingConfig(
            method=self.config.position_sizing_method,
            max_position_pct=self.config.max_position_pct,
            kelly_fraction=self.config.kelly_fraction,
            fixed_fraction=self.config.fixed_fraction
        )
        self.position_sizer = PositionSizer(
            portfolio_value=100000.0,
            config=position_config
        )

        # State tracking
        self.positions: Dict[str, Dict[str, Any]] = {}  # {pair_key: position_data}
        self.portfolio_value: float = 100000.0
        self.trade_history: list[Dict] = []
        self.win_loss_stats: Dict[str, Dict] = {}  # Track per-pair statistics

        logger.info(
            f"Initialized ETF arbitrage strategy with {len(self.config.pairs)} pairs"
        )

    def run_iteration(self) -> Dict[str, Any]:
        """Execute one strategy iteration.

        Returns:
            Dictionary with trades, signals, errors, and metrics
        """
        try:
            logger.info("Starting ETF arbitrage iteration")

            # Fetch current prices
            prices = self._fetch_price_data()

            # Process each pair
            trades = []
            signals = []
            errors = []

            for pair in self.config.pairs:
                symbol1, symbol2 = pair
                pair_key = f"{symbol1}/{symbol2}"

                try:
                    # Get price data for pair
                    if symbol1 not in prices or symbol2 not in prices:
                        logger.warning(f"Missing price data for {pair_key}")
                        continue

                    prices1 = prices[symbol1]['Close']
                    prices2 = prices[symbol2]['Close']

                    # Calculate spread metrics
                    metrics = self.spread_calc.get_current_metrics(
                        prices1, prices2, method='ratio'
                    )

                    # Get current position state
                    current_position = self.positions.get(pair_key, {}).get('direction', None)

                    # Generate signal
                    signal = self.signal_gen.generate_signal(
                        z_score=metrics.z_score,
                        spread=metrics.spread,
                        half_life=metrics.half_life,
                        timestamp=pd.Timestamp.now(),
                        current_position=current_position
                    )

                    signals.append({
                        'pair': pair_key,
                        'signal_type': signal.signal_type.value,
                        'z_score': signal.z_score,
                        'spread': signal.spread,
                        'reason': signal.reason
                    })

                    # Process signal
                    pair_trades = self._process_signal(
                        signal, pair, prices1.iloc[-1], prices2.iloc[-1], metrics
                    )
                    trades.extend(pair_trades)

                except Exception as e:
                    logger.error(f"Error processing {pair_key}: {e}")
                    errors.append({'pair': pair_key, 'error': str(e)})

            # Calculate metrics
            metrics = self._calculate_metrics()

            logger.info(
                f"Iteration complete: {len(trades)} trades, "
                f"{len(signals)} signals, {len(errors)} errors"
            )

            return {
                'trades': trades,
                'signals': signals,
                'errors': errors,
                'metrics': metrics
            }

        except Exception as e:
            logger.error(f"Iteration failed: {e}")
            return {
                'trades': [],
                'signals': [],
                'errors': [{'error': str(e)}],
                'metrics': {}
            }

    def get_status(self) -> Dict[str, Any]:
        """Get current strategy status.

        Returns:
            Dictionary with positions, portfolio value, and metrics
        """
        return {
            'positions': self.positions,
            'portfolio_value': self.portfolio_value,
            'num_trades': len(self.trade_history),
            'active_pairs': len([p for p in self.positions.values() if p.get('quantity1', 0) != 0])
        }

    def shutdown(self) -> None:
        """Cleanup before strategy termination."""
        logger.info("Shutting down ETF arbitrage strategy")

        # Close all positions
        for pair_key, position in self.positions.items():
            if position.get('quantity1', 0) != 0:
                logger.warning(f"Closing position in {pair_key} on shutdown")
                # In production, would execute closing trades here

        # Export trade history
        if self.trade_history:
            df = pd.DataFrame(self.trade_history)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filepath = f'trades_etf_arb_{timestamp}.csv'
            df.to_csv(filepath, index=False)
            logger.info(f"Exported {len(self.trade_history)} trades to {filepath}")

    def _fetch_price_data(self) -> Dict[str, pd.DataFrame]:
        """Fetch historical price data for all ETFs.

        Returns:
            Dict mapping symbol -> OHLCV DataFrame
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.config.lookback_window + 30)

        prices = {}

        # Get unique symbols from all pairs
        symbols = set()
        for pair in self.config.pairs:
            symbols.add(pair[0])
            symbols.add(pair[1])

        for symbol in symbols:
            try:
                df = self.data_ingestor.fetch_ohlcv(
                    symbol, start_date, end_date, frequency='1d'
                )
                if len(df) > 0:
                    prices[symbol] = df
                else:
                    logger.warning(f"No data for {symbol}")
            except Exception as e:
                logger.warning(f"Failed to fetch data for {symbol}: {e}")

        logger.info(f"Fetched price data for {len(prices)} ETFs")

        return prices

    def _process_signal(
        self,
        signal,
        pair: tuple,
        price1: float,
        price2: float,
        spread_metrics
    ) -> list[Dict]:
        """Process trading signal and execute trades.

        Args:
            signal: Generated signal
            pair: ETF pair tuple (symbol1, symbol2)
            price1: Current price of first ETF
            price2: Current price of second ETF
            spread_metrics: Current spread metrics

        Returns:
            List of executed trades
        """
        symbol1, symbol2 = pair
        pair_key = f"{symbol1}/{symbol2}"
        trades = []

        # Get win/loss statistics for Kelly criterion
        stats = self.win_loss_stats.get(pair_key, {
            'wins': 0, 'losses': 0, 'total_win': 0.0, 'total_loss': 0.0
        })
        win_rate = stats['wins'] / (stats['wins'] + stats['losses']) if (stats['wins'] + stats['losses']) > 0 else 0.5
        avg_win = stats['total_win'] / stats['wins'] if stats['wins'] > 0 else 0.01
        avg_loss = stats['total_loss'] / stats['losses'] if stats['losses'] > 0 else 0.01

        if signal.signal_type in [SignalType.ENTER_LONG_SPREAD, SignalType.ENTER_SHORT_SPREAD]:
            # Calculate position size
            position = self.position_sizer.calculate_position_size(
                symbol1=symbol1,
                symbol2=symbol2,
                price1=price1,
                price2=price2,
                z_score=signal.z_score,
                spread_volatility=spread_metrics.rolling_std,
                win_rate=win_rate,
                avg_win=avg_win,
                avg_loss=avg_loss
            )

            # Validate trades with risk manager
            if not self._validate_trade_with_risk_manager(symbol1, position.quantity1, price1):
                logger.warning(f"Risk manager rejected {symbol1} trade")
                return trades

            if not self._validate_trade_with_risk_manager(symbol2, position.quantity2, price2):
                logger.warning(f"Risk manager rejected {symbol2} trade")
                return trades

            # Record position
            self.positions[pair_key] = {
                'direction': 'long_spread' if signal.signal_type == SignalType.ENTER_LONG_SPREAD else 'short_spread',
                'quantity1': position.quantity1,
                'quantity2': position.quantity2,
                'entry_price1': price1,
                'entry_price2': price2,
                'entry_z_score': signal.z_score,
                'entry_time': datetime.now()
            }

            # Record trades
            trades.append({
                'timestamp': datetime.now(),
                'pair': pair_key,
                'symbol': symbol1,
                'action': 'buy' if position.quantity1 > 0 else 'sell',
                'quantity': abs(position.quantity1),
                'price': price1,
                'signal_type': signal.signal_type.value
            })
            trades.append({
                'timestamp': datetime.now(),
                'pair': pair_key,
                'symbol': symbol2,
                'action': 'buy' if position.quantity2 > 0 else 'sell',
                'quantity': abs(position.quantity2),
                'price': price2,
                'signal_type': signal.signal_type.value
            })

            # Update risk manager
            self.risk_manager.record_trade(
                symbol1, 'etfs', 'etf_arb', position.quantity1, price1, price1
            )
            self.risk_manager.record_trade(
                symbol2, 'etfs', 'etf_arb', position.quantity2, price2, price2
            )

        elif signal.signal_type in [SignalType.EXIT_POSITION, SignalType.STOP_LOSS]:
            # Exit position
            if pair_key in self.positions:
                position_data = self.positions[pair_key]

                # Calculate P&L
                pnl1 = position_data['quantity1'] * (price1 - position_data['entry_price1'])
                pnl2 = position_data['quantity2'] * (price2 - position_data['entry_price2'])
                total_pnl = pnl1 + pnl2

                # Update win/loss statistics
                if total_pnl > 0:
                    stats['wins'] += 1
                    stats['total_win'] += total_pnl
                else:
                    stats['losses'] += 1
                    stats['total_loss'] += abs(total_pnl)
                self.win_loss_stats[pair_key] = stats

                # Record trades
                trades.append({
                    'timestamp': datetime.now(),
                    'pair': pair_key,
                    'symbol': symbol1,
                    'action': 'sell' if position_data['quantity1'] > 0 else 'buy',
                    'quantity': abs(position_data['quantity1']),
                    'price': price1,
                    'signal_type': signal.signal_type.value,
                    'pnl': pnl1
                })
                trades.append({
                    'timestamp': datetime.now(),
                    'pair': pair_key,
                    'symbol': symbol2,
                    'action': 'sell' if position_data['quantity2'] > 0 else 'buy',
                    'quantity': abs(position_data['quantity2']),
                    'price': price2,
                    'signal_type': signal.signal_type.value,
                    'pnl': pnl2
                })

                # Update portfolio value
                self.portfolio_value += total_pnl

                # Update risk manager and position sizer
                self.risk_manager.record_trade(
                    symbol1, 'etfs', 'etf_arb', -position_data['quantity1'], price1, price1
                )
                self.risk_manager.record_trade(
                    symbol2, 'etfs', 'etf_arb', -position_data['quantity2'], price2, price2
                )
                self.position_sizer.update_portfolio_value(self.portfolio_value)

                # Clear position
                del self.positions[pair_key]

        # Add trades to history
        self.trade_history.extend(trades)

        return trades

    def _validate_trade_with_risk_manager(
        self,
        symbol: str,
        quantity: int,
        price: float
    ) -> bool:
        """Validate trade with RiskManager.

        Args:
            symbol: ETF symbol
            quantity: Position quantity (positive for long, negative for short)
            price: Current price

        Returns:
            True if trade approved, False if rejected
        """
        try:
            result = self.risk_manager.validate_trade(
                symbol=symbol,
                asset_class='etfs',
                strategy_id='etf_arb',
                quantity=quantity,
                price=price
            )

            if not result.passed:
                logger.warning(
                    f"Trade rejected for {symbol}: {result.violation_reason}"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"Risk validation error for {symbol}: {e}")
            return False

    def _calculate_metrics(self) -> Dict[str, Any]:
        """Calculate current strategy metrics.

        Returns:
            Dictionary of performance metrics
        """
        return {
            'portfolio_value': self.portfolio_value,
            'num_positions': len(self.positions),
            'num_trades': len(self.trade_history),
            'total_exposure': sum(
                abs(p.get('quantity1', 0) * p.get('entry_price1', 0)) +
                abs(p.get('quantity2', 0) * p.get('entry_price2', 0))
                for p in self.positions.values()
            )
        }
