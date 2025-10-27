"""Strategy adapter for statistical arbitrage to integrate with StrategyRunner.

Wraps PaperTradingEngine in the Strategy interface for daemon execution.
"""

import logging
from typing import Any, Dict

from ...execution.strategy_runner import Strategy
from .paper_trading import PaperTradingEngine

logger = logging.getLogger(__name__)


class StatArbStrategy(Strategy):
    """Strategy adapter for statistical arbitrage.

    Implements the Strategy ABC to enable integration with StrategyRunner daemon.

    Example usage:
        >>> universe = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'JPM', 'BAC']
        >>> engine = PaperTradingEngine(
        ...     universe=universe,
        ...     asset_class='equities',
        ...     portfolio_value=100_000,
        ...     strategy_id='stat_arb_1',
        ... )
        >>> strategy = StatArbStrategy(engine)
        >>>
        >>> from ...execution.strategy_runner import StrategyRunner, StrategyConfig, RunMode
        >>> config = StrategyConfig(
        ...     strategy_id='stat_arb_1',
        ...     strategy_name='Statistical Arbitrage - Equities',
        ...     run_mode=RunMode.MARKET_HOURS,
        ...     interval_seconds=3600,  # Run hourly
        ... )
        >>> runner = StrategyRunner()
        >>> runner.register_strategy(strategy, config)
        >>> runner.start()  # Blocks until SIGTERM
    """

    def __init__(self, engine: PaperTradingEngine):
        """Initialize strategy adapter.

        Args:
            engine: PaperTradingEngine instance to wrap
        """
        self.engine = engine
        self.iteration_count = 0

        logger.info(
            f"Initialized StatArbStrategy adapter for strategy_id={engine.strategy_id}"
        )

    def run_iteration(self) -> Dict[str, Any]:
        """Execute one trading iteration.

        Returns:
            Dict with iteration results:
                - trades: Number of trades executed
                - positions: Current number of positions
                - portfolio_value: Current portfolio value
                - errors: List of error messages (empty if successful)

        Raises:
            Exception: If iteration fails critically
        """
        self.iteration_count += 1

        try:
            # Run engine iteration
            result = self.engine.run_iteration()

            # Get current status
            status = self.engine.get_status()

            # Extract trade count
            trades = result.get("trades", [])

            logger.info(
                f"StatArb iteration {self.iteration_count}: "
                f"{len(trades)} trades, "
                f"{status['num_positions']} positions, "
                f"${status['portfolio_value']:.2f} value"
            )

            return {
                "trades": len(trades),
                "positions": status["num_positions"],
                "portfolio_value": status["portfolio_value"],
                "total_return": status["total_return"],
                "long_exposure": status["long_exposure"],
                "short_exposure": status["short_exposure"],
                "net_exposure": status["net_exposure"],
                "errors": [],
            }

        except Exception as e:
            logger.error(f"StatArb iteration failed: {e}", exc_info=True)
            raise

    def get_status(self) -> Dict[str, Any]:
        """Get current strategy status.

        Returns:
            Dict with strategy state
        """
        status = self.engine.get_status()

        return {
            "strategy_id": self.engine.strategy_id,
            "iteration_count": self.iteration_count,
            "portfolio_value": status["portfolio_value"],
            "total_return": status["total_return"],
            "num_positions": status["num_positions"],
            "num_trades": status["num_trades"],
            "long_exposure": status["long_exposure"],
            "short_exposure": status["short_exposure"],
            "gross_exposure": status["gross_exposure"],
            "net_exposure": status["net_exposure"],
            "factor_model_fitted": status["factor_model_fitted"],
        }

    def shutdown(self) -> None:
        """Shutdown strategy gracefully."""
        logger.info(f"Shutting down StatArbStrategy (iteration {self.iteration_count})")

        try:
            self.engine.shutdown()
            logger.info("StatArbStrategy shutdown complete")
        except Exception as e:
            logger.error(f"Error during StatArb shutdown: {e}", exc_info=True)
