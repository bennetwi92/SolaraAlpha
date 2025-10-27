"""Tests for execution configuration."""

import os
import pytest

from src.execution.config import BrokerConfig, load_alpaca_config


class TestBrokerConfig:
    """Test suite for BrokerConfig."""

    def test_broker_config_creation(self):
        """Test creating BrokerConfig."""
        config = BrokerConfig(
            api_key="test_key",
            secret_key="test_secret",
            paper_trading=True,
            base_url="https://paper-api.alpaca.markets",
        )

        assert config.api_key == "test_key"
        assert config.secret_key == "test_secret"
        assert config.paper_trading is True
        assert config.base_url == "https://paper-api.alpaca.markets"

    def test_broker_config_defaults(self):
        """Test BrokerConfig with defaults."""
        config = BrokerConfig(api_key="key", secret_key="secret")

        assert config.paper_trading is True
        assert config.base_url is None


class TestLoadAlpacaConfig:
    """Test suite for load_alpaca_config."""

    def test_load_alpaca_config_success(self, monkeypatch):
        """Test successful config loading from environment."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_api_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret_key")
        monkeypatch.setenv("ALPACA_PAPER", "true")

        config = load_alpaca_config()

        assert config.api_key == "test_api_key"
        assert config.secret_key == "test_secret_key"
        assert config.paper_trading is True
        assert config.base_url == "https://paper-api.alpaca.markets"

    def test_load_alpaca_config_paper_false(self, monkeypatch):
        """Test config loading with paper trading disabled."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_api_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret_key")
        monkeypatch.setenv("ALPACA_PAPER", "false")

        config = load_alpaca_config()

        assert config.paper_trading is False
        assert config.base_url is None

    def test_load_alpaca_config_missing_api_key(self, monkeypatch):
        """Test that missing API key raises error."""
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret_key")

        with pytest.raises(ValueError, match="ALPACA_API_KEY and ALPACA_SECRET_KEY"):
            load_alpaca_config()

    def test_load_alpaca_config_missing_secret_key(self, monkeypatch):
        """Test that missing secret key raises error."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_api_key")
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

        with pytest.raises(ValueError, match="ALPACA_API_KEY and ALPACA_SECRET_KEY"):
            load_alpaca_config()

    def test_load_alpaca_config_default_paper(self, monkeypatch):
        """Test that paper trading defaults to true."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_api_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret_key")
        monkeypatch.delenv("ALPACA_PAPER", raising=False)

        config = load_alpaca_config()

        assert config.paper_trading is True
