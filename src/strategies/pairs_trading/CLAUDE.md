# Pairs Trading Strategy

SolaraAlpha's first relative value strategy - statistical arbitrage through mean-reversion of cointegrated pairs.

## Architecture

**Statistical Foundation**: Engle-Granger & Johansen cointegration tests with ADF stationarity validation

**Signal Generation**: Z-score based thresholds (±2σ entry, 0.5σ exit, 3σ stop-loss)

**Position Sizing**: Volatility-adjusted allocation with risk limit integration

**Backtesting**: Walk-forward optimization with 60/40 train/test split, rolling parameter re-estimation

**Paper Trading**: Real-time simulation with risk validation and order fill simulation

## Components

- `cointegration.py` - Cointegration testing (Engle-Granger, Johansen, ADF)
- `signals.py` - Z-score signal generation with entry/exit logic
- `position_sizing.py` - Volatility-based sizing with risk compliance
- `backtest.py` - Walk-forward backtesting with transaction costs
- `paper_trading.py` - Live simulation engine with risk integration

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

### Paper Trading
```python
from src.strategies.pairs_trading import PaperTradingEngine

engine = PaperTradingEngine(portfolio_value=100000)
engine.add_pair('SPY', 'IVV', 'etfs')
trades = engine.run_iteration()  # Run periodically
```

## Risk Integration

**Pre-Trade**: All trades validated against position limits via RiskManager (#57)
**Post-Trade**: Positions recorded for exposure tracking and circuit breaker monitoring
**Circuit Breaker**: Automatic halt propagates to paper trading engine

## Testing

Comprehensive test suite: `pytest tests/strategies/pairs_trading/ --cov=src/strategies/pairs_trading`

**Coverage Target**: >85% (non-negotiable per CLAUDE.md)
