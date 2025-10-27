"""Portfolio construction for statistical arbitrage strategy.

Constructs market-neutral long/short portfolios by ranking stocks by z-score magnitude
and selecting top/bottom deciles.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .signals import SignalType, PortfolioSignal


@dataclass
class PortfolioPosition:
    """Represents a portfolio position in a stock."""

    symbol: str
    signal_type: SignalType
    z_score: float
    residual: float
    target_weight: float  # Target portfolio weight (-1 to 1)
    rank: int  # Rank by z-score magnitude (1 = most extreme)


class PortfolioConstructor:
    """Construct market-neutral long/short portfolios from stat arb signals.

    Uses ranking approach:
    1. Rank stocks by z-score magnitude
    2. Long bottom N stocks (most negative z-scores)
    3. Short top N stocks (most positive z-scores)
    4. Market-neutral: equal dollar long/short
    """

    def __init__(
        self,
        min_positions: int = 20,
        max_positions: int = 50,
        target_positions: int = 30,
        long_short_ratio: float = 1.0,
    ):
        """Initialize portfolio constructor.

        Args:
            min_positions: Minimum total positions (reject if less)
            max_positions: Maximum total positions
            target_positions: Target total positions (split equally long/short)
            long_short_ratio: Ratio of long to short exposure (1.0 = market-neutral)
        """
        if target_positions < min_positions or target_positions > max_positions:
            raise ValueError(
                f"target_positions ({target_positions}) must be between "
                f"min_positions ({min_positions}) and max_positions ({max_positions})"
            )

        if target_positions % 2 != 0:
            raise ValueError(f"target_positions ({target_positions}) must be even")

        self.min_positions = min_positions
        self.max_positions = max_positions
        self.target_positions = target_positions
        self.long_short_ratio = long_short_ratio

        self.target_long = int(target_positions / 2)
        self.target_short = int(target_positions / 2)

    def construct_portfolio(
        self, signal: PortfolioSignal
    ) -> Dict[str, PortfolioPosition]:
        """Construct portfolio from signals.

        Ranks stocks by z-score magnitude and selects top/bottom for long/short.

        Args:
            signal: PortfolioSignal with z-scores for all stocks

        Returns:
            Dict mapping symbol to PortfolioPosition

        Raises:
            ValueError: If insufficient valid signals
        """
        # Filter out NO_POSITION and HOLD signals (only consider entries)
        entry_signals = {
            symbol: sig
            for symbol, sig in signal.signals.items()
            if sig in [SignalType.LONG, SignalType.SHORT]
        }

        if len(entry_signals) < self.min_positions:
            raise ValueError(
                f"Insufficient entry signals: {len(entry_signals)} < "
                f"{self.min_positions}"
            )

        # Rank stocks by z-score magnitude
        ranked_stocks = self._rank_by_z_score(signal.z_scores, entry_signals)

        # Select top/bottom stocks
        portfolio = self._select_positions(
            ranked_stocks, signal.z_scores, signal.residuals, entry_signals
        )

        return portfolio

    def _rank_by_z_score(
        self, z_scores: Dict[str, float], entry_signals: Dict[str, SignalType]
    ) -> List[Tuple[str, float, SignalType]]:
        """Rank stocks by absolute z-score magnitude.

        Args:
            z_scores: Dict of z-scores
            entry_signals: Dict of entry signals (LONG/SHORT)

        Returns:
            List of (symbol, z_score, signal) tuples sorted by |z_score| descending
        """
        ranked = []

        for symbol, signal in entry_signals.items():
            z_score = z_scores.get(symbol, np.nan)
            if not pd.isna(z_score):
                ranked.append((symbol, z_score, signal))

        # Sort by absolute z-score descending (most extreme first)
        ranked.sort(key=lambda x: abs(x[1]), reverse=True)

        return ranked

    def _select_positions(
        self,
        ranked_stocks: List[Tuple[str, float, SignalType]],
        z_scores: Dict[str, float],
        residuals: Dict[str, float],
        entry_signals: Dict[str, SignalType],
    ) -> Dict[str, PortfolioPosition]:
        """Select top/bottom stocks for long/short portfolio.

        Args:
            ranked_stocks: List of (symbol, z_score, signal) sorted by |z_score|
            z_scores: Dict of all z-scores
            residuals: Dict of all residuals
            entry_signals: Dict of entry signals

        Returns:
            Dict mapping symbol to PortfolioPosition
        """
        portfolio = {}
        long_count = 0
        short_count = 0

        for rank, (symbol, z_score, signal) in enumerate(ranked_stocks, start=1):
            # Stop if we have enough positions
            if long_count >= self.target_long and short_count >= self.target_short:
                break

            # Skip if position limit reached for this side
            if signal == SignalType.LONG and long_count >= self.target_long:
                continue
            if signal == SignalType.SHORT and short_count >= self.target_short:
                continue

            # Add position
            position = PortfolioPosition(
                symbol=symbol,
                signal_type=signal,
                z_score=z_score,
                residual=residuals.get(symbol, np.nan),
                target_weight=0.0,  # Will be set in position sizing
                rank=rank,
            )

            portfolio[symbol] = position

            if signal == SignalType.LONG:
                long_count += 1
            elif signal == SignalType.SHORT:
                short_count += 1

        # Check minimum positions
        total_positions = len(portfolio)
        if total_positions < self.min_positions:
            raise ValueError(
                f"Insufficient positions after selection: {total_positions} < "
                f"{self.min_positions}"
            )

        return portfolio

    def rebalance_portfolio(
        self,
        current_portfolio: Dict[str, PortfolioPosition],
        new_signal: PortfolioSignal,
    ) -> Tuple[Dict[str, PortfolioPosition], Dict[str, PortfolioPosition]]:
        """Rebalance portfolio based on new signals.

        Args:
            current_portfolio: Current portfolio positions
            new_signal: New portfolio signal

        Returns:
            Tuple of (positions_to_add, positions_to_remove)
        """
        # Construct ideal new portfolio
        try:
            new_portfolio = self.construct_portfolio(new_signal)
        except ValueError:
            # If construction fails, keep current portfolio
            return {}, {}

        # Find positions to add (in new but not in current)
        positions_to_add = {
            symbol: pos
            for symbol, pos in new_portfolio.items()
            if symbol not in current_portfolio
        }

        # Find positions to remove (in current but not in new)
        positions_to_remove = {
            symbol: pos
            for symbol, pos in current_portfolio.items()
            if symbol not in new_portfolio
        }

        return positions_to_add, positions_to_remove

    def get_portfolio_statistics(
        self, portfolio: Dict[str, PortfolioPosition]
    ) -> Dict[str, float]:
        """Calculate portfolio statistics.

        Args:
            portfolio: Dict of portfolio positions

        Returns:
            Dict with portfolio statistics
        """
        if not portfolio:
            return {
                "total_positions": 0,
                "long_positions": 0,
                "short_positions": 0,
                "long_short_ratio": 0.0,
                "avg_z_score": 0.0,
                "avg_long_z_score": 0.0,
                "avg_short_z_score": 0.0,
            }

        long_positions = [
            pos for pos in portfolio.values() if pos.signal_type == SignalType.LONG
        ]
        short_positions = [
            pos for pos in portfolio.values() if pos.signal_type == SignalType.SHORT
        ]

        all_z_scores = [pos.z_score for pos in portfolio.values()]
        long_z_scores = [pos.z_score for pos in long_positions]
        short_z_scores = [pos.z_score for pos in short_positions]

        return {
            "total_positions": len(portfolio),
            "long_positions": len(long_positions),
            "short_positions": len(short_positions),
            "long_short_ratio": (
                len(long_positions) / len(short_positions)
                if short_positions
                else 0.0
            ),
            "avg_z_score": np.mean(np.abs(all_z_scores)) if all_z_scores else 0.0,
            "avg_long_z_score": np.mean(long_z_scores) if long_z_scores else 0.0,
            "avg_short_z_score": np.mean(short_z_scores) if short_z_scores else 0.0,
        }
