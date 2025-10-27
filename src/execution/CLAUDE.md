# Execution System

**Purpose**: Centralized order execution, fill tracking, execution quality measurement, and strategy orchestration for live trading operations.

**Architecture**: R-E-R separation—strategies submit orders via abstract interface, execution team optimizes implementation across all brokers.

**Components**:
- `clients/`: Broker integrations (Alpaca paper trading, mock client)
- `order_manager.py`: OMS logic, order state tracking
- `fill_tracker.py`: Position reconciliation, fill aggregation
- `metrics.py`: Slippage, latency, fill rate calculation
- `config.py`: Broker configuration, API key management
- `strategy_runner.py`: 24/7 strategy execution daemon with monitoring, scheduling, and graceful shutdown

**Strategy Runner Daemon**:
- **Purpose**: Runs multiple strategies continuously in paper trading mode
- **Features**: Concurrent execution, market hours scheduling, health monitoring, daily reporting, graceful shutdown (SIGTERM)
- **Integration**: Full monitoring/alerting, risk management pre-trade checks, structured logging
- **Interface**: Strategies implement `Strategy` abstract class (`run_iteration()`, `get_status()`, `shutdown()`)
- **Usage**: Register strategies with configs, call `start()` to run until shutdown signal

**Standards**: Paper trading ONLY (Q1 2025); live trading requires additional governance (Q4 2025).

**Dependencies**: Requires data pipeline for price feeds, risk management for pre-trade validation, monitoring for observability.
