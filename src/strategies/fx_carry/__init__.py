"""FX Carry Trade Strategy.

Implements interest rate differential-based carry trade strategy for foreign exchange markets.
"""
from .backtest import FXCarryBacktester, BacktestConfig
from .signals import FXCarrySignals
from .strategy import FXCarryStrategy

__all__ = [
    "FXCarryBacktester",
    "BacktestConfig",
    "FXCarrySignals",
    "FXCarryStrategy",
]
