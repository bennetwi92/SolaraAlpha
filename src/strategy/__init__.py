"""
Strategy Framework

Unified framework for implementing, backtesting, and deploying trading strategies.
"""

# Base classes and dataclasses
from src.strategy.base import (
    BaseStrategy,
    RelativeValueStrategy,
    Signal,
    SignalType,
    Position,
    FillEvent,
    StrategyConfig,
    StrategyState,
    HedgeRatio
)

# Parameter management
from src.strategy.parameters import (
    ParameterManager,
    ParameterSet
)

# State management
from src.strategy.state import (
    StateManager,
    StrategyStateEncoder,
    state_decoder
)

# Performance tracking
from src.strategy.performance import (
    PerformanceTracker,
    StrategyPerformance
)

# Integration components
from src.strategy.backtest_runner import StrategyBacktestRunner
from src.strategy.risk_integration import RiskAwareStrategy
from src.strategy.data_adapter import StrategyDataAdapter

__all__ = [
    # Base classes
    'BaseStrategy',
    'RelativeValueStrategy',

    # Dataclasses
    'Signal',
    'SignalType',
    'Position',
    'FillEvent',
    'StrategyConfig',
    'StrategyState',
    'HedgeRatio',

    # Parameter management
    'ParameterManager',
    'ParameterSet',

    # State management
    'StateManager',
    'StrategyStateEncoder',
    'state_decoder',

    # Performance tracking
    'PerformanceTracker',
    'StrategyPerformance',

    # Integration components
    'StrategyBacktestRunner',
    'RiskAwareStrategy',
    'StrategyDataAdapter',
]
