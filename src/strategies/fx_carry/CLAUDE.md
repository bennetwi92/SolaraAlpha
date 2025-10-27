# FX Carry Trade Strategy

## Overview

Multi-currency carry trade strategy exploiting interest rate differentials between currencies. Combines carry trade fundamentals with trend filtering and volatility-based position sizing to generate uncorrelated returns relative to equity-focused strategies.

## Strategy Logic

1. **Interest Rate Differential**: Calculate yield differential between base and quote currencies
2. **Trend Filtering**: Apply MA crossover and momentum filters to avoid counter-trend trades
3. **Volatility Adjustment**: Scale positions inversely to realized volatility
4. **Position Sizing**: Target portfolio volatility through inverse volatility weighting
5. **Rebalancing**: Weekly portfolio rebalancing with 10% drift threshold

## Components

- **fx_ingestor.py**: Free FX data (Frankfurter API) + interest rates (API Ninjas)
- **signals.py**: Interest rate differential signals with trend/volatility filters
- **position_sizing.py**: Inverse volatility weighting for equal risk contribution
- **backtest.py**: Walk-forward backtesting with transaction costs
- **strategy.py**: Live execution with RiskManager integration

## Configuration

Default parameters (FXCarryConfig):
- Portfolio: $100k initial capital
- Positions: 10 currency pairs (major + high-yield)
- Signals: Min 1% rate differential, 60-day trend, 30-day volatility
- Position Limits: Max 20% per pair, 1.0x leverage (no leverage)
- Rebalancing: Weekly (7 days), 10% drift threshold

## Currency Pairs

**Default Universe** (10 pairs):
- **High-Yield**: AUD-USD, NZD-USD, AUD-JPY, NZD-JPY (carry favorites)
- **Majors**: EUR-USD, GBP-USD, USD-JPY, USD-CHF
- **Crosses**: EUR-JPY, GBP-JPY

Supports 30+ currency pairs from 19 currencies (G8, Scandinavia, emerging markets)

## Usage

### Backtesting

```python
from src.strategies.fx_carry import FXCarryBacktester, BacktestConfig

config = BacktestConfig(
    portfolio_value=100_000,
    max_positions=10,
    vol_target=10.0,
    min_rate_diff=1.0,
)

backtester = FXCarryBacktester(config)
result = backtester.backtest(prices_dict, rate_differentials)

print(f"Sharpe: {result.metrics['sharpe_ratio']:.2f}")
print(f"Return: {result.metrics['total_return']:.2%}")
```

### Live Execution

```python
from src.execution.strategy_runner import StrategyRunner, StrategyConfig, RunMode
from src.strategies.fx_carry import FXCarryStrategy, FXCarryConfig

# Configure strategy
config = FXCarryConfig(
    strategy_id='fx_carry_1',
    portfolio_value=100_000,
    max_positions=10,
)

strategy = FXCarryStrategy(config)

# Configure runner
runner_config = StrategyConfig(
    strategy_id='fx_carry_1',
    strategy_name='FX Carry Trade',
    run_mode=RunMode.CONTINUOUS,  # 24/7 FX markets
    interval_seconds=86400,  # Daily rebalancing check
)

# Run daemon (blocks until SIGTERM)
runner = StrategyRunner()
runner.register_strategy(strategy, runner_config)
runner.start()
```

## Data Sources

**FX Rates**: Frankfurter API (https://frankfurter.app)
- Free, no API key required
- ECB exchange rates for 30+ currencies
- Historical data back to 1999
- Daily close prices

**Interest Rates**: API Ninjas (https://api-ninjas.com/api/interestrate)
- Free tier: 1,000 requests/month
- 22 countries covered
- Central bank policy rates
- Updated every 4 hours
- Set environment variable: `export API_NINJAS_KEY=your_key_here`

## Risk Management

**Pre-Trade Validation**:
- All trades validated against position limits via RiskManager
- Max 20% per currency pair
- Max 1.0x portfolio leverage

**Position Tracking**:
- Trades recorded with risk manager for exposure monitoring
- Circuit breaker integration for systematic halt

**Portfolio Risk**:
- Target 10% annualized volatility
- Inverse volatility weighting for equal risk contribution
- Diversification across 10 uncorrelated currency pairs

## Performance Expectations

**Target Metrics** (based on carry trade literature):
- **Sharpe Ratio**: 0.5-0.8 (carry strategies typically 0.4-1.0)
- **Annualized Return**: 8-12% (depends on rate environment)
- **Max Drawdown**: <20% (with proper risk management)
- **Correlation to Equities**: <0.3 (low correlation for diversification)

**Trade Frequency**: Low (weekly rebalancing, 7-day frequency)

**Transaction Costs**: 0.1% commission + 0.05% slippage (15 bps total)

## Strategy Rationale

**Why Carry Trade Works**:
1. **Interest Rate Parity Deviation**: Forward rates overestimate depreciation
2. **Risk Premium**: Compensation for crash risk in high-yield currencies
3. **Volatility Risk Premium**: Selling volatility through carry positions

**Why Add Trend Filter**:
- Avoid carrying against strong trends (momentum crashes)
- Exit positions during risk-off episodes
- Improve risk-adjusted returns by 20-30%

**Why Volatility Scaling**:
- Equal risk contribution across positions
- Prevent concentration in high-volatility pairs
- Stabilize portfolio volatility near target

## Testing

Run tests:
```bash
pytest tests/strategies/fx_carry/ tests/test_fx_ingestor.py -v
```

Coverage target: >85% (currently 93% for signals module)

## Implementation Notes

- **Market Hours**: FX markets trade 24/7 (Sunday 5pm ET - Friday 5pm ET)
- **Rate Updates**: Interest rates updated monthly (30-day frequency)
- **Price Data**: 120-day lookback for trend/volatility calculation
- **Rebalancing**: Weekly check, 10% drift threshold to minimize turnover
- **Synthetic OHLC**: Frankfurter provides close prices; synthetic OHLC generated with 0.05% spreads

## Limitations

- **Daily Frequency Only**: Free data limited to daily closes (no intraday)
- **Interest Rate Lag**: Central bank rates update monthly (not real-time)
- **No Leverage**: Default 1.0x leverage (could use 2-3x for institutional)
- **Transaction Costs**: Assume 15 bps total (may vary by broker/pair)
- **Rate Stability**: Requires stable interest rate differentials (>1% minimum)

## Future Enhancements

- **Intraday Data**: Integrate paid FX data provider for sub-daily rebalancing
- **Forward Points**: Use forward rates for precise carry calculation
- **Regime Detection**: Adjust leverage based on volatility regime
- **Options Overlay**: Hedge tail risk with FX options
- **Momentum Overlay**: Combine with short-term momentum for higher returns
- **Liquidity Filters**: Exclude low-liquidity pairs during stress periods
