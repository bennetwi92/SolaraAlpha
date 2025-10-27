"""YFinance data ingestor."""
import logging
import time
from datetime import datetime

import pandas as pd
import yfinance as yf

from .base import DataIngestor

logger = logging.getLogger(__name__)


class YFinanceIngestor(DataIngestor):
    """Data ingestor for Yahoo Finance.

    Primary data source for equities and crypto. Free, no API key required.

    Example:
        >>> ingestor = YFinanceIngestor()
        >>> data = ingestor.fetch_ohlcv('AAPL', start_date, end_date)
    """

    def __init__(self, rate_limit: float = 2.0, timeout: int = 30) -> None:
        """Initialize YFinance ingestor.

        Args:
            rate_limit: Maximum requests per second.
            timeout: Request timeout in seconds.
        """
        self.rate_limit = rate_limit
        self.timeout = timeout
        self._last_request_time = 0.0

    def _rate_limit_wait(self) -> None:
        """Wait if necessary to respect rate limits."""
        elapsed = time.time() - self._last_request_time
        wait_time = (1.0 / self.rate_limit) - elapsed

        if wait_time > 0:
            time.sleep(wait_time)

        self._last_request_time = time.time()

    def fetch_ohlcv(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV data from Yahoo Finance.

        Args:
            symbol: Symbol ticker (e.g., 'AAPL', 'BTC-USD').
            start_date: Start date for data.
            end_date: End date for data.
            frequency: Data frequency ('1d', '1h', etc.).

        Returns:
            DataFrame with datetime index and OHLCV columns.

        Raises:
            Exception: If data fetch fails or returns empty data.
        """
        self._rate_limit_wait()

        logger.info(
            f"Fetching {symbol} from YFinance "
            f"({start_date.date()} to {end_date.date()}, {frequency})"
        )

        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(
                start=start_date,
                end=end_date,
                interval=frequency,
                timeout=self.timeout,
            )

            if data.empty:
                raise ValueError(
                    f"YFinance returned empty data for {symbol}"
                )

            # Standardize column names
            data = data.rename(
                columns={
                    "Open": "Open",
                    "High": "High",
                    "Low": "Low",
                    "Close": "Close",
                    "Volume": "Volume",
                }
            )

            # Keep only OHLCV columns
            required_cols = ["Open", "High", "Low", "Close", "Volume"]
            data = data[required_cols]

            # Ensure datetime index
            if not isinstance(data.index, pd.DatetimeIndex):
                data.index = pd.to_datetime(data.index)

            logger.info(
                f"Fetched {len(data)} records for {symbol} from YFinance"
            )

            return data

        except Exception as e:
            logger.error(f"Failed to fetch {symbol} from YFinance: {e}")
            raise

    def get_name(self) -> str:
        """Get data source name.

        Returns:
            'yfinance'
        """
        return "yfinance"
