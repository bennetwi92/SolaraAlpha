"""Debug backtest to understand why no trades are generated."""
import logging
from datetime import datetime, timedelta
import numpy as np

from src.data.ingest.yfinance_ingestor import YFinanceIngestor
from .spread import SpreadCalculator
from .signals import SignalGenerator, SignalConfig

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def debug_backtest():
    """Debug the backtest process step by step."""
    # Fetch data
    ingestor = YFinanceIngestor()
    end_date = datetime.now()
    start_date = end_date - timedelta(days=730)

    spy_data = ingestor.fetch_ohlcv('SPY', start_date, end_date, frequency='1d')
    ivv_data = ingestor.fetch_ohlcv('IVV', start_date, end_date, frequency='1d')

    spy_close = spy_data['Close']
    ivv_close = ivv_data['Close']

    common_idx = spy_close.index.intersection(ivv_close.index)
    spy_close = spy_close.loc[common_idx]
    ivv_close = ivv_close.loc[common_idx]

    # Split train/test (60/40)
    split_idx = int(len(spy_close) * 0.6)
    test_spy = spy_close.iloc[split_idx:]
    test_ivv = ivv_close.iloc[split_idx:]

    logger.info(f"Test period: {len(test_spy)} days ({test_spy.index[0]} to {test_spy.index[-1]})")

    # Calculate spread
    calc = SpreadCalculator(lookback_window=60)
    spread_df = calc.get_spread_history(test_spy, test_ivv, method='ratio')

    logger.info("\nSpread statistics on test period:")
    logger.info(f"  Min z-score: {spread_df['z_score'].min():.2f}")
    logger.info(f"  Max z-score: {spread_df['z_score'].max():.2f}")
    logger.info(f"  Mean z-score: {spread_df['z_score'].mean():.2f}")

    # Check for entry signals
    signal_config = SignalConfig(
        entry_threshold=1.5,
        exit_threshold=0.5,
        stop_loss_threshold=2.5,
        min_half_life=0.1,
        max_half_life=30.0
    )

    # Calculate half-life
    half_life = calc.calculate_half_life(spread_df['spread'].dropna())
    logger.info(f"\nHalf-life: {half_life:.2f} days")
    logger.info(f"Half-life in range [{signal_config.min_half_life}, {signal_config.max_half_life}]? {signal_config.min_half_life <= half_life <= signal_config.max_half_life}")

    # Check entry signals
    z_scores = spread_df['z_score'].dropna()
    long_signals = (z_scores <= -signal_config.entry_threshold).sum()
    short_signals = (z_scores >= signal_config.entry_threshold).sum()

    logger.info("\nPotential signals:")
    logger.info(f"  Long signals (z <= -1.5): {long_signals}")
    logger.info(f"  Short signals (z >= 1.5): {short_signals}")

    # Generate actual signals with signal generator
    signal_gen = SignalGenerator(config=signal_config)
    signals_generated = []

    for i in range(60, len(spread_df)):
        z_score = spread_df['z_score'].iloc[i]
        spread = spread_df['spread'].iloc[i]

        if np.isnan(z_score) or np.isnan(spread):
            continue

        signal = signal_gen.generate_signal(
            z_score=z_score,
            spread=spread,
            half_life=half_life,
            timestamp=spread_df.index[i],
            current_position=None
        )

        if signal.signal_type.value != 'no_signal':
            signals_generated.append({
                'date': spread_df.index[i],
                'signal': signal.signal_type.value,
                'z_score': z_score,
                'reason': signal.reason
            })

    logger.info(f"\nActual signals generated: {len(signals_generated)}")
    if signals_generated:
        for sig in signals_generated[:10]:  # Show first 10
            logger.info(f"  {sig['date'].date()}: {sig['signal']}, z={sig['z_score']:.2f}, reason={sig['reason']}")


if __name__ == '__main__':
    debug_backtest()
