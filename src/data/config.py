"""
Data source configuration for SolaraAlpha.
"""

import os
from pathlib import Path

# Primary data source
DATA_SOURCE = "yahoo_finance"

# Yahoo Finance configuration
# UPDATE 2025-10-23: Standard yfinance works without curl_cffi (rate limits appear relaxed)
# Yahoo Finance has history of breaking changes (Oct 2024, early 2024) - Alpaca Markets documented as fallback
YAHOO_FINANCE_CONFIG = {
    "use_curl_cffi": False,  # UPDATE 2025-10-23: No longer required (rate limits relaxed)
    "browser_impersonate": "chrome",  # Legacy: TLS fingerprint impersonation (no longer needed)
    "delay_between_requests": 0.0,  # No delay needed
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
TEST_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA", "GOOGL"]
