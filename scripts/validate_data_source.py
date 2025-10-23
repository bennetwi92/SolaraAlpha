#!/usr/bin/env python3
"""
Data source validation script for SolaraAlpha.

Validates Yahoo Finance API access, data quality, and retry logic.
Run this script periodically to ensure data source remains operational.
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path for config imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import yfinance as yf
from data.config import (
    YAHOO_FINANCE_CONFIG,
    TEST_SYMBOLS,
    MIN_YEARS_HISTORICAL_DATA,
    REQUIRED_OHLCV_COLUMNS
)


def validate_version():
    """Validate yfinance version meets minimum requirement."""
    print("=== VERSION VALIDATION ===")
    current_version = yf.__version__
    min_version = YAHOO_FINANCE_CONFIG["min_version"]
    print(f"Current yfinance version: {current_version}")
    print(f"Minimum required version: {min_version}")

    # Simple version comparison (assumes semantic versioning)
    current_parts = [int(x) for x in current_version.split('.')]
    min_parts = [int(x) for x in min_version.split('.')]

    if current_parts >= min_parts:
        print("✓ Version check PASSED\n")
        return True
    else:
        print("✗ Version check FAILED - upgrade required\n")
        return False


def fetch_with_retry(symbol, start_date, end_date, max_retries=3, retry_delay=5.0):
    """
    Fetch data with retry logic and exponential backoff.

    Implements retry logic marked as TODO in config.py.
    """
    for attempt in range(max_retries):
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(start=start_date, end=end_date, auto_adjust=True)

            if len(hist) > 0:
                return hist, None
            else:
                error_msg = f"No data returned for {symbol}"
                if attempt < max_retries - 1:
                    print(f"  Attempt {attempt + 1} failed: {error_msg}, retrying...")
                    time.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
                else:
                    return None, error_msg

        except Exception as e:
            error_msg = f"Exception for {symbol}: {str(e)}"
            if attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1} failed: {error_msg}, retrying...")
                time.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
            else:
                return None, error_msg

    return None, f"Max retries exceeded for {symbol}"


def validate_api_access():
    """Validate API access with test symbols and retry logic."""
    print("=== API ACCESS VALIDATION ===")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=MIN_YEARS_HISTORICAL_DATA * 365)

    print(f"Testing {len(TEST_SYMBOLS)} symbols:")
    print(f"Date range: {start_date.date()} to {end_date.date()}")
    print()

    results = {}
    for symbol in TEST_SYMBOLS:
        print(f"Fetching {symbol}...", end=" ", flush=True)

        hist, error = fetch_with_retry(
            symbol,
            start_date,
            end_date,
            max_retries=YAHOO_FINANCE_CONFIG["retry_attempts"],
            retry_delay=YAHOO_FINANCE_CONFIG["retry_delay"]
        )

        if hist is not None:
            results[symbol] = {
                "success": True,
                "records": len(hist),
                "date_range": f"{hist.index[0].date()} to {hist.index[-1].date()}",
                "columns": list(hist.columns),
                "missing_pct": (hist.isna().sum().sum() / (len(hist) * len(hist.columns))) * 100
            }
            print(f"✓ {len(hist)} records")
        else:
            results[symbol] = {
                "success": False,
                "error": error
            }
            print(f"✗ {error}")

        # Rate limit protection
        time.sleep(YAHOO_FINANCE_CONFIG["delay_between_requests"])

    print()
    return results


def validate_data_quality(results):
    """Validate data quality meets requirements."""
    print("=== DATA QUALITY VALIDATION ===")

    all_passed = True

    for symbol, data in results.items():
        if not data["success"]:
            print(f"✗ {symbol}: API fetch failed")
            all_passed = False
            continue

        # Check record count (expect ~750 days for 3 years of trading days)
        # Use 240 trading days/year to account for holidays, weekends
        min_expected_records = MIN_YEARS_HISTORICAL_DATA * 240  # Conservative estimate
        if data["records"] < min_expected_records:
            print(f"✗ {symbol}: Insufficient records ({data['records']} < {min_expected_records})")
            all_passed = False
            continue

        # Check required columns
        missing_cols = set(REQUIRED_OHLCV_COLUMNS) - set(data["columns"])
        if missing_cols:
            print(f"✗ {symbol}: Missing columns {missing_cols}")
            all_passed = False
            continue

        # Check data quality
        if data["missing_pct"] > 1.0:  # More than 1% missing data
            print(f"✗ {symbol}: High missing data percentage ({data['missing_pct']:.2f}%)")
            all_passed = False
            continue

        print(f"✓ {symbol}: {data['records']} records, {data['missing_pct']:.2f}% missing")

    print()
    return all_passed


def main():
    """Run all validation checks."""
    print("SolaraAlpha Data Source Validation")
    print("=" * 50)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()

    # Version check
    if not validate_version():
        print("VALIDATION FAILED: Version check failed")
        sys.exit(1)

    # API access check
    results = validate_api_access()

    # Data quality check
    quality_passed = validate_data_quality(results)

    # Summary
    print("=== VALIDATION SUMMARY ===")
    successful = sum(1 for r in results.values() if r["success"])
    total = len(results)

    print(f"Symbols tested: {total}")
    print(f"Successful: {successful}")
    print(f"Failed: {total - successful}")
    print()

    if successful == total and quality_passed:
        print("✓ ALL VALIDATION CHECKS PASSED")
        print("\nYahoo Finance API is operational and meets requirements.")
        sys.exit(0)
    else:
        print("✗ VALIDATION FAILED")
        print("\nData source issues detected. Review errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
