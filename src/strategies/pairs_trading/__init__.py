"""Pairs trading strategy implementation.

Statistical arbitrage through mean-reversion of cointegrated pairs.
"""
from .backtest import BacktestConfig, BacktestResult, PairsBacktester
from .cointegration import CointegrationResult, CointegrationTester
from .paper_trading import PaperTradingEngine
from .position_sizing import PairPosition, PositionSizer
from .signals import SignalGenerator, SignalType, TradingSignal

__all__ = [
    "CointegrationTester",
    "CointegrationResult",
    "SignalGenerator",
    "SignalType",
    "TradingSignal",
    "PositionSizer",
    "PairPosition",
    "PairsBacktester",
    "BacktestConfig",
    "BacktestResult",
    "PaperTradingEngine",
]
