"""Orchestrates data ingestion from multiple sources with fallback logic."""
import logging
from datetime import datetime, timedelta
from typing import List

import pandas as pd

from ..config.config_manager import ConfigManager
from .base import DataIngestor
from .yfinance_ingestor import YFinanceIngestor

logger = logging.getLogger(__name__)


class IngestManager:
    """Manages data ingestion from multiple sources with fallback.

    Attempts to fetch data from primary source (YFinance), falls back to
    secondary sources if primary fails. Handles retry logic and rate limiting.

    Example:
        >>> manager = IngestManager()
        >>> data = manager.fetch('AAPL', 'equities')
    """

    def __init__(self, config: ConfigManager | None = None) -> None:
        """Initialize ingestion manager.

        Args:
            config: Configuration manager. If None, creates new instance.
        """
        self.config = config or ConfigManager()
        self.ingestors: List[DataIngestor] = []

        # Initialize ingestors based on config
        self._init_ingestors()

    def _init_ingestors(self) -> None:
        """Initialize data source ingestors based on configuration."""
        # YFinance (primary source)
        if self.config.is_source_enabled("yfinance"):
            yf_config = self.config.get_data_source_config("yfinance")
            rate_limit = yf_config.get("rate_limit_per_second", 2)
            timeout = yf_config.get("timeout", 30)

            self.ingestors.append(
                YFinanceIngestor(rate_limit=rate_limit, timeout=timeout)
            )
            logger.info("Initialized YFinance ingestor")

        if not self.ingestors:
            logger.warning("No data ingestors enabled in configuration")

    def fetch(
        self,
        symbol: str,
        asset_class: str,
        frequency: str = "1d",
        lookback_years: int | None = None,
    ) -> tuple[pd.DataFrame, str]:
        """Fetch OHLCV data for a symbol with automatic fallback.

        Args:
            symbol: Symbol ticker.
            asset_class: Asset class (for logging).
            frequency: Data frequency.
            lookback_years: Years of historical data. If None, uses config default.

        Returns:
            Tuple of (DataFrame with OHLCV data, source name).

        Raises:
            Exception: If all data sources fail.
        """
        if lookback_years is None:
            lookback_years = self.config.get_lookback_years()

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_years * 365)

        # Try each ingestor in order
        last_error = None
        for ingestor in self.ingestors:
            try:
                logger.info(
                    f"Attempting to fetch {symbol} from {ingestor.get_name()}"
                )

                data = ingestor.fetch_ohlcv(
                    symbol, start_date, end_date, frequency
                )

                if not data.empty:
                    return data, ingestor.get_name()

            except Exception as e:
                logger.warning(
                    f"Failed to fetch {symbol} from {ingestor.get_name()}: {e}"
                )
                last_error = e
                continue

        # All sources failed
        error_msg = f"All data sources failed for {symbol}"
        if last_error:
            error_msg += f": {last_error}"

        raise Exception(error_msg)

    def fetch_incremental(
        self,
        symbol: str,
        asset_class: str,
        last_date: datetime,
        frequency: str = "1d",
    ) -> tuple[pd.DataFrame, str]:
        """Fetch incremental data since last update.

        Args:
            symbol: Symbol ticker.
            asset_class: Asset class.
            last_date: Last date in existing data.
            frequency: Data frequency.

        Returns:
            Tuple of (DataFrame with new OHLCV data, source name).

        Raises:
            Exception: If all data sources fail.
        """
        # Add buffer to catch corrections
        ingestion_config = self.config.get_ingestion_config()
        buffer_days = ingestion_config.get("update_buffer_days", 5)
        start_date = last_date - timedelta(days=buffer_days)
        end_date = datetime.now()

        # Try each ingestor in order
        last_error = None
        for ingestor in self.ingestors:
            try:
                logger.info(
                    f"Fetching incremental update for {symbol} "
                    f"from {ingestor.get_name()}"
                )

                data = ingestor.fetch_ohlcv(
                    symbol, start_date, end_date, frequency
                )

                if not data.empty:
                    return data, ingestor.get_name()

            except Exception as e:
                logger.warning(
                    f"Failed to fetch incremental {symbol} "
                    f"from {ingestor.get_name()}: {e}"
                )
                last_error = e
                continue

        # All sources failed
        error_msg = f"All data sources failed for incremental {symbol}"
        if last_error:
            error_msg += f": {last_error}"

        raise Exception(error_msg)

    def get_available_sources(self) -> List[str]:
        """Get list of available data sources.

        Returns:
            List of data source names.
        """
        return [ingestor.get_name() for ingestor in self.ingestors]
