"""
Tests for ParameterManager
"""

import pytest
import tempfile
import json
from pathlib import Path
from src.strategy.parameters import ParameterManager, ParameterSet


@pytest.fixture
def temp_storage():
    """Create temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def param_manager(temp_storage):
    """Create parameter manager with temp storage."""
    return ParameterManager(temp_storage)


def test_parameter_set_creation():
    """Test ParameterSet creation and hash calculation."""
    params = {'fast_period': 20, 'slow_period': 50}
    param_set = ParameterSet(
        name="TestStrategy",
        version="1.0.0",
        parameters=params,
        created_at="2024-01-01T00:00:00"
    )

    assert param_set.name == "TestStrategy"
    assert param_set.version == "1.0.0"
    assert param_set.parameters == params
    assert param_set.hash != ""
    assert len(param_set.hash) == 8  # First 8 chars of SHA256


def test_parameter_set_hash_consistency():
    """Test that same parameters produce same hash."""
    params = {'a': 1, 'b': 2}

    param_set1 = ParameterSet(
        name="Test", version="1.0", parameters=params, created_at="2024-01-01"
    )
    param_set2 = ParameterSet(
        name="Test", version="1.0", parameters=params, created_at="2024-01-02"
    )

    # Same parameters should produce same hash regardless of created_at
    assert param_set1.hash == param_set2.hash


def test_save_parameters(param_manager):
    """Test saving parameters to disk."""
    params = {'fast_period': 20, 'slow_period': 50}

    param_set = param_manager.save_parameters(
        name="TestStrategy",
        version="1.0.0",
        parameters=params
    )

    assert param_set.name == "TestStrategy"
    assert param_set.version == "1.0.0"
    assert param_set.parameters == params

    # Check file exists
    files = list(param_manager.storage_dir.glob("TestStrategy_v1.0.0_*.json"))
    assert len(files) == 1


def test_load_parameters(param_manager):
    """Test loading parameters from disk."""
    params = {'fast_period': 20, 'slow_period': 50}

    # Save first
    saved_set = param_manager.save_parameters(
        name="TestStrategy",
        version="1.0.0",
        parameters=params
    )

    # Load
    loaded_set = param_manager.load_parameters(
        name="TestStrategy",
        version="1.0.0"
    )

    assert loaded_set is not None
    assert loaded_set.name == saved_set.name
    assert loaded_set.version == saved_set.version
    assert loaded_set.parameters == saved_set.parameters
    assert loaded_set.hash == saved_set.hash


def test_load_nonexistent_parameters(param_manager):
    """Test loading parameters that don't exist."""
    result = param_manager.load_parameters(
        name="NonExistent",
        version="1.0.0"
    )

    assert result is None


def test_list_versions(param_manager):
    """Test listing parameter versions."""
    # Save multiple versions
    param_manager.save_parameters(
        name="TestStrategy",
        version="1.0.0",
        parameters={'a': 1}
    )
    param_manager.save_parameters(
        name="TestStrategy",
        version="1.1.0",
        parameters={'a': 2}
    )
    param_manager.save_parameters(
        name="TestStrategy",
        version="2.0.0",
        parameters={'a': 3}
    )

    versions = param_manager.list_versions("TestStrategy")

    assert len(versions) == 3
    assert "1.0.0" in versions
    assert "1.1.0" in versions
    assert "2.0.0" in versions


def test_list_versions_empty(param_manager):
    """Test listing versions when none exist."""
    versions = param_manager.list_versions("NonExistent")
    assert len(versions) == 0


def test_multiple_saves_same_version(param_manager):
    """Test saving multiple parameter sets with same version."""
    # Save twice with same version but different parameters
    param_manager.save_parameters(
        name="TestStrategy",
        version="1.0.0",
        parameters={'a': 1}
    )
    param_manager.save_parameters(
        name="TestStrategy",
        version="1.0.0",
        parameters={'a': 2}
    )

    # Should have 2 files (different hashes)
    files = list(param_manager.storage_dir.glob("TestStrategy_v1.0.0_*.json"))
    assert len(files) == 2

    # Load should return most recent
    loaded = param_manager.load_parameters("TestStrategy", "1.0.0")
    assert loaded.parameters == {'a': 2}


def test_json_serialization(param_manager, temp_storage):
    """Test JSON serialization format."""
    params = {'fast': 20, 'slow': 50, 'nested': {'key': 'value'}}

    param_set = param_manager.save_parameters(
        name="TestStrategy",
        version="1.0.0",
        parameters=params
    )

    # Read JSON file directly
    files = list(temp_storage.glob("TestStrategy_v1.0.0_*.json"))
    assert len(files) == 1

    with open(files[0], 'r') as f:
        data = json.load(f)

    assert data['name'] == "TestStrategy"
    assert data['version'] == "1.0.0"
    assert data['parameters'] == params
    assert 'created_at' in data
    assert 'hash' in data
