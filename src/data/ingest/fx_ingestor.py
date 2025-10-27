"""Foreign exchange (FX) data ingestor with interest rate integration.

Uses free data sources:
- Exchange rates: Frankfurter API (ECB rates, no API key)
- Interest rates: API Ninjas (22 countries, free tier)
"""
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd
import requests

from .base import DataIngestor

logger = logging.getLogger(__name__)


class FXIngestor(DataIngestor):
    """Data ingestor for foreign exchange rates with interest rate data.

    Features:
    - Free historical data via Frankfurter (ECB rates)
    - No API key required for FX rates
    - Interest rates from API Ninjas (requires free API key)
    - Support for 30+ currency pairs
    - Daily frequency (aligned with FX carry strategy)

    Example:
        >>> ingestor = FXIngestor()
        >>> data = ingestor.fetch_ohlcv('EUR-USD', start_date, end_date, '1d')
        >>> rates = ingestor.get_interest_rates()
    """

    # Supported currency codes (major and emerging market currencies)
    SUPPORTED_CURRENCIES = [
        "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",  # G8
        "SEK", "NOK", "DKK",  # Scandinavia
        "MXN", "BRL", "ZAR", "TRY", "INR", "KRW", "SGD", "HKD",  # Emerging
    ]

    # API endpoints
    FRANKFURTER_BASE = "https://api.frankfurter.app"
    API_NINJAS_BASE = "https://api.api-ninjas.com/v1"

    # Rate limits
    RATE_LIMIT_DELAY = 0.5  # Seconds between requests

    def __init__(
        self,
        api_ninjas_key: Optional[str] = None,
        rate_limit: float = 0.5,
    ) -> None:
        """Initialize FX ingestor.

        Args:
            api_ninjas_key: API Ninjas API key (from env: API_NINJAS_KEY).
                          Optional - FX rates work without it, but interest rates require it.
            rate_limit: Minimum seconds between requests.
        """
        self.rate_limit = rate_limit
        self._last_request_time = 0.0

        # Get API key from parameter or environment
        self.api_ninjas_key = api_ninjas_key or os.environ.get("API_NINJAS_KEY")

        if not self.api_ninjas_key:
            logger.warning(
                "API_NINJAS_KEY not set - interest rate functionality disabled. "
                "Get free key at: https://api-ninjas.com/api/interestrate"
            )

        logger.info("Initialized FX ingestor (Frankfurter + API Ninjas)")

    def _rate_limit_wait(self) -> None:
        """Wait if necessary to respect rate limits."""
        elapsed = time.time() - self._last_request_time
        wait_time = self.rate_limit - elapsed

        if wait_time > 0:
            time.sleep(wait_time)

        self._last_request_time = time.time()

    def _normalize_symbol(self, symbol: str) -> tuple[str, str]:
        """Convert SolaraAlpha symbol format to base and quote currencies.

        Args:
            symbol: Symbol in format 'EUR-USD', 'GBP-JPY', etc.

        Returns:
            Tuple of (base_currency, quote_currency): ('EUR', 'USD')
        """
        parts = symbol.split("-")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid FX symbol format: {symbol}. Expected 'BASE-QUOTE' (e.g., 'EUR-USD')"
            )

        base, quote = parts
        if base not in self.SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported base currency: {base}")
        if quote not in self.SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported quote currency: {quote}")

        return base, quote

    def fetch_ohlcv(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV data for FX pair.

        Note: Frankfurter provides daily close rates only (not full OHLCV).
        For FX carry strategy, we primarily need close prices and will
        calculate synthetic OHLC based on close prices.

        Args:
            symbol: FX pair ticker (e.g., 'EUR-USD', 'GBP-JPY').
            start_date: Start date for data.
            end_date: End date for data.
            frequency: Data frequency (only '1d' supported for free tier).

        Returns:
            DataFrame with datetime index and columns:
            ['Open', 'High', 'Low', 'Close', 'Volume']
            Note: Volume will be 0 (not available for spot FX),
                  OHLC will be synthetic based on close prices.

        Raises:
            ValueError: If frequency not supported or data is empty.
            Exception: If data fetch fails.
        """
        if frequency != "1d":
            raise ValueError(
                f"Unsupported frequency: {frequency}. "
                f"Only '1d' supported for free FX data."
            )

        base, quote = self._normalize_symbol(symbol)

        logger.info(
            f"Fetching {symbol} from Frankfurter "
            f"({start_date.date()} to {end_date.date()})"
        )

        # Format dates for API (YYYY-MM-DD)
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        # Build API request
        url = f"{self.FRANKFURTER_BASE}/{start_str}..{end_str}"
        params = {
            "from": base,
            "to": quote,
        }

        self._rate_limit_wait()

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            # Parse response
            rates_dict = data.get("rates", {})
            if not rates_dict:
                raise ValueError(f"No data returned for {symbol}")

            # Convert to DataFrame
            dates = []
            rates = []
            for date_str, rate_data in rates_dict.items():
                dates.append(pd.to_datetime(date_str))
                # Extract rate for quote currency
                rate = rate_data.get(quote)
                if rate is None:
                    logger.warning(f"Missing rate for {date_str}")
                    continue
                rates.append(rate)

            if not dates:
                raise ValueError(f"No valid rates found for {symbol}")

            # Create DataFrame with close prices
            df = pd.DataFrame({"Close": rates}, index=dates)
            df.index.name = "timestamp"

            # Create synthetic OHLCV (FX carry strategy mainly uses close)
            # Use small spreads to simulate intraday range
            spread_pct = 0.0005  # 0.05% typical FX spread
            df["Open"] = df["Close"]
            df["High"] = df["Close"] * (1 + spread_pct)
            df["Low"] = df["Close"] * (1 - spread_pct)
            df["Volume"] = 0  # Spot FX has no volume concept

            # Sort and filter to exact range
            df = df.sort_index()
            df = df[(df.index >= start_date) & (df.index <= end_date)]

            logger.info(f"Fetched {len(df)} records for {symbol} from Frankfurter")

            return df[["Open", "High", "Low", "Close", "Volume"]]

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch FX data for {symbol}: {e}")
            raise Exception(f"FX data fetch failed: {e}")

    def get_name(self) -> str:
        """Get data source name.

        Returns:
            Data source identifier
        """
        return "frankfurter_fx"

    def get_interest_rates(
        self, countries: Optional[list[str]] = None
    ) -> pd.DataFrame:
        """Fetch current central bank interest rates.

        This is critical for FX carry trade strategy to calculate interest rate differentials.

        Args:
            countries: List of country names (e.g., ['United States', 'Eurozone']).
                      If None, fetches all available countries.

        Returns:
            DataFrame with columns: ['country', 'currency', 'rate', 'last_updated']

        Raises:
            RuntimeError: If API key not configured.
            Exception: If API request fails.
        """
        if not self.api_ninjas_key:
            raise RuntimeError(
                "API_NINJAS_KEY required for interest rate data. "
                "Set environment variable or pass to constructor."
            )

        logger.info("Fetching interest rates from API Ninjas")

        # Country to currency mapping (for major currencies)
        country_currency_map = {
            "United States": "USD",
            "Eurozone": "EUR",
            "United Kingdom": "GBP",
            "Japan": "JPY",
            "Switzerland": "CHF",
            "Canada": "CAD",
            "Australia": "AUD",
            "New Zealand": "NZD",
            "Sweden": "SEK",
            "Norway": "NOK",
            "Mexico": "MXN",
            "Brazil": "BRL",
            "South Africa": "ZAR",
            "Turkey": "TRY",
            "India": "INR",
            "South Korea": "KRW",
        }

        target_countries = countries or list(country_currency_map.keys())

        results = []
        for country in target_countries:
            self._rate_limit_wait()

            url = f"{self.API_NINJAS_BASE}/interestrate"
            params = {"name": country}
            headers = {"X-Api-Key": self.api_ninjas_key}

            try:
                response = requests.get(
                    url, params=params, headers=headers, timeout=30
                )
                response.raise_for_status()
                data = response.json()

                if not data or "central_bank_rates" not in data:
                    logger.warning(f"No interest rate data for {country}")
                    continue

                # Parse central bank rates
                rates = data["central_bank_rates"]
                if not rates:
                    continue

                # Take the most recent rate
                rate_info = rates[0]
                currency = country_currency_map.get(country, "UNK")

                results.append(
                    {
                        "country": country,
                        "currency": currency,
                        "rate": rate_info.get("rate_pct", 0.0),
                        "last_updated": pd.Timestamp.now(),
                    }
                )

                logger.debug(
                    f"{country} ({currency}): {rate_info.get('rate_pct')}%"
                )

            except requests.exceptions.RequestException as e:
                logger.warning(f"Failed to fetch rate for {country}: {e}")
                continue

        if not results:
            raise Exception("Failed to fetch any interest rate data")

        df = pd.DataFrame(results)
        logger.info(f"Fetched interest rates for {len(df)} countries")

        return df

    def get_supported_pairs(self) -> list[str]:
        """Get list of supported FX pairs.

        Returns:
            List of FX pairs in SolaraAlpha format (e.g., ['EUR-USD', 'GBP-JPY'])
        """
        # Generate all possible pairs from supported currencies
        pairs = []
        for base in self.SUPPORTED_CURRENCIES:
            for quote in self.SUPPORTED_CURRENCIES:
                if base != quote:
                    # Follow convention: major currencies as quote (USD, EUR, GBP, JPY)
                    if quote in ["USD", "EUR", "GBP", "JPY"]:
                        pairs.append(f"{base}-{quote}")

        # Add major crosses
        major_pairs = [
            "EUR-USD", "GBP-USD", "USD-JPY", "USD-CHF",
            "AUD-USD", "USD-CAD", "NZD-USD",
            "EUR-GBP", "EUR-JPY", "GBP-JPY",
        ]

        # Return major pairs first, then others
        return major_pairs + [p for p in pairs if p not in major_pairs]

    def calculate_rate_differential(
        self,
        base_currency: str,
        quote_currency: str,
        interest_rates: pd.DataFrame,
    ) -> float:
        """Calculate interest rate differential for a currency pair.

        This is the core calculation for FX carry trade:
        Rate Differential = Base Currency Rate - Quote Currency Rate

        Positive differential = receive interest (long base, short quote)
        Negative differential = pay interest (short base, long quote)

        Args:
            base_currency: Base currency code (e.g., 'AUD')
            quote_currency: Quote currency code (e.g., 'USD')
            interest_rates: DataFrame from get_interest_rates()

        Returns:
            Interest rate differential in percentage points

        Raises:
            ValueError: If currency rates not found
        """
        base_rate = interest_rates[
            interest_rates["currency"] == base_currency
        ]["rate"]
        quote_rate = interest_rates[
            interest_rates["currency"] == quote_currency
        ]["rate"]

        if base_rate.empty:
            raise ValueError(f"Interest rate not found for {base_currency}")
        if quote_rate.empty:
            raise ValueError(f"Interest rate not found for {quote_currency}")

        differential = base_rate.iloc[0] - quote_rate.iloc[0]

        logger.debug(
            f"{base_currency}/{quote_currency} rate differential: "
            f"{differential:.2f}% ({base_rate.iloc[0]:.2f}% - {quote_rate.iloc[0]:.2f}%)"
        )

        return differential
