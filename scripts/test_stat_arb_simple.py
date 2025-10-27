"""Simple test of statistical arbitrage strategy with minimal synthetic data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging

import numpy as np
import pandas as pd

from src.strategies.stat_arb.backtest import StatArbBacktester, BacktestConfig

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def generate_simple_data(n_stocks=30, n_periods=600):
    """Generate simple synthetic data with realistic price movements."""
    np.random.seed(42)

    logger.info(f"Generating data: {n_stocks} stocks, {n_periods} periods")

    # Generate daily returns (realistic: ~0-2% daily vol)
    daily_returns = np.random.randn(n_periods, n_stocks) * 0.01

    # Add common factor (market)
    market_factor = np.random.randn(n_periods) * 0.008
    for i in range(n_stocks):
        factor_loading = 0.5 + np.random.rand() * 0.5  # 0.5 to 1.0
        daily_returns[:, i] += market_factor * factor_loading

    # Convert to prices starting at 100
    prices = np.zeros((n_periods, n_stocks))
    prices[0] = 100
    for t in range(1, n_periods):
        prices[t] = prices[t - 1] * (1 + daily_returns[t])

    # Create DataFrame
    dates = pd.date_range("2023-01-01", periods=n_periods, freq="D")
    symbols = [f"STOCK{i:02d}" for i in range(n_stocks)]

    prices_df = pd.DataFrame(prices, index=dates, columns=symbols)

    logger.info(f"Generated prices: {prices_df.shape}")
    logger.info(
        f"Price range: ${prices_df.min().min():.2f} - ${prices_df.max().max():.2f}"
    )
    logger.info(f"Mean price: ${prices_df.mean().mean():.2f}")

    return prices_df


def main():
    """Run simple backtest."""
    logger.info("Starting simple statistical arbitrage test")

    # Generate data
    prices = generate_simple_data(n_stocks=30, n_periods=600)

    # Run backtest
    logger.info("Running backtest...")

    config = BacktestConfig(
        portfolio_value=100_000,
        n_factors=3,
        target_positions=20,
        min_positions=10,  # Lower minimum
        entry_threshold=1.5,  # Lower threshold for testing
        exit_threshold=0.3,
        stop_threshold=3.0,
    )

    backtester = StatArbBacktester(config)

    # Use last 504 days (2 years)
    start_idx = len(prices) - 504
    result = backtester.backtest(prices.iloc[start_idx:])

    logger.info(f"\nBacktest Results:")
    logger.info(f"{'=' * 60}")
    logger.info(f"Total trades: {len(result.trades)}")
    logger.info(f"Total return: {result.metrics['total_return']:.2%}")
    logger.info(f"Sharpe ratio: {result.metrics['sharpe_ratio']:.3f}")
    logger.info(f"Max drawdown: {result.metrics['max_drawdown']:.2%}")
    logger.info(f"Equity curve length: {len(result.equity_curve)}")
    logger.info(f"Returns length: {len(result.returns)}")
    logger.info(f"{'=' * 60}")

    if len(result.trades) > 0:
        logger.info("✅ Strategy executed trades successfully")
        logger.info(f"First trade: {result.trades[0]}")
        logger.info(f"Last trade: {result.trades[-1]}")
    else:
        logger.warning("⚠ No trades executed")

    # Show sample of trades
    if len(result.trades) > 0:
        logger.info(f"\nSample trades (first 5):")
        for trade in result.trades[:5]:
            logger.info(
                f"  {trade.timestamp.date()}: {trade.signal_type.value} {trade.symbol} "
                f"qty={trade.quantity} @ ${trade.price:.2f}"
            )


if __name__ == "__main__":
    main()
