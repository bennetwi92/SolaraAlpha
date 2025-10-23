"""
Data source configuration for SolaraAlpha.
"""

import os
from pathlib import Path

# Primary data source
DATA_SOURCE = "yahoo_finance"

# Yahoo Finance configuration
# UPDATE 2025-10-23 FINAL: yfinance 0.2.66+ works reliably without curl_cffi
# Requires version 0.2.66+ and 1-second delays between ticker requests
YAHOO_FINANCE_CONFIG = {
    "min_version": "0.2.66",  # Minimum yfinance version for reliable rate limit handling
    "delay_between_requests": 1.0,  # seconds - rate limit protection
    "retry_attempts": 3,  # Retry logic implemented in scripts/validate_data_source.py
    "retry_delay": 5.0,  # seconds - exponential backoff in validation script
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
