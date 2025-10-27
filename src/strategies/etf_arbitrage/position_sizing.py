"""Position sizing for ETF arbitrage strategy using Kelly criterion.

Implements Kelly criterion and fixed fractional position sizing with capital constraints.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SpreadPosition:
    """Position details for spread trade."""
    symbol1: str
    symbol2: str
    quantity1: int  # Positive for long, negative for short
    quantity2: int  # Positive for long, negative for short
    price1: float
    price2: float
    dollar_exposure1: float
    dollar_exposure2: float
    total_capital: float
    allocation_pct: float  # Percentage of portfolio allocated


@dataclass
class PositionSizingConfig:
    """Configuration for position sizing."""
    method: str = 'kelly'  # 'kelly' or 'fixed_fractional'
    max_position_pct: float = 0.20  # Max 20% of portfolio per spread
    kelly_fraction: float = 0.5  # Half Kelly for safety
    fixed_fraction: float = 0.10  # 10% for fixed fractional
    min_capital_per_trade: float = 1000.0  # Minimum capital per trade


class PositionSizer:
    """Calculate position sizes for spread trades."""

    def __init__(
        self,
        portfolio_value: float,
        config: Optional[PositionSizingConfig] = None
    ):
        """Initialize position sizer.

        Args:
            portfolio_value: Total portfolio value
            config: Position sizing configuration
        """
        self.portfolio_value = portfolio_value
        self.config = config or PositionSizingConfig()

    def calculate_position_size(
        self,
        symbol1: str,
        symbol2: str,
        price1: float,
        price2: float,
        z_score: float,
        spread_volatility: float,
        win_rate: Optional[float] = None,
        avg_win: Optional[float] = None,
        avg_loss: Optional[float] = None
    ) -> SpreadPosition:
        """Calculate position size for spread trade.

        Args:
            symbol1: First ETF symbol
            symbol2: Second ETF symbol
            price1: Current price of first ETF
            price2: Current price of second ETF
            z_score: Current z-score (determines direction)
            spread_volatility: Standard deviation of spread
            win_rate: Historical win rate (for Kelly criterion)
            avg_win: Average win size (for Kelly criterion)
            avg_loss: Average loss size (for Kelly criterion)

        Returns:
            SpreadPosition with calculated quantities
        """
        if self.config.method == 'kelly' and all([win_rate, avg_win, avg_loss]):
            allocation_pct = self._kelly_criterion(win_rate, avg_win, avg_loss)
        else:
            allocation_pct = self.config.fixed_fraction

        # Apply maximum position constraint
        allocation_pct = min(allocation_pct, self.config.max_position_pct)

        # Calculate capital to allocate
        capital = self.portfolio_value * allocation_pct

        # Ensure minimum capital requirement
        if capital < self.config.min_capital_per_trade:
            capital = 0
            allocation_pct = 0

        # Calculate quantities based on spread direction
        if z_score < 0:
            # Spread below mean: long ETF1, short ETF2
            quantity1, quantity2 = self._calculate_market_neutral_quantities(
                capital, price1, price2, direction='long_spread'
            )
        else:
            # Spread above mean: short ETF1, long ETF2
            quantity1, quantity2 = self._calculate_market_neutral_quantities(
                capital, price1, price2, direction='short_spread'
            )

        # Calculate dollar exposures
        dollar_exposure1 = quantity1 * price1
        dollar_exposure2 = quantity2 * price2
        total_capital = abs(dollar_exposure1) + abs(dollar_exposure2)

        return SpreadPosition(
            symbol1=symbol1,
            symbol2=symbol2,
            quantity1=quantity1,
            quantity2=quantity2,
            price1=price1,
            price2=price2,
            dollar_exposure1=dollar_exposure1,
            dollar_exposure2=dollar_exposure2,
            total_capital=total_capital,
            allocation_pct=allocation_pct
        )

    def _kelly_criterion(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float
    ) -> float:
        """Calculate optimal position size using Kelly criterion.

        Kelly % = W - [(1 - W) / R]
        where W = win rate, R = avg_win / avg_loss

        Args:
            win_rate: Probability of winning trade
            avg_win: Average winning trade return
            avg_loss: Average losing trade return (positive value)

        Returns:
            Optimal allocation percentage (0 to 1)
        """
        if avg_loss <= 0:
            return 0.0

        win_loss_ratio = avg_win / avg_loss
        kelly_pct = win_rate - ((1 - win_rate) / win_loss_ratio)

        # Apply fractional Kelly for safety
        kelly_pct *= self.config.kelly_fraction

        # Ensure non-negative
        kelly_pct = max(0.0, kelly_pct)

        return kelly_pct

    def _calculate_market_neutral_quantities(
        self,
        capital: float,
        price1: float,
        price2: float,
        direction: str
    ) -> tuple[int, int]:
        """Calculate market-neutral quantities for spread trade.

        Allocates capital equally between long and short legs to maintain
        market neutrality.

        Args:
            capital: Total capital to allocate
            price1: Price of first ETF
            price2: Price of second ETF
            direction: 'long_spread' or 'short_spread'

        Returns:
            Tuple of (quantity1, quantity2)
        """
        # Split capital equally between legs
        capital_per_leg = capital / 2

        # Calculate quantities (round down to ensure we don't exceed capital)
        if direction == 'long_spread':
            # Long ETF1, short ETF2
            quantity1 = int(capital_per_leg / price1)
            quantity2 = -int(capital_per_leg / price2)
        else:
            # Short ETF1, long ETF2
            quantity1 = -int(capital_per_leg / price1)
            quantity2 = int(capital_per_leg / price2)

        return quantity1, quantity2

    def update_portfolio_value(self, new_value: float) -> None:
        """Update portfolio value for position sizing.

        Args:
            new_value: New portfolio value
        """
        self.portfolio_value = new_value

    def get_max_position_capital(self) -> float:
        """Get maximum capital allowed per position.

        Returns:
            Maximum capital per position
        """
        return self.portfolio_value * self.config.max_position_pct
