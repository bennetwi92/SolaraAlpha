"""Run backtest for ETF arbitrage strategy and validate performance."""
import logging
from datetime import datetime, timedelta

from src.data.ingest.yfinance_ingestor import YFinanceIngestor
from .backtest import ETFArbBacktester, BacktestConfig
from .signals import SignalConfig
from .position_sizing import PositionSizingConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_spy_ivv_backtest():
    """Run backtest on SPY/IVV pair and print results."""
    logger.info("=" * 80)
    logger.info("ETF ARBITRAGE BACKTEST - SPY/IVV")
    logger.info("=" * 80)

    # Fetch data
    logger.info("Fetching historical data...")
    ingestor = YFinanceIngestor()

    end_date = datetime.now()
    start_date = end_date - timedelta(days=730)  # 2 years of data

    spy_data = ingestor.fetch_ohlcv('SPY', start_date, end_date, frequency='1d')
    ivv_data = ingestor.fetch_ohlcv('IVV', start_date, end_date, frequency='1d')

    logger.info(f"SPY data: {len(spy_data)} days")
    logger.info(f"IVV data: {len(ivv_data)} days")

    # Align data
    spy_close = spy_data['Close']
    ivv_close = ivv_data['Close']

    # Ensure same index
    common_idx = spy_close.index.intersection(ivv_close.index)
    spy_close = spy_close.loc[common_idx]
    ivv_close = ivv_close.loc[common_idx]

    logger.info(f"Aligned data: {len(spy_close)} days")

    # Configure backtest
    backtest_config = BacktestConfig(
        train_pct=0.6,
        lookback_window=60,
        reestimate_every=20,
        commission_pct=0.001,
        slippage_pct=0.0005,
        initial_capital=100000.0
    )

    signal_config = SignalConfig(
        entry_threshold=1.5,  # Lower to 1.5σ for more opportunities
        exit_threshold=0.5,
        stop_loss_threshold=2.5,  # Tighter stop loss
        min_half_life=0.1,  # Allow very fast mean reversion (SPY/IVV revert quickly)
        max_half_life=30.0
    )

    position_config = PositionSizingConfig(
        method='kelly',
        max_position_pct=0.20,
        kelly_fraction=0.5,
        fixed_fraction=0.10
    )

    # Run backtest
    logger.info("\nRunning backtest...")
    backtester = ETFArbBacktester(
        config=backtest_config,
        signal_config=signal_config,
        position_config=position_config
    )

    result = backtester.backtest_pair(
        series1=spy_close,
        series2=ivv_close,
        symbol1='SPY',
        symbol2='IVV'
    )

    # Print results
    logger.info("\n" + "=" * 80)
    logger.info("BACKTEST RESULTS")
    logger.info("=" * 80)

    metrics = result.metrics

    logger.info("\nPerformance Metrics:")
    logger.info(f"  Total Return:        {metrics['total_return']:.2%}")
    logger.info(f"  Annualized Return:   {metrics['annualized_return']:.2%}")
    logger.info(f"  Sharpe Ratio:        {metrics['sharpe_ratio']:.2f}")
    logger.info(f"  Max Drawdown:        {metrics['max_drawdown']:.2%}")

    logger.info("\nTrade Statistics:")
    logger.info(f"  Number of Trades:    {metrics['num_trades']}")
    logger.info(f"  Win Rate:            {metrics['win_rate']:.2%}")
    logger.info(f"  Average Win:         ${metrics['avg_win']:.2f}")
    logger.info(f"  Average Loss:        ${metrics['avg_loss']:.2f}")
    logger.info(f"  Profit Factor:       {metrics['profit_factor']:.2f}")

    logger.info("\nCapital:")
    logger.info(f"  Initial Capital:     ${backtest_config.initial_capital:,.2f}")
    logger.info(f"  Final Equity:        ${metrics['final_equity']:,.2f}")
    logger.info(f"  Total Costs:         ${metrics['total_costs']:,.2f}")

    # Validate acceptance criteria
    logger.info("\n" + "=" * 80)
    logger.info("ACCEPTANCE CRITERIA VALIDATION")
    logger.info("=" * 80)

    criteria_passed = True

    # Check Sharpe ratio > 1.5
    if metrics['sharpe_ratio'] >= 1.5:
        logger.info(f"✓ Sharpe ratio {metrics['sharpe_ratio']:.2f} >= 1.5")
    else:
        logger.warning(f"✗ Sharpe ratio {metrics['sharpe_ratio']:.2f} < 1.5 (TARGET NOT MET)")
        criteria_passed = False

    # Check positive P&L
    if metrics['total_return'] > 0:
        logger.info(f"✓ Positive total return: {metrics['total_return']:.2%}")
    else:
        logger.warning(f"✗ Negative total return: {metrics['total_return']:.2%}")
        criteria_passed = False

    # Check max drawdown < 10%
    if metrics['max_drawdown'] > -0.10:
        logger.info(f"✓ Max drawdown {metrics['max_drawdown']:.2%} > -10%")
    else:
        logger.warning(f"✗ Max drawdown {metrics['max_drawdown']:.2%} < -10% (TARGET NOT MET)")
        criteria_passed = False

    if criteria_passed:
        logger.info("\n🎉 ALL ACCEPTANCE CRITERIA PASSED!")
    else:
        logger.warning("\n⚠️  SOME ACCEPTANCE CRITERIA NOT MET")

    logger.info("=" * 80)

    return result, criteria_passed


if __name__ == '__main__':
    result, passed = run_spy_ivv_backtest()
