"""FX carry trade strategy implementation with risk management integration."""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from src.data.ingest.fx_ingestor import FXIngestor
from src.execution.strategy_runner import Strategy
from src.risk.risk_manager import RiskManager

from .position_sizing import FXCarryPositionSizer
from .signals import FXCarrySignals

logger = logging.getLogger(__name__)


@dataclass
class FXCarryConfig:
    """Configuration for FX carry strategy."""

    # Strategy identification
    strategy_id: str = "fx_carry_1"
    asset_class: str = "forex"

    # Portfolio parameters
    portfolio_value: float = 100_000
    max_positions: int = 10

    # Currency pairs (major + high-yield pairs)
    currency_pairs: list[str] = None

    # Signal parameters
    trend_lookback: int = 60
    trend_threshold: float = 0.0
    vol_lookback: int = 30
    vol_target: float = 10.0
    min_rate_diff: float = 1.0

    # Position sizing
    max_position_pct: float = 0.20
    max_leverage: float = 1.0
    rebalance_threshold: float = 0.10

    # Data parameters
    lookback_days: int = 120  # Historical data for backtesting
    rebalance_frequency_days: int = 7  # Weekly rebalancing

    # Interest rate update frequency
    rate_update_frequency_days: int = 30  # Monthly rate updates

    def __post_init__(self):
        """Set default currency pairs if not provided."""
        if self.currency_pairs is None:
            # Default to major pairs with good carry opportunities
            self.currency_pairs = [
                "AUD-USD",  # High yield (AUD)
                "NZD-USD",  # High yield (NZD)
                "EUR-USD",  # Major pair
                "GBP-USD",  # Major pair
                "USD-JPY",  # Carry favorite (long USD, short JPY)
                "USD-CHF",  # Safe haven differential
                "EUR-JPY",  # Cross with good volatility
                "GBP-JPY",  # Cross with good volatility
                "AUD-JPY",  # Classic carry trade
                "NZD-JPY",  # Classic carry trade
            ]


class FXCarryStrategy(Strategy):
    """FX carry trade strategy with risk management integration.

    Implements multi-currency carry trade strategy:
    1. Calculate interest rate differentials
    2. Apply trend filtering
    3. Size positions based on volatility
    4. Rebalance portfolio periodically
    5. Integrate with RiskManager for pre-trade validation

    Example:
        >>> config = FXCarryConfig(portfolio_value=100000)
        >>> strategy = FXCarryStrategy(config)
        >>> runner = StrategyRunner()
        >>> runner.register_strategy(strategy, runner_config)
        >>> runner.start()
    """

    def __init__(
        self,
        config: Optional[FXCarryConfig] = None,
        risk_manager: Optional[RiskManager] = None,
    ) -> None:
        """Initialize FX carry strategy.

        Args:
            config: Strategy configuration
            risk_manager: RiskManager instance (creates new if None)
        """
        self.config = config or FXCarryConfig()
        self.risk_manager = risk_manager or RiskManager(
            portfolio_value=self.config.portfolio_value
        )

        # Initialize data ingestor
        self.fx_ingestor = FXIngestor()

        # Initialize strategy components
        self.signal_generator = FXCarrySignals(
            trend_lookback=self.config.trend_lookback,
            trend_threshold=self.config.trend_threshold,
            vol_lookback=self.config.vol_lookback,
            vol_target=self.config.vol_target,
            min_rate_diff=self.config.min_rate_diff,
        )

        self.position_sizer = FXCarryPositionSizer(
            vol_target=self.config.vol_target,
            max_position_pct=self.config.max_position_pct,
            max_leverage=self.config.max_leverage,
        )

        # State tracking
        self.positions: Dict[str, Dict[str, float]] = {}  # {pair: {size, entry_price}}
        self.interest_rates: Optional[pd.DataFrame] = None
        self.last_rate_update: Optional[datetime] = None
        self.last_rebalance: Optional[datetime] = None
        self.trade_history: list[Dict] = []

        logger.info(
            f"Initialized FX carry strategy: {self.config.strategy_id} "
            f"with {len(self.config.currency_pairs)} pairs"
        )

    def _update_interest_rates(self, force: bool = False) -> None:
        """Update interest rates from API.

        Args:
            force: Force update even if recent data available
        """
        # Check if update needed
        if not force and self.last_rate_update is not None:
            days_since_update = (datetime.now() - self.last_rate_update).days
            if days_since_update < self.config.rate_update_frequency_days:
                logger.debug(
                    f"Interest rates updated {days_since_update} days ago - skipping"
                )
                return

        logger.info("Updating interest rates")

        try:
            self.interest_rates = self.fx_ingestor.get_interest_rates()
            self.last_rate_update = datetime.now()

            logger.info(
                f"Updated interest rates for {len(self.interest_rates)} countries"
            )

        except Exception as e:
            logger.error(f"Failed to update interest rates: {e}")
            if self.interest_rates is None:
                raise RuntimeError(
                    "No interest rate data available - cannot run strategy"
                )

    def _fetch_price_data(self) -> Dict[str, pd.DataFrame]:
        """Fetch historical price data for all currency pairs.

        Returns:
            Dict mapping pair -> OHLCV DataFrame
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.config.lookback_days)

        prices = {}

        for pair in self.config.currency_pairs:
            try:
                df = self.fx_ingestor.fetch_ohlcv(
                    pair, start_date, end_date, frequency="1d"
                )
                if len(df) > 0:
                    prices[pair] = df
                else:
                    logger.warning(f"No data for {pair}")
            except Exception as e:
                logger.warning(f"Failed to fetch data for {pair}: {e}")

        logger.info(f"Fetched price data for {len(prices)} pairs")

        return prices

    def _calculate_rate_differentials(self) -> Dict[str, float]:
        """Calculate interest rate differentials for all pairs.

        Returns:
            Dict mapping pair -> rate differential (%)
        """
        if self.interest_rates is None:
            raise RuntimeError("Interest rates not loaded")

        differentials = {}

        for pair in self.config.currency_pairs:
            base, quote = pair.split("-")

            try:
                diff = self.fx_ingestor.calculate_rate_differential(
                    base, quote, self.interest_rates
                )
                differentials[pair] = diff
            except ValueError as e:
                logger.warning(f"Could not calculate differential for {pair}: {e}")

        return differentials

    def _validate_trade_with_risk_manager(
        self, pair: str, size: float, price: float
    ) -> bool:
        """Validate trade with RiskManager.

        Args:
            pair: Currency pair
            size: Position size ($)
            price: Current price

        Returns:
            True if trade approved, False if rejected
        """
        # Calculate shares (for FX, this is lot size)
        shares = abs(size) / price

        # Validate with risk manager
        try:
            result = self.risk_manager.validate_trade(
                symbol=pair,
                asset_class=self.config.asset_class,
                strategy_id=self.config.strategy_id,
                quantity=shares if size > 0 else -shares,
                price=price,
            )

            if not result.passed:
                logger.warning(
                    f"Trade rejected for {pair}: {result.violation_reason}"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"Risk validation failed for {pair}: {e}")
            return False

    def _execute_rebalance(
        self, prices: Dict[str, pd.DataFrame]
    ) -> Dict[str, Any]:
        """Execute portfolio rebalance.

        Args:
            prices: Historical price data for all pairs

        Returns:
            Dict with rebalance results
        """
        # Get current prices
        current_prices = {pair: df.iloc[-1]["Close"] for pair, df in prices.items()}

        # Calculate rate differentials
        rate_diffs = self._calculate_rate_differentials()

        # Generate signals
        signals = self.signal_generator.generate_portfolio_signals(
            prices,
            rate_diffs,
            timestamp=pd.Timestamp.now(),
            max_positions=self.config.max_positions,
        )

        # Calculate current equity (including mark-to-market P&L)
        mtm_pnl = 0.0
        for pair, pos in self.positions.items():
            if pair in current_prices:
                current_price = current_prices[pair]
                entry_price = pos["entry_price"]
                pnl = (current_price - entry_price) / entry_price * pos["size"]
                mtm_pnl += pnl

        current_equity = self.config.portfolio_value + mtm_pnl

        # Calculate target positions
        target_positions = self.position_sizer.calculate_position_sizes(
            signals, current_equity
        )

        # Calculate trades needed
        current_sizes = {pair: pos["size"] for pair, pos in self.positions.items()}
        trades_needed = self.position_sizer.calculate_rebalance(
            current_sizes, target_positions, self.config.rebalance_threshold
        )

        # Execute trades (with risk validation)
        executed_trades = 0
        rejected_trades = 0

        for pair, trade_size in trades_needed.items():
            if pair not in current_prices:
                logger.warning(f"No price for {pair}")
                continue

            price = current_prices[pair]

            # Validate with risk manager
            if not self._validate_trade_with_risk_manager(pair, trade_size, price):
                rejected_trades += 1
                continue

            # Execute trade
            current_size = current_sizes.get(pair, 0.0)
            new_size = current_size + trade_size

            # Update position
            if abs(new_size) < 1.0:
                # Close position
                if pair in self.positions:
                    del self.positions[pair]
            else:
                # Update or create position
                if pair in self.positions:
                    # Weighted average entry price for increases
                    if abs(new_size) > abs(current_size):
                        old_entry = self.positions[pair]["entry_price"]
                        new_entry = (
                            old_entry * abs(current_size) + price * abs(trade_size)
                        ) / abs(new_size)
                    else:
                        new_entry = self.positions[pair]["entry_price"]
                else:
                    new_entry = price

                self.positions[pair] = {"size": new_size, "entry_price": new_entry}

            # Record trade
            executed_trades += 1
            self.trade_history.append(
                {
                    "timestamp": datetime.now(),
                    "pair": pair,
                    "trade_size": trade_size,
                    "price": price,
                    "new_size": new_size,
                }
            )

            # Record trade with risk manager
            shares = abs(trade_size) / price
            self.risk_manager.record_trade(
                symbol=pair,
                asset_class=self.config.asset_class,
                strategy_id=self.config.strategy_id,
                quantity=shares if trade_size > 0 else -shares,
                entry_price=price,
                current_price=price,
            )

        self.last_rebalance = datetime.now()

        return {
            "signals_generated": len(signals),
            "trades_needed": len(trades_needed),
            "trades_executed": executed_trades,
            "trades_rejected": rejected_trades,
            "active_positions": len(self.positions),
        }

    def run_iteration(self) -> Dict[str, Any]:
        """Execute one iteration of FX carry strategy.

        Returns:
            Dict with iteration results
        """
        try:
            logger.info(f"Running FX carry iteration: {self.config.strategy_id}")

            # Update interest rates if needed
            self._update_interest_rates()

            # Fetch price data
            prices = self._fetch_price_data()

            if len(prices) == 0:
                logger.error("No price data available")
                return {
                    "trades": 0,
                    "signals": 0,
                    "errors": ["No price data available"],
                }

            # Check if rebalance needed
            need_rebalance = False
            if self.last_rebalance is None:
                need_rebalance = True
            else:
                days_since_rebalance = (
                    datetime.now() - self.last_rebalance
                ).days
                if days_since_rebalance >= self.config.rebalance_frequency_days:
                    need_rebalance = True

            if need_rebalance:
                result = self._execute_rebalance(prices)
                result["rebalanced"] = True
            else:
                result = {
                    "rebalanced": False,
                    "trades_executed": 0,
                    "trades_rejected": 0,
                    "active_positions": len(self.positions),
                }

            result["errors"] = []
            result["trades"] = result.get("trades_executed", 0)
            result["signals"] = result.get("signals_generated", 0)

            logger.info(f"Iteration complete: {result}")

            return result

        except Exception as e:
            logger.error(f"Iteration failed: {e}", exc_info=True)
            return {"trades": 0, "signals": 0, "errors": [str(e)]}

    def get_status(self) -> Dict[str, Any]:
        """Get current strategy status.

        Returns:
            Dict with strategy state
        """
        return {
            "strategy_id": self.config.strategy_id,
            "strategy_type": "fx_carry",
            "portfolio_value": self.config.portfolio_value,
            "active_positions": len(self.positions),
            "currency_pairs": len(self.config.currency_pairs),
            "last_rebalance": (
                self.last_rebalance.isoformat() if self.last_rebalance else None
            ),
            "last_rate_update": (
                self.last_rate_update.isoformat() if self.last_rate_update else None
            ),
            "total_trades": len(self.trade_history),
            "positions": [
                {
                    "pair": pair,
                    "size": pos["size"],
                    "entry_price": pos["entry_price"],
                }
                for pair, pos in self.positions.items()
            ],
        }

    def shutdown(self) -> None:
        """Cleanup before strategy termination."""
        logger.info(f"Shutting down FX carry strategy: {self.config.strategy_id}")

        # Export trade history
        if self.trade_history:
            trades_df = pd.DataFrame(self.trade_history)
            output_path = (
                Path("logs")
                / f"{self.config.strategy_id}_trades_{datetime.now():%Y%m%d_%H%M%S}.csv"
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            trades_df.to_csv(output_path, index=False)
            logger.info(f"Exported {len(trades_df)} trades to {output_path}")
