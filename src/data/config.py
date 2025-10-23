"""
Data source configuration for SolaraAlpha.
"""

import os
from pathlib import Path

# Primary data source
DATA_SOURCE = "yahoo_finance"

# Rate limiting configuration for Yahoo Finance
# NOTE: retry_attempts and retry_delay defined but not yet implemented in fetcher module
# Yahoo Finance has history of breaking changes (Feb 2025, Oct 2024) - Alpaca Markets documented as fallback
YAHOO_FINANCE_CONFIG = {
    "delay_between_requests": 2.0,  # seconds (may need 5-10s for bulk downloads)
    "retry_attempts": 3,  # TODO: implement in fetcher module
    "retry_delay": 5.0,  # seconds - TODO: implement exponential backoff
    "cache_dir": os.getenv("YFINANCE_CACHE_DIR", str(Path.home() / ".cache" / "yfinance"))
}

# Validate data source configuration
if DATA_SOURCE != "yahoo_finance":
    raise ValueError(f"Unsupported data source: {DATA_SOURCE}. Only 'yahoo_finance' is currently implemented.")

# Data validation settings
MIN_YEARS_HISTORICAL_DATA = 3
MIN_SYMBOLS_REQUIRED = 5
REQUIRED_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

# Default symbols for validation/testing
TEST_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL"]
