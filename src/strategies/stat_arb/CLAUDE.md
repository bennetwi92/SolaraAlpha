# Statistical Arbitrage Strategy

## Overview

Multi-stock mean-reversion strategy using PCA factor models to identify and trade idiosyncratic risk. Complements pairs trading by operating on larger portfolios (20-50 stocks) with lower correlation.

## Strategy Logic

1. **Factor Decomposition**: Extract 3-5 principal components from stock returns
2. **Residual Calculation**: Compute idiosyncratic returns = actual - predicted (from factors)
3. **Signal Generation**: Z-score mean reversion on residuals
   - Entry: |z| > 2.0 (long if z < -2, short if z > 2)
   - Exit: |z| < 0.5
   - Stop: |z| > 3.5
4. **Portfolio Construction**: Rank stocks by |z-score|, select top/bottom deciles
5. **Position Sizing**: Inverse volatility weighting for equal risk contribution
6. **Market-Neutral**: Equal dollar long/short exposure

## Components

- **factor_model.py**: PCA-based factor extraction and residual calculation
- **signals.py**: Z-score signal generation from residuals
- **portfolio.py**: Long/short portfolio construction by ranking
- **position_sizing.py**: Volatility-weighted position sizing
- **backtest.py**: Walk-forward validation with rolling factor re-estimation
- **paper_trading.py**: Real-time simulation engine
- **strategy_adapter.py**: StrategyRunner integration wrapper

## Configuration

Default parameters (BacktestConfig):
- Portfolio: $100k initial capital
- Positions: 20-50 stocks (target 30)
- Factors: 5 principal components
- Signals: Entry ±2σ, exit 0.5σ, stop 3.5σ
- Re-estimation: Every 20 days
- Leverage: 1.0× (100% long + 100% short)

## Usage

### Backtesting

```python
from src.strategies.stat_arb.backtest import StatArbBacktester, BacktestConfig

config = BacktestConfig(
    portfolio_value=100_000,
    n_factors=5,
    target_positions=30,
    entry_threshold=2.0,
)

backtester = StatArbBacktester(config)
result = backtester.backtest(prices_df)

print(f"Sharpe: {result.metrics['sharpe_ratio']:.2f}")
print(f"Return: {result.metrics['total_return']:.2%}")
```

### Paper Trading

```python
from src.strategies.stat_arb.paper_trading import PaperTradingEngine
from src.strategies.stat_arb.strategy_adapter import StatArbStrategy

universe = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'JPM', 'BAC', ...]

engine = PaperTradingEngine(
    universe=universe,
    asset_class='equities',
    portfolio_value=100_000,
    strategy_id='stat_arb_1',
    n_factors=5,
    target_positions=30,
)

strategy = StatArbStrategy(engine)

# Integrate with StrategyRunner for 24/7 execution
from src.execution.strategy_runner import StrategyRunner, StrategyConfig, RunMode

config = StrategyConfig(
    strategy_id='stat_arb_1',
    strategy_name='Statistical Arbitrage - Equities',
    run_mode=RunMode.MARKET_HOURS,
    interval_seconds=3600,  # Hourly
)

runner = StrategyRunner()
runner.register_strategy(strategy, config)
runner.start()
```

## Risk Management

- **Position Limits**: Max 5% per stock, 10% per strategy
- **Market-Neutral**: Net exposure <10% of gross
- **Circuit Breaker**: Integrated with RiskManager
- **Pre-Trade Validation**: All trades validated before execution

## Performance Expectations

Based on synthetic testing:
- **Target Sharpe**: >1.0 on 2+ years historical data
- **Correlation to Pairs**: <0.4 (low correlation for diversification)
- **Market-Neutral**: Net exposure variance <10%
- **Trade Frequency**: Moderate (rebalancing as residuals mean-revert)

## Data Requirements

- **Universe**: 30-50 liquid stocks
- **History**: 60 days for factor estimation + 20 days for signals
- **Frequency**: Daily OHLC data
- **Quality**: Complete data (no gaps) for all stocks

## Testing

Run tests:
```bash
pytest tests/strategies/stat_arb/ -v
```

Run backtest validation:
```bash
python scripts/test_stat_arb_simple.py
```

## Implementation Notes

- **Walk-Forward Validation**: Factor model re-estimated every 20 days
- **Rolling Windows**: 60-day factor estimation, 20-day signal lookback
- **Transaction Costs**: 0.1% commission + 0.05% slippage
- **P&L Tracking**: Entry price tracking for accurate realized P&L calculation
- **Equity Curve**: Built from realized P&L (not mark-to-market during holding)

## Future Enhancements

- Sector-neutral constraints
- Factor timing (switch factors based on regime)
- Machine learning for residual prediction
- Intraday rebalancing
- Options overlay for downside protection
