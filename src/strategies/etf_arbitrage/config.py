"""Configuration for ETF arbitrage strategy."""

from dataclasses import dataclass
from typing import List


@dataclass
class ETFArbConfig:
    """Configuration for ETF arbitrage strategy.

    Attributes:
        pairs: List of ETF pairs to trade (each pair as tuple)
        lookback_window: Days for rolling spread statistics
        entry_threshold: Z-score threshold for entry (±)
        exit_threshold: Z-score threshold for exit (±)
        stop_loss_threshold: Z-score threshold for stop loss (±)
        min_half_life: Minimum mean reversion half-life in days
        max_half_life: Maximum mean reversion half-life in days
        max_position_pct: Max portfolio allocation per spread
        position_sizing_method: 'kelly' or 'fixed_fractional'
        kelly_fraction: Fraction of Kelly to use (for safety)
        fixed_fraction: Fixed fraction if not using Kelly
        update_interval: Minutes between strategy iterations
    """

    # ETF pairs to trade
    pairs: List[tuple] = None

    # Spread calculation
    lookback_window: int = 60

    # Signal thresholds
    entry_threshold: float = 2.0
    exit_threshold: float = 0.5
    stop_loss_threshold: float = 3.0

    # Mean reversion constraints
    min_half_life: float = 1.0
    max_half_life: float = 30.0

    # Position sizing
    max_position_pct: float = 0.20  # Max 20% per spread
    position_sizing_method: str = 'kelly'
    kelly_fraction: float = 0.5
    fixed_fraction: float = 0.10

    # Execution
    update_interval: int = 60  # 1 hour

    def __post_init__(self):
        """Set default pairs if not provided."""
        if self.pairs is None:
            self.pairs = [
                ('SPY', 'IVV'),  # S&P 500 ETFs
                ('GLD', 'IAU'),  # Gold ETFs
            ]


# Default configuration instance
DEFAULT_CONFIG = ETFArbConfig()
