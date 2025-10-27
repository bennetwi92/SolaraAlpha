"""Fetch data for statistical arbitrage backtest."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging

from src.data.pipeline import DataPipeline

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    """Fetch data for stat arb universe."""
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

    pipeline = DataPipeline()

    for symbol in universe:
        try:
            logger.info(f"Fetching {symbol}...")
            result = pipeline.fetch_and_store(symbol, "equities", force_refetch=True)
            logger.info(f"✓ {symbol}: {result.is_valid}")
        except Exception as e:
            logger.error(f"✗ {symbol}: {e}")

    logger.info("Data fetch complete")


if __name__ == "__main__":
    main()
