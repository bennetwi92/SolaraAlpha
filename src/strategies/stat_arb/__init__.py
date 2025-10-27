"""Statistical Arbitrage Strategy - Multi-stock mean-reversion using PCA factor models."""

from .factor_model import FactorModel, FactorResult
from .signals import SignalGenerator, SignalType, PortfolioSignal
from .portfolio import PortfolioConstructor, PortfolioPosition
from .position_sizing import PositionSizer
from .backtest import StatArbBacktester, BacktestConfig, BacktestResult
from .paper_trading import PaperTradingEngine
from .strategy_adapter import StatArbStrategy

__all__ = [
    "FactorModel",
    "FactorResult",
    "SignalGenerator",
    "SignalType",
    "PortfolioSignal",
    "PortfolioConstructor",
    "PortfolioPosition",
    "PositionSizer",
    "StatArbBacktester",
    "BacktestConfig",
    "BacktestResult",
    "PaperTradingEngine",
    "StatArbStrategy",
]
