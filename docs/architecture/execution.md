# Order Management System (OMS) Architecture

**Version**: 1.0
**Last Updated**: 2025-10-25
**Status**: Design Phase

## Executive Summary

The Order Management System (OMS) is the critical execution layer for SolaraAlpha's trading infrastructure, managing the complete order lifecycle from signal generation through execution and position reconciliation. This architecture defines a robust, scalable system that bridges strategy signals and broker execution while maintaining strict risk controls and execution quality monitoring.

**Key Design Principles**:
- **Risk-first execution**: Pre-trade validation before every order
- **State machine clarity**: Explicit order state transitions with validation
- **Execution quality**: Slippage, fill rate, and performance tracking
- **Paper → Live migration**: Seamless transition from simulation to production
- **Broker abstraction**: Unified interface across multiple brokers (Alpaca initially)

**Core Components**:
1. **OrderManager** - Central order lifecycle management and state machine
2. **BrokerInterface** - Abstract broker integration (Alpaca implementation)
3. **PositionManager** - Real-time position tracking and P&L calculation
4. **ExecutionMonitor** - Quality metrics, slippage, fill rates
5. **OrderBook** - Order history, audit trail, reconciliation

**Critical Integration Points**:
- **Strategy Framework** (src/strategy/base.py): Signal generation → order creation
- **Risk Manager** (src/risk/risk_manager.py): Pre-trade validation
- **Backtesting Engine** (src/backtest/engine.py): Paper trading simulation

---

## 1. Architecture Overview

### 1.1 System Context

```
┌─────────────────────────────────────────────────────────────────┐
│                    Strategy Framework                            │
│  - Signal generation                                             │
│  - Position sizing                                               │
└──────────────────────┬──────────────────────────────────────────┘
                       │ Signals + Target Positions
                       ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Risk Manager                                  │
│  - Pre-trade validation                                          │
│  - Position limits check                                         │
│  - Exposure constraints                                          │
└──────────────────────┬──────────────────────────────────────────┘
                       │ Risk Decision (approved/rejected)
                       ↓
┌─────────────────────────────────────────────────────────────────┐
│               ORDER MANAGEMENT SYSTEM (OMS)                      │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Order        │  │ Position     │  │ Execution    │         │
│  │ Manager      │  │ Manager      │  │ Monitor      │         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
│         │                  │                  │                  │
│         └──────────────────┼──────────────────┘                  │
│                            │                                     │
│         ┌──────────────────┼──────────────────┐                 │
│         │                  │                  │                  │
│  ┌──────▼───────┐  ┌──────▼───────┐  ┌──────▼───────┐         │
│  │ Order Book   │  │ Broker       │  │ Fill         │         │
│  │              │  │ Interface    │  │ Handler      │         │
│  └──────────────┘  └──────┬───────┘  └──────────────┘         │
│                            │                                     │
└────────────────────────────┼─────────────────────────────────────┘
                             │ API Calls
                             ↓
                    ┌────────────────────┐
                    │   Broker (Alpaca)  │
                    │                    │
                    │  - Paper Trading   │
                    │  - Live Trading    │
                    └────────────────────┘
```

### 1.2 Data Flow

```
1. Signal Generation
   Strategy.generate_signals() → {symbol: Signal}
        ↓
2. Position Calculation
   Strategy.calculate_positions() → {symbol: target_quantity}
        ↓
3. Risk Validation
   RiskManager.pre_trade_check() → RiskDecision
        ↓
4. Order Creation
   OrderManager.create_order() → Order (state: PENDING)
        ↓
5. Broker Submission
   BrokerInterface.submit_order() → Order (state: SUBMITTED)
        ↓
6. Fill Notification
   BrokerInterface.poll_fills() → Fill
        ↓
7. Position Update
   PositionManager.process_fill() → Position
        ↓
8. Performance Tracking
   ExecutionMonitor.record_fill() → Metrics
```

---

## 2. Order Types and States

### 2.1 Supported Order Types

#### **2.1.1 Market Orders**
```python
@dataclass
class MarketOrder:
    """
    Market order - execute immediately at best available price.

    Attributes:
        symbol: Ticker symbol
        quantity: Number of shares (positive = buy, negative = sell)
        side: Order side ("buy" or "sell")
        time_in_force: Time-in-force ("day", "gtc", "ioc", "fok")
    """
    symbol: str
    quantity: float
    side: Literal["buy", "sell"]
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"
```

**Use Case**: Default order type for most strategies. Fast execution, accepts market price.

**Pros**: Fast, high fill probability
**Cons**: Slippage risk, no price control

#### **2.1.2 Limit Orders**
```python
@dataclass
class LimitOrder:
    """
    Limit order - execute only at specified price or better.

    Attributes:
        symbol: Ticker symbol
        quantity: Number of shares
        side: Order side
        limit_price: Maximum buy price / minimum sell price
        time_in_force: Time-in-force
    """
    symbol: str
    quantity: float
    side: Literal["buy", "sell"]
    limit_price: float
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"
```

**Use Case**: Price-sensitive executions, reducing slippage

**Pros**: Price control, reduced slippage
**Cons**: May not fill, slower execution

#### **2.1.3 Stop Orders**
```python
@dataclass
class StopOrder:
    """
    Stop order - triggers market order when stop price reached.

    Attributes:
        symbol: Ticker symbol
        quantity: Number of shares
        side: Order side
        stop_price: Trigger price
        time_in_force: Time-in-force
    """
    symbol: str
    quantity: float
    side: Literal["buy", "sell"]
    stop_price: float
    time_in_force: Literal["day", "gtc"] = "day"
```

**Use Case**: Stop-loss protection, breakout entries

**Pros**: Automatic risk management
**Cons**: Slippage on trigger, gap risk

#### **2.1.4 Stop-Limit Orders**
```python
@dataclass
class StopLimitOrder:
    """
    Stop-limit order - triggers limit order when stop price reached.

    Attributes:
        symbol: Ticker symbol
        quantity: Number of shares
        side: Order side
        stop_price: Trigger price
        limit_price: Limit price after trigger
        time_in_force: Time-in-force
    """
    symbol: str
    quantity: float
    side: Literal["buy", "sell"]
    stop_price: float
    limit_price: float
    time_in_force: Literal["day", "gtc"] = "day"
```

**Use Case**: Stop-loss with price protection

**Pros**: Price control after trigger
**Cons**: May not fill after trigger

### 2.2 Order State Machine

```
                    ┌───────────┐
                    │  CREATED  │ ← Order object initialized
                    └─────┬─────┘
                          │
                          ↓ pre_trade_check()
                    ┌───────────┐
              ┌─────┤  PENDING  │ ← Risk validation passed
              │     └─────┬─────┘
              │           │
              │           ↓ submit_order()
              │     ┌───────────┐
              │     │ SUBMITTED │ ← Sent to broker
              │     └─────┬─────┘
              │           │
              │           ├──→ broker confirms
              │           │
              │           ↓
              │     ┌───────────┐
              │     │   OPEN    │ ← Accepted by broker
              │     └─────┬─────┘
              │           │
              │           ├──→ partial fill
              │           │
              │           ↓
              │     ┌───────────┐
              │     │  PARTIAL  │ ← Partially filled
              │     └─────┬─────┘
              │           │
              │           ├──→ full fill
              │           │
              │           ↓
              │     ┌───────────┐
              │     │  FILLED   │ ← Completely filled (TERMINAL)
              │     └───────────┘
              │
              │           ↓ cancel_order()
              │     ┌───────────┐
              └────→│ CANCELED  │ ← Canceled by user/system (TERMINAL)
                    └───────────┘

                          ↓ broker rejection
                    ┌───────────┐
                    │ REJECTED  │ ← Rejected by broker (TERMINAL)
                    └───────────┘

                          ↓ system error
                    ┌───────────┐
                    │  ERROR    │ ← System error occurred (TERMINAL)
                    └───────────┘
```

**State Definitions**:

| State | Description | Terminal | Next States |
|-------|-------------|----------|-------------|
| CREATED | Order object created but not validated | No | PENDING, REJECTED |
| PENDING | Passed risk validation, awaiting submission | No | SUBMITTED, CANCELED |
| SUBMITTED | Sent to broker, awaiting confirmation | No | OPEN, REJECTED, ERROR |
| OPEN | Accepted by broker, awaiting fill | No | PARTIAL, FILLED, CANCELED |
| PARTIAL | Partially filled | No | FILLED, CANCELED |
| FILLED | Completely filled | **Yes** | - |
| CANCELED | Canceled before completion | **Yes** | - |
| REJECTED | Rejected by broker | **Yes** | - |
| ERROR | System error occurred | **Yes** | - |

**State Transition Rules**:
- Terminal states (FILLED, CANCELED, REJECTED, ERROR) cannot transition
- PARTIAL → CANCELED only if exchange supports partial cancels
- State changes must be logged with timestamps for audit trail

### 2.3 Time-in-Force (TIF) Options

| TIF | Description | Use Case |
|-----|-------------|----------|
| **DAY** | Valid until market close | Default for most orders |
| **GTC** | Good-til-canceled (until manual cancel) | Limit orders that may take days |
| **IOC** | Immediate-or-cancel (fill immediately, cancel remainder) | Large orders, minimize market impact |
| **FOK** | Fill-or-kill (fill completely or cancel) | All-or-nothing executions |

---

## 3. Core Components

### 3.1 Order Manager

**File**: `src/execution/order_manager.py`

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Literal
from enum import Enum
from datetime import datetime
import pandas as pd
import logging

logger = logging.getLogger(__name__)


class OrderType(Enum):
    """Order type enumeration."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderState(Enum):
    """Order state enumeration."""
    CREATED = "created"
    PENDING = "pending"
    SUBMITTED = "submitted"
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    ERROR = "error"


@dataclass
class Order:
    """
    Order representation with full lifecycle tracking.

    Attributes:
        order_id: Unique order identifier
        symbol: Ticker symbol
        quantity: Number of shares (positive only, use side for direction)
        side: Order side ("buy" or "sell")
        order_type: Order type (market, limit, stop, stop_limit)
        state: Current order state
        created_at: Order creation timestamp
        updated_at: Last state update timestamp

        # Price parameters
        limit_price: Limit price (for limit/stop-limit orders)
        stop_price: Stop price (for stop/stop-limit orders)

        # Execution parameters
        time_in_force: Time-in-force instruction
        filled_quantity: Quantity filled so far
        avg_fill_price: Average fill price

        # Broker tracking
        broker_order_id: Broker's order identifier
        broker_name: Broker name (e.g., "alpaca")

        # Metadata
        strategy_name: Strategy that generated this order
        metadata: Additional order metadata
    """
    order_id: str
    symbol: str
    quantity: float
    side: Literal["buy", "sell"]
    order_type: OrderType
    state: OrderState = OrderState.CREATED
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Price parameters
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None

    # Execution parameters
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0

    # Broker tracking
    broker_order_id: Optional[str] = None
    broker_name: Optional[str] = None

    # Metadata
    strategy_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Fill:
    """
    Fill event representing order execution.

    Attributes:
        fill_id: Unique fill identifier
        order_id: Associated order ID
        symbol: Ticker symbol
        quantity: Filled quantity
        price: Fill price
        timestamp: Fill timestamp
        commission: Commission charged
        side: Fill side ("buy" or "sell")
        broker_fill_id: Broker's fill identifier
    """
    fill_id: str
    order_id: str
    symbol: str
    quantity: float
    price: float
    timestamp: datetime
    commission: float = 0.0
    side: Literal["buy", "sell"] = "buy"
    broker_fill_id: Optional[str] = None


class OrderManager:
    """
    Central order management system.

    Responsibilities:
    - Order lifecycle management
    - State transition validation
    - Fill processing
    - Order history tracking
    - Integration with risk manager and broker

    Example:
        >>> from src.execution.order_manager import OrderManager, OrderType
        >>> from src.risk.risk_manager import RiskManager
        >>> from src.execution.alpaca_broker import AlpacaBroker
        >>>
        >>> risk_mgr = RiskManager()
        >>> broker = AlpacaBroker(api_key="...", secret_key="...", paper=True)
        >>> order_mgr = OrderManager(risk_manager=risk_mgr, broker=broker)
        >>>
        >>> # Create market order
        >>> order = order_mgr.create_market_order(
        ...     symbol="AAPL",
        ...     quantity=100,
        ...     side="buy",
        ...     strategy_name="PairsTradingStrategy"
        ... )
        >>>
        >>> # Submit to broker
        >>> if order_mgr.submit_order(order):
        ...     print(f"Order {order.order_id} submitted successfully")
    """

    def __init__(
        self,
        risk_manager: 'RiskManager',
        broker: 'BrokerInterface',
        enable_risk_checks: bool = True
    ):
        """
        Initialize order manager.

        Args:
            risk_manager: Risk manager for pre-trade validation
            broker: Broker interface for order execution
            enable_risk_checks: Enable pre-trade risk validation
        """
        self.risk_manager = risk_manager
        self.broker = broker
        self.enable_risk_checks = enable_risk_checks

        # Order tracking
        self.active_orders: Dict[str, Order] = {}  # order_id → Order
        self.order_history: List[Order] = []  # All orders
        self.fills: List[Fill] = []  # All fills

        # Order ID counter
        self._order_counter = 0

    def create_market_order(
        self,
        symbol: str,
        quantity: float,
        side: Literal["buy", "sell"],
        time_in_force: str = "day",
        strategy_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Order:
        """
        Create market order.

        Args:
            symbol: Ticker symbol
            quantity: Number of shares
            side: Order side ("buy" or "sell")
            time_in_force: Time-in-force ("day", "gtc", "ioc", "fok")
            strategy_name: Strategy name for tracking
            metadata: Additional metadata

        Returns:
            Order object in CREATED state
        """
        order_id = self._generate_order_id()

        order = Order(
            order_id=order_id,
            symbol=symbol,
            quantity=quantity,
            side=side,
            order_type=OrderType.MARKET,
            time_in_force=time_in_force,
            strategy_name=strategy_name,
            metadata=metadata or {}
        )

        self._add_order(order)
        return order

    def create_limit_order(
        self,
        symbol: str,
        quantity: float,
        side: Literal["buy", "sell"],
        limit_price: float,
        time_in_force: str = "day",
        strategy_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Order:
        """Create limit order."""
        order_id = self._generate_order_id()

        order = Order(
            order_id=order_id,
            symbol=symbol,
            quantity=quantity,
            side=side,
            order_type=OrderType.LIMIT,
            limit_price=limit_price,
            time_in_force=time_in_force,
            strategy_name=strategy_name,
            metadata=metadata or {}
        )

        self._add_order(order)
        return order

    def create_stop_order(
        self,
        symbol: str,
        quantity: float,
        side: Literal["buy", "sell"],
        stop_price: float,
        time_in_force: str = "day",
        strategy_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Order:
        """Create stop order."""
        order_id = self._generate_order_id()

        order = Order(
            order_id=order_id,
            symbol=symbol,
            quantity=quantity,
            side=side,
            order_type=OrderType.STOP,
            stop_price=stop_price,
            time_in_force=time_in_force,
            strategy_name=strategy_name,
            metadata=metadata or {}
        )

        self._add_order(order)
        return order

    def create_stop_limit_order(
        self,
        symbol: str,
        quantity: float,
        side: Literal["buy", "sell"],
        stop_price: float,
        limit_price: float,
        time_in_force: str = "day",
        strategy_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Order:
        """Create stop-limit order."""
        order_id = self._generate_order_id()

        order = Order(
            order_id=order_id,
            symbol=symbol,
            quantity=quantity,
            side=side,
            order_type=OrderType.STOP_LIMIT,
            stop_price=stop_price,
            limit_price=limit_price,
            time_in_force=time_in_force,
            strategy_name=strategy_name,
            metadata=metadata or {}
        )

        self._add_order(order)
        return order

    def submit_order(self, order: Order) -> bool:
        """
        Submit order to broker after risk validation.

        Args:
            order: Order to submit

        Returns:
            True if submission successful, False otherwise
        """
        # Validate order state
        if order.state != OrderState.CREATED and order.state != OrderState.PENDING:
            logger.error(
                f"Cannot submit order {order.order_id} in state {order.state.value}"
            )
            return False

        # Pre-trade risk check
        if self.enable_risk_checks:
            # Get current portfolio from position manager
            portfolio_df = self._get_current_portfolio()

            decision = self.risk_manager.pre_trade_check(
                symbol=order.symbol,
                quantity=order.quantity,
                side=order.side,
                price=order.limit_price or 0.0,  # Use limit price if available
                current_portfolio=portfolio_df
            )

            if not decision.approved:
                logger.warning(
                    f"Order {order.order_id} rejected by risk manager: {decision.reason}"
                )
                self._update_order_state(order, OrderState.REJECTED)
                order.metadata['rejection_reason'] = decision.reason
                return False

            # Update to PENDING state after risk approval
            self._update_order_state(order, OrderState.PENDING)

        # Submit to broker
        try:
            broker_order_id = self.broker.submit_order(order)

            if broker_order_id:
                order.broker_order_id = broker_order_id
                order.broker_name = self.broker.name
                self._update_order_state(order, OrderState.SUBMITTED)
                logger.info(
                    f"Order {order.order_id} submitted to {self.broker.name}: "
                    f"broker_id={broker_order_id}"
                )
                return True
            else:
                self._update_order_state(order, OrderState.ERROR)
                return False

        except Exception as e:
            logger.error(f"Error submitting order {order.order_id}: {e}")
            self._update_order_state(order, OrderState.ERROR)
            order.metadata['error'] = str(e)
            return False

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel active order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancellation successful
        """
        if order_id not in self.active_orders:
            logger.warning(f"Order {order_id} not found in active orders")
            return False

        order = self.active_orders[order_id]

        # Check if order can be canceled
        if order.state in [OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED]:
            logger.warning(f"Cannot cancel order {order_id} in terminal state {order.state.value}")
            return False

        # Cancel with broker if submitted
        if order.broker_order_id:
            try:
                if self.broker.cancel_order(order.broker_order_id):
                    self._update_order_state(order, OrderState.CANCELED)
                    logger.info(f"Order {order_id} canceled successfully")
                    return True
                else:
                    logger.error(f"Broker failed to cancel order {order_id}")
                    return False
            except Exception as e:
                logger.error(f"Error canceling order {order_id}: {e}")
                return False
        else:
            # Order not yet submitted to broker
            self._update_order_state(order, OrderState.CANCELED)
            return True

    def process_fill(self, fill: Fill) -> None:
        """
        Process fill event from broker.

        Args:
            fill: Fill event
        """
        # Find associated order
        if fill.order_id not in self.active_orders:
            logger.warning(f"Received fill for unknown order {fill.order_id}")
            return

        order = self.active_orders[fill.order_id]

        # Update order fill information
        order.filled_quantity += fill.quantity

        # Update average fill price
        if order.filled_quantity > 0:
            total_cost = (order.avg_fill_price * (order.filled_quantity - fill.quantity) +
                         fill.price * fill.quantity)
            order.avg_fill_price = total_cost / order.filled_quantity

        # Update order state
        if order.filled_quantity >= order.quantity:
            self._update_order_state(order, OrderState.FILLED)
            logger.info(
                f"Order {order.order_id} fully filled: {order.quantity} @ {order.avg_fill_price:.2f}"
            )
        else:
            self._update_order_state(order, OrderState.PARTIAL)
            logger.info(
                f"Order {order.order_id} partially filled: {order.filled_quantity}/{order.quantity}"
            )

        # Record fill
        self.fills.append(fill)

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        return self.active_orders.get(order_id)

    def get_active_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """
        Get all active orders, optionally filtered by symbol.

        Args:
            symbol: Optional symbol filter

        Returns:
            List of active orders
        """
        orders = list(self.active_orders.values())

        if symbol:
            orders = [o for o in orders if o.symbol == symbol]

        return orders

    def _generate_order_id(self) -> str:
        """Generate unique order ID."""
        self._order_counter += 1
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        return f"ORD-{timestamp}-{self._order_counter:06d}"

    def _add_order(self, order: Order) -> None:
        """Add order to tracking."""
        self.active_orders[order.order_id] = order
        self.order_history.append(order)

    def _update_order_state(self, order: Order, new_state: OrderState) -> None:
        """Update order state with validation."""
        old_state = order.state
        order.state = new_state
        order.updated_at = datetime.now()

        logger.debug(f"Order {order.order_id} state: {old_state.value} → {new_state.value}")

        # Remove from active orders if terminal state
        if new_state in [OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.ERROR]:
            if order.order_id in self.active_orders:
                del self.active_orders[order.order_id]

    def _get_current_portfolio(self) -> pd.DataFrame:
        """
        Get current portfolio for risk checks.

        Returns:
            DataFrame with columns [symbol, quantity, price, value, sector]
        """
        # This would integrate with PositionManager
        # Placeholder implementation
        return pd.DataFrame(columns=['symbol', 'quantity', 'price', 'value', 'sector'])
```

### 3.2 Broker Interface

**File**: `src/execution/broker_interface.py`

```python
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class BrokerAccount:
    """
    Broker account information.

    Attributes:
        account_id: Account identifier
        broker_name: Broker name
        account_type: Account type ("paper" or "live")
        cash: Available cash
        portfolio_value: Total portfolio value
        buying_power: Available buying power
        equity: Total equity
    """
    account_id: str
    broker_name: str
    account_type: Literal["paper", "live"]
    cash: float
    portfolio_value: float
    buying_power: float
    equity: float


class BrokerInterface(ABC):
    """
    Abstract broker interface.

    Defines unified API for broker integration. Implementations
    handle broker-specific authentication, API calls, and data mapping.

    Required implementations:
    - AlpacaBroker (paper + live)
    - Future: InteractiveBrokers, TD Ameritrade, etc.
    """

    def __init__(self, name: str, paper_trading: bool = True):
        """
        Initialize broker interface.

        Args:
            name: Broker name
            paper_trading: Use paper trading (simulation) mode
        """
        self.name = name
        self.paper_trading = paper_trading

    @abstractmethod
    def connect(self) -> bool:
        """
        Connect to broker API.

        Returns:
            True if connection successful
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from broker API."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to broker."""
        pass

    @abstractmethod
    def get_account(self) -> BrokerAccount:
        """
        Get account information.

        Returns:
            BrokerAccount with current account state
        """
        pass

    @abstractmethod
    def submit_order(self, order: Order) -> Optional[str]:
        """
        Submit order to broker.

        Args:
            order: Order to submit

        Returns:
            Broker order ID if successful, None otherwise
        """
        pass

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """
        Cancel order with broker.

        Args:
            broker_order_id: Broker's order ID

        Returns:
            True if cancellation successful
        """
        pass

    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> Dict[str, Any]:
        """
        Get order status from broker.

        Args:
            broker_order_id: Broker's order ID

        Returns:
            Order status dictionary
        """
        pass

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get all current positions from broker.

        Returns:
            List of position dictionaries
        """
        pass

    @abstractmethod
    def poll_fills(self) -> List[Fill]:
        """
        Poll broker for new fills.

        Returns:
            List of new fill events
        """
        pass
```

### 3.3 Alpaca Broker Implementation

**File**: `src/execution/alpaca_broker.py`

```python
from src.execution.broker_interface import BrokerInterface, BrokerAccount
from src.execution.order_manager import Order, Fill, OrderType
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

# Alpaca SDK
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, LimitOrderRequest, StopOrderRequest, StopLimitOrderRequest
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderType as AlpacaOrderType
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    logger.warning("Alpaca SDK not installed. Install with: pip install alpaca-py")

logger = logging.getLogger(__name__)


class AlpacaBroker(BrokerInterface):
    """
    Alpaca broker implementation.

    Supports:
    - Paper trading (default)
    - Live trading
    - Market, limit, stop, stop-limit orders
    - Real-time position and fill updates

    Example:
        >>> # Paper trading
        >>> broker = AlpacaBroker(
        ...     api_key="PK...",
        ...     secret_key="...",
        ...     paper=True
        ... )
        >>> broker.connect()
        >>>
        >>> # Check account
        >>> account = broker.get_account()
        >>> print(f"Buying power: ${account.buying_power:,.2f}")
        >>>
        >>> # Submit order
        >>> order = Order(...)  # Order from OrderManager
        >>> broker_order_id = broker.submit_order(order)
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool = True
    ):
        """
        Initialize Alpaca broker.

        Args:
            api_key: Alpaca API key
            secret_key: Alpaca secret key
            paper: Use paper trading (default: True)
        """
        super().__init__(name="alpaca", paper_trading=paper)

        if not ALPACA_AVAILABLE:
            raise ImportError("Alpaca SDK not installed. Install with: pip install alpaca-py")

        self.api_key = api_key
        self.secret_key = secret_key
        self.client: Optional[TradingClient] = None

        # Fill tracking
        self._last_fill_check = datetime.now()
        self._processed_fill_ids = set()

    def connect(self) -> bool:
        """Connect to Alpaca API."""
        try:
            self.client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=self.paper_trading
            )

            # Verify connection
            account = self.client.get_account()

            logger.info(
                f"Connected to Alpaca ({'paper' if self.paper_trading else 'live'}): "
                f"account_id={account.account_number}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Alpaca: {e}")
            return False

    def disconnect(self) -> None:
        """Disconnect from Alpaca."""
        self.client = None
        logger.info("Disconnected from Alpaca")

    def is_connected(self) -> bool:
        """Check connection status."""
        return self.client is not None

    def get_account(self) -> BrokerAccount:
        """Get account information."""
        if not self.client:
            raise RuntimeError("Not connected to Alpaca")

        account = self.client.get_account()

        return BrokerAccount(
            account_id=account.account_number,
            broker_name=self.name,
            account_type="paper" if self.paper_trading else "live",
            cash=float(account.cash),
            portfolio_value=float(account.portfolio_value),
            buying_power=float(account.buying_power),
            equity=float(account.equity)
        )

    def submit_order(self, order: Order) -> Optional[str]:
        """Submit order to Alpaca."""
        if not self.client:
            raise RuntimeError("Not connected to Alpaca")

        # Map order side
        side = OrderSide.BUY if order.side == "buy" else OrderSide.SELL

        # Map time-in-force
        tif_map = {
            "day": TimeInForce.DAY,
            "gtc": TimeInForce.GTC,
            "ioc": TimeInForce.IOC,
            "fok": TimeInForce.FOK
        }
        tif = tif_map.get(order.time_in_force, TimeInForce.DAY)

        try:
            # Create order request based on type
            if order.order_type == OrderType.MARKET:
                request = MarketOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    time_in_force=tif
                )
            elif order.order_type == OrderType.LIMIT:
                request = LimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    time_in_force=tif,
                    limit_price=order.limit_price
                )
            elif order.order_type == OrderType.STOP:
                request = StopOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    time_in_force=tif,
                    stop_price=order.stop_price
                )
            elif order.order_type == OrderType.STOP_LIMIT:
                request = StopLimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    time_in_force=tif,
                    stop_price=order.stop_price,
                    limit_price=order.limit_price
                )
            else:
                logger.error(f"Unsupported order type: {order.order_type}")
                return None

            # Submit order
            alpaca_order = self.client.submit_order(request)

            logger.info(
                f"Alpaca order submitted: {alpaca_order.id} ({order.symbol} "
                f"{order.side} {order.quantity})"
            )

            return alpaca_order.id

        except Exception as e:
            logger.error(f"Error submitting order to Alpaca: {e}")
            return None

    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel order."""
        if not self.client:
            raise RuntimeError("Not connected to Alpaca")

        try:
            self.client.cancel_order_by_id(broker_order_id)
            logger.info(f"Alpaca order canceled: {broker_order_id}")
            return True
        except Exception as e:
            logger.error(f"Error canceling Alpaca order {broker_order_id}: {e}")
            return False

    def get_order_status(self, broker_order_id: str) -> Dict[str, Any]:
        """Get order status."""
        if not self.client:
            raise RuntimeError("Not connected to Alpaca")

        try:
            order = self.client.get_order_by_id(broker_order_id)

            return {
                'order_id': order.id,
                'symbol': order.symbol,
                'quantity': float(order.qty),
                'filled_quantity': float(order.filled_qty),
                'status': order.status,
                'filled_avg_price': float(order.filled_avg_price) if order.filled_avg_price else 0.0
            }
        except Exception as e:
            logger.error(f"Error getting order status from Alpaca: {e}")
            return {}

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all positions."""
        if not self.client:
            raise RuntimeError("Not connected to Alpaca")

        try:
            positions = self.client.get_all_positions()

            return [
                {
                    'symbol': pos.symbol,
                    'quantity': float(pos.qty),
                    'avg_entry_price': float(pos.avg_entry_price),
                    'current_price': float(pos.current_price),
                    'market_value': float(pos.market_value),
                    'unrealized_pnl': float(pos.unrealized_pl)
                }
                for pos in positions
            ]
        except Exception as e:
            logger.error(f"Error getting positions from Alpaca: {e}")
            return []

    def poll_fills(self) -> List[Fill]:
        """Poll for new fills."""
        if not self.client:
            raise RuntimeError("Not connected to Alpaca")

        fills = []

        try:
            # Get all orders updated since last check
            # Note: Alpaca doesn't have a direct "fills" endpoint,
            # so we check filled orders
            orders = self.client.get_orders(
                status='all',  # Get all statuses to catch fills
                after=self._last_fill_check
            )

            for order in orders:
                # Skip if already processed
                fill_id = f"{order.id}_{order.filled_qty}"
                if fill_id in self._processed_fill_ids:
                    continue

                # Check if filled
                if float(order.filled_qty) > 0:
                    fill = Fill(
                        fill_id=fill_id,
                        order_id=order.client_order_id or order.id,
                        symbol=order.symbol,
                        quantity=float(order.filled_qty),
                        price=float(order.filled_avg_price),
                        timestamp=order.filled_at or order.updated_at,
                        commission=0.0,  # Alpaca doesn't charge commissions
                        side=order.side.value,
                        broker_fill_id=order.id
                    )

                    fills.append(fill)
                    self._processed_fill_ids.add(fill_id)

            self._last_fill_check = datetime.now()

        except Exception as e:
            logger.error(f"Error polling fills from Alpaca: {e}")

        return fills
```

---

## 4. Position Management

### 4.1 Position Manager

**File**: `src/execution/position_manager.py`

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import pandas as pd
import logging

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """
    Position representation with P&L tracking.

    Attributes:
        symbol: Ticker symbol
        quantity: Current quantity (positive = long, negative = short)
        avg_price: Average entry price
        current_price: Latest market price
        market_value: Current market value (quantity * current_price)
        cost_basis: Total cost basis
        unrealized_pnl: Unrealized profit/loss
        realized_pnl: Realized profit/loss (from closed trades)
        opened_at: Position open timestamp
        updated_at: Last update timestamp
    """
    symbol: str
    quantity: float
    avg_price: float
    current_price: float
    market_value: float = 0.0
    cost_basis: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    opened_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


class PositionManager:
    """
    Position tracking and P&L calculation.

    Responsibilities:
    - Real-time position tracking
    - Average price calculation
    - Unrealized P&L calculation
    - Realized P&L calculation (FIFO method)
    - Position reconciliation with broker

    Example:
        >>> pos_mgr = PositionManager()
        >>>
        >>> # Process fill
        >>> fill = Fill(...)
        >>> pos_mgr.process_fill(fill)
        >>>
        >>> # Get position
        >>> position = pos_mgr.get_position("AAPL")
        >>> print(f"P&L: ${position.unrealized_pnl:,.2f}")
        >>>
        >>> # Update market prices
        >>> pos_mgr.update_prices({"AAPL": 150.50, "MSFT": 380.25})
    """

    def __init__(self):
        """Initialize position manager."""
        self.positions: Dict[str, Position] = {}  # symbol → Position
        self.trade_history: List[Dict] = []  # All trades for P&L calculation

    def process_fill(self, fill: Fill) -> None:
        """
        Process fill and update positions.

        Args:
            fill: Fill event
        """
        symbol = fill.symbol

        # Determine direction (buy increases quantity, sell decreases)
        quantity_delta = fill.quantity if fill.side == "buy" else -fill.quantity

        if symbol not in self.positions:
            # New position
            if quantity_delta != 0:
                self.positions[symbol] = Position(
                    symbol=symbol,
                    quantity=quantity_delta,
                    avg_price=fill.price,
                    current_price=fill.price,
                    cost_basis=abs(quantity_delta) * fill.price,
                    market_value=quantity_delta * fill.price
                )
                logger.info(f"Opened position: {symbol} {quantity_delta} @ {fill.price}")
        else:
            # Update existing position
            position = self.positions[symbol]
            old_quantity = position.quantity
            new_quantity = old_quantity + quantity_delta

            if old_quantity == 0:
                # Reopening position
                position.quantity = quantity_delta
                position.avg_price = fill.price
                position.cost_basis = abs(quantity_delta) * fill.price
                position.opened_at = fill.timestamp

            elif (old_quantity > 0 and quantity_delta > 0) or (old_quantity < 0 and quantity_delta < 0):
                # Adding to position (same direction)
                total_cost = position.avg_price * abs(old_quantity) + fill.price * abs(quantity_delta)
                position.quantity = new_quantity
                position.avg_price = total_cost / abs(new_quantity)
                position.cost_basis = total_cost

            else:
                # Reducing or flipping position
                if abs(new_quantity) < abs(old_quantity):
                    # Reducing position - realize P&L on closed portion
                    closed_quantity = abs(quantity_delta)
                    pnl_per_share = fill.price - position.avg_price if old_quantity > 0 else position.avg_price - fill.price
                    realized_pnl = pnl_per_share * closed_quantity

                    position.realized_pnl += realized_pnl
                    position.quantity = new_quantity
                    position.cost_basis = abs(new_quantity) * position.avg_price

                    logger.info(
                        f"Reduced position: {symbol} {old_quantity} → {new_quantity}, "
                        f"realized P&L: ${realized_pnl:,.2f}"
                    )

                elif new_quantity == 0:
                    # Closing position completely
                    pnl_per_share = fill.price - position.avg_price if old_quantity > 0 else position.avg_price - fill.price
                    realized_pnl = pnl_per_share * abs(old_quantity)

                    position.realized_pnl += realized_pnl
                    position.quantity = 0
                    position.cost_basis = 0

                    logger.info(
                        f"Closed position: {symbol}, realized P&L: ${realized_pnl:,.2f}"
                    )

                    # Remove position from active tracking
                    del self.positions[symbol]

                else:
                    # Flipping position (long → short or short → long)
                    # First, close old position
                    pnl_per_share = fill.price - position.avg_price if old_quantity > 0 else position.avg_price - fill.price
                    realized_pnl = pnl_per_share * abs(old_quantity)
                    position.realized_pnl += realized_pnl

                    # Then, open new position
                    position.quantity = new_quantity
                    position.avg_price = fill.price
                    position.cost_basis = abs(new_quantity) * fill.price
                    position.opened_at = fill.timestamp

                    logger.info(
                        f"Flipped position: {symbol} {old_quantity} → {new_quantity}, "
                        f"realized P&L: ${realized_pnl:,.2f}"
                    )

            # Update market value and unrealized P&L
            position.current_price = fill.price
            position.market_value = position.quantity * position.current_price

            if position.quantity != 0:
                pnl_per_share = position.current_price - position.avg_price if position.quantity > 0 else position.avg_price - position.current_price
                position.unrealized_pnl = pnl_per_share * abs(position.quantity)
            else:
                position.unrealized_pnl = 0.0

            position.updated_at = fill.timestamp

        # Record trade
        self.trade_history.append({
            'timestamp': fill.timestamp,
            'symbol': symbol,
            'quantity': quantity_delta,
            'price': fill.price,
            'side': fill.side,
            'commission': fill.commission
        })

    def update_prices(self, prices: Dict[str, float]) -> None:
        """
        Update current prices and recalculate unrealized P&L.

        Args:
            prices: Dict of {symbol: current_price}
        """
        for symbol, price in prices.items():
            if symbol in self.positions:
                position = self.positions[symbol]
                position.current_price = price
                position.market_value = position.quantity * price

                if position.quantity != 0:
                    pnl_per_share = price - position.avg_price if position.quantity > 0 else position.avg_price - price
                    position.unrealized_pnl = pnl_per_share * abs(position.quantity)

                position.updated_at = datetime.now()

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position by symbol."""
        return self.positions.get(symbol)

    def get_all_positions(self) -> List[Position]:
        """Get all positions."""
        return list(self.positions.values())

    def get_portfolio_value(self) -> float:
        """Calculate total portfolio value."""
        return sum(pos.market_value for pos in self.positions.values())

    def get_total_pnl(self) -> Dict[str, float]:
        """
        Calculate total P&L.

        Returns:
            Dict with {unrealized_pnl, realized_pnl, total_pnl}
        """
        unrealized = sum(pos.unrealized_pnl for pos in self.positions.values())
        realized = sum(pos.realized_pnl for pos in self.positions.values())

        return {
            'unrealized_pnl': unrealized,
            'realized_pnl': realized,
            'total_pnl': unrealized + realized
        }

    def to_dataframe(self) -> pd.DataFrame:
        """
        Convert positions to DataFrame for risk checks.

        Returns:
            DataFrame with columns [symbol, quantity, price, value, sector]
        """
        if not self.positions:
            return pd.DataFrame(columns=['symbol', 'quantity', 'price', 'value', 'sector'])

        records = []
        for pos in self.positions.values():
            records.append({
                'symbol': pos.symbol,
                'quantity': pos.quantity,
                'price': pos.current_price,
                'value': pos.market_value,
                'sector': 'Unknown'  # Would need sector mapping
            })

        return pd.DataFrame(records)
```

---

## 5. Execution Quality Monitoring

### 5.1 Execution Monitor

**File**: `src/execution/execution_monitor.py`

```python
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


@dataclass
class ExecutionMetrics:
    """
    Execution quality metrics.

    Attributes:
        total_orders: Total orders submitted
        filled_orders: Orders completely filled
        partial_fills: Orders partially filled
        canceled_orders: Orders canceled
        rejected_orders: Orders rejected
        fill_rate: Percentage of orders filled
        avg_fill_time: Average time to fill (seconds)
        avg_slippage: Average slippage (dollars per share)
        avg_slippage_bps: Average slippage (basis points)
        total_commission: Total commissions paid
    """
    total_orders: int = 0
    filled_orders: int = 0
    partial_fills: int = 0
    canceled_orders: int = 0
    rejected_orders: int = 0
    fill_rate: float = 0.0
    avg_fill_time: float = 0.0
    avg_slippage: float = 0.0
    avg_slippage_bps: float = 0.0
    total_commission: float = 0.0


class ExecutionMonitor:
    """
    Execution quality monitoring and analytics.

    Tracks:
    - Fill rates by order type
    - Slippage analysis
    - Fill time distribution
    - Commission costs
    - Execution quality by strategy

    Example:
        >>> monitor = ExecutionMonitor()
        >>>
        >>> # Record order submission
        >>> monitor.record_order_submitted(order)
        >>>
        >>> # Record fill
        >>> monitor.record_fill(order, fill, expected_price=150.00)
        >>>
        >>> # Get metrics
        >>> metrics = monitor.get_metrics()
        >>> print(f"Fill rate: {metrics.fill_rate:.1%}")
        >>> print(f"Avg slippage: {metrics.avg_slippage_bps:.1f} bps")
    """

    def __init__(self):
        """Initialize execution monitor."""
        self.order_submissions: List[Dict] = []
        self.fills: List[Dict] = []
        self.slippage_records: List[Dict] = []

    def record_order_submitted(self, order: Order) -> None:
        """
        Record order submission.

        Args:
            order: Submitted order
        """
        self.order_submissions.append({
            'timestamp': order.created_at,
            'order_id': order.order_id,
            'symbol': order.symbol,
            'order_type': order.order_type.value,
            'quantity': order.quantity,
            'side': order.side,
            'strategy_name': order.strategy_name
        })

    def record_fill(
        self,
        order: Order,
        fill: Fill,
        expected_price: Optional[float] = None
    ) -> None:
        """
        Record fill and calculate slippage.

        Args:
            order: Associated order
            fill: Fill event
            expected_price: Expected fill price (for slippage calculation)
        """
        # Calculate fill time
        fill_time = (fill.timestamp - order.created_at).total_seconds()

        # Calculate slippage if expected price provided
        slippage = 0.0
        slippage_bps = 0.0

        if expected_price:
            # For buys: positive slippage = paid more than expected
            # For sells: positive slippage = received less than expected
            if order.side == "buy":
                slippage = fill.price - expected_price
            else:
                slippage = expected_price - fill.price

            # Calculate in basis points
            if expected_price > 0:
                slippage_bps = (slippage / expected_price) * 10000

        # Record fill
        self.fills.append({
            'timestamp': fill.timestamp,
            'order_id': order.order_id,
            'symbol': order.symbol,
            'order_type': order.order_type.value,
            'quantity': fill.quantity,
            'price': fill.price,
            'side': fill.side,
            'fill_time_seconds': fill_time,
            'commission': fill.commission,
            'strategy_name': order.strategy_name
        })

        # Record slippage if calculated
        if expected_price:
            self.slippage_records.append({
                'timestamp': fill.timestamp,
                'order_id': order.order_id,
                'symbol': order.symbol,
                'order_type': order.order_type.value,
                'expected_price': expected_price,
                'actual_price': fill.price,
                'slippage': slippage,
                'slippage_bps': slippage_bps,
                'side': fill.side
            })

    def get_metrics(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        strategy_name: Optional[str] = None
    ) -> ExecutionMetrics:
        """
        Calculate execution metrics.

        Args:
            start_date: Filter start date
            end_date: Filter end date
            strategy_name: Filter by strategy

        Returns:
            ExecutionMetrics
        """
        # Convert to DataFrames for analysis
        orders_df = pd.DataFrame(self.order_submissions)
        fills_df = pd.DataFrame(self.fills)
        slippage_df = pd.DataFrame(self.slippage_records)

        # Apply filters
        if not orders_df.empty:
            if start_date:
                orders_df = orders_df[orders_df['timestamp'] >= start_date]
                fills_df = fills_df[fills_df['timestamp'] >= start_date] if not fills_df.empty else fills_df
                slippage_df = slippage_df[slippage_df['timestamp'] >= start_date] if not slippage_df.empty else slippage_df

            if end_date:
                orders_df = orders_df[orders_df['timestamp'] <= end_date]
                fills_df = fills_df[fills_df['timestamp'] <= end_date] if not fills_df.empty else fills_df
                slippage_df = slippage_df[slippage_df['timestamp'] <= end_date] if not slippage_df.empty else slippage_df

            if strategy_name:
                orders_df = orders_df[orders_df['strategy_name'] == strategy_name]
                fills_df = fills_df[fills_df['strategy_name'] == strategy_name] if not fills_df.empty else fills_df

        # Calculate metrics
        metrics = ExecutionMetrics()

        if not orders_df.empty:
            metrics.total_orders = len(orders_df)

            # Fill rate
            if not fills_df.empty:
                filled_order_ids = fills_df['order_id'].unique()
                metrics.filled_orders = len(filled_order_ids)
                metrics.fill_rate = metrics.filled_orders / metrics.total_orders

                # Average fill time
                metrics.avg_fill_time = fills_df['fill_time_seconds'].mean()

                # Total commission
                metrics.total_commission = fills_df['commission'].sum()

            # Slippage
            if not slippage_df.empty:
                metrics.avg_slippage = slippage_df['slippage'].mean()
                metrics.avg_slippage_bps = slippage_df['slippage_bps'].mean()

        return metrics

    def get_slippage_by_order_type(self) -> pd.DataFrame:
        """
        Analyze slippage by order type.

        Returns:
            DataFrame with slippage stats by order type
        """
        if not self.slippage_records:
            return pd.DataFrame()

        df = pd.DataFrame(self.slippage_records)

        stats = df.groupby('order_type').agg({
            'slippage': ['mean', 'median', 'std'],
            'slippage_bps': ['mean', 'median', 'std']
        }).round(4)

        return stats

    def get_fill_time_distribution(self) -> pd.DataFrame:
        """
        Analyze fill time distribution.

        Returns:
            DataFrame with fill time percentiles
        """
        if not self.fills:
            return pd.DataFrame()

        df = pd.DataFrame(self.fills)

        percentiles = df['fill_time_seconds'].quantile([0.25, 0.50, 0.75, 0.90, 0.95, 0.99])

        return pd.DataFrame({
            'percentile': ['p25', 'p50', 'p75', 'p90', 'p95', 'p99'],
            'fill_time_seconds': percentiles.values
        })
```

---

## 6. Risk Management Integration

### 6.1 Integration Touchpoints

```python
# File: src/execution/risk_integration.py

from src.risk.risk_manager import RiskManager, RiskDecision
from src.execution.order_manager import OrderManager, Order
from src.execution.position_manager import PositionManager
import logging

logger = logging.getLogger(__name__)


class RiskIntegratedOrderManager(OrderManager):
    """
    Order manager with enhanced risk integration.

    Extends base OrderManager with:
    - Automatic position reconciliation
    - Real-time risk limit monitoring
    - Automatic order rejection on risk violations
    """

    def __init__(
        self,
        risk_manager: RiskManager,
        position_manager: PositionManager,
        broker: 'BrokerInterface',
        enable_risk_checks: bool = True
    ):
        """Initialize risk-integrated order manager."""
        super().__init__(risk_manager, broker, enable_risk_checks)
        self.position_manager = position_manager

    def submit_order(self, order: Order) -> bool:
        """
        Submit order with enhanced risk checks.

        Includes:
        - Pre-trade validation
        - Position limit checks
        - Sector exposure validation
        - Portfolio constraints
        """
        # Get current portfolio from position manager
        portfolio_df = self.position_manager.to_dataframe()

        # Enhanced pre-trade check with current positions
        if self.enable_risk_checks:
            decision = self.risk_manager.pre_trade_check(
                symbol=order.symbol,
                quantity=order.quantity,
                side=order.side,
                price=order.limit_price or 0.0,
                current_portfolio=portfolio_df
            )

            if not decision.approved:
                logger.warning(
                    f"Order {order.order_id} rejected: {decision.reason}"
                )
                self._update_order_state(order, OrderState.REJECTED)
                order.metadata['rejection_reason'] = decision.reason

                # Notify strategy of rejection (if available)
                # This would call strategy.on_risk_rejection()

                return False

            # If risk manager suggests different size, update order
            if decision.suggested_size and decision.suggested_size != order.quantity:
                logger.info(
                    f"Order {order.order_id} size adjusted by risk manager: "
                    f"{order.quantity} → {decision.suggested_size}"
                )
                order.quantity = decision.suggested_size
                order.metadata['risk_adjusted'] = True

        # Proceed with parent's submit logic
        return super().submit_order(order)

    def process_fill(self, fill: Fill) -> None:
        """Process fill with position updates."""
        # Update order fill tracking
        super().process_fill(fill)

        # Update position manager
        self.position_manager.process_fill(fill)

        # Log updated position
        position = self.position_manager.get_position(fill.symbol)
        if position:
            logger.info(
                f"Position updated: {fill.symbol} qty={position.quantity} "
                f"avg={position.avg_price:.2f} unrealized_pnl=${position.unrealized_pnl:,.2f}"
            )
```

---

## 7. Paper Trading to Live Migration

### 7.1 Migration Strategy

**Phase 1: Paper Trading Validation** (2-4 weeks)
```
1. Deploy OMS with Alpaca paper trading
2. Run strategies in paper mode
3. Validate execution quality:
   - Fill rates > 95%
   - Slippage < 5 bps (market orders)
   - No order rejections due to system errors
4. Monitor position reconciliation accuracy
5. Verify P&L calculation matches broker
```

**Phase 2: Live Trading Preparation** (1-2 weeks)
```
1. Audit all OMS components
2. Implement additional safeguards:
   - Daily loss limits
   - Position size caps
   - Maximum order frequency limits
3. Create kill switch mechanism
4. Set up monitoring and alerting
5. Document runbook for live operations
```

**Phase 3: Live Trading Launch** (Gradual rollout)
```
1. Start with single strategy, small position sizes
2. Monitor for 1 week:
   - Execution quality unchanged vs paper
   - Risk limits enforced correctly
   - No unexpected behavior
3. Gradually increase position sizes
4. Add additional strategies one at a time
5. Scale to full capital allocation
```

### 7.2 Configuration Management

```python
# File: src/execution/config.py

from dataclasses import dataclass
from typing import Literal
import os


@dataclass
class ExecutionConfig:
    """
    Execution system configuration.

    Attributes:
        mode: Trading mode ("paper" or "live")
        broker_name: Broker identifier
        api_key: Broker API key
        secret_key: Broker secret key
        enable_risk_checks: Enable pre-trade validation
        max_orders_per_minute: Rate limiting
        enable_kill_switch: Enable emergency kill switch
        kill_switch_daily_loss_pct: Daily loss threshold for kill switch
    """
    mode: Literal["paper", "live"]
    broker_name: str = "alpaca"
    api_key: str = ""
    secret_key: str = ""
    enable_risk_checks: bool = True
    max_orders_per_minute: int = 60
    enable_kill_switch: bool = True
    kill_switch_daily_loss_pct: float = 0.05  # 5% daily loss triggers kill switch

    @classmethod
    def from_env(cls) -> 'ExecutionConfig':
        """Load configuration from environment variables."""
        return cls(
            mode=os.getenv('TRADING_MODE', 'paper'),
            broker_name=os.getenv('BROKER_NAME', 'alpaca'),
            api_key=os.getenv('ALPACA_API_KEY', ''),
            secret_key=os.getenv('ALPACA_SECRET_KEY', ''),
            enable_risk_checks=os.getenv('ENABLE_RISK_CHECKS', 'true').lower() == 'true',
            max_orders_per_minute=int(os.getenv('MAX_ORDERS_PER_MINUTE', '60')),
            enable_kill_switch=os.getenv('ENABLE_KILL_SWITCH', 'true').lower() == 'true',
            kill_switch_daily_loss_pct=float(os.getenv('KILL_SWITCH_DAILY_LOSS_PCT', '0.05'))
        )
```

### 7.3 Kill Switch Implementation

```python
# File: src/execution/kill_switch.py

from dataclasses import dataclass
from typing import Optional
from datetime import datetime, date
import logging

logger = logging.getLogger(__name__)


@dataclass
class KillSwitchState:
    """Kill switch state."""
    triggered: bool = False
    trigger_reason: Optional[str] = None
    trigger_time: Optional[datetime] = None
    daily_loss: float = 0.0


class KillSwitch:
    """
    Emergency kill switch for trading operations.

    Monitors:
    - Daily loss limits
    - Risk violations
    - System errors

    Actions when triggered:
    - Halt all new orders
    - Cancel all pending orders
    - Optionally close all positions
    """

    def __init__(
        self,
        order_manager: OrderManager,
        position_manager: PositionManager,
        daily_loss_limit_pct: float = 0.05
    ):
        """Initialize kill switch."""
        self.order_manager = order_manager
        self.position_manager = position_manager
        self.daily_loss_limit_pct = daily_loss_limit_pct

        self.state = KillSwitchState()
        self._starting_portfolio_value = 0.0
        self._last_reset_date = date.today()

    def check_and_trigger(self) -> bool:
        """
        Check conditions and trigger if necessary.

        Returns:
            True if kill switch triggered
        """
        # Reset daily tracking if new day
        if date.today() > self._last_reset_date:
            self._reset_daily_tracking()

        # Check daily loss limit
        portfolio_value = self.position_manager.get_portfolio_value()
        daily_loss_pct = (self._starting_portfolio_value - portfolio_value) / self._starting_portfolio_value

        if daily_loss_pct > self.daily_loss_limit_pct:
            self.trigger(
                f"Daily loss limit exceeded: {daily_loss_pct:.2%} > {self.daily_loss_limit_pct:.2%}"
            )
            return True

        return self.state.triggered

    def trigger(self, reason: str) -> None:
        """
        Trigger kill switch.

        Args:
            reason: Reason for triggering
        """
        if self.state.triggered:
            logger.warning("Kill switch already triggered")
            return

        logger.critical(f"KILL SWITCH TRIGGERED: {reason}")

        self.state.triggered = True
        self.state.trigger_reason = reason
        self.state.trigger_time = datetime.now()

        # Cancel all pending orders
        active_orders = self.order_manager.get_active_orders()
        for order in active_orders:
            try:
                self.order_manager.cancel_order(order.order_id)
                logger.info(f"Canceled order: {order.order_id}")
            except Exception as e:
                logger.error(f"Failed to cancel order {order.order_id}: {e}")

        logger.critical("All trading halted. Manual intervention required.")

    def reset(self) -> None:
        """Reset kill switch (manual operation)."""
        logger.warning("Kill switch manually reset")
        self.state = KillSwitchState()
        self._reset_daily_tracking()

    def _reset_daily_tracking(self) -> None:
        """Reset daily loss tracking."""
        self._starting_portfolio_value = self.position_manager.get_portfolio_value()
        self._last_reset_date = date.today()
        self.state.daily_loss = 0.0
```

---

## 8. Component Interaction Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        Strategy Layer                             │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Strategy.generate_signals() → {symbol: Signal}              │ │
│  │ Strategy.calculate_positions() → {symbol: target_quantity}  │ │
│  └────────────┬────────────────────────────────────────────────┘ │
└───────────────┼──────────────────────────────────────────────────┘
                │
                ↓ Target Positions
┌──────────────────────────────────────────────────────────────────┐
│                      Risk Management Layer                        │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ RiskManager.pre_trade_check()                               │ │
│  │   - Position size limits                                    │ │
│  │   - Sector exposure                                         │ │
│  │   - Portfolio constraints                                   │ │
│  │ → RiskDecision (approved/rejected + suggested_size)         │ │
│  └────────────┬────────────────────────────────────────────────┘ │
└───────────────┼──────────────────────────────────────────────────┘
                │
                ↓ Risk Decision
┌──────────────────────────────────────────────────────────────────┐
│                      Execution Layer (OMS)                        │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ OrderManager.create_order() → Order (CREATED)                ││
│  │ OrderManager.submit_order() → Order (SUBMITTED)              ││
│  └────────────┬─────────────────────────────────────────────────┘│
│               │                                                   │
│               ↓ Submit Order                                     │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ BrokerInterface (Alpaca)                                     ││
│  │   broker.submit_order() → broker_order_id                   ││
│  │   broker.poll_fills() → List[Fill]                          ││
│  └────────────┬─────────────────────────────────────────────────┘│
│               │                                                   │
│               ↓ Fill Events                                      │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ PositionManager.process_fill()                               ││
│  │   - Update positions                                         ││
│  │   - Calculate realized/unrealized P&L                        ││
│  │ → Position                                                   ││
│  └────────────┬─────────────────────────────────────────────────┘│
│               │                                                   │
│               ↓ Execution Metrics                                │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ ExecutionMonitor.record_fill()                               ││
│  │   - Slippage calculation                                     ││
│  │   - Fill time tracking                                       ││
│  │   - Fill rate analysis                                       ││
│  └──────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

---

## 9. Data Models

### 9.1 Database Schema

```sql
-- File: src/execution/schema.sql

-- Orders table
CREATE TABLE orders (
    order_id VARCHAR(50) PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    quantity REAL NOT NULL,
    side VARCHAR(10) NOT NULL,  -- 'buy' or 'sell'
    order_type VARCHAR(20) NOT NULL,  -- 'market', 'limit', 'stop', 'stop_limit'
    state VARCHAR(20) NOT NULL,

    -- Prices
    limit_price REAL,
    stop_price REAL,
    avg_fill_price REAL DEFAULT 0.0,

    -- Quantities
    filled_quantity REAL DEFAULT 0.0,

    -- Execution
    time_in_force VARCHAR(10) DEFAULT 'day',

    -- Broker tracking
    broker_order_id VARCHAR(100),
    broker_name VARCHAR(50),

    -- Metadata
    strategy_name VARCHAR(100),
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,

    -- Indexes
    INDEX idx_symbol (symbol),
    INDEX idx_state (state),
    INDEX idx_strategy (strategy_name),
    INDEX idx_created_at (created_at)
);

-- Fills table
CREATE TABLE fills (
    fill_id VARCHAR(50) PRIMARY KEY,
    order_id VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    commission REAL DEFAULT 0.0,
    side VARCHAR(10) NOT NULL,
    broker_fill_id VARCHAR(100),

    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    INDEX idx_order_id (order_id),
    INDEX idx_symbol (symbol),
    INDEX idx_timestamp (timestamp)
);

-- Positions table (current state)
CREATE TABLE positions (
    symbol VARCHAR(20) PRIMARY KEY,
    quantity REAL NOT NULL,
    avg_price REAL NOT NULL,
    current_price REAL NOT NULL,
    market_value REAL NOT NULL,
    cost_basis REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    realized_pnl REAL DEFAULT 0.0,
    opened_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

-- Execution metrics (daily snapshots)
CREATE TABLE execution_metrics (
    date DATE NOT NULL,
    strategy_name VARCHAR(100),
    total_orders INTEGER DEFAULT 0,
    filled_orders INTEGER DEFAULT 0,
    fill_rate REAL DEFAULT 0.0,
    avg_fill_time REAL DEFAULT 0.0,
    avg_slippage REAL DEFAULT 0.0,
    avg_slippage_bps REAL DEFAULT 0.0,
    total_commission REAL DEFAULT 0.0,

    PRIMARY KEY (date, strategy_name),
    INDEX idx_date (date)
);
```

---

## 10. Error Handling & Edge Cases

### 10.1 Error Scenarios

| Scenario | Detection | Handling | Recovery |
|----------|-----------|----------|----------|
| **Broker API Down** | Connection timeout | Log error, queue orders | Retry with exponential backoff |
| **Order Rejected** | Broker response | Update state to REJECTED | Alert strategy, log reason |
| **Partial Fill Timeout** | Time-based check | Cancel remaining quantity | Process partial fill, update position |
| **Price Gap (slippage)** | Fill price vs expected | Log slippage, alert if > threshold | Continue, monitor execution quality |
| **Position Mismatch** | Reconciliation check | Alert, log discrepancy | Manual review required |
| **Risk Limit Violation** | Pre-trade check | Reject order before submission | Notify strategy via on_risk_rejection() |
| **Network Interruption** | Connection loss | Cache orders locally | Resubmit after reconnection |
| **Kill Switch Trigger** | Daily loss limit | Halt all trading, cancel orders | Manual reset required |

### 10.2 Reconciliation Process

```python
# File: src/execution/reconciliation.py

from src.execution.position_manager import PositionManager
from src.execution.broker_interface import BrokerInterface
import logging

logger = logging.getLogger(__name__)


class Reconciliation:
    """
    Position reconciliation between OMS and broker.

    Runs periodically to detect and resolve discrepancies.
    """

    def __init__(
        self,
        position_manager: PositionManager,
        broker: BrokerInterface
    ):
        """Initialize reconciliation."""
        self.position_manager = position_manager
        self.broker = broker

    def reconcile_positions(self) -> Dict[str, Any]:
        """
        Reconcile positions with broker.

        Returns:
            Reconciliation report with discrepancies
        """
        # Get positions from both sources
        oms_positions = self.position_manager.get_all_positions()
        broker_positions = self.broker.get_positions()

        # Build comparison
        discrepancies = []

        oms_symbols = {pos.symbol for pos in oms_positions}
        broker_symbols = {pos['symbol'] for pos in broker_positions}

        # Check for missing positions
        missing_in_broker = oms_symbols - broker_symbols
        missing_in_oms = broker_symbols - oms_symbols

        for symbol in missing_in_broker:
            oms_pos = next(p for p in oms_positions if p.symbol == symbol)
            discrepancies.append({
                'symbol': symbol,
                'type': 'missing_in_broker',
                'oms_quantity': oms_pos.quantity,
                'broker_quantity': 0
            })

        for symbol in missing_in_oms:
            broker_pos = next(p for p in broker_positions if p['symbol'] == symbol)
            discrepancies.append({
                'symbol': symbol,
                'type': 'missing_in_oms',
                'oms_quantity': 0,
                'broker_quantity': broker_pos['quantity']
            })

        # Check for quantity mismatches
        common_symbols = oms_symbols & broker_symbols
        for symbol in common_symbols:
            oms_pos = next(p for p in oms_positions if p.symbol == symbol)
            broker_pos = next(p for p in broker_positions if p['symbol'] == symbol)

            if abs(oms_pos.quantity - broker_pos['quantity']) > 0.01:
                discrepancies.append({
                    'symbol': symbol,
                    'type': 'quantity_mismatch',
                    'oms_quantity': oms_pos.quantity,
                    'broker_quantity': broker_pos['quantity']
                })

        # Log discrepancies
        if discrepancies:
            logger.error(f"Position reconciliation found {len(discrepancies)} discrepancies")
            for disc in discrepancies:
                logger.error(
                    f"  {disc['symbol']}: {disc['type']} - "
                    f"OMS={disc['oms_quantity']} Broker={disc['broker_quantity']}"
                )
        else:
            logger.info("Position reconciliation: All positions match")

        return {
            'timestamp': datetime.now(),
            'total_discrepancies': len(discrepancies),
            'discrepancies': discrepancies
        }
```

---

## 11. Testing Strategy

### 11.1 Unit Tests

```python
# tests/test_order_manager.py

import pytest
from src.execution.order_manager import OrderManager, OrderType, OrderState
from src.risk.risk_manager import RiskManager


def test_create_market_order():
    """Test market order creation."""
    risk_mgr = RiskManager()
    broker = MockBroker()
    order_mgr = OrderManager(risk_mgr, broker)

    order = order_mgr.create_market_order(
        symbol="AAPL",
        quantity=100,
        side="buy",
        strategy_name="TestStrategy"
    )

    assert order.symbol == "AAPL"
    assert order.quantity == 100
    assert order.side == "buy"
    assert order.order_type == OrderType.MARKET
    assert order.state == OrderState.CREATED


def test_order_submission_with_risk_approval():
    """Test order submission with risk approval."""
    risk_mgr = RiskManager()
    broker = MockBroker()
    order_mgr = OrderManager(risk_mgr, broker, enable_risk_checks=True)

    order = order_mgr.create_market_order("AAPL", 10, "buy")

    # Should pass risk check and submit
    success = order_mgr.submit_order(order)

    assert success
    assert order.state == OrderState.SUBMITTED


def test_order_rejection_by_risk():
    """Test order rejection by risk manager."""
    risk_mgr = RiskManager()
    broker = MockBroker()
    order_mgr = OrderManager(risk_mgr, broker, enable_risk_checks=True)

    # Create oversized order
    order = order_mgr.create_market_order("AAPL", 10000, "buy")

    # Should be rejected by risk manager
    success = order_mgr.submit_order(order)

    assert not success
    assert order.state == OrderState.REJECTED
```

### 11.2 Integration Tests

```python
# tests/integration/test_oms_integration.py

import pytest
from src.execution.order_manager import OrderManager
from src.execution.position_manager import PositionManager
from src.execution.alpaca_broker import AlpacaBroker
from src.risk.risk_manager import RiskManager


@pytest.mark.integration
def test_full_order_lifecycle_paper_trading():
    """Test complete order lifecycle with Alpaca paper trading."""
    # Setup
    risk_mgr = RiskManager()
    broker = AlpacaBroker(
        api_key=os.getenv('ALPACA_API_KEY'),
        secret_key=os.getenv('ALPACA_SECRET_KEY'),
        paper=True
    )
    broker.connect()

    pos_mgr = PositionManager()
    order_mgr = OrderManager(risk_mgr, broker)

    # Create and submit order
    order = order_mgr.create_market_order(
        symbol="AAPL",
        quantity=1,  # Small size for testing
        side="buy"
    )

    success = order_mgr.submit_order(order)
    assert success

    # Poll for fill (may take a few seconds)
    import time
    max_wait = 30
    waited = 0

    while waited < max_wait:
        fills = broker.poll_fills()
        if fills:
            break
        time.sleep(1)
        waited += 1

    assert len(fills) > 0

    # Process fill
    for fill in fills:
        order_mgr.process_fill(fill)
        pos_mgr.process_fill(fill)

    # Verify position
    position = pos_mgr.get_position("AAPL")
    assert position is not None
    assert position.quantity == 1
```

### 11.3 Coverage Targets

| Component | Coverage Target | Priority |
|-----------|----------------|----------|
| OrderManager | 95% | Critical |
| PositionManager | 95% | Critical |
| BrokerInterface | 90% | Critical |
| AlpacaBroker | 85% | High |
| ExecutionMonitor | 85% | High |
| KillSwitch | 95% | Critical |

---

## 12. Deployment & Operations

### 12.1 Environment Configuration

```bash
# .env.example

# Trading Mode
TRADING_MODE=paper  # or 'live'

# Broker Configuration (Alpaca)
BROKER_NAME=alpaca
ALPACA_API_KEY=your_api_key_here
ALPACA_SECRET_KEY=your_secret_key_here

# Risk Management
ENABLE_RISK_CHECKS=true
MAX_ORDERS_PER_MINUTE=60

# Kill Switch
ENABLE_KILL_SWITCH=true
KILL_SWITCH_DAILY_LOSS_PCT=0.05

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/execution.log
```

### 12.2 Monitoring & Alerting

**Key Metrics to Monitor**:
- Fill rate (target: > 95%)
- Average slippage (target: < 5 bps for market orders)
- Order submission failures (target: < 1%)
- Position reconciliation discrepancies (target: 0)
- Daily loss vs kill switch threshold
- API latency (target: < 500ms)

**Alert Conditions**:
- Fill rate drops below 90%
- Slippage exceeds 10 bps
- Position reconciliation fails
- Daily loss approaches kill switch threshold (80%)
- Broker API connection lost

---

## 13. Document Control

**Approvals**:
- [ ] Peer review (via Task tool with general-purpose agent)
- [ ] CEO approval (via commit to main branch)

**Peer Review Protocol**:
- **Agent**: general-purpose
- **Focus Areas**:
  - Architecture completeness and scalability
  - Risk management integration points
  - Realistic execution modeling for paper trading
  - Error handling robustness
  - Migration path clarity

**Change History**:
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-10-25 | Claude (general-purpose) | Initial OMS architecture design |

**Next Review**: After peer review and before implementation

---

**End of Document**
