"""Validation script for pairs trading strategy.

Runs backtests on target pairs (KO/PEP, XOM/CVX, JPM/BAC, SPY/IVV, GLD/IAU)
using historical data to validate Sharpe > 1.0 and MaxDD < 15% requirements.
"""
import logging
import sys
from pathlib import Path

import pandas as pd

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.storage.parquet_storage import ParquetStorage
from src.strategies.pairs_trading import BacktestConfig, PairsBacktester

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Target pairs from issue specification
TARGET_PAIRS = [
    ("KO", "PEP", "equities"),  # Coca-Cola / PepsiCo
    ("XOM", "CVX", "equities"),  # Exxon Mobil / Chevron
    ("JPM", "BAC", "equities"),  # JPMorgan / Bank of America
    ("SPY", "IVV", "etfs"),  # S&P 500 trackers
    ("GLD", "IAU", "etfs"),  # Gold trackers
]


def load_pair_data(
    symbol1: str, symbol2: str, asset_class: str, storage: ParquetStorage
) -> tuple:
    """Load price data for a pair.

    Args:
        symbol1: First asset symbol.
        symbol2: Second asset symbol.
        asset_class: Asset class.
        storage: ParquetStorage instance.

    Returns:
        Tuple of (series1, series2) or None if data unavailable.
    """
    try:
        data1 = storage.read(asset_class, symbol1, frequency="1d")
        data2 = storage.read(asset_class, symbol2, frequency="1d")

        if data1 is None or data2 is None:
            logger.error(f"Data not found for {symbol1}/{symbol2}")
            return None

        # Align on common dates
        common_dates = data1.index.intersection(data2.index)

        if len(common_dates) < 252:
            logger.error(
                f"Insufficient data for {symbol1}/{symbol2}: {len(common_dates)} days"
            )
            return None

        series1 = data1.loc[common_dates, "Close"]
        series2 = data2.loc[common_dates, "Close"]

        logger.info(
            f"Loaded {symbol1}/{symbol2}: {len(series1)} days "
            f"({series1.index[0]} to {series1.index[-1]})"
        )

        return series1, series2

    except Exception as e:
        logger.error(f"Failed to load {symbol1}/{symbol2}: {e}")
        return None


def validate_pairs():
    """Run backtest validation on all target pairs.

    Acceptance criteria:
    - Sharpe ratio > 1.0
    - Maximum drawdown < 15%
    - Win rate > 50%
    - Positive out-of-sample returns
    """
    storage = ParquetStorage()

    config = BacktestConfig(
        train_pct=0.6,
        lookback_window=60,
        reestimate_every=20,
        initial_capital=100000,
    )

    backtester = PairsBacktester(config)

    results = []

    for symbol1, symbol2, asset_class in TARGET_PAIRS:
        pair_name = f"{symbol1}/{symbol2}"
        logger.info(f"\n{'='*60}")
        logger.info(f"BACKTESTING: {pair_name}")
        logger.info(f"{'='*60}")

        # Load data
        pair_data = load_pair_data(symbol1, symbol2, asset_class, storage)

        if pair_data is None:
            results.append(
                {
                    "pair": pair_name,
                    "status": "FAILED",
                    "reason": "Data unavailable",
                    "sharpe": None,
                    "max_dd": None,
                    "win_rate": None,
                    "trades": None,
                }
            )
            continue

        series1, series2 = pair_data

        # Run backtest
        try:
            result = backtester.backtest_pair(
                series1, series2, symbol1, symbol2
            )

            metrics = result.metrics

            # Check acceptance criteria
            sharpe = metrics.get("sharpe_ratio", 0)
            max_dd = metrics.get("max_drawdown", -1)
            win_rate = metrics.get("win_rate", 0)
            total_return = metrics.get("total_return", 0)
            trades = metrics.get("total_trades", 0)

            passed = (
                sharpe > 1.0
                and max_dd > -0.15
                and win_rate > 0.5
                and total_return > 0
            )

            status = "PASSED ✓" if passed else "FAILED ✗"

            logger.info(f"\nResults for {pair_name}:")
            logger.info(f"  Status: {status}")
            logger.info(f"  Sharpe Ratio: {sharpe:.2f} (target > 1.0)")
            logger.info(f"  Max Drawdown: {max_dd:.2%} (target < -15%)")
            logger.info(f"  Win Rate: {win_rate:.2%} (target > 50%)")
            logger.info(f"  Total Return: {total_return:.2%} (target > 0%)")
            logger.info(f"  Total Trades: {trades}")

            results.append(
                {
                    "pair": pair_name,
                    "status": status,
                    "sharpe": sharpe,
                    "max_dd": max_dd,
                    "win_rate": win_rate,
                    "total_return": total_return,
                    "trades": trades,
                }
            )

        except Exception as e:
            logger.error(f"Backtest failed for {pair_name}: {e}")
            results.append(
                {
                    "pair": pair_name,
                    "status": "ERROR",
                    "reason": str(e),
                    "sharpe": None,
                    "max_dd": None,
                    "win_rate": None,
                    "trades": None,
                }
            )

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info("VALIDATION SUMMARY")
    logger.info(f"{'='*60}\n")

    results_df = pd.DataFrame(results)
    logger.info(results_df.to_string(index=False))

    passed_count = sum(1 for r in results if "PASSED" in r["status"])
    logger.info(f"\nPairs passed: {passed_count}/{len(TARGET_PAIRS)}")

    # Determine overall status
    if passed_count >= 2:
        logger.info("\n✓ VALIDATION PASSED (>2 pairs meet criteria)")
        return True
    else:
        logger.warning(
            f"\n✗ VALIDATION INCOMPLETE ({passed_count}/5 pairs met criteria)"
        )
        logger.warning("Note: May need more historical data or pair tuning")
        return False


if __name__ == "__main__":
    validate_pairs()
