"""Parquet-based storage engine for market data."""
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..config.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class ParquetStorage:
    """Manages market data persistence using Parquet format.

    Stores data in partitioned directory structure:
    {base_path}/{asset_class}/{symbol}/{frequency}/data.parquet

    Supports incremental updates, metadata tracking, and efficient querying.

    Example:
        >>> storage = ParquetStorage()
        >>> storage.write(df, 'equities', 'AAPL', '1d')
        >>> data = storage.read('equities', 'AAPL', '1d')
    """

    def __init__(self, config: ConfigManager | None = None) -> None:
        """Initialize storage engine.

        Args:
            config: Configuration manager. If None, creates new instance.
        """
        self.config = config or ConfigManager()
        storage_config = self.config.get_storage_config()

        # Get base path from config
        base_path = storage_config.get("base_path", "data")
        repo_root = Path(__file__).parent.parent.parent.parent
        self.base_path = repo_root / base_path

        self.compression = storage_config.get("compression", "snappy")

        # Ensure base directory exists
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_data_path(
        self, asset_class: str, symbol: str, frequency: str
    ) -> Path:
        """Get file path for a symbol's data.

        Args:
            asset_class: Asset class (e.g., 'equities', 'crypto').
            symbol: Symbol ticker.
            frequency: Data frequency (e.g., '1d', '1h').

        Returns:
            Path object for the data file.
        """
        path = (
            self.base_path / asset_class / symbol / frequency / "data.parquet"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _get_metadata_path(
        self, asset_class: str, symbol: str, frequency: str
    ) -> Path:
        """Get file path for a symbol's metadata.

        Args:
            asset_class: Asset class.
            symbol: Symbol ticker.
            frequency: Data frequency.

        Returns:
            Path object for the metadata file.
        """
        return (
            self.base_path
            / asset_class
            / symbol
            / frequency
            / "metadata.parquet"
        )

    def write(
        self,
        data: pd.DataFrame,
        asset_class: str,
        symbol: str,
        frequency: str,
        source: str = "unknown",
        validation_status: str = "unknown",
    ) -> None:
        """Write market data to storage.

        Args:
            data: DataFrame with datetime index and OHLCV columns.
            asset_class: Asset class.
            symbol: Symbol ticker.
            frequency: Data frequency.
            source: Data source name (e.g., 'yfinance').
            validation_status: Validation result ('passed', 'failed', etc.).

        Raises:
            ValueError: If data is empty or missing required columns.
        """
        if data.empty:
            raise ValueError(f"Cannot write empty data for {symbol}")

        # Validate required columns
        required_cols = ["Open", "High", "Low", "Close", "Volume"]
        missing_cols = [col for col in required_cols if col not in data.columns]
        if missing_cols:
            raise ValueError(
                f"Missing required columns for {symbol}: {missing_cols}"
            )

        # Ensure datetime index
        if not isinstance(data.index, pd.DatetimeIndex):
            raise ValueError(f"Data for {symbol} must have DatetimeIndex")

        # Sort by date
        data = data.sort_index()

        # Get file path
        data_path = self._get_data_path(asset_class, symbol, frequency)

        # Write data to Parquet
        table = pa.Table.from_pandas(data)
        pq.write_table(
            table, data_path, compression=self.compression, version="2.6"
        )

        # Write metadata
        metadata = pd.DataFrame(
            {
                "last_updated": [datetime.now()],
                "source": [source],
                "validation_status": [validation_status],
                "record_count": [len(data)],
                "start_date": [data.index.min()],
                "end_date": [data.index.max()],
            }
        )
        metadata_path = self._get_metadata_path(asset_class, symbol, frequency)
        metadata_table = pa.Table.from_pandas(metadata)
        pq.write_table(metadata_table, metadata_path, compression=self.compression)

        logger.info(
            f"Wrote {len(data)} records for {symbol} ({asset_class}/{frequency}) "
            f"to {data_path}"
        )

    def read(
        self,
        asset_class: str,
        symbol: str,
        frequency: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> pd.DataFrame:
        """Read market data from storage.

        Args:
            asset_class: Asset class.
            symbol: Symbol ticker.
            frequency: Data frequency.
            start_date: Optional start date filter.
            end_date: Optional end date filter.

        Returns:
            DataFrame with datetime index and OHLCV columns.

        Raises:
            FileNotFoundError: If data file doesn't exist.
        """
        data_path = self._get_data_path(asset_class, symbol, frequency)

        if not data_path.exists():
            raise FileNotFoundError(
                f"No data found for {symbol} ({asset_class}/{frequency})"
            )

        # Read Parquet file
        table = pq.read_table(data_path)
        data = table.to_pandas()

        # Set index to datetime
        if not isinstance(data.index, pd.DatetimeIndex):
            # Assume first column is datetime if not already index
            data = data.set_index(data.columns[0])

        # Filter by date range if specified
        if start_date is not None:
            data = data[data.index >= start_date]
        if end_date is not None:
            data = data[data.index <= end_date]

        logger.info(
            f"Read {len(data)} records for {symbol} ({asset_class}/{frequency})"
        )

        return data

    def get_metadata(
        self, asset_class: str, symbol: str, frequency: str
    ) -> Dict[str, any]:
        """Get metadata for a symbol's data.

        Args:
            asset_class: Asset class.
            symbol: Symbol ticker.
            frequency: Data frequency.

        Returns:
            Dictionary with metadata fields.

        Raises:
            FileNotFoundError: If metadata file doesn't exist.
        """
        metadata_path = self._get_metadata_path(asset_class, symbol, frequency)

        if not metadata_path.exists():
            raise FileNotFoundError(
                f"No metadata found for {symbol} ({asset_class}/{frequency})"
            )

        table = pq.read_table(metadata_path)
        metadata_df = table.to_pandas()

        # Convert to dictionary (take first row)
        return metadata_df.iloc[0].to_dict()

    def exists(self, asset_class: str, symbol: str, frequency: str) -> bool:
        """Check if data exists for a symbol.

        Args:
            asset_class: Asset class.
            symbol: Symbol ticker.
            frequency: Data frequency.

        Returns:
            True if data file exists, False otherwise.
        """
        data_path = self._get_data_path(asset_class, symbol, frequency)
        return data_path.exists()

    def list_symbols(self, asset_class: str) -> List[str]:
        """List all symbols with stored data for an asset class.

        Args:
            asset_class: Asset class.

        Returns:
            List of symbol strings.
        """
        asset_path = self.base_path / asset_class

        if not asset_path.exists():
            return []

        # Get all symbol directories
        symbols = [
            d.name for d in asset_path.iterdir() if d.is_dir()
        ]

        return sorted(symbols)

    def delete(self, asset_class: str, symbol: str, frequency: str) -> None:
        """Delete data for a symbol.

        Args:
            asset_class: Asset class.
            symbol: Symbol ticker.
            frequency: Data frequency.
        """
        data_path = self._get_data_path(asset_class, symbol, frequency)
        metadata_path = self._get_metadata_path(asset_class, symbol, frequency)

        if data_path.exists():
            data_path.unlink()
            logger.info(f"Deleted data for {symbol} ({asset_class}/{frequency})")

        if metadata_path.exists():
            metadata_path.unlink()

        # Clean up empty directories
        freq_dir = data_path.parent
        if freq_dir.exists() and not any(freq_dir.iterdir()):
            freq_dir.rmdir()

        symbol_dir = freq_dir.parent
        if symbol_dir.exists() and not any(symbol_dir.iterdir()):
            symbol_dir.rmdir()
