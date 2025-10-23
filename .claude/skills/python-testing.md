# Python Testing

## When
Testing data pipeline, backtest, strategy, risk, or execution systems

## What to Test

**ALWAYS**:
- Risk logic (limits, stop-loss, drawdown)
- Backtest fills (slippage, partial, commission)
- Data validation (gaps, duplicates, bad timestamps)
- Strategy signals (various market conditions)
- Order lifecycle

**NEVER**:
- Getters/setters
- Third-party internals
- Config loading
- Structures without logic

**Rule**: If failure = financial loss, TEST IT.

## Structure (Arrange-Act-Assert)

```python
def test_stop_loss_triggers_on_threshold():
    # ARRANGE
    position = Position(symbol="AAPL", shares=100, entry_price=150.0)
    risk_mgr = RiskManager(stop_loss_pct=0.05)

    # ACT
    should_exit = risk_mgr.check_stop_loss(position, current_price=142.0)

    # ASSERT
    assert should_exit is True, "Stop loss triggers at 5% loss"
```

## Patterns

**Fixtures**:
```python
@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01 09:30", periods=78, freq="5min"),
        "close": [100 + i * 0.1 for i in range(78)],
    })
```

**Parametrize**:
```python
@pytest.mark.parametrize("prices,signal", [
    ([100, 101, 102, 103], "buy"),    # Uptrend
    ([100, 99, 98, 97], "sell"),       # Downtrend
])
def test_signals(prices, signal):
    assert strategy.generate(prices) == signal
```

**Mock APIs**:
```python
def test_api_error(monkeypatch):
    def mock_timeout(*args):
        raise requests.Timeout()
    monkeypatch.setattr("requests.get", mock_timeout)

    with pytest.raises(DataFetchError):
        fetcher.get_bars("AAPL")
```

**Float assertions**:
```python
# Use tolerance
assert profit == pytest.approx(1234.56, abs=0.01)
```

## Examples

**Data validation**:
```python
def test_detect_missing_bars():
    dates = [datetime(2024,1,1,9,30), datetime(2024,1,1,9,31),
             datetime(2024,1,1,9,33)]  # Missing 9:32
    validator = DataValidator(interval_minutes=1)
    gaps = validator.find_missing_bars(dates)
    assert len(gaps) == 1
```

**Risk limits**:
```python
def test_position_limit_enforcement():
    risk_mgr = RiskManager(max_position_value=10000)
    portfolio.add_position("AAPL", shares=50, price=150.0)  # $7,500
    order = MarketOrder("AAPL", "buy", shares=34, price=150.0)  # +$5k
    assert risk_mgr.check_order(order, portfolio) is False
```

**Edge cases**:
```python
def test_strategy_handles_market_gap():
    df = pd.DataFrame({
        "timestamp": [datetime(2024,1,1,16,0), datetime(2024,1,2,9,30)],
        "close": [100, 105],  # 5% overnight gap
    })
    signal = strategy.generate_signal(df)
    assert signal in ["buy", "sell", "hold"]  # No crash
```

## Run

```bash
pytest tests/
pytest tests/test_risk.py::test_stop_loss_triggers_on_threshold
pytest --cov=src tests/
```

## Gotchas

- Test real scenarios (gaps, halts), not toy data
- Mock external APIs (never hit real broker)
- Use `pytest.approx` for floats
- Edge cases required (gaps, extreme moves)
- Type hints in test code
