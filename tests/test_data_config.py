"""Unit tests for data configuration management."""
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.data.config.config_manager import ConfigManager


@pytest.fixture
def test_config_file():
    """Create a temporary test configuration file."""
    config_data = {
        "data_sources": {
            "yfinance": {"enabled": True, "rate_limit_per_second": 2, "timeout": 30},
            "alphavantage": {"enabled": False, "rate_limit_per_minute": 5},
        },
        "symbols": {
            "equities": ["AAPL", "GOOGL", "MSFT"],
            "crypto": ["BTC-USD", "ETH-USD"],
        },
        "ingestion": {
            "default_lookback_years": 2,
            "frequencies": ["1d"],
            "max_retries": 3,
        },
        "validation": {
            "price_spike_threshold": 0.10,
            "volume_spike_multiplier": 5.0,
        },
        "storage": {
            "base_path": "data",
            "format": "parquet",
            "compression": "snappy",
        },
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        yaml.dump(config_data, f)
        temp_path = f.name

    yield temp_path

    # Cleanup
    os.unlink(temp_path)


def test_config_manager_initialization(test_config_file):
    """Test ConfigManager initialization with custom config path."""
    config = ConfigManager(config_path=test_config_file)
    assert config.config_path == Path(test_config_file)
    assert config._config is not None


def test_config_manager_missing_file():
    """Test ConfigManager raises error for missing file."""
    with pytest.raises(FileNotFoundError):
        ConfigManager(config_path="/nonexistent/path.yaml")


def test_get_symbols(test_config_file):
    """Test retrieving symbols for an asset class."""
    config = ConfigManager(config_path=test_config_file)

    equities = config.get_symbols("equities")
    assert equities == ["AAPL", "GOOGL", "MSFT"]

    crypto = config.get_symbols("crypto")
    assert crypto == ["BTC-USD", "ETH-USD"]


def test_get_symbols_invalid_asset_class(test_config_file):
    """Test getting symbols for invalid asset class raises error."""
    config = ConfigManager(config_path=test_config_file)

    with pytest.raises(KeyError):
        config.get_symbols("invalid_asset_class")


def test_get_all_symbols(test_config_file):
    """Test retrieving all symbols across asset classes."""
    config = ConfigManager(config_path=test_config_file)

    all_symbols = config.get_all_symbols()
    assert "equities" in all_symbols
    assert "crypto" in all_symbols
    assert all_symbols["equities"] == ["AAPL", "GOOGL", "MSFT"]


def test_get_data_source_config(test_config_file):
    """Test retrieving data source configuration."""
    config = ConfigManager(config_path=test_config_file)

    yf_config = config.get_data_source_config("yfinance")
    assert yf_config["enabled"] is True
    assert yf_config["rate_limit_per_second"] == 2


def test_get_data_source_config_invalid_source(test_config_file):
    """Test getting config for invalid data source raises error."""
    config = ConfigManager(config_path=test_config_file)

    with pytest.raises(KeyError):
        config.get_data_source_config("invalid_source")


def test_is_source_enabled(test_config_file):
    """Test checking if data source is enabled."""
    config = ConfigManager(config_path=test_config_file)

    assert config.is_source_enabled("yfinance") is True
    assert config.is_source_enabled("alphavantage") is False
    assert config.is_source_enabled("nonexistent") is False


def test_get_ingestion_config(test_config_file):
    """Test retrieving ingestion configuration."""
    config = ConfigManager(config_path=test_config_file)

    ingestion = config.get_ingestion_config()
    assert ingestion["default_lookback_years"] == 2
    assert ingestion["frequencies"] == ["1d"]


def test_get_validation_config(test_config_file):
    """Test retrieving validation configuration."""
    config = ConfigManager(config_path=test_config_file)

    validation = config.get_validation_config()
    assert validation["price_spike_threshold"] == 0.10
    assert validation["volume_spike_multiplier"] == 5.0


def test_get_storage_config(test_config_file):
    """Test retrieving storage configuration."""
    config = ConfigManager(config_path=test_config_file)

    storage = config.get_storage_config()
    assert storage["base_path"] == "data"
    assert storage["format"] == "parquet"


def test_get_api_key(test_config_file, monkeypatch):
    """Test retrieving API keys from environment variables."""
    config = ConfigManager(config_path=test_config_file)

    # Set environment variable
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "test_key_123")

    api_key = config.get_api_key("alphavantage")
    assert api_key == "test_key_123"

    # Test missing API key
    api_key_missing = config.get_api_key("fredapi")
    assert api_key_missing is None

    # Test invalid source
    api_key_invalid = config.get_api_key("yfinance")
    assert api_key_invalid is None


def test_get_lookback_years(test_config_file):
    """Test retrieving default lookback years."""
    config = ConfigManager(config_path=test_config_file)

    lookback = config.get_lookback_years()
    assert lookback == 2


def test_get_frequencies(test_config_file):
    """Test retrieving data frequencies."""
    config = ConfigManager(config_path=test_config_file)

    frequencies = config.get_frequencies()
    assert frequencies == ["1d"]
