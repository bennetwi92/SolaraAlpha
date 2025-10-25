# Backtesting Engine

Core backtesting functionality for validating trading strategies on historical data.

## Components

- **engine.py**: BacktestEngine class with position tracking, P&L calculation, transaction costs, and performance metrics
- **__init__.py**: Public API exports

## Key Features

- Position tracking (long/short positions, entry/exit prices, quantities)
- Realistic P&L calculation (realized/unrealized gains, cumulative returns)
- Transaction cost modeling (percentage-based commission, fixed costs, slippage)
- Comprehensive performance metrics (Sharpe ratio, max drawdown, win rate, profit factor)
- Type hints throughout (PEP 484)
- Google-style docstrings
- 96% test coverage

## Usage

See `examples/backtest_ma_crossover.py` for complete example.

## Testing

Tests in `tests/backtest/test_engine.py` achieve 96% coverage.
