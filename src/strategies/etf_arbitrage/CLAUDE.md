# ETF Arbitrage Strategy

**Type**: Relative value, market-neutral
**Target**: SPY/IVV spread (both track S&P 500)

**Components**:
- `spread.py`: Z-score spread calculation (rolling mean/std)
- `signals.py`: Entry/exit logic (±2σ entry, mean reversion exit)
- `position_sizing.py`: Kelly criterion with capital constraints
- `backtest.py`: Walk-forward validation on historical data
- `strategy_adapter.py`: StrategyRunner integration
- `config.py`: Strategy parameters

**Performance Target**: Sharpe >1.5, max drawdown <10%
**Risk Constraint**: Max 20% capital per spread position
