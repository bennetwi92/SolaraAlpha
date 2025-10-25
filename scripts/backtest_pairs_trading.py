#!/usr/bin/env python3
"""
Pairs Trading Backtest Script

Demonstrates the PairsTradingStrategy on liquid ETF pairs:
- SPY/IWM (Large cap vs Small cap)
- GLD/SLV (Gold vs Silver)
- XLE/XLF (Energy vs Financials)

Generates comprehensive performance metrics and visualizations.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

from src.strategy.pairs_trading import PairsTradingStrategy
from src.strategy.base import StrategyConfig
from src.strategy.backtest_runner import StrategyBacktestRunner
from src.strategy.performance import PerformanceTracker
from src.data.market_client import MarketDataClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_pairs_backtest(
    start_date: str = "2020-01-01",
    end_date: str = "2024-12-31",
    initial_capital: float = 100000.0
):
    """
    Run pairs trading backtest on ETF pairs.

    Args:
        start_date: Backtest start date (YYYY-MM-DD)
        end_date: Backtest end date (YYYY-MM-DD)
        initial_capital: Starting capital

    Returns:
        Performance metrics dictionary
    """
    logger.info("=" * 80)
    logger.info("PAIRS TRADING BACKTEST")
    logger.info("=" * 80)

    # ETF universe: liquid pairs with diverse sectors
    symbols = [
        'SPY',   # S&P 500
        'IWM',   # Russell 2000 Small Cap
        'GLD',   # Gold
        'SLV',   # Silver
        'XLE',   # Energy Sector
        'XLF',   # Financial Sector
        'QQQ',   # Nasdaq 100
        'DIA',   # Dow Jones
    ]

    # Strategy configuration
    config = StrategyConfig(
        name="PairsTradingETF",
        version="1.0.0",
        parameters={
            'lookback_period': 60,           # 60 days for cointegration
            'zscore_lookback': 30,           # 30 days for z-score
            'z_entry': 2.0,                  # Enter at 2 std dev
            'z_exit': 0.5,                   # Exit at 0.5 std dev
            'max_holding_days': 30,          # Max 30 day hold
            'correlation_threshold': 0.7,    # Min 0.7 correlation
            'coint_pvalue_threshold': 0.05,  # 5% significance
            'position_size_pct': 0.2,        # 20% per pair
            'max_pairs': 3,                  # Max 3 concurrent pairs
            'rebalance_hedge_ratio_days': 20,
            'pair_selection_frequency_days': 30
        },
        symbols=symbols,
        max_positions=6,  # 3 pairs × 2 legs
        enable_short=True,
        rebalance_frequency='1D'
    )

    logger.info(f"\nStrategy Configuration:")
    logger.info(f"  Name: {config.name} v{config.version}")
    logger.info(f"  Universe: {len(symbols)} ETFs")
    logger.info(f"  Parameters:")
    for key, value in config.parameters.items():
        logger.info(f"    {key}: {value}")

    # Initialize strategy
    strategy = PairsTradingStrategy(config)

    # Initialize data client
    data_client = MarketDataClient()

    logger.info(f"\nFetching market data...")
    logger.info(f"  Period: {start_date} to {end_date}")

    # Fetch historical data
    data = {}
    for symbol in symbols:
        try:
            df = data_client.get_ohlcv(
                symbol,
                start=start_date,
                end=end_date,
                interval='1d'
            )
            data[symbol] = df
            logger.info(f"  {symbol}: {len(df)} bars")
        except Exception as e:
            logger.error(f"  Failed to fetch {symbol}: {e}")

    if len(data) < 2:
        raise ValueError("Need at least 2 symbols with data for pairs trading")

    # Initialize backtest runner
    runner = StrategyBacktestRunner(
        strategy=strategy,
        data=data,
        initial_capital=initial_capital,
        commission_pct=0.001,  # 0.1% commission
        slippage_pct=0.001     # 0.1% slippage
    )

    logger.info(f"\nRunning backtest...")
    logger.info(f"  Initial Capital: ${initial_capital:,.2f}")
    logger.info(f"  Commission: 0.1%")
    logger.info(f"  Slippage: 0.1%")

    # Run backtest
    results = runner.run()

    # Extract performance metrics
    logger.info("\n" + "=" * 80)
    logger.info("BACKTEST RESULTS")
    logger.info("=" * 80)

    equity_curve = results['equity_curve']
    trades = results['trades']
    positions_history = results['positions']

    # Calculate metrics
    final_value = equity_curve.iloc[-1]
    total_return = (final_value - initial_capital) / initial_capital
    total_return_pct = total_return * 100

    # Daily returns
    daily_returns = equity_curve.pct_change().dropna()

    # Sharpe ratio (annualized, assuming 252 trading days)
    if len(daily_returns) > 0 and daily_returns.std() > 0:
        sharpe_ratio = np.sqrt(252) * daily_returns.mean() / daily_returns.std()
    else:
        sharpe_ratio = 0.0

    # Maximum drawdown
    cumulative = (1 + daily_returns).cumprod()
    running_max = cumulative.expanding().max()
    drawdown = (cumulative - running_max) / running_max
    max_drawdown = drawdown.min()
    max_drawdown_pct = max_drawdown * 100

    # Win rate
    if len(trades) > 0:
        winning_trades = [t for t in trades if t['pnl'] > 0]
        losing_trades = [t for t in trades if t['pnl'] < 0]
        win_rate = len(winning_trades) / len(trades) if len(trades) > 0 else 0.0

        # Profit factor
        gross_profit = sum(t['pnl'] for t in winning_trades)
        gross_loss = abs(sum(t['pnl'] for t in losing_trades))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.inf

        avg_win = gross_profit / len(winning_trades) if len(winning_trades) > 0 else 0
        avg_loss = gross_loss / len(losing_trades) if len(losing_trades) > 0 else 0
    else:
        win_rate = 0.0
        profit_factor = 0.0
        avg_win = 0.0
        avg_loss = 0.0

    # Print summary
    logger.info(f"\nPerformance Summary:")
    logger.info(f"  Final Portfolio Value: ${final_value:,.2f}")
    logger.info(f"  Total Return: {total_return_pct:.2f}%")
    logger.info(f"  Sharpe Ratio: {sharpe_ratio:.2f}")
    logger.info(f"  Max Drawdown: {max_drawdown_pct:.2f}%")
    logger.info(f"  Total Trades: {len(trades)}")
    logger.info(f"  Win Rate: {win_rate * 100:.2f}%")
    logger.info(f"  Profit Factor: {profit_factor:.2f}")
    logger.info(f"  Avg Win: ${avg_win:,.2f}")
    logger.info(f"  Avg Loss: ${avg_loss:,.2f}")

    # Trade breakdown
    if len(trades) > 0:
        logger.info(f"\nTrade Breakdown:")
        logger.info(f"  Winning Trades: {len(winning_trades)}")
        logger.info(f"  Losing Trades: {len(losing_trades)}")

        # Sample trades
        logger.info(f"\nSample Trades (first 5):")
        for i, trade in enumerate(trades[:5]):
            logger.info(
                f"  {i+1}. {trade['symbol']}: "
                f"{trade['quantity']:+.0f} shares @ ${trade['price']:.2f}, "
                f"PnL: ${trade['pnl']:+,.2f}"
            )

    # Pair statistics
    logger.info(f"\nPair Statistics:")
    if hasattr(strategy, 'selected_pairs') and strategy.selected_pairs:
        logger.info(f"  Selected Pairs: {len(strategy.selected_pairs)}")
        for pair in strategy.selected_pairs:
            logger.info(
                f"    {pair.symbol_long}/{pair.symbol_short}: "
                f"score={pair.score:.1f}, corr={pair.correlation:.3f}, "
                f"p={pair.coint_pvalue:.4f}"
            )

    logger.info("\n" + "=" * 80)

    # Return performance dictionary
    return {
        'final_value': final_value,
        'total_return': total_return,
        'sharpe_ratio': sharpe_ratio,
        'max_drawdown': max_drawdown,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'num_trades': len(trades),
        'equity_curve': equity_curve,
        'trades': trades,
        'daily_returns': daily_returns
    }


def main():
    """Main entry point."""
    try:
        # Run backtest
        results = run_pairs_backtest(
            start_date="2020-01-01",
            end_date="2024-12-31",
            initial_capital=100000.0
        )

        # Success
        logger.info("\nBacktest completed successfully!")

        # Export results
        output_dir = Path(__file__).parent.parent / "backtest_results"
        output_dir.mkdir(exist_ok=True)

        # Save equity curve
        equity_file = output_dir / "pairs_trading_equity.csv"
        results['equity_curve'].to_csv(equity_file)
        logger.info(f"\nEquity curve saved to: {equity_file}")

        # Save trades
        if len(results['trades']) > 0:
            trades_df = pd.DataFrame(results['trades'])
            trades_file = output_dir / "pairs_trading_trades.csv"
            trades_df.to_csv(trades_file, index=False)
            logger.info(f"Trades saved to: {trades_file}")

        # Save summary
        summary_file = output_dir / "pairs_trading_summary.txt"
        with open(summary_file, 'w') as f:
            f.write("PAIRS TRADING BACKTEST SUMMARY\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"Final Value: ${results['final_value']:,.2f}\n")
            f.write(f"Total Return: {results['total_return'] * 100:.2f}%\n")
            f.write(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}\n")
            f.write(f"Max Drawdown: {results['max_drawdown'] * 100:.2f}%\n")
            f.write(f"Win Rate: {results['win_rate'] * 100:.2f}%\n")
            f.write(f"Profit Factor: {results['profit_factor']:.2f}\n")
            f.write(f"Total Trades: {results['num_trades']}\n")

        logger.info(f"Summary saved to: {summary_file}")

    except Exception as e:
        logger.error(f"\nBacktest failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
