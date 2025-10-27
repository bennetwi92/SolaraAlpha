"""Base classes for data ingestors."""
from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd


class DataIngestor(ABC):
    """Abstract base class for data ingestors.

    All data source implementations must inherit from this class and implement
    the fetch_ohlcv method.
    """

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV data for a symbol.

        Args:
            symbol: Symbol ticker.
            start_date: Start date for data.
            end_date: End date for data.
            frequency: Data frequency (e.g., '1d', '1h').

        Returns:
            DataFrame with datetime index and columns:
            ['Open', 'High', 'Low', 'Close', 'Volume']

        Raises:
            Exception: If data fetch fails.
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Get the name of this data source.

        Returns:
            Data source name string.
        """
        pass
