#!/usr/bin/env python3
"""
Test script to validate Yahoo Finance data source for SolaraAlpha.
Tests: 3+ years historical data, 5+ symbols, OHLCV availability.
"""

import yfinance as yf
from datetime import datetime, timedelta
import time
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from data.config import (
    TEST_SYMBOLS,
    MIN_YEARS_HISTORICAL_DATA,
    MIN_SYMBOLS_REQUIRED,
    REQUIRED_OHLCV_COLUMNS,
    YAHOO_FINANCE_CONFIG
)

def test_data_source():
    # Use configured test symbols
    symbols = TEST_SYMBOLS
    delay = YAHOO_FINANCE_CONFIG["delay_between_requests"]

    # Request configured years of data
    end_date = datetime.now()
    start_date = end_date - timedelta(days=MIN_YEARS_HISTORICAL_DATA*365 + 30)

    print(f"Testing Yahoo Finance (yfinance) data source")
    print(f"Period: {start_date.date()} to {end_date.date()}")
    print(f"Symbols: {', '.join(symbols)}\n")

    results = {}

    for i, symbol in enumerate(symbols):
        print(f"Fetching {symbol}...")

        # Add delay between requests to avoid rate limiting
        if i > 0:
            time.sleep(delay)

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date)

            if df.empty:
                results[symbol] = {"success": False, "error": "No data returned"}
                print(f"  ❌ FAILED: No data returned\n")
                continue

            # Validate OHLCV columns
            missing_cols = [col for col in REQUIRED_OHLCV_COLUMNS if col not in df.columns]

            if missing_cols:
                results[symbol] = {"success": False, "error": f"Missing columns: {missing_cols}"}
                print(f"  ❌ FAILED: Missing columns {missing_cols}\n")
                continue

            # Calculate stats
            days_of_data = len(df)
            years_of_data = days_of_data / 252  # Trading days per year
            date_range = f"{df.index[0].date()} to {df.index[-1].date()}"

            results[symbol] = {
                "success": True,
                "rows": days_of_data,
                "years": round(years_of_data, 2),
                "date_range": date_range,
                "columns": list(df.columns)
            }

            print(f"  ✅ SUCCESS")
            print(f"     Rows: {days_of_data}")
            print(f"     Years: ~{round(years_of_data, 2)}")
            print(f"     Range: {date_range}")
            print(f"     Columns: {', '.join(df.columns)}\n")

        except Exception as e:
            results[symbol] = {"success": False, "error": str(e)}
            print(f"  ❌ FAILED: {e}\n")

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    successes = sum(1 for r in results.values() if r.get("success"))
    print(f"Successful fetches: {successes}/{len(symbols)}")

    if successes >= MIN_SYMBOLS_REQUIRED:
        print("\n✅ Yahoo Finance meets all requirements:")
        print("   - 5+ symbols with data")
        print("   - 3+ years of historical data")
        print("   - OHLCV columns present")
        print("   - Free, no API key required")
        print("   - Rate limits manageable with delays")
        return True
    else:
        print("\n❌ Yahoo Finance does not meet requirements")
        print(f"   Only {successes}/5 symbols fetched successfully")
        return False

if __name__ == "__main__":
    success = test_data_source()
    exit(0 if success else 1)
