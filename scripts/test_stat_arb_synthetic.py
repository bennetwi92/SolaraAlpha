"""Test statistical arbitrage strategy with synthetic data.

Generates synthetic price data with known factor structure and mean-reverting residuals
to validate the strategy implementation.
"""

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


def generate_synthetic_data(
    n_stocks=30, n_periods=600, n_factors=3, residual_mean_reversion_speed=0.1
):
    """Generate synthetic price data with factor structure and mean-reverting residuals.

    Args:
        n_stocks: Number of stocks
        n_periods: Number of time periods
        n_factors: Number of common factors
        residual_mean_reversion_speed: Speed of residual mean reversion (higher = faster)

    Returns:
        DataFrame with stock prices
    """
    np.random.seed(42)

    logger.info(
        f"Generating synthetic data: {n_stocks} stocks, {n_periods} periods, "
        f"{n_factors} factors"
    )

    # Generate common factors (random walks)
    factors = np.cumsum(np.random.randn(n_periods, n_factors) * 0.01, axis=0)

    # Generate factor loadings (betas) for each stock
    factor_loadings = np.random.randn(n_stocks, n_factors) * 0.5 + 1.0

    # Generate mean-reverting residuals using Ornstein-Uhlenbeck process
    residuals = np.zeros((n_periods, n_stocks))
    residuals[0] = np.random.randn(n_stocks) * 0.02

    for t in range(1, n_periods):
        # OU process: dX = theta * (mu - X) * dt + sigma * dW
        # Here: mu = 0, theta = mean_reversion_speed, sigma = 0.01
        drift = -residual_mean_reversion_speed * residuals[t - 1]
        diffusion = np.random.randn(n_stocks) * 0.01
        residuals[t] = residuals[t - 1] + drift + diffusion

    # Generate stock returns = factor returns + residuals
    factor_returns = factors @ factor_loadings.T  # (n_periods × n_stocks)
    total_returns = factor_returns + residuals

    # Convert returns to prices (starting at 100)
    prices = 100 * np.exp(np.cumsum(total_returns, axis=0))

    # Create DataFrame
    dates = pd.date_range("2023-01-01", periods=n_periods, freq="D")
    symbols = [f"STOCK{i:02d}" for i in range(n_stocks)]

    prices_df = pd.DataFrame(prices, index=dates, columns=symbols)

    logger.info(f"Generated prices: {prices_df.shape}")
    logger.info(f"Price range: ${prices_df.min().min():.2f} - ${prices_df.max().max():.2f}")

    return prices_df


def run_backtest(prices: pd.DataFrame) -> dict:
    """Run stat arb backtest on synthetic data.

    Args:
        prices: Price DataFrame

    Returns:
        Dict with results
    """
    logger.info("Running statistical arbitrage backtest...")

    config = BacktestConfig(
        portfolio_value=100_000,
        n_factors=3,
        target_positions=20,
        entry_threshold=2.0,
        exit_threshold=0.5,
        stop_threshold=3.5,
    )

    backtester = StatArbBacktester(config)

    # Use last 2 years (504 trading days) for backtest
    start_idx = len(prices) - 504
    result = backtester.backtest(prices.iloc[start_idx:])

    logger.info(f"\nBacktest Results:")
    logger.info(f"{'=' * 60}")
    logger.info(f"Total trades: {len(result.trades)}")
    logger.info(f"Total return: {result.metrics['total_return']:.2%}")
    logger.info(f"Sharpe ratio: {result.metrics['sharpe_ratio']:.3f}")
    logger.info(f"Max drawdown: {result.metrics['max_drawdown']:.2%}")
    logger.info(f"Win rate: {result.metrics['win_rate']:.2%}")
    logger.info(f"Volatility: {result.metrics['volatility']:.2%}")
    logger.info(f"Avg daily return: {result.metrics['avg_daily_return']:.4%}")
    logger.info(f"{'=' * 60}")

    return {
        "result": result,
        "sharpe": result.metrics["sharpe_ratio"],
        "total_return": result.metrics["total_return"],
        "max_drawdown": result.metrics["max_drawdown"],
    }


def validate_results(backtest_result: dict) -> bool:
    """Validate backtest meets acceptance criteria.

    Args:
        backtest_result: Dict with backtest results

    Returns:
        True if all checks pass
    """
    logger.info("\nValidation:")
    logger.info(f"{'=' * 60}")

    sharpe = backtest_result["sharpe"]
    sharpe_pass = sharpe > 1.0
    logger.info(f"✓ Sharpe Ratio: {sharpe:.3f} {'PASS' if sharpe_pass else 'FAIL'} (target > 1.0)")

    # Check backtest length (should be 2 years = 504 days)
    result = backtest_result["result"]
    years = len(result.returns) / 252
    years_pass = years >= 2.0
    logger.info(f"✓ Backtest Length: {years:.1f} years {'PASS' if years_pass else 'FAIL'} (target ≥ 2.0)")

    # Check that trades were executed
    num_trades = len(result.trades)
    trades_pass = num_trades > 0
    logger.info(f"✓ Trade Execution: {num_trades} trades {'PASS' if trades_pass else 'FAIL'} (target > 0)")

    logger.info(f"{'=' * 60}")

    all_pass = sharpe_pass and years_pass and trades_pass

    if all_pass:
        logger.info("✅ ALL VALIDATION CHECKS PASSED")
    else:
        logger.warning("❌ SOME VALIDATION CHECKS FAILED")

    return all_pass


def main():
    """Run synthetic data backtest validation."""
    logger.info("Starting statistical arbitrage validation with synthetic data")

    # Generate synthetic data
    prices = generate_synthetic_data(
        n_stocks=30, n_periods=600, n_factors=3, residual_mean_reversion_speed=0.15
    )

    # Run backtest
    try:
        backtest_result = run_backtest(prices)
    except Exception as e:
        logger.error(f"Backtest failed: {e}", exc_info=True)
        sys.exit(1)

    # Validate
    try:
        validation_passed = validate_results(backtest_result)
    except Exception as e:
        logger.error(f"Validation failed: {e}", exc_info=True)
        sys.exit(1)

    if validation_passed:
        logger.info("\n✅ Synthetic backtest validation successful")
        logger.info("Strategy implementation is working correctly")
        sys.exit(0)
    else:
        logger.warning("\n⚠ Validation failed - review results")
        sys.exit(1)


if __name__ == "__main__":
    main()
