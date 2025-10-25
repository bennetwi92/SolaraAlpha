"""
Market Data Client - Unified interface to multiple free market data sources.

Provides access to:
- yfinance: OHLCV for equities/ETFs/crypto (unlimited requests)
- Alpha Vantage: Forex quotes and fundamentals (25 requests/day free tier)
- FRED: Economic indicators (unlimited requests)

Features:
- Local SQLite caching with configurable TTL
- Error handling with exponential backoff
- Rate limit management
- Type hints and comprehensive docstrings
"""

import time
import sqlite3
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Literal
from dataclasses import dataclass

import pandas as pd
import yfinance as yf
from alpha_vantage.fundamentaldata import FundamentalData
from alpha_vantage.foreignexchange import ForeignExchange
from fredapi import Fred


@dataclass
class CacheConfig:
    """Configuration for cache behavior."""
    enabled: bool = True
    ttl_seconds: int = 3600  # 1 hour default
    cache_dir: Path = Path.home() / ".solaraalpha" / "cache"


class RateLimitError(Exception):
    """Raised when API rate limit is exceeded."""
    pass


class MarketDataClient:
    """
    Unified market data client providing access to multiple data sources.

    Attributes:
        cache_config: Configuration for local caching
        alpha_vantage_key: API key for Alpha Vantage (optional)
        fred_api_key: API key for FRED (optional)

    Example:
        >>> client = MarketDataClient()
        >>> df = client.get_ohlcv("AAPL", start="2024-01-01", end="2024-12-31")
        >>> print(df.head())
    """

    # Rate limits (requests per day)
    ALPHA_VANTAGE_DAILY_LIMIT = 25

    def __init__(
        self,
        cache_config: Optional[CacheConfig] = None,
        alpha_vantage_key: Optional[str] = None,
        fred_api_key: Optional[str] = None
    ):
        """
        Initialize the market data client.

        Args:
            cache_config: Cache configuration (uses defaults if None)
            alpha_vantage_key: Alpha Vantage API key (optional for free tier)
            fred_api_key: FRED API key (optional for basic access)
        """
        self.cache_config = cache_config or CacheConfig()
        self.alpha_vantage_key = alpha_vantage_key
        self.fred_api_key = fred_api_key

        # Initialize cache
        if self.cache_config.enabled:
            self._init_cache()

        # Initialize API clients
        self._init_api_clients()

    def _init_cache(self) -> None:
        """Initialize SQLite cache database."""
        self.cache_config.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_db_path = self.cache_config.cache_dir / "market_data.db"

        with sqlite3.connect(self.cache_db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    cache_key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    ttl_seconds INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON cache(timestamp)
            """)
            conn.commit()

    def _init_api_clients(self) -> None:
        """Initialize API clients for external data sources."""
        # Alpha Vantage clients (if key provided)
        if self.alpha_vantage_key:
            self.av_fundamental = FundamentalData(
                key=self.alpha_vantage_key,
                output_format='pandas'
            )
            self.av_forex = ForeignExchange(
                key=self.alpha_vantage_key,
                output_format='pandas'
            )
        else:
            self.av_fundamental = None
            self.av_forex = None

        # FRED client (if key provided)
        if self.fred_api_key:
            self.fred = Fred(api_key=self.fred_api_key)
        else:
            self.fred = None

    def _get_cache_key(self, prefix: str, **kwargs) -> str:
        """
        Generate a cache key from method parameters.

        Args:
            prefix: Cache key prefix (typically method name)
            **kwargs: Method parameters to include in key

        Returns:
            Hashed cache key
        """
        key_data = f"{prefix}:{json.dumps(kwargs, sort_keys=True)}"
        return hashlib.sha256(key_data.encode()).hexdigest()

    def _get_from_cache(self, cache_key: str) -> Optional[Any]:
        """
        Retrieve data from cache if available and not expired.

        Args:
            cache_key: Cache key to lookup

        Returns:
            Cached data or None if not found/expired
        """
        if not self.cache_config.enabled:
            return None

        try:
            with sqlite3.connect(self.cache_db_path) as conn:
                cursor = conn.execute(
                    "SELECT data, timestamp, ttl_seconds FROM cache WHERE cache_key = ?",
                    (cache_key,)
                )
                row = cursor.fetchone()

                if row:
                    data_json, timestamp, ttl = row
                    age = time.time() - timestamp

                    if age < ttl:
                        return json.loads(data_json)
        except Exception:
            pass

        return None

    def _save_to_cache(self, cache_key: str, data: Any, ttl: Optional[int] = None) -> None:
        """
        Save data to cache.

        Args:
            cache_key: Cache key
            data: Data to cache (must be JSON serializable)
            ttl: Time-to-live in seconds (uses default if None)
        """
        if not self.cache_config.enabled:
            return

        ttl = ttl or self.cache_config.ttl_seconds

        try:
            with sqlite3.connect(self.cache_db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cache (cache_key, data, timestamp, ttl_seconds)
                    VALUES (?, ?, ?, ?)
                    """,
                    (cache_key, json.dumps(data), time.time(), ttl)
                )
                conn.commit()
        except Exception:
            pass

    def _clear_expired_cache(self) -> None:
        """Remove expired entries from cache."""
        if not self.cache_config.enabled:
            return

        try:
            with sqlite3.connect(self.cache_db_path) as conn:
                conn.execute(
                    "DELETE FROM cache WHERE (? - timestamp) > ttl_seconds",
                    (time.time(),)
                )
                conn.commit()
        except Exception:
            pass

    def _retry_with_backoff(
        self,
        func,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0
    ) -> Any:
        """
        Retry a function with exponential backoff.

        Args:
            func: Function to retry
            max_retries: Maximum number of retry attempts
            initial_delay: Initial delay in seconds
            backoff_factor: Multiplier for delay after each retry

        Returns:
            Function result

        Raises:
            Last exception encountered after all retries
        """
        delay = initial_delay
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                return func()
            except RateLimitError:
                # Don't retry rate limit errors
                raise
            except Exception as e:
                last_exception = e

                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= backoff_factor

        raise last_exception

    def get_ohlcv(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        interval: Literal["1d", "1h", "5m", "1m"] = "1d"
    ) -> pd.DataFrame:
        """
        Get OHLCV (Open, High, Low, Close, Volume) data for a symbol.

        Uses yfinance for equities, ETFs, and crypto. Data is cached locally.

        Args:
            symbol: Ticker symbol (e.g., "AAPL", "BTC-USD", "SPY")
            start: Start date in YYYY-MM-DD format (default: 1 year ago)
            end: End date in YYYY-MM-DD format (default: today)
            interval: Data interval ("1d", "1h", "5m", "1m")

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume, Date (index)

        Raises:
            ValueError: If symbol is invalid or data unavailable
            ConnectionError: If network request fails

        Example:
            >>> client = MarketDataClient()
            >>> df = client.get_ohlcv("AAPL", start="2024-01-01", end="2024-12-31")
            >>> print(df.tail())
        """
        # Set defaults
        if end is None:
            end = datetime.now().strftime("%Y-%m-%d")
        if start is None:
            start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        # Check cache
        cache_key = self._get_cache_key(
            "ohlcv",
            symbol=symbol,
            start=start,
            end=end,
            interval=interval
        )
        cached_data = self._get_from_cache(cache_key)
        if cached_data is not None:
            df = pd.DataFrame(
                data=cached_data['data'],
                columns=cached_data['columns']
            )
            # Restore the DatetimeIndex with frequency
            df.index = pd.DatetimeIndex(pd.to_datetime(cached_data['index']))
            if cached_data.get('index_freq'):
                try:
                    df.index.freq = cached_data['index_freq']
                except (ValueError, TypeError):
                    # Frequency might not be inferrable, that's okay
                    pass
            return df

        # Fetch from yfinance with retry
        def fetch():
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start, end=end, interval=interval)

            if df.empty:
                raise ValueError(f"No data available for symbol: {symbol}")

            # Standardize column names
            required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
            # Check if we need to filter columns
            if not all(col in df.columns for col in required_cols):
                raise ValueError(f"Missing required columns for symbol: {symbol}")

            # Always reorder to ensure consistent column order
            df = df.reindex(columns=required_cols)

            return df

        df = self._retry_with_backoff(fetch)

        # Cache result (preserve index and frequency)
        cache_data = {
            'data': df.to_dict('list'),
            'index': df.index.astype(str).tolist(),  # Convert datetime to string for JSON
            'index_freq': df.index.freqstr if hasattr(df.index, 'freqstr') and df.index.freqstr else None,
            'columns': df.columns.tolist()
        }
        self._save_to_cache(cache_key, cache_data)

        return df

    def get_fundamentals(
        self,
        symbol: str,
        report_type: Literal["income_statement", "balance_sheet", "cash_flow"] = "income_statement"
    ) -> pd.DataFrame:
        """
        Get fundamental data for a symbol using Alpha Vantage.

        Note: Alpha Vantage free tier allows 25 requests per day.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL")
            report_type: Type of financial report

        Returns:
            DataFrame with fundamental data

        Raises:
            ValueError: If Alpha Vantage API key not provided
            RateLimitError: If rate limit exceeded
            ConnectionError: If network request fails

        Example:
            >>> client = MarketDataClient(alpha_vantage_key="YOUR_KEY")
            >>> df = client.get_fundamentals("AAPL", report_type="income_statement")
            >>> print(df.head())
        """
        if not self.av_fundamental:
            raise ValueError("Alpha Vantage API key required for fundamentals data")

        # Check cache
        cache_key = self._get_cache_key(
            "fundamentals",
            symbol=symbol,
            report_type=report_type
        )
        cached_data = self._get_from_cache(cache_key)
        if cached_data is not None:
            return pd.DataFrame(
                data=cached_data['data'],
                index=cached_data['index'],
                columns=cached_data['columns']
            )

        # Fetch with retry
        def fetch():
            try:
                if report_type == "income_statement":
                    data, _ = self.av_fundamental.get_income_statement_annual(symbol)
                elif report_type == "balance_sheet":
                    data, _ = self.av_fundamental.get_balance_sheet_annual(symbol)
                elif report_type == "cash_flow":
                    data, _ = self.av_fundamental.get_cash_flow_annual(symbol)
                else:
                    raise ValueError(f"Invalid report_type: {report_type}")

                if data.empty:
                    raise ValueError(f"No fundamental data for symbol: {symbol}")

                return data
            except Exception as e:
                if "rate limit" in str(e).lower():
                    raise RateLimitError(f"Alpha Vantage rate limit exceeded: {e}")
                raise

        df = self._retry_with_backoff(fetch)

        # Cache with longer TTL (fundamentals change less frequently)
        cache_data = {
            'data': df.to_dict('list'),
            'index': df.index.tolist(),
            'columns': df.columns.tolist()
        }
        self._save_to_cache(cache_key, cache_data, ttl=86400)  # 24 hours

        return df

    def get_forex_quote(
        self,
        from_currency: str,
        to_currency: str
    ) -> Dict[str, Any]:
        """
        Get forex exchange rate using Alpha Vantage.

        Note: Alpha Vantage free tier allows 25 requests per day.

        Args:
            from_currency: Source currency code (e.g., "USD")
            to_currency: Target currency code (e.g., "EUR")

        Returns:
            Dictionary with exchange rate data including:
                - rate: Current exchange rate
                - bid: Bid price
                - ask: Ask price
                - timestamp: Quote timestamp

        Raises:
            ValueError: If Alpha Vantage API key not provided
            RateLimitError: If rate limit exceeded
            ConnectionError: If network request fails

        Example:
            >>> client = MarketDataClient(alpha_vantage_key="YOUR_KEY")
            >>> quote = client.get_forex_quote("USD", "EUR")
            >>> print(f"USD/EUR rate: {quote['rate']}")
        """
        if not self.av_forex:
            raise ValueError("Alpha Vantage API key required for forex data")

        # Check cache
        cache_key = self._get_cache_key(
            "forex",
            from_currency=from_currency,
            to_currency=to_currency
        )
        cached_data = self._get_from_cache(cache_key)
        if cached_data is not None:
            return cached_data

        # Fetch with retry
        def fetch():
            try:
                data, _ = self.av_forex.get_currency_exchange_rate(
                    from_currency=from_currency,
                    to_currency=to_currency
                )

                if data.empty:
                    raise ValueError(
                        f"No forex data for {from_currency}/{to_currency}"
                    )

                # Extract relevant fields
                result = {
                    'rate': float(data['5. Exchange Rate'].iloc[0]),
                    'bid': float(data['8. Bid Price'].iloc[0]),
                    'ask': float(data['9. Ask Price'].iloc[0]),
                    'timestamp': data['6. Last Refreshed'].iloc[0]
                }

                return result
            except Exception as e:
                if "rate limit" in str(e).lower():
                    raise RateLimitError(f"Alpha Vantage rate limit exceeded: {e}")
                raise

        result = self._retry_with_backoff(fetch)

        # Cache with shorter TTL (forex rates change frequently)
        self._save_to_cache(cache_key, result, ttl=300)  # 5 minutes

        return result

    def get_economic_indicator(
        self,
        series_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None
    ) -> pd.Series:
        """
        Get economic indicator data from FRED.

        Args:
            series_id: FRED series ID (e.g., "GDP", "DFF", "CPIAUCSL")
            start: Start date in YYYY-MM-DD format (default: 10 years ago)
            end: End date in YYYY-MM-DD format (default: today)

        Returns:
            Pandas Series with economic indicator values (date index)

        Raises:
            ValueError: If FRED API key not provided or series not found
            ConnectionError: If network request fails

        Example:
            >>> client = MarketDataClient(fred_api_key="YOUR_KEY")
            >>> gdp = client.get_economic_indicator("GDP", start="2020-01-01")
            >>> print(gdp.tail())

        Common FRED Series IDs:
            - GDP: Gross Domestic Product
            - DFF: Federal Funds Rate
            - CPIAUCSL: Consumer Price Index (inflation)
            - UNRATE: Unemployment Rate
            - T10Y2Y: 10-Year Treasury Minus 2-Year Treasury Yield
        """
        if not self.fred:
            raise ValueError("FRED API key required for economic indicators")

        # Set defaults
        if end is None:
            end = datetime.now().strftime("%Y-%m-%d")
        if start is None:
            start = (datetime.now() - timedelta(days=3650)).strftime("%Y-%m-%d")

        # Check cache
        cache_key = self._get_cache_key(
            "fred",
            series_id=series_id,
            start=start,
            end=end
        )
        cached_data = self._get_from_cache(cache_key)
        if cached_data is not None:
            return pd.Series(
                data=cached_data['data'],
                index=cached_data['index']
            )

        # Fetch with retry
        def fetch():
            try:
                series = self.fred.get_series(
                    series_id,
                    observation_start=start,
                    observation_end=end
                )

                if series.empty:
                    raise ValueError(f"No data for FRED series: {series_id}")

                return series
            except Exception as e:
                if "not found" in str(e).lower():
                    raise ValueError(f"FRED series not found: {series_id}")
                raise

        series = self._retry_with_backoff(fetch)

        # Cache with longer TTL (economic data updates infrequently)
        cache_data = {
            'data': series.tolist(),
            'index': series.index.tolist()
        }
        self._save_to_cache(cache_key, cache_data, ttl=86400)  # 24 hours

        return series

    def clear_cache(self) -> None:
        """
        Clear all cached data.

        Example:
            >>> client = MarketDataClient()
            >>> client.clear_cache()
        """
        if not self.cache_config.enabled:
            return

        try:
            with sqlite3.connect(self.cache_db_path) as conn:
                conn.execute("DELETE FROM cache")
                conn.commit()
        except Exception:
            pass
