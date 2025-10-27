# Portfolio Allocation & Coordination (PAC)

**Purpose**: Multi-strategy capital allocation and portfolio-level risk management

## Components

**pac.py**: Core allocation engine with algorithms (equal weight, volatility-weighted, Sharpe-optimized, risk parity)
**performance_tracker.py**: Strategy performance tracking (returns, Sharpe, drawdown, correlation)
**portfolio_metrics.py**: Portfolio-level risk metrics (correlation matrix, diversification, factor exposure)

## Key Features

- **Dynamic Allocation**: Automated capital allocation across strategies based on performance
- **Risk Management**: Portfolio-level correlation monitoring and diversification analysis
- **Rebalancing**: Trigger-based reallocation (performance degradation, correlation changes, drawdown)
- **Audit Trail**: Complete logging of allocation decisions for compliance

## Usage

```python
from src.portfolio.pac import PortfolioAllocator, AllocationAlgorithm

# Initialize allocator
allocator = PortfolioAllocator(
    total_capital=1000000,
    algorithm=AllocationAlgorithm.SHARPE_OPTIMIZED,
    min_allocation_pct=0.05,
    max_allocation_pct=0.30
)

# Register strategies
allocator.register_strategy('pairs_1', max_capacity=200000)
allocator.register_strategy('momentum_1', max_capacity=150000)

# Record daily returns
allocator.record_strategy_return('pairs_1', 0.015)
allocator.record_strategy_return('momentum_1', 0.008)

# Rebalance (automatic or manual)
allocations = allocator.rebalance(trigger='manual')

# Get portfolio metrics
metrics = allocator.get_portfolio_metrics()
print(f"Portfolio Sharpe: {metrics.risk_metrics.portfolio_sharpe:.2f}")
print(f"Avg Correlation: {metrics.risk_metrics.avg_correlation:.2f}")
```

## Integration

**Risk Manager**: PAC coordinates with risk manager for position-level limits
**Strategy Runner**: Strategies report returns to PAC for allocation decisions
**Monitoring**: Allocation changes logged to audit trail and monitoring systems
