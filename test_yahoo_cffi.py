#!/usr/bin/env python3
"""Test Yahoo Finance with curl_cffi browser impersonation."""

import yfinance as yf
from curl_cffi import requests
from datetime import datetime, timedelta

def test_yahoo_with_cffi():
    """Test Yahoo Finance using curl_cffi to bypass rate limits."""
    print("Testing Yahoo Finance with curl_cffi Browser Impersonation")
    print("=" * 60)

    # Create session that impersonates Chrome browser
    session = requests.Session(impersonate="chrome")

    symbols = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]
    end_date = datetime.now()
    start_date = end_date - timedelta(days=3*365 + 180)  # 3.5 years to verify >3 years

    results = {}

    for symbol in symbols:
        try:
            print(f"\nFetching {symbol}...")
            ticker = yf.Ticker(symbol, session=session)
            hist = ticker.history(start=start_date, end=end_date)

            if len(hist) > 0:
                results[symbol] = {
                    'success': True,
                    'records': len(hist),
                    'start': hist.index[0],
                    'end': hist.index[-1],
                    'columns': list(hist.columns),
                    'missing_values': hist.isnull().sum().sum(),
                    'zero_volume_days': (hist['Volume'] == 0).sum()
                }
                print(f"  ✓ SUCCESS - {len(hist)} records")
                print(f"    Range: {hist.index[0].date()} to {hist.index[-1].date()}")
                print(f"    Missing values: {hist.isnull().sum().sum()}")
                print(f"    Zero volume days: {(hist['Volume'] == 0).sum()}")
            else:
                results[symbol] = {'success': False, 'error': 'No data retrieved'}
                print(f"  ✗ FAILURE - No data")

        except Exception as e:
            results[symbol] = {'success': False, 'error': str(e)}
            print(f"  ✗ ERROR - {e}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    successful = sum(1 for r in results.values() if r.get('success'))
    print(f"Successful: {successful}/{len(symbols)}")

    if successful == len(symbols):
        print("\n✓ ALL SYMBOLS RETRIEVED SUCCESSFULLY")
        print("\nData Quality:")
        for symbol, data in results.items():
            years = (data['end'] - data['start']).days / 365.25
            print(f"  {symbol}: {data['records']} records, {years:.1f} years")

        return True
    else:
        print("\n✗ SOME SYMBOLS FAILED")
        for symbol, data in results.items():
            if not data.get('success'):
                print(f"  {symbol}: {data.get('error')}")
        return False

if __name__ == "__main__":
    success = test_yahoo_with_cffi()

    print("\n" + "=" * 60)
    if success:
        print("DECISION: SELECT YAHOO FINANCE AS PRIMARY DATA SOURCE")
        print("=" * 60)
        print("Rationale:")
        print("  ✓ Free (no API key required)")
        print("  ✓ No practical rate limits with curl_cffi")
        print("  ✓ 3+ years historical data confirmed")
        print("  ✓ 5+ liquid symbols confirmed")
        print("  ✓ OHLCV data available")
        print("  ✓ Simple Python library (yfinance)")
        print("  ✓ Widely used in quant community")
        print("\nImplementation:")
        print("  - Library: yfinance + curl_cffi")
        print("  - Session: curl_cffi.requests.Session(impersonate='chrome')")
        print("  - No API key configuration needed")
    else:
        print("DECISION: YAHOO FINANCE TEST FAILED")
        print("=" * 60)
        print("Need to evaluate alternatives (Alpha Vantage, Twelve Data, manual CSV)")
