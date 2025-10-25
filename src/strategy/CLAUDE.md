# Strategy Framework

Unified framework for implementing, backtesting, and deploying trading strategies.

**Core Components**:
- `base.py`: BaseStrategy, RelativeValueStrategy, Signal/Position/FillEvent dataclasses
- `parameters.py`: ParameterManager for versioning and serialization
- `state.py`: StateManager for checkpointing and recovery
- `performance.py`: PerformanceTracker for metrics and attribution
- `optimization.py`: WalkForwardOptimizer for parameter optimization
- `backtest_runner.py`: StrategyBacktestRunner for backtesting integration
- `risk_integration.py`: RiskAwareStrategy wrapper for risk management
- `data_adapter.py`: StrategyDataAdapter for market data integration

**Design Philosophy**:
- Relative value strategies prioritized (market-neutral, pairs trading, stat arb)
- Lifecycle-based (on_data → generate_signals → calculate_positions → on_fill)
- Integration-first with backtesting, risk, and market data systems
- Parameter discipline with versioning and optimization support
- State persistence for recovery and replay

**Architecture Doc**: `docs/architecture/strategy_framework.md`
