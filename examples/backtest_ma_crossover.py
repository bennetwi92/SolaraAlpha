"""
Example: Moving Average Crossover Backtest

Demonstrates BacktestEngine functionality with a simple moving average
crossover strategy on AAPL stock.

Strategy Logic:
- Long signal: Fast MA crosses above Slow MA
- Flat signal: Fast MA crosses below Slow MA
- Uses 20-day fast MA and 50-day slow MA

Expected Metrics:
- Displays total return, Sharpe ratio, max drawdown
- Shows trade statistics (win rate, profit factor)
- Plots equity curve
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd

# Optional plotting dependency
try:
    import matplotlib.pyplot as plt
    PLOTTING_AVAILABLE = True
except ImportError:
    PLOTTING_AVAILABLE = False
    print("Warning: matplotlib not installed. Plotting disabled.")

from src.data.market_client import MarketDataClient
from src.backtest.engine import BacktestEngine, BacktestConfig


def generate_ma_crossover_signals(
    data: pd.DataFrame,
    fast_window: int = 20,
    slow_window: int = 50
) -> pd.Series:
    """
    Generate moving average crossover signals.

    Args:
        data: OHLCV DataFrame
        fast_window: Fast moving average period (default: 20)
        slow_window: Slow moving average period (default: 50)

    Returns:
        Series with signals (1.0 = long, 0.0 = flat)
    """
    # Calculate moving averages
    close = data['Close']
    fast_ma = close.rolling(window=fast_window).mean()
    slow_ma = close.rolling(window=slow_window).mean()

    # Generate signals
    signals = pd.Series(0.0, index=data.index)
    signals[fast_ma > slow_ma] = 1.0  # Long when fast > slow
    signals[fast_ma <= slow_ma] = 0.0  # Flat when fast <= slow

    return signals


def plot_results(data: pd.DataFrame, signals: pd.Series, result):
    """
    Plot backtest results.

    Args:
        data: OHLCV data
        signals: Trading signals
        result: BacktestResult object
    """
    if not PLOTTING_AVAILABLE:
        print("   Plotting skipped (matplotlib not installed)")
        return

    fig, axes = plt.subplots(3, 1, figsize=(14, 10))

    # Plot 1: Price and signals
    ax1 = axes[0]
    ax1.plot(data.index, data['Close'], label='Close Price', linewidth=1.5)

    # Calculate MAs for visualization
    fast_ma = data['Close'].rolling(window=20).mean()
    slow_ma = data['Close'].rolling(window=50).mean()
    ax1.plot(data.index, fast_ma, label='Fast MA (20)', alpha=0.7)
    ax1.plot(data.index, slow_ma, label='Slow MA (50)', alpha=0.7)

    # Mark entry/exit points
    entries = signals.diff() == 1.0
    exits = signals.diff() == -1.0

    if entries.any():
        ax1.scatter(data.index[entries], data['Close'][entries],
                   marker='^', color='green', s=100, label='Entry', zorder=5)
    if exits.any():
        ax1.scatter(data.index[exits], data['Close'][exits],
                   marker='v', color='red', s=100, label='Exit', zorder=5)

    ax1.set_title('Moving Average Crossover Strategy - Price & Signals')
    ax1.set_ylabel('Price ($)')
    ax1.legend(loc='best')
    ax1.grid(True, alpha=0.3)

    # Plot 2: Equity curve
    ax2 = axes[1]
    ax2.plot(result.equity_curve.index, result.equity_curve.values,
            label='Equity Curve', color='blue', linewidth=2)
    ax2.axhline(y=100000, color='gray', linestyle='--', label='Initial Capital')
    ax2.set_title('Equity Curve')
    ax2.set_ylabel('Portfolio Value ($)')
    ax2.legend(loc='best')
    ax2.grid(True, alpha=0.3)

    # Plot 3: Drawdown
    ax3 = axes[2]
    running_max = result.equity_curve.cummax()
    drawdown = (result.equity_curve - running_max) / running_max
    ax3.fill_between(drawdown.index, drawdown.values, 0,
                     color='red', alpha=0.3, label='Drawdown')
    ax3.set_title('Drawdown')
    ax3.set_ylabel('Drawdown (%)')
    ax3.set_xlabel('Date')
    ax3.legend(loc='best')
    ax3.grid(True, alpha=0.3)

    # Format y-axis as percentage
    ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.1%}'))

    plt.tight_layout()
    plt.savefig(project_root / 'examples' / 'backtest_results.png', dpi=150)
    print(f"\nPlot saved to: {project_root / 'examples' / 'backtest_results.png'}")
    plt.show()


def main():
    """Run moving average crossover backtest example."""
    print("=" * 70)
    print("Moving Average Crossover Backtest Example")
    print("=" * 70)

    # Fetch data
    print("\n1. Fetching AAPL data (2023-01-01 to 2023-12-31)...")
    client = MarketDataClient()
    data = client.get_ohlcv("AAPL", start="2023-01-01", end="2023-12-31")
    print(f"   Loaded {len(data)} bars")

    # Generate signals
    print("\n2. Generating moving average crossover signals...")
    signals = generate_ma_crossover_signals(data, fast_window=20, slow_window=50)
    num_signals = (signals.diff() != 0).sum()
    print(f"   Generated {num_signals} signal changes")

    # Configure backtest
    print("\n3. Configuring backtest...")
    config = BacktestConfig(
        initial_capital=100000.0,      # $100k starting capital
        commission_pct=0.001,           # 0.1% commission (10 bps)
        slippage_pct=0.0005,            # 0.05% slippage (5 bps)
        position_size_pct=0.95,         # Use 95% of capital per position
        allow_short=False,              # Long-only strategy
        log_trades=False                # Set to True to see all trades
    )
    print(f"   Initial Capital: ${config.initial_capital:,.0f}")
    print(f"   Commission: {config.commission_pct:.2%}")
    print(f"   Slippage: {config.slippage_pct:.2%}")

    # Run backtest
    print("\n4. Running backtest...")
    engine = BacktestEngine(config)
    result = engine.run(data, signals, symbol="AAPL")

    # Display results
    print("\n" + "=" * 70)
    print("BACKTEST RESULTS")
    print("=" * 70)

    summary = result.summary()
    for key, value in summary.items():
        print(f"{key:.<30} {value:>20}")

    # Trade details
    print("\n" + "-" * 70)
    print("TRADE DETAILS")
    print("-" * 70)

    if result.trades:
        print(f"\nFirst 5 trades:")
        print(f"{'Entry':<12} {'Exit':<12} {'Side':<6} {'Qty':<8} {'Entry $':<10} "
              f"{'Exit $':<10} {'P&L $':<12} {'Return':<10}")
        print("-" * 90)

        for trade in result.trades[:5]:
            print(f"{trade.entry_time.strftime('%Y-%m-%d'):<12} "
                  f"{trade.exit_time.strftime('%Y-%m-%d'):<12} "
                  f"{trade.side.name:<6} "
                  f"{trade.quantity:<8.0f} "
                  f"{trade.entry_price:<10.2f} "
                  f"{trade.exit_price:<10.2f} "
                  f"{trade.pnl:<12.2f} "
                  f"{trade.return_pct:<10.2%}")

        if len(result.trades) > 5:
            print(f"\n... and {len(result.trades) - 5} more trades")
    else:
        print("\nNo trades executed (insufficient signals or capital)")

    # Plot results
    print("\n5. Generating plots...")
    try:
        plot_results(data, signals, result)
    except Exception as e:
        print(f"   Warning: Could not generate plots: {e}")

    print("\n" + "=" * 70)
    print("Backtest complete!")
    print("=" * 70)


if __name__ == "__main__":
    main()
