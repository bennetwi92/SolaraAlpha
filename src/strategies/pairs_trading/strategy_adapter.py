"""Adapter for PaperTradingEngine to work with StrategyRunner.

Wraps PaperTradingEngine to implement the Strategy interface required
by the StrategyRunner daemon.
"""
import logging
from typing import Any, Dict

from src.execution.strategy_runner import Strategy
from .paper_trading import PaperTradingEngine

logger = logging.getLogger(__name__)


class PairsStrategy(Strategy):
    """Adapter for pairs trading strategy to StrategyRunner interface.

    Wraps PaperTradingEngine and implements the Strategy interface for
    integration with the StrategyRunner daemon.

    Example:
        >>> engine = PaperTradingEngine(
        ...     portfolio_value=100000,
        ...     strategy_id='pairs_1'
        ... )
        >>> engine.add_pair('AAPL', 'MSFT', 'equities')
        >>>
        >>> strategy = PairsStrategy(engine)
        >>> runner.register_strategy(strategy, config)
    """

    def __init__(self, engine: PaperTradingEngine) -> None:
        """Initialize pairs strategy adapter.

        Args:
            engine: PaperTradingEngine instance
        """
        self.engine = engine
        logger.info(
            f"PairsStrategy adapter initialized for {engine.strategy_id}"
        )

    def run_iteration(self) -> Dict[str, Any]:
        """Execute one iteration of pairs trading strategy.

        Runs the paper trading engine iteration and returns standardized
        results for the StrategyRunner.

        Returns:
            Dict with:
                - trades: Number of trades executed
                - signals: Number of signals generated
                - errors: List of errors if any
                - pairs_processed: Number of pairs processed
                - trades_rejected: Number of trades rejected by risk

        Raises:
            Exception: If iteration fails critically
        """
        try:
            # Run paper trading iteration
            iteration_trades = self.engine.run_iteration()

            # Count trades and rejections
            total_trades = 0
            trades_rejected = 0

            for pair_name, trades in iteration_trades.items():
                for trade in trades:
                    total_trades += 1
                    if trade.fill_status == "rejected":
                        trades_rejected += 1

            result = {
                "trades": total_trades - trades_rejected,
                "signals": total_trades,  # Each trade corresponds to a signal
                "errors": [],
                "pairs_processed": len(self.engine.active_pairs),
                "trades_rejected": trades_rejected,
                "open_positions": len(self.engine.positions),
            }

            logger.debug(
                f"Pairs iteration completed: {result}",
                extra={
                    "context": {
                        "strategy_id": self.engine.strategy_id,
                        "result": result,
                    }
                },
            )

            return result

        except Exception as e:
            logger.error(
                f"Pairs iteration failed: {e}",
                extra={"context": {"strategy_id": self.engine.strategy_id}},
                exc_info=True,
            )
            raise

    def get_status(self) -> Dict[str, Any]:
        """Get current strategy status.

        Returns:
            Dict with strategy state including:
                - portfolio_value: Current portfolio value
                - active_pairs: Number of active pairs
                - open_positions: Number of open positions
                - total_trades: Total trade count
                - positions: List of position details
                - exposure: Portfolio exposure metrics
        """
        try:
            status = self.engine.get_status()

            # Enrich with strategy-specific info
            status["strategy_type"] = "pairs_trading"
            status["lookback_window"] = self.engine.lookback_window

            return status

        except Exception as e:
            logger.error(
                f"Failed to get strategy status: {e}",
                extra={"context": {"strategy_id": self.engine.strategy_id}},
                exc_info=True,
            )
            return {
                "error": str(e),
                "strategy_type": "pairs_trading",
            }

    def shutdown(self) -> None:
        """Cleanup before strategy termination.

        Exports final trade history and closes any open positions.
        """
        try:
            logger.info(
                f"Shutting down pairs strategy: {self.engine.strategy_id}"
            )

            # Export trade history
            trades_df = self.engine.export_trades()
            if not trades_df.empty:
                # Save to logs directory
                from pathlib import Path

                logs_dir = Path("logs/strategies")
                logs_dir.mkdir(parents=True, exist_ok=True)

                filename = (
                    f"{self.engine.strategy_id}_trades_"
                    f"{trades_df['timestamp'].min().strftime('%Y%m%d')}_"
                    f"{trades_df['timestamp'].max().strftime('%Y%m%d')}.csv"
                )
                filepath = logs_dir / filename

                trades_df.to_csv(filepath, index=False)
                logger.info(
                    f"Trade history exported to {filepath} "
                    f"({len(trades_df)} trades)"
                )

            # Log final status
            final_status = self.get_status()
            logger.info(
                f"Final strategy status: {final_status}",
                extra={
                    "context": {
                        "strategy_id": self.engine.strategy_id,
                        "status": final_status,
                    }
                },
            )

        except Exception as e:
            logger.error(
                f"Error during shutdown: {e}",
                extra={"context": {"strategy_id": self.engine.strategy_id}},
                exc_info=True,
            )
