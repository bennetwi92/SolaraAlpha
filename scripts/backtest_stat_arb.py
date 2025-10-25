#!/usr/bin/env python3
"""
Statistical Arbitrage Backtest Script

Demonstrates the StatArbStrategy on sector ETFs:
- XLF (Financials)
- XLE (Energy)
- XLK (Technology)
- XLV (Healthcare)
- XLI (Industrials)
- XLP (Consumer Staples)
- XLY (Consumer Discretionary)
- XLU (Utilities)
- XLB (Materials)

Uses PCA factor model to identify alpha signals and maintain market neutrality.
Generates comprehensive performance metrics and factor exposure analysis.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

from src.strategy.stat_arb import StatArbStrategy
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


def analyze_factor_exposure(
    strategy: StatArbStrategy,
    positions_history: pd.DataFrame,
    data: dict
) -> dict:
    """
    Analyze factor exposure over time.

    Args:
        strategy: StatArbStrategy instance
        positions_history: Historical positions
        data: Market data

    Returns:
        Factor exposure statistics
    """
    if strategy.factor_model is None:
        return {}

    # Calculate portfolio beta exposure over time
    # (simplified - just check dollar neutrality)

    net_exposures = []

    for timestamp, row in positions_history.iterrows():
        long_value = 0.0
        short_value = 0.0

        for symbol in strategy.config.symbols:
            if symbol in row and symbol in data:
                qty = row[symbol]

                # Get price at this timestamp
                symbol_df = data[symbol]
                if timestamp in symbol_df.index:
                    price = symbol_df.loc[timestamp, 'Close']

                    if qty > 0:
                        long_value += qty * price
                    elif qty < 0:
                        short_value += abs(qty) * price

        net_exposure = (long_value - short_value) / (long_value + short_value) if (long_value + short_value) > 0 else 0.0
        net_exposures.append(net_exposure)

    avg_net_exposure = np.mean(net_exposures) if net_exposures else 0.0
    max_net_exposure = np.max(np.abs(net_exposures)) if net_exposures else 0.0

    return {
        'avg_net_exposure': avg_net_exposure,
        'max_net_exposure': max_net_exposure,
        'net_exposures': net_exposures
    }


def run_stat_arb_backtest(
    start_date: str = "2020-01-01",
    end_date: str = "2024-12-31",
    initial_capital: float = 100000.0
):
    """
    Run statistical arbitrage backtest on sector ETFs.

    Args:
        start_date: Backtest start date (YYYY-MM-DD)
        end_date: Backtest end date (YYYY-MM-DD)
        initial_capital: Starting capital

    Returns:
        Performance metrics dictionary
    """
    logger.info("=" * 80)
    logger.info("STATISTICAL ARBITRAGE BACKTEST")
    logger.info("=" * 80)

    # Sector ETF universe (liquid, diverse sectors)
    symbols = [
        'XLF',   # Financials
        'XLE',   # Energy
        'XLK',   # Technology
        'XLV',   # Healthcare
        'XLI',   # Industrials
        'XLP',   # Consumer Staples
        'XLY',   # Consumer Discretionary
        'XLU',   # Utilities
        'XLB',   # Materials
    ]

    # Strategy configuration
    config = StrategyConfig(
        name="StatArb_SectorETFs",
        version="1.0.0",
        parameters={
            'factor_model_type': 'pca',           # Use PCA factor model
            'n_factors': 3,                       # 3 principal components
            'lookback_period': 60,                # 60 days for factor estimation
            'residual_lookback': 30,              # 30 days for z-score
            'z_entry': 2.0,                       # Enter at 2 std dev
            'z_exit': 0.5,                        # Exit at 0.5 std dev
            'max_holding_days': 30,               # Max 30 day hold
            'max_positions': 10,                  # Max 10 positions (5 long, 5 short)
            'position_size_pct': 0.1,             # 10% per position
            'neutrality_type': 'dollar',          # Dollar-neutral
            'max_sector_exposure': 0.3,           # Max 30% sector exposure
            'min_correlation': 0.3,               # Min 0.3 avg correlation
            'rebalance_frequency_days': 5,        # Rebalance factor model every 5 days
            'transaction_cost_pct': 0.001,        # 0.1% transaction cost
            'min_variance_explained': 0.7,        # Min 70% variance explained
        },
        symbols=symbols,
        max_positions=10,
        enable_short=True,
        rebalance_frequency='1D'
    )

    logger.info(f"\nStrategy Configuration:")
    logger.info(f"  Name: {config.name} v{config.version}")
    logger.info(f"  Universe: {len(symbols)} Sector ETFs")
    logger.info(f"  Parameters:")
    for key, value in config.parameters.items():
        logger.info(f"    {key}: {value}")

    # Initialize strategy
    strategy = StatArbStrategy(config)

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

    if len(data) < 3:
        raise ValueError("Need at least 3 symbols with data for statistical arbitrage")

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

    # Sortino ratio (downside deviation)
    downside_returns = daily_returns[daily_returns < 0]
    if len(downside_returns) > 0 and downside_returns.std() > 0:
        sortino_ratio = np.sqrt(252) * daily_returns.mean() / downside_returns.std()
    else:
        sortino_ratio = 0.0

    # Calmar ratio (return / max drawdown)
    annualized_return = (1 + total_return) ** (252 / len(daily_returns)) - 1 if len(daily_returns) > 0 else 0
    calmar_ratio = annualized_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

    # Win rate and profit metrics
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

        # Turnover (total traded value / avg portfolio value)
        total_traded_value = sum(abs(t['quantity'] * t['price']) for t in trades)
        avg_portfolio_value = equity_curve.mean()
        turnover = total_traded_value / avg_portfolio_value if avg_portfolio_value > 0 else 0
    else:
        win_rate = 0.0
        profit_factor = 0.0
        avg_win = 0.0
        avg_loss = 0.0
        turnover = 0.0

    # Print summary
    logger.info(f"\nPerformance Summary:")
    logger.info(f"  Final Portfolio Value: ${final_value:,.2f}")
    logger.info(f"  Total Return: {total_return_pct:.2f}%")
    logger.info(f"  Annualized Return: {annualized_return * 100:.2f}%")
    logger.info(f"  Sharpe Ratio: {sharpe_ratio:.2f}")
    logger.info(f"  Sortino Ratio: {sortino_ratio:.2f}")
    logger.info(f"  Calmar Ratio: {calmar_ratio:.2f}")
    logger.info(f"  Max Drawdown: {max_drawdown_pct:.2f}%")
    logger.info(f"  Total Trades: {len(trades)}")
    logger.info(f"  Win Rate: {win_rate * 100:.2f}%")
    logger.info(f"  Profit Factor: {profit_factor:.2f}")
    logger.info(f"  Avg Win: ${avg_win:,.2f}")
    logger.info(f"  Avg Loss: ${avg_loss:,.2f}")
    logger.info(f"  Turnover: {turnover:.2f}x")

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

    # Factor model analysis
    logger.info(f"\nFactor Model Analysis:")
    if strategy.factor_model is not None:
        logger.info(f"  Number of Factors: {strategy.factor_model.n_components}")
        logger.info(f"  Variance Explained:")
        for i, var_ratio in enumerate(strategy.factor_model.explained_variance_ratio):
            logger.info(f"    Factor {i + 1}: {var_ratio * 100:.2f}%")

        total_variance = strategy.factor_model.explained_variance_ratio.sum()
        logger.info(f"  Total Variance Explained: {total_variance * 100:.2f}%")

    # Factor exposure analysis
    logger.info(f"\nMarket Neutrality Analysis:")
    exposure_stats = analyze_factor_exposure(strategy, positions_history, data)

    if exposure_stats:
        logger.info(f"  Average Net Exposure: {exposure_stats['avg_net_exposure'] * 100:.2f}%")
        logger.info(f"  Max Net Exposure: {exposure_stats['max_net_exposure'] * 100:.2f}%")

    logger.info("\n" + "=" * 80)

    # Return performance dictionary
    return {
        'final_value': final_value,
        'total_return': total_return,
        'annualized_return': annualized_return,
        'sharpe_ratio': sharpe_ratio,
        'sortino_ratio': sortino_ratio,
        'calmar_ratio': calmar_ratio,
        'max_drawdown': max_drawdown,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'num_trades': len(trades),
        'turnover': turnover,
        'equity_curve': equity_curve,
        'trades': trades,
        'daily_returns': daily_returns,
        'exposure_stats': exposure_stats
    }


def main():
    """Main entry point."""
    try:
        # Run backtest
        results = run_stat_arb_backtest(
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
        equity_file = output_dir / "stat_arb_equity.csv"
        results['equity_curve'].to_csv(equity_file)
        logger.info(f"\nEquity curve saved to: {equity_file}")

        # Save trades
        if len(results['trades']) > 0:
            trades_df = pd.DataFrame(results['trades'])
            trades_file = output_dir / "stat_arb_trades.csv"
            trades_df.to_csv(trades_file, index=False)
            logger.info(f"Trades saved to: {trades_file}")

        # Save summary
        summary_file = output_dir / "stat_arb_summary.txt"
        with open(summary_file, 'w') as f:
            f.write("STATISTICAL ARBITRAGE BACKTEST SUMMARY\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"Final Value: ${results['final_value']:,.2f}\n")
            f.write(f"Total Return: {results['total_return'] * 100:.2f}%\n")
            f.write(f"Annualized Return: {results['annualized_return'] * 100:.2f}%\n")
            f.write(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}\n")
            f.write(f"Sortino Ratio: {results['sortino_ratio']:.2f}\n")
            f.write(f"Calmar Ratio: {results['calmar_ratio']:.2f}\n")
            f.write(f"Max Drawdown: {results['max_drawdown'] * 100:.2f}%\n")
            f.write(f"Win Rate: {results['win_rate'] * 100:.2f}%\n")
            f.write(f"Profit Factor: {results['profit_factor']:.2f}\n")
            f.write(f"Total Trades: {results['num_trades']}\n")
            f.write(f"Turnover: {results['turnover']:.2f}x\n\n")

            if results['exposure_stats']:
                f.write("Market Neutrality:\n")
                f.write(f"  Average Net Exposure: {results['exposure_stats']['avg_net_exposure'] * 100:.2f}%\n")
                f.write(f"  Max Net Exposure: {results['exposure_stats']['max_net_exposure'] * 100:.2f}%\n")

        logger.info(f"Summary saved to: {summary_file}")

    except Exception as e:
        logger.error(f"\nBacktest failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
