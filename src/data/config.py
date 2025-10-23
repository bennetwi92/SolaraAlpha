"""
Data source configuration for SolaraAlpha.

IMPORTANT LEGAL NOTICE:
Yahoo Finance is for RESEARCH AND DEVELOPMENT ONLY (Phase 1 prototype).
Yahoo Finance's Terms of Service restrict usage to "personal use only."
Commercial trading operations require migration to a commercial-friendly data source.

See DEPLOYMENT_READINESS.md before deploying to production.
"""

import os
from pathlib import Path

# Environment: development, staging, production
ENVIRONMENT = os.getenv("SOLARA_ENV", "development")

# Primary data source
DATA_SOURCE = "yahoo_finance"

# LEGAL SAFEGUARD: Prevent production deployment with Yahoo Finance
if ENVIRONMENT == "production" and DATA_SOURCE == "yahoo_finance":
    raise ValueError(
        "Yahoo Finance is not authorized for production use due to Terms of Service restrictions. "
        "Migrate to Twelve Data or another commercial-friendly source before production deployment. "
        "See /src/data/README_DATASOURCE.md 'Legal Considerations' section."
    )

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

# Twelve Data configuration (alternative/fallback source)
# See README_DATASOURCE.md for migration instructions
TWELVE_DATA_CONFIG = {
    "api_key_env": "TWELVE_DATA_API_KEY",  # Environment variable for API key
    "rate_limit_calls_per_min": 8,  # Free tier: 8 calls/minute
    "rate_limit_calls_per_day": 800,  # Free tier: 800 calls/day
    "max_data_points_per_request": 5000,  # Maximum data points per API call
    "retry_attempts": 3,
    "retry_delay": 5.0,
    "timezone": "America/New_York"
}

# Validate data source configuration
SUPPORTED_SOURCES = ["yahoo_finance", "twelve_data"]
if DATA_SOURCE not in SUPPORTED_SOURCES:
    raise ValueError(f"Unsupported data source: {DATA_SOURCE}. Supported: {SUPPORTED_SOURCES}")

# For Twelve Data, validate API key is available
if DATA_SOURCE == "twelve_data":
    if not os.getenv(TWELVE_DATA_CONFIG["api_key_env"]):
        raise ValueError(
            f"Twelve Data selected but {TWELVE_DATA_CONFIG['api_key_env']} environment variable not set. "
            f"Sign up at https://twelvedata.com/register and set your API key."
        )

# Data validation settings
MIN_YEARS_HISTORICAL_DATA = 3
MIN_SYMBOLS_REQUIRED = 5
REQUIRED_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

# Default symbols for validation/testing
TEST_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA", "GOOGL"]
