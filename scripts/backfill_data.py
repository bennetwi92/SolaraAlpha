#!/usr/bin/env python3
"""CLI tool for backfilling market data.

Usage:
    python scripts/backfill_data.py --all
    python scripts/backfill_data.py --asset-class equities
    python scripts/backfill_data.py --symbol AAPL --asset-class equities
"""
import argparse
import sys
from pathlib import Path

# Add src to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root / "src"))

from data.pipeline import DataPipeline


def main() -> None:
    """Run data backfill based on CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Backfill market data for SolaraAlpha"
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Backfill all configured symbols",
    )

    parser.add_argument(
        "--asset-class",
        type=str,
        choices=["equities", "crypto", "forex", "etfs", "fixed_income"],
        help="Asset class to backfill",
    )

    parser.add_argument(
        "--symbol",
        type=str,
        help="Specific symbol to backfill",
    )

    parser.add_argument(
        "--frequency",
        type=str,
        default="1d",
        help="Data frequency (default: 1d)",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force refetch even if data exists",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.symbol and not args.asset_class:
        parser.error("--symbol requires --asset-class")

    if not args.all and not args.asset_class and not args.symbol:
        parser.error("Must specify --all, --asset-class, or --symbol")

    # Initialize pipeline
    pipeline = DataPipeline()

    # Execute backfill
    if args.all:
        print("Starting full backfill of all configured symbols...")
        results = pipeline.backfill_all(
            frequency=args.frequency, force_refetch=args.force
        )

        # Print summary
        print("\n=== Backfill Summary ===")
        for asset_class, symbol_results in results.items():
            passed = sum(1 for r in symbol_results.values() if r.passed)
            total = len(symbol_results)
            print(f"{asset_class}: {passed}/{total} symbols validated")

    elif args.symbol:
        print(
            f"Backfilling {args.symbol} ({args.asset_class}/{args.frequency})..."
        )
        result = pipeline.fetch_and_store(
            args.symbol,
            args.asset_class,
            frequency=args.frequency,
            force_refetch=args.force,
        )

        if result.passed:
            print(f"✓ {args.symbol} validated successfully")
        else:
            print(f"✗ {args.symbol} validation failed")
            for issue in result.get_errors():
                print(f"  ERROR: {issue.message}")

    elif args.asset_class:
        config = pipeline.config
        symbols = config.get_symbols(args.asset_class)

        print(
            f"Backfilling {len(symbols)} {args.asset_class} symbols "
            f"({args.frequency})..."
        )

        results = pipeline.backfill_symbols(
            symbols,
            args.asset_class,
            frequency=args.frequency,
            force_refetch=args.force,
        )

        # Print summary
        passed = sum(1 for r in results.values() if r.passed)
        print(f"\n{args.asset_class}: {passed}/{len(symbols)} symbols validated")

    print("\nBackfill complete!")


if __name__ == "__main__":
    main()
