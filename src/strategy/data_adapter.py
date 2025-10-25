"""
Strategy Framework - Market Data Integration

Adapter for feeding market data to strategies.
"""

from src.data.market_client import MarketDataClient
from src.strategy.base import BaseStrategy
import pandas as pd
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class StrategyDataAdapter:
    """
    Adapter for feeding market data to strategies.

    Handles data fetching, caching, and formatting for strategy consumption.
    """

    def __init__(
        self,
        data_client: MarketDataClient,
        strategy: BaseStrategy
    ):
        """
        Initialize data adapter.

        Args:
            data_client: Market data client
            strategy: Strategy requiring data
        """
        self.data_client = data_client
        self.strategy = strategy
        self.data_cache: Dict[str, pd.DataFrame] = {}

    def fetch_data(
        self,
        start_date: str,
        end_date: str,
        interval: str = '1d'
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetch data for all strategy symbols.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            interval: Data interval

        Returns:
            Dict mapping symbols to OHLCV DataFrames
        """
        data = {}

        for symbol in self.strategy.config.symbols:
            try:
                df = self.data_client.get_ohlcv(
                    symbol, start=start_date, end=end_date, interval=interval
                )
                data[symbol] = df
                self.data_cache[symbol] = df
            except Exception as e:
                logger.error(f"Failed to fetch data for {symbol}: {e}")

        return data

    def get_latest_data(
        self,
        symbols: Optional[list] = None,
        lookback_bars: int = 100
    ) -> Dict[str, pd.DataFrame]:
        """
        Get latest N bars of data for symbols.

        Args:
            symbols: List of symbols (None = all strategy symbols)
            lookback_bars: Number of bars to return

        Returns:
            Dict mapping symbols to recent OHLCV data
        """
        if symbols is None:
            symbols = self.strategy.config.symbols

        data = {}

        for symbol in symbols:
            if symbol in self.data_cache:
                df = self.data_cache[symbol].tail(lookback_bars)
                data[symbol] = df
            else:
                logger.warning(f"No cached data for {symbol}")

        return data
