"""Data pipeline orchestrator for SolaraAlpha.

Main entry point for data ingestion, validation, and storage operations.
"""
import logging
from datetime import datetime
from typing import Dict, List

from .config.config_manager import ConfigManager
from .ingest.ingest_manager import IngestManager
from .storage.parquet_storage import ParquetStorage
from .validate.validator import DataValidator, ValidationResult

logger = logging.getLogger(__name__)


class DataPipeline:
    """Orchestrates the complete data pipeline.

    Coordinates data fetching, validation, and storage across multiple symbols
    and asset classes. Provides both full backfill and incremental update modes.

    Example:
        >>> pipeline = DataPipeline()
        >>> results = pipeline.backfill_all()
        >>> print(f"Fetched {len(results)} symbols")
    """

    def __init__(self, config: ConfigManager | None = None) -> None:
        """Initialize data pipeline.

        Args:
            config: Configuration manager. If None, creates new instance.
        """
        self.config = config or ConfigManager()
        self.ingest_manager = IngestManager(self.config)
        self.validator = DataValidator(self.config)
        self.storage = ParquetStorage(self.config)

        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

    def fetch_and_store(
        self,
        symbol: str,
        asset_class: str,
        frequency: str = "1d",
        force_refetch: bool = False,
    ) -> ValidationResult:
        """Fetch, validate, and store data for a single symbol.

        Args:
            symbol: Symbol ticker.
            asset_class: Asset class (e.g., 'equities', 'crypto').
            frequency: Data frequency.
            force_refetch: If True, refetch all data even if exists.

        Returns:
            ValidationResult with validation status and issues.

        Raises:
            Exception: If data fetch or storage fails.
        """
        logger.info(f"Processing {symbol} ({asset_class}/{frequency})")

        # Check if data exists and decide on full vs incremental fetch
        if (
            not force_refetch
            and self.storage.exists(asset_class, symbol, frequency)
        ):
            try:
                # Incremental update
                metadata = self.storage.get_metadata(
                    asset_class, symbol, frequency
                )
                last_date = metadata["end_date"]

                logger.info(
                    f"Existing data found for {symbol}, "
                    f"fetching incremental update since {last_date}"
                )

                new_data, source = self.ingest_manager.fetch_incremental(
                    symbol, asset_class, last_date, frequency
                )

                # Merge with existing data
                existing_data = self.storage.read(
                    asset_class, symbol, frequency
                )

                # Combine and deduplicate
                combined = pd.concat([existing_data, new_data])
                combined = combined[~combined.index.duplicated(keep="last")]
                combined = combined.sort_index()

                data = combined

            except Exception as e:
                logger.warning(
                    f"Incremental update failed for {symbol}, "
                    f"falling back to full refetch: {e}"
                )
                data, source = self.ingest_manager.fetch(
                    symbol, asset_class, frequency
                )
        else:
            # Full fetch
            data, source = self.ingest_manager.fetch(
                symbol, asset_class, frequency
            )

        # Validate data
        validation_result = self.validator.validate(data, symbol)

        # Store data (even if validation has warnings)
        validation_status = "passed" if validation_result.passed else "failed"

        self.storage.write(
            data,
            asset_class,
            symbol,
            frequency,
            source=source,
            validation_status=validation_status,
        )

        # Log validation issues
        if validation_result.issues:
            for issue in validation_result.get_errors():
                logger.error(f"  {issue.message}")
            for issue in validation_result.get_warnings():
                logger.warning(f"  {issue.message}")

        return validation_result

    def backfill_symbols(
        self,
        symbols: List[str],
        asset_class: str,
        frequency: str = "1d",
        force_refetch: bool = False,
    ) -> Dict[str, ValidationResult]:
        """Backfill data for multiple symbols.

        Args:
            symbols: List of symbol tickers.
            asset_class: Asset class.
            frequency: Data frequency.
            force_refetch: If True, refetch all data even if exists.

        Returns:
            Dictionary mapping symbol to ValidationResult.
        """
        results = {}

        for symbol in symbols:
            try:
                result = self.fetch_and_store(
                    symbol, asset_class, frequency, force_refetch
                )
                results[symbol] = result

            except Exception as e:
                logger.error(
                    f"Failed to process {symbol} ({asset_class}): {e}"
                )
                # Continue with next symbol

        # Summary
        successful = sum(1 for r in results.values() if r.passed)
        logger.info(
            f"Backfill complete: {successful}/{len(symbols)} symbols "
            f"validated successfully"
        )

        return results

    def backfill_all(
        self, frequency: str = "1d", force_refetch: bool = False
    ) -> Dict[str, Dict[str, ValidationResult]]:
        """Backfill data for all configured symbols across all asset classes.

        Args:
            frequency: Data frequency.
            force_refetch: If True, refetch all data even if exists.

        Returns:
            Nested dict: {asset_class: {symbol: ValidationResult}}
        """
        all_results = {}

        all_symbols = self.config.get_all_symbols()

        for asset_class, symbols in all_symbols.items():
            logger.info(
                f"Starting backfill for {len(symbols)} {asset_class} symbols"
            )

            results = self.backfill_symbols(
                symbols, asset_class, frequency, force_refetch
            )

            all_results[asset_class] = results

        # Overall summary
        total_symbols = sum(len(symbols) for symbols in all_symbols.values())
        total_successful = sum(
            sum(1 for r in results.values() if r.passed)
            for results in all_results.values()
        )

        logger.info(
            f"Complete backfill finished: "
            f"{total_successful}/{total_symbols} symbols validated successfully"
        )

        return all_results


# Import pandas here to avoid circular import
import pandas as pd
