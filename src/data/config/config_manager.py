"""Configuration management for SolaraAlpha data infrastructure."""
import os
from pathlib import Path
from typing import Any, Dict, List

import yaml


class ConfigManager:
    """Manages data source configuration from YAML files.

    Loads configuration for data sources, symbols, validation rules, and storage
    settings. Provides environment variable support for API keys.

    Example:
        >>> config = ConfigManager()
        >>> symbols = config.get_symbols('equities')
        >>> ['AAPL', 'GOOGL', 'MSFT', ...]
    """

    def __init__(self, config_path: str | None = None) -> None:
        """Initialize configuration manager.

        Args:
            config_path: Path to YAML config file. If None, uses default location.
        """
        if config_path is None:
            repo_root = Path(__file__).parent.parent.parent.parent
            config_path = repo_root / "config" / "data_sources.yaml"

        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file.

        Returns:
            Parsed configuration dictionary.

        Raises:
            FileNotFoundError: If config file doesn't exist.
            yaml.YAMLError: If config file is invalid.
        """
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {self.config_path}"
            )

        with open(self.config_path, "r") as f:
            return yaml.safe_load(f)

    def get_symbols(self, asset_class: str) -> List[str]:
        """Get list of symbols for an asset class.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto', 'forex').

        Returns:
            List of symbol strings.

        Raises:
            KeyError: If asset class not found in config.
        """
        symbols = self._config.get("symbols", {})
        if asset_class not in symbols:
            raise KeyError(f"Asset class '{asset_class}' not found in config")
        return symbols[asset_class]

    def get_all_symbols(self) -> Dict[str, List[str]]:
        """Get all symbols across all asset classes.

        Returns:
            Dictionary mapping asset class to list of symbols.
        """
        return self._config.get("symbols", {})

    def get_data_source_config(self, source: str) -> Dict[str, Any]:
        """Get configuration for a specific data source.

        Args:
            source: Data source name (e.g., 'yfinance', 'alphavantage').

        Returns:
            Data source configuration dictionary.

        Raises:
            KeyError: If data source not found in config.
        """
        sources = self._config.get("data_sources", {})
        if source not in sources:
            raise KeyError(f"Data source '{source}' not found in config")
        return sources[source]

    def is_source_enabled(self, source: str) -> bool:
        """Check if a data source is enabled.

        Args:
            source: Data source name.

        Returns:
            True if source is enabled, False otherwise.
        """
        try:
            config = self.get_data_source_config(source)
            return config.get("enabled", False)
        except KeyError:
            return False

    def get_ingestion_config(self) -> Dict[str, Any]:
        """Get data ingestion configuration.

        Returns:
            Ingestion configuration dictionary.
        """
        return self._config.get("ingestion", {})

    def get_validation_config(self) -> Dict[str, Any]:
        """Get data validation configuration.

        Returns:
            Validation configuration dictionary.
        """
        return self._config.get("validation", {})

    def get_storage_config(self) -> Dict[str, Any]:
        """Get storage configuration.

        Returns:
            Storage configuration dictionary.
        """
        return self._config.get("storage", {})

    def get_api_key(self, source: str) -> str | None:
        """Get API key for a data source from environment variables.

        Args:
            source: Data source name.

        Returns:
            API key string or None if not set.

        Environment Variables:
            ALPHAVANTAGE_API_KEY: Alpha Vantage API key
            FRED_API_KEY: FRED API key
        """
        env_var_map = {
            "alphavantage": "ALPHAVANTAGE_API_KEY",
            "fredapi": "FRED_API_KEY",
        }

        env_var = env_var_map.get(source.lower())
        if env_var is None:
            return None

        return os.getenv(env_var)

    def get_lookback_years(self) -> int:
        """Get default lookback period for historical data.

        Returns:
            Number of years to fetch for historical backfill.
        """
        ingestion = self.get_ingestion_config()
        return ingestion.get("default_lookback_years", 2)

    def get_frequencies(self) -> List[str]:
        """Get list of data frequencies to fetch.

        Returns:
            List of frequency strings (e.g., ['1d', '1h']).
        """
        ingestion = self.get_ingestion_config()
        return ingestion.get("frequencies", ["1d"])
