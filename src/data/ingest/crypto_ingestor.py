"""CCXT-based crypto data ingestor."""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import ccxt
import pandas as pd

from .base import DataIngestor

logger = logging.getLogger(__name__)


class CryptoIngestor(DataIngestor):
    """Data ingestor for cryptocurrency using CCXT.

    Supports multiple exchanges and provides unified OHLCV data access.
    Designed for 24/7 markets with no gaps.

    Features:
    - Multiple timeframe support (1m, 5m, 1h, 1d)
    - Historical backfill (2+ years)
    - Real-time streaming capability
    - Automatic symbol normalization (BTC-USD -> BTC/USD)

    Example:
        >>> ingestor = CryptoIngestor(exchange='binance')
        >>> data = ingestor.fetch_ohlcv('BTC-USD', start_date, end_date, '1d')
    """

    # Timeframe mappings (SolaraAlpha format -> CCXT format)
    TIMEFRAME_MAP = {
        "1m": "1m",
        "5m": "5m",
        "1h": "1h",
        "1d": "1d",
    }

    # Maximum data points per request (exchange limits)
    MAX_CANDLES_PER_REQUEST = 1000

    def __init__(
        self,
        exchange: str = "binance",
        rate_limit: float = 1.0,
        timeout: int = 30,
    ) -> None:
        """Initialize crypto ingestor.

        Args:
            exchange: CCXT exchange ID (default: 'binance' - high liquidity, free API).
            rate_limit: Maximum requests per second.
            timeout: Request timeout in seconds.
        """
        self.exchange_id = exchange
        self.rate_limit = rate_limit
        self.timeout = timeout
        self._last_request_time = 0.0

        # Initialize exchange
        try:
            exchange_class = getattr(ccxt, exchange)
            self.exchange = exchange_class(
                {
                    "enableRateLimit": True,
                    "timeout": timeout * 1000,  # CCXT uses milliseconds
                }
            )
            logger.info(f"Initialized {exchange} exchange for crypto data")
        except Exception as e:
            logger.error(f"Failed to initialize {exchange} exchange: {e}")
            raise

    def _rate_limit_wait(self) -> None:
        """Wait if necessary to respect rate limits."""
        elapsed = time.time() - self._last_request_time
        wait_time = (1.0 / self.rate_limit) - elapsed

        if wait_time > 0:
            time.sleep(wait_time)

        self._last_request_time = time.time()

    def _normalize_symbol(self, symbol: str) -> str:
        """Convert SolaraAlpha symbol format to CCXT format.

        Args:
            symbol: Symbol in format 'BTC-USD', 'ETH-USD', etc.

        Returns:
            CCXT format symbol: 'BTC/USD', 'ETH/USD', etc.
        """
        # Convert hyphen to slash for CCXT
        return symbol.replace("-", "/")

    def _get_timeframe_duration(self, timeframe: str) -> timedelta:
        """Get timedelta for a given timeframe.

        Args:
            timeframe: Timeframe string ('1m', '5m', '1h', '1d')

        Returns:
            Timedelta representing the duration
        """
        durations = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "1h": timedelta(hours=1),
            "1d": timedelta(days=1),
        }
        return durations.get(timeframe, timedelta(days=1))

    def _fetch_ohlcv_batch(
        self,
        symbol: str,
        since: int,
        timeframe: str,
    ) -> pd.DataFrame:
        """Fetch a single batch of OHLCV data.

        Args:
            symbol: CCXT format symbol (e.g., 'BTC/USD')
            since: Start timestamp in milliseconds
            timeframe: CCXT timeframe string

        Returns:
            DataFrame with OHLCV data
        """
        self._rate_limit_wait()

        try:
            ohlcv = self.exchange.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                since=since,
                limit=self.MAX_CANDLES_PER_REQUEST,
            )

            if not ohlcv:
                return pd.DataFrame()

            # Convert to DataFrame
            df = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "Open", "High", "Low", "Close", "Volume"],
            )

            # Convert timestamp to datetime
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)

            return df

        except Exception as e:
            logger.warning(f"Failed to fetch batch for {symbol}: {e}")
            return pd.DataFrame()

    def fetch_ohlcv(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV data for cryptocurrency.

        Automatically handles pagination for large date ranges.
        Supports 24/7 crypto markets with no gaps.

        Args:
            symbol: Symbol ticker (e.g., 'BTC-USD', 'ETH-USD').
            start_date: Start date for data.
            end_date: End date for data.
            frequency: Data frequency ('1m', '5m', '1h', '1d').

        Returns:
            DataFrame with datetime index and OHLCV columns.

        Raises:
            ValueError: If timeframe not supported or data is empty.
            Exception: If data fetch fails.
        """
        # Validate timeframe
        if frequency not in self.TIMEFRAME_MAP:
            raise ValueError(
                f"Unsupported frequency: {frequency}. "
                f"Supported: {list(self.TIMEFRAME_MAP.keys())}"
            )

        ccxt_timeframe = self.TIMEFRAME_MAP[frequency]
        ccxt_symbol = self._normalize_symbol(symbol)

        logger.info(
            f"Fetching {symbol} from {self.exchange_id} "
            f"({start_date.date()} to {end_date.date()}, {frequency})"
        )

        # Convert dates to milliseconds (CCXT format)
        since = int(start_date.timestamp() * 1000)
        end_ms = int(end_date.timestamp() * 1000)

        # Fetch data in batches
        all_data = []
        current_since = since
        timeframe_duration = self._get_timeframe_duration(frequency)
        batch_duration_ms = int(
            timeframe_duration.total_seconds()
            * self.MAX_CANDLES_PER_REQUEST
            * 1000
        )

        while current_since < end_ms:
            batch_df = self._fetch_ohlcv_batch(
                ccxt_symbol,
                current_since,
                ccxt_timeframe,
            )

            if batch_df.empty:
                # Move forward by batch duration to avoid infinite loop
                current_since += batch_duration_ms
                continue

            all_data.append(batch_df)

            # Get last timestamp and move forward
            last_timestamp = batch_df.index[-1]
            current_since = int(last_timestamp.timestamp() * 1000) + int(
                timeframe_duration.total_seconds() * 1000
            )

            # Stop if we've reached the end date
            if current_since >= end_ms:
                break

            logger.debug(
                f"Fetched batch: {len(batch_df)} records, "
                f"last timestamp: {last_timestamp}"
            )

        # Combine all batches
        if not all_data:
            raise ValueError(
                f"No data returned for {symbol} from {self.exchange_id}"
            )

        data = pd.concat(all_data)

        # Remove duplicates (can occur at batch boundaries)
        data = data[~data.index.duplicated(keep="first")]

        # Filter to exact date range
        data = data[(data.index >= start_date) & (data.index <= end_date)]

        # Sort by index
        data.sort_index(inplace=True)

        # Validate required columns
        required_cols = ["Open", "High", "Low", "Close", "Volume"]
        if not all(col in data.columns for col in required_cols):
            raise ValueError(f"Missing required columns: {required_cols}")

        logger.info(
            f"Fetched {len(data)} records for {symbol} from {self.exchange_id}"
        )

        return data

    def get_name(self) -> str:
        """Get data source name.

        Returns:
            Exchange ID (e.g., 'binance', 'coinbase')
        """
        return f"ccxt_{self.exchange_id}"

    def fetch_realtime_ohlcv(
        self,
        symbol: str,
        frequency: str = "1m",
        lookback_periods: int = 100,
    ) -> pd.DataFrame:
        """Fetch recent OHLCV data for real-time streaming.

        This is optimized for real-time applications where you need
        the latest N candles.

        Args:
            symbol: Symbol ticker (e.g., 'BTC-USD').
            frequency: Data frequency ('1m', '5m', '1h', '1d').
            lookback_periods: Number of recent candles to fetch.

        Returns:
            DataFrame with datetime index and OHLCV columns.

        Raises:
            ValueError: If timeframe not supported or data is empty.
        """
        if frequency not in self.TIMEFRAME_MAP:
            raise ValueError(
                f"Unsupported frequency: {frequency}. "
                f"Supported: {list(self.TIMEFRAME_MAP.keys())}"
            )

        ccxt_timeframe = self.TIMEFRAME_MAP[frequency]
        ccxt_symbol = self._normalize_symbol(symbol)

        logger.info(
            f"Fetching {lookback_periods} recent {frequency} candles for {symbol}"
        )

        self._rate_limit_wait()

        try:
            ohlcv = self.exchange.fetch_ohlcv(
                ccxt_symbol,
                timeframe=ccxt_timeframe,
                limit=lookback_periods,
            )

            if not ohlcv:
                raise ValueError(f"No data returned for {symbol}")

            # Convert to DataFrame
            df = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "Open", "High", "Low", "Close", "Volume"],
            )

            # Convert timestamp to datetime
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)

            logger.info(f"Fetched {len(df)} recent candles for {symbol}")

            return df

        except Exception as e:
            logger.error(f"Failed to fetch realtime data for {symbol}: {e}")
            raise

    def get_supported_symbols(self) -> list[str]:
        """Get list of supported trading pairs on the exchange.

        Returns:
            List of symbols in CCXT format (e.g., ['BTC/USD', 'ETH/USD'])
        """
        try:
            self.exchange.load_markets()
            return list(self.exchange.markets.keys())
        except Exception as e:
            logger.error(f"Failed to load markets: {e}")
            return []
