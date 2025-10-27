# Risk Management Framework

**Priority**: Risk Management > Profitability > Operational Efficiency

Real-time position tracking, limit enforcement, circuit breakers, and risk metrics calculation to prevent catastrophic losses before any live trading.

## Architecture

**Core Principle**: Defense-in-depth with multiple safety layers operating independently.

**Components**:
- `position_tracker.py` - Real-time position state across all strategies
- `limit_enforcer.py` - Pre-trade validation against position limits
- `circuit_breaker.py` - Drawdown monitoring with automatic trading halt
- `metrics_calculator.py` - VaR, CVaR, Sharpe ratio, portfolio beta
- `risk_manager.py` - Unified orchestration interface

**Configuration**: `config/risk_limits.yaml` - All thresholds, limits, parameters

## Usage

### Strategy Integration

```python
from src.risk import RiskManager

# Initialize (once at strategy startup)
risk_mgr = RiskManager(portfolio_value=100000)

# Before EVERY trade - validate against limits
result = risk_mgr.validate_trade(
    symbol='AAPL',
    asset_class='equities',
    strategy_id='pairs_1',
    quantity=100,
    price=150.0
)

if not result.passed:
    print(f"Trade rejected: {result.message}")
    # DO NOT EXECUTE TRADE
    return

# After trade execution - record position
risk_mgr.record_trade(
    symbol='AAPL',
    asset_class='equities',
    strategy_id='pairs_1',
    quantity=100,
    entry_price=150.0,
    current_price=150.0
)

# Regularly check circuit breaker (e.g., every price update)
if risk_mgr.check_circuit_breaker():
    print("🚨 CIRCUIT BREAKER ACTIVE - HALT ALL TRADING")
    # Flatten positions, alert IRM
```

### Risk Metrics Monitoring

```python
# Calculate current risk metrics
metrics = risk_mgr.calculate_risk_metrics()

print(f"95% VaR: ${metrics.var_95:,.2f}")
print(f"95% CVaR: ${metrics.cvar_95:,.2f}")
print(f"30-day Sharpe: {metrics.sharpe_30d:.2f}")
print(f"Portfolio Beta: {metrics.beta:.2f}")
print(f"Annualized Vol: {metrics.volatility:.2%}")

# Get exposure breakdown
exposure = risk_mgr.get_portfolio_exposure()
print(f"Gross: ${exposure['gross']:,.2f}")
print(f"Net: ${exposure['net']:,.2f}")
print(f"Total P&L: ${exposure['total_pnl']:,.2f}")
```

## Position Limits (Enforced Pre-Trade)

1. **Per-Strategy Limit**: Max 5% of portfolio per strategy
2. **Per-Asset Limit**: Max 10% of portfolio per symbol
3. **Gross Exposure**: Max 200% (long + short)
4. **Net Exposure**: Max 20% (market-neutral constraint)

**Violation Response**: Trade rejection with detailed reason logged

## Circuit Breaker (Automatic Halt)

**Trigger**: 10% drawdown from high-water mark (HWM)

**Response**:
1. Automatic trading halt across ALL strategies
2. Log critical alert with timestamp and reason
3. Persist HWM to survive restarts
4. Require IRM justification for reset

**IRM Authority** (per fund_structure_ceo_insights.md):
- Manual circuit breaker trigger (emergency kill switch)
- Override authority with required justification
- Real-time monitoring and escalation capability

## Risk Metrics

**Value at Risk (VaR)**: 95% confidence daily loss estimate (252-day lookback)

**Conditional VaR (CVaR)**: Expected loss beyond VaR threshold (tail risk)

**Sharpe Ratio**: Risk-adjusted returns at 30/90/365-day windows

**Portfolio Beta**: Systematic risk relative to SPY benchmark

**Volatility**: Annualized standard deviation of daily returns

**Max Drawdown**: Largest peak-to-trough decline in period

## Multi-Strategy Support

**Relative Value Strategies** (per CLAUDE.md focus):
- Pairs trading: Track spread exposure, not just individual legs
- Market-neutral: Monitor net exposure approaching zero
- Correlation risk: Detect correlation breakdown between pairs

**Multi-Timeframe**:
- High-frequency: Sub-second position updates supported
- Long-term: Daily risk metric recalculation acceptable
- Circuit breaker: Halts ALL timeframes simultaneously

## Configuration

Edit `config/risk_limits.yaml` to adjust:
- Position limits (per-strategy, per-asset, exposure)
- Drawdown threshold
- VaR/CVaR confidence levels and lookback windows
- Sharpe ratio calculation windows
- Benchmark symbol for beta calculation

**NEVER** bypass limits in production without IRM approval.

## Testing

Comprehensive test suite: `pytest tests/test_risk_*.py --cov=src/risk`

**Coverage Target**: >85% (non-negotiable per CLAUDE.md)

**Test Categories**:
- Unit tests: Each component independently
- Integration tests: Full workflow with mock market data
- Edge cases: Zero positions, extreme volatility, data gaps

## Integration Points

**Data Infrastructure** (#56): Leverages ParquetStorage for historical prices

**Orchestrator**: Risk manager provides system status for monitoring

**Strategies**: All strategies MUST call validate_trade() before execution

**Future**: Execution layer will integrate limit checks at order submission
