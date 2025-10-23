"""
Data source configuration for SolaraAlpha.
"""

import os
from pathlib import Path

# Primary data source
DATA_SOURCE = "yahoo_finance"

# Yahoo Finance configuration
# UPDATE 2025-10-23: curl_cffi REQUIRED (rate limits confirmed active)
# Yahoo Finance has history of breaking changes (Oct 2024, early 2024) - Alpaca Markets documented as fallback
YAHOO_FINANCE_CONFIG = {
    "use_curl_cffi": True,  # REQUIRED 2025-10-23: Rate limits confirmed active
    "browser_impersonate": "chrome",  # TLS fingerprint impersonation for rate limit bypass
    "delay_between_requests": 0.0,  # No delay needed with curl_cffi
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
