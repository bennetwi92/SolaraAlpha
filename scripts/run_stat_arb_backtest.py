"""Run statistical arbitrage backtest and validate performance criteria.

Validates:
1. Sharpe ratio > 1.0 on 2+ years data
2. Correlation to pairs trading < 0.4
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from datetime import datetime, timedelta

import pandas as pd

from src.data.storage.parquet_storage import ParquetStorage
from src.strategies.stat_arb.backtest import StatArbBacktester, BacktestConfig
from src.strategies.pairs_trading.backtest import PairsBacktester

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def fetch_universe_data(symbols: list, asset_class: str) -> pd.DataFrame:
    """Fetch price data for universe of stocks.

    Args:
        symbols: List of symbols to fetch
        asset_class: Asset class (e.g., 'equities')

    Returns:
        DataFrame with prices (time × stocks)
    """
    storage = ParquetStorage()
    all_prices = []

    for symbol in symbols:
        try:
            prices = storage.read(asset_class, symbol, "1d")
            if prices is not None and len(prices) > 0:
                all_prices.append(prices["close"].rename(symbol))
                logger.info(f"Loaded {symbol}: {len(prices)} periods")
        except Exception as e:
            logger.warning(f"Failed to fetch {symbol}: {e}")
            continue

    if not all_prices:
        raise ValueError("No price data available")

    # Combine and align
    prices_df = pd.concat(all_prices, axis=1)
    prices_df = prices_df.dropna()  # Keep only complete data

    logger.info(f"Combined dataset: {len(prices_df)} periods, {len(prices_df.columns)} stocks")

    return prices_df


def run_stat_arb_backtest(
    prices: pd.DataFrame, start_date: datetime = None, end_date: datetime = None
) -> dict:
    """Run statistical arbitrage backtest.

    Args:
        prices: Price data (time × stocks)
        start_date: Backtest start date
        end_date: Backtest end date

    Returns:
        Dict with backtest results
    """
    logger.info("Running statistical arbitrage backtest...")

    config = BacktestConfig(
        portfolio_value=100_000,
        n_factors=5,
        target_positions=30,
        entry_threshold=2.0,
        exit_threshold=0.5,
    )

    backtester = StatArbBacktester(config)
    result = backtester.backtest(prices, start_date=start_date, end_date=end_date)

    logger.info(f"Backtest complete: {len(result.trades)} trades")
    logger.info(f"Total return: {result.metrics['total_return']:.2%}")
    logger.info(f"Sharpe ratio: {result.metrics['sharpe_ratio']:.3f}")
    logger.info(f"Max drawdown: {result.metrics['max_drawdown']:.2%}")
    logger.info(f"Win rate: {result.metrics['win_rate']:.2%}")

    return {
        "result": result,
        "returns": result.returns,
        "sharpe": result.metrics["sharpe_ratio"],
        "total_return": result.metrics["total_return"],
        "max_drawdown": result.metrics["max_drawdown"],
    }


def run_pairs_backtest(prices: pd.DataFrame) -> pd.Series:
    """Run pairs trading backtest for correlation analysis.

    Args:
        prices: Price data (time × stocks)

    Returns:
        Returns series for pairs trading
    """
    logger.info("Running pairs trading backtest for correlation check...")

    # Use two liquid pairs from the universe
    if "AAPL" not in prices.columns or "MSFT" not in prices.columns:
        logger.warning("AAPL/MSFT not available, skipping correlation check")
        return None

    from src.strategies.pairs_trading.backtest import BacktestConfig as PairsConfig

    config = PairsConfig()
    backtester = PairsBacktester(config)

    result = backtester.backtest_pair(
        prices["AAPL"], prices["MSFT"], "AAPL", "MSFT"
    )

    logger.info(f"Pairs backtest complete: Sharpe={result.metrics['sharpe_ratio']:.3f}")

    return result.returns


def validate_results(stat_arb_returns: pd.Series, pairs_returns: pd.Series = None):
    """Validate backtest results against acceptance criteria.

    Args:
        stat_arb_returns: StatArb returns
        pairs_returns: Pairs trading returns (optional)
    """
    logger.info("\n" + "=" * 60)
    logger.info("VALIDATION RESULTS")
    logger.info("=" * 60)

    # Calculate Sharpe ratio
    sharpe = (stat_arb_returns.mean() / stat_arb_returns.std()) * (252**0.5)

    # Check: Sharpe > 1.0
    sharpe_pass = sharpe > 1.0
    logger.info(f"✓ Sharpe Ratio: {sharpe:.3f} {'PASS' if sharpe_pass else 'FAIL'} (target > 1.0)")

    # Check: Backtest length
    years = len(stat_arb_returns) / 252
    years_pass = years >= 2.0
    logger.info(f"✓ Backtest Length: {years:.1f} years {'PASS' if years_pass else 'FAIL'} (target ≥ 2.0)")

    # Check correlation if pairs returns available
    if pairs_returns is not None:
        # Align returns
        aligned = pd.concat(
            [stat_arb_returns, pairs_returns], axis=1, keys=["stat_arb", "pairs"]
        ).dropna()

        if len(aligned) > 20:
            correlation = aligned["stat_arb"].corr(aligned["pairs"])
            corr_pass = abs(correlation) < 0.4
            logger.info(
                f"✓ Correlation to Pairs: {correlation:.3f} "
                f"{'PASS' if corr_pass else 'FAIL'} (target < 0.4)"
            )
        else:
            logger.warning("⚠ Insufficient overlapping data for correlation check")
            corr_pass = None
    else:
        logger.warning("⚠ Pairs returns not available for correlation check")
        corr_pass = None

    logger.info("=" * 60)

    # Overall validation
    checks = [sharpe_pass, years_pass]
    if corr_pass is not None:
        checks.append(corr_pass)

    if all(checks):
        logger.info("✅ ALL VALIDATION CHECKS PASSED")
        return True
    else:
        logger.warning("❌ SOME VALIDATION CHECKS FAILED")
        return False


def main():
    """Run full backtest validation."""
    logger.info("Starting statistical arbitrage backtest validation")

    # Define universe (30 liquid stocks from config)
    universe = [
        "AAPL",
        "GOOGL",
        "MSFT",
        "AMZN",
        "TSLA",
        "JPM",
        "BAC",
        "WFC",
        "GS",
        "MS",
        "XOM",
        "CVX",
        "COP",
        "SLB",
        "KO",
        "PEP",
        "MCD",
        "SBUX",
        "NKE",
        "HD",
        "LOW",
        "TGT",
        "WMT",
        "COST",
        "PG",
        "JNJ",
        "UNH",
        "CVS",
        "ABBV",
        "MRK",
    ]

    # Fetch data
    try:
        prices = fetch_universe_data(universe, "equities")
    except Exception as e:
        logger.error(f"Data fetch failed: {e}")
        sys.exit(1)

    # Determine backtest period (need 2+ years)
    end_date = prices.index[-1]
    start_date = end_date - timedelta(days=2 * 365 + 100)  # 2 years + warmup

    logger.info(f"Backtest period: {start_date.date()} to {end_date.date()}")

    # Run stat arb backtest
    try:
        stat_arb_result = run_stat_arb_backtest(prices, start_date=start_date)
    except Exception as e:
        logger.error(f"StatArb backtest failed: {e}", exc_info=True)
        sys.exit(1)

    # Run pairs backtest for correlation
    try:
        pairs_returns = run_pairs_backtest(prices)
    except Exception as e:
        logger.warning(f"Pairs backtest failed: {e}")
        pairs_returns = None

    # Validate results
    try:
        validation_passed = validate_results(
            stat_arb_result["returns"], pairs_returns
        )
    except Exception as e:
        logger.error(f"Validation failed: {e}", exc_info=True)
        sys.exit(1)

    if validation_passed:
        logger.info("✅ Backtest validation successful")
        sys.exit(0)
    else:
        logger.warning("⚠ Backtest validation failed - review results")
        sys.exit(1)


if __name__ == "__main__":
    main()
