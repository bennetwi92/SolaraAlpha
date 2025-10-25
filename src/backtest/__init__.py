"""
Backtesting Engine - Core backtesting functionality for strategy validation.

Main Components:
- BacktestEngine: Main engine for running backtests
- Position: Position tracking for long/short positions
- Trade: Individual trade records
- PerformanceMetrics: Comprehensive performance analysis
"""

from src.backtest.engine import (
    BacktestEngine,
    Position,
    Trade,
    BacktestConfig,
    BacktestResult
)

__all__ = [
    'BacktestEngine',
    'Position',
    'Trade',
    'BacktestConfig',
    'BacktestResult'
]
