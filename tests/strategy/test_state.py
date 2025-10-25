"""
Tests for StateManager
"""

import pytest
import tempfile
import json
from pathlib import Path
import pandas as pd
import numpy as np
from src.strategy.state import StateManager, StrategyStateEncoder, state_decoder
from src.strategy.base import (
    BaseStrategy,
    StrategyConfig,
    Signal,
    SignalType,
    Position,
    FillEvent
)


class DummyStrategy(BaseStrategy):
    """Dummy strategy for testing."""

    def generate_signals(self, timestamp, data):
        return {}

    def calculate_positions(self, signals, current_positions, portfolio_value):
        return {}


@pytest.fixture
def temp_storage():
    """Create temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def state_manager(temp_storage):
    """Create state manager with temp storage."""
    return StateManager(temp_storage, checkpoint_interval=10)


@pytest.fixture
def sample_strategy():
    """Create sample strategy."""
    config = StrategyConfig(
        name="TestStrategy",
        version="1.0.0",
        parameters={'param1': 123},
        symbols=['AAPL', 'MSFT']
    )
    return DummyStrategy(config)


def test_state_manager_initialization(temp_storage):
    """Test StateManager initialization."""
    manager = StateManager(temp_storage, checkpoint_interval=50)

    assert manager.storage_dir == temp_storage
    assert manager.checkpoint_interval == 50
    assert manager.update_count == 0
    assert temp_storage.exists()


def test_save_checkpoint(state_manager, sample_strategy):
    """Test saving strategy checkpoint."""
    timestamp = pd.Timestamp('2024-01-01 12:00:00')

    # Add some state to strategy
    sample_strategy.update_state('test_key', 'test_value')
    sample_strategy.performance['sharpe'] = 1.5

    # Add position
    fill = FillEvent(
        symbol='AAPL',
        quantity=100,
        price=150.0,
        timestamp=timestamp
    )
    sample_strategy.on_fill(fill)

    # Save checkpoint
    checkpoint_path = state_manager.save_checkpoint(sample_strategy, timestamp)

    assert checkpoint_path.exists()
    assert checkpoint_path.suffix == '.json'


def test_load_checkpoint(state_manager, sample_strategy):
    """Test loading strategy checkpoint."""
    timestamp = pd.Timestamp('2024-01-01 12:00:00')

    # Set up strategy state
    sample_strategy.update_state('test_key', 'test_value')
    sample_strategy.performance['sharpe'] = 1.5

    fill = FillEvent(
        symbol='AAPL',
        quantity=100,
        price=150.0,
        timestamp=timestamp
    )
    sample_strategy.on_fill(fill)

    # Save checkpoint
    checkpoint_path = state_manager.save_checkpoint(sample_strategy, timestamp)

    # Create new strategy instance
    config = StrategyConfig(
        name="TestStrategy",
        version="1.0.0",
        symbols=['AAPL', 'MSFT']
    )
    new_strategy = DummyStrategy(config)

    # Load checkpoint
    success = state_manager.load_checkpoint(new_strategy, checkpoint_path)

    assert success
    assert new_strategy.get_state('test_key') == 'test_value'
    assert new_strategy.performance['sharpe'] == 1.5
    assert 'AAPL' in new_strategy.current_positions
    assert new_strategy.current_positions['AAPL'].quantity == 100


def test_load_latest_checkpoint(state_manager, sample_strategy):
    """Test loading latest checkpoint when path not specified."""
    timestamp1 = pd.Timestamp('2024-01-01 12:00:00')
    timestamp2 = pd.Timestamp('2024-01-01 13:00:00')

    # Save two checkpoints
    sample_strategy.update_state('version', 1)
    state_manager.save_checkpoint(sample_strategy, timestamp1)

    import time
    time.sleep(0.1)  # Ensure different modification times

    sample_strategy.update_state('version', 2)
    state_manager.save_checkpoint(sample_strategy, timestamp2)

    # Load latest
    config = StrategyConfig(
        name="TestStrategy",
        version="1.0.0",
        symbols=['AAPL']
    )
    new_strategy = DummyStrategy(config)

    success = state_manager.load_checkpoint(new_strategy)

    assert success
    assert new_strategy.get_state('version') == 2  # Latest version


def test_load_checkpoint_not_found(state_manager):
    """Test loading checkpoint when none exist."""
    config = StrategyConfig(
        name="NonExistent",
        version="1.0.0",
        symbols=['AAPL']
    )
    strategy = DummyStrategy(config)

    success = state_manager.load_checkpoint(strategy)

    assert not success


def test_auto_checkpoint(state_manager, sample_strategy):
    """Test automatic checkpointing."""
    timestamp = pd.Timestamp('2024-01-01 12:00:00')

    # Call auto_checkpoint 9 times (interval is 10)
    for i in range(9):
        result = state_manager.auto_checkpoint(sample_strategy, timestamp, safe_point=True)
        assert result is None  # No checkpoint yet

    # 10th call should trigger checkpoint
    result = state_manager.auto_checkpoint(sample_strategy, timestamp, safe_point=True)
    assert result is not None
    assert result.exists()


def test_auto_checkpoint_unsafe_point(state_manager, sample_strategy):
    """Test auto_checkpoint doesn't checkpoint at unsafe points."""
    timestamp = pd.Timestamp('2024-01-01 12:00:00')

    # Call with safe_point=False
    result = state_manager.auto_checkpoint(sample_strategy, timestamp, safe_point=False)

    assert result is None


def test_state_encoder_numpy_array():
    """Test encoding numpy arrays."""
    encoder = StrategyStateEncoder()

    arr = np.array([1, 2, 3])
    encoded = encoder.default(arr)

    assert '__numpy_array__' in encoded
    assert encoded['__numpy_array__'] == [1, 2, 3]


def test_state_encoder_timestamp():
    """Test encoding pandas Timestamp."""
    encoder = StrategyStateEncoder()

    ts = pd.Timestamp('2024-01-01 12:00:00')
    encoded = encoder.default(ts)

    assert '__timestamp__' in encoded


def test_state_encoder_datetime():
    """Test encoding datetime."""
    from datetime import datetime
    encoder = StrategyStateEncoder()

    dt = datetime(2024, 1, 1, 12, 0, 0)
    encoded = encoder.default(dt)

    assert '__datetime__' in encoded


def test_state_decoder():
    """Test decoding custom types."""
    # Numpy array
    decoded = state_decoder({'__numpy_array__': [1, 2, 3]})
    assert isinstance(decoded, np.ndarray)
    assert list(decoded) == [1, 2, 3]

    # Timestamp
    decoded = state_decoder({'__timestamp__': '2024-01-01T12:00:00'})
    assert isinstance(decoded, pd.Timestamp)

    # Datetime
    decoded = state_decoder({'__datetime__': '2024-01-01T12:00:00'})
    assert isinstance(decoded.hour, int)  # Check it's a datetime-like object

    # Regular dict
    decoded = state_decoder({'key': 'value'})
    assert decoded == {'key': 'value'}


def test_checkpoint_serialization_with_custom_types(state_manager, sample_strategy):
    """Test checkpoint serialization with numpy/pandas types."""
    timestamp = pd.Timestamp('2024-01-01 12:00:00')

    # Add custom types to state
    sample_strategy.update_state('array', np.array([1, 2, 3]))
    sample_strategy.update_state('timestamp', pd.Timestamp('2024-01-01'))

    # Save checkpoint
    checkpoint_path = state_manager.save_checkpoint(sample_strategy, timestamp)

    # Load checkpoint
    config = StrategyConfig(
        name="TestStrategy",
        version="1.0.0",
        symbols=['AAPL']
    )
    new_strategy = DummyStrategy(config)

    success = state_manager.load_checkpoint(new_strategy, checkpoint_path)

    assert success
    assert isinstance(new_strategy.get_state('array'), np.ndarray)
    assert list(new_strategy.get_state('array')) == [1, 2, 3]
    assert isinstance(new_strategy.get_state('timestamp'), pd.Timestamp)


def test_checkpoint_thread_safety(state_manager, sample_strategy):
    """Test checkpoint thread safety with lock."""
    import threading
    timestamp = pd.Timestamp('2024-01-01 12:00:00')

    results = []

    def checkpoint_worker():
        for _ in range(5):
            result = state_manager.auto_checkpoint(sample_strategy, timestamp, safe_point=True)
            results.append(result)

    # Run multiple threads
    threads = [threading.Thread(target=checkpoint_worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Should have created 1-2 checkpoints (depends on timing)
    checkpoints = [r for r in results if r is not None]
    assert len(checkpoints) >= 1
