# Pairs Trading Strategy

SolaraAlpha's first relative value strategy - statistical arbitrage through mean-reversion of cointegrated pairs.

## Architecture

**Statistical Foundation**: Engle-Granger & Johansen cointegration tests with ADF stationarity validation

**Signal Generation**: Z-score based thresholds (±2σ entry, 0.5σ exit, 3σ stop-loss)

**Position Sizing**: Volatility-adjusted allocation with risk limit integration

**Backtesting**: Walk-forward optimization with 60/40 train/test split, rolling parameter re-estimation

**Paper Trading**: Real-time simulation with risk validation and order fill simulation

**Live Execution**: Production-ready integration with StrategyRunner daemon for 24/7 operations

## Components

- `cointegration.py` - Cointegration testing (Engle-Granger, Johansen, ADF)
- `signals.py` - Z-score signal generation with entry/exit logic
- `position_sizing.py` - Volatility-based sizing with risk compliance
- `backtest.py` - Walk-forward backtesting with transaction costs
- `paper_trading.py` - Live simulation engine with risk integration
- `strategy_adapter.py` - StrategyRunner interface adapter for daemon execution

## Target Pairs

**Equities**: KO/PEP, XOM/CVX, JPM/BAC
**ETFs**: SPY/IVV, GLD/IAU

## Usage

### Backtesting
```python
from src.strategies.pairs_trading import PairsBacktester, BacktestConfig

backtester = PairsBacktester(config=BacktestConfig())
result = backtester.backtest_pair(prices_ko, prices_pep, 'KO', 'PEP')
print(f"Sharpe: {result.metrics['sharpe_ratio']:.2f}")
```

### Paper Trading (Manual)
```python
from src.strategies.pairs_trading import PaperTradingEngine

engine = PaperTradingEngine(portfolio_value=100000)
engine.add_pair('SPY', 'IVV', 'etfs')
trades = engine.run_iteration()  # Run periodically
```

### Live Execution (Daemon)
```python
from src.execution.strategy_runner import StrategyRunner, StrategyConfig, RunMode
from src.strategies.pairs_trading import PaperTradingEngine, PairsStrategy

# Setup engine
engine = PaperTradingEngine(portfolio_value=100000, strategy_id='pairs_1')
engine.add_pair('SPY', 'IVV', 'etfs')
engine.add_pair('KO', 'PEP', 'equities')

# Wrap in adapter
strategy = PairsStrategy(engine)

# Configure execution
config = StrategyConfig(
    strategy_id='pairs_1',
    strategy_name='Pairs Trading - Equities/ETFs',
    run_mode=RunMode.MARKET_HOURS,
    interval_seconds=300,  # 5 minutes
)

# Run daemon (blocks until SIGTERM)
runner = StrategyRunner()
runner.register_strategy(strategy, config)
runner.start()
```

## Risk Integration

**Pre-Trade**: All trades validated against position limits via RiskManager (#57)
**Post-Trade**: Positions recorded for exposure tracking and circuit breaker monitoring
**Circuit Breaker**: Automatic halt propagates to paper trading engine

## Testing

Comprehensive test suite: `pytest tests/strategies/pairs_trading/ --cov=src/strategies/pairs_trading`

**Coverage Target**: >85% (non-negotiable per CLAUDE.md)
