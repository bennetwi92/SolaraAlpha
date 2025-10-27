"""Analyze spread characteristics for ETF pairs."""
import logging
from datetime import datetime, timedelta

from src.data.ingest.yfinance_ingestor import YFinanceIngestor
from .spread import SpreadCalculator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def analyze_spread(symbol1, symbol2, lookback_window=60):
    """Analyze spread characteristics between two ETFs."""
    logger.info(f"\n{'='*80}")
    logger.info(f"SPREAD ANALYSIS: {symbol1}/{symbol2}")
    logger.info(f"{'='*80}")

    # Fetch data
    ingestor = YFinanceIngestor()
    end_date = datetime.now()
    start_date = end_date - timedelta(days=730)

    data1 = ingestor.fetch_ohlcv(symbol1, start_date, end_date, frequency='1d')
    data2 = ingestor.fetch_ohlcv(symbol2, start_date, end_date, frequency='1d')

    # Align data
    prices1 = data1['Close']
    prices2 = data2['Close']
    common_idx = prices1.index.intersection(prices2.index)
    prices1 = prices1.loc[common_idx]
    prices2 = prices2.loc[common_idx]

    logger.info(f"Data: {len(prices1)} days")

    # Calculate spread
    calc = SpreadCalculator(lookback_window=lookback_window)
    spread_df = calc.get_spread_history(prices1, prices2, method='ratio')

    # Statistics
    z_scores = spread_df['z_score'].dropna()

    logger.info("\nZ-Score Statistics:")
    logger.info(f"  Mean:     {z_scores.mean():.4f}")
    logger.info(f"  Std:      {z_scores.std():.4f}")
    logger.info(f"  Min:      {z_scores.min():.4f}")
    logger.info(f"  Max:      {z_scores.max():.4f}")
    logger.info(f"  Median:   {z_scores.median():.4f}")

    # Percentiles
    logger.info("\nPercentiles:")
    for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        val = z_scores.quantile(p/100)
        logger.info(f"  {p:3d}%:    {val:7.4f}")

    # Entry opportunities
    logger.info("\nEntry Opportunities (±2σ):")
    long_entries = (z_scores <= -2.0).sum()
    short_entries = (z_scores >= 2.0).sum()
    logger.info(f"  Long spread (z <= -2.0):  {long_entries} days ({long_entries/len(z_scores)*100:.1f}%)")
    logger.info(f"  Short spread (z >= 2.0):  {short_entries} days ({short_entries/len(z_scores)*100:.1f}%)")

    # More relaxed thresholds
    logger.info("\nEntry Opportunities (±1.5σ):")
    long_entries_15 = (z_scores <= -1.5).sum()
    short_entries_15 = (z_scores >= 1.5).sum()
    logger.info(f"  Long spread (z <= -1.5):  {long_entries_15} days ({long_entries_15/len(z_scores)*100:.1f}%)")
    logger.info(f"  Short spread (z >= 1.5):  {short_entries_15} days ({short_entries_15/len(z_scores)*100:.1f}%)")

    # Calculate half-life
    spread = spread_df['spread'].dropna()
    half_life = calc.calculate_half_life(spread)
    logger.info(f"\nMean Reversion Half-Life: {half_life:.2f} days")

    # Recent extremes
    logger.info("\nRecent Extremes (last 60 days):")
    recent_z = z_scores.tail(60)
    logger.info(f"  Min z-score: {recent_z.min():.4f}")
    logger.info(f"  Max z-score: {recent_z.max():.4f}")

    logger.info(f"{'='*80}\n")

    return spread_df


if __name__ == '__main__':
    # Analyze SPY/IVV
    analyze_spread('SPY', 'IVV')

    # Analyze GLD/IAU
    analyze_spread('GLD', 'IAU')
