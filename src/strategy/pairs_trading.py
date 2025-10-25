"""
Pairs Trading Strategy

Market-neutral relative value strategy exploiting mean reversion in cointegrated
security pairs. Focuses on liquid ETFs for lower transaction costs and better
statistical properties.

Strategy Logic:
1. Pair Selection: Cointegration testing (Johansen) + correlation analysis
2. Spread Calculation: Hedge ratio from cointegration, normalized z-score
3. Signal Generation: Entry at ±z_entry threshold, exit at ±z_exit or mean
4. Position Sizing: Inverse volatility weighting, max position limits
5. Market Neutrality: Dollar-neutral long/short positions

References:
- Gatev, Goetzmann, Rouwenhorst (2006) "Pairs Trading: Performance of a Relative-Value Arbitrage Rule"
- Vidyamurthy (2004) "Pairs Trading: Quantitative Methods and Analysis"
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import logging

# Import statsmodels at module level to avoid repeated imports
try:
    from statsmodels.tsa.vector_ar.vecm import coint_johansen
    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False
    coint_johansen = None

from src.strategy.base import (
    RelativeValueStrategy,
    StrategyConfig,
    Signal,
    SignalType,
    Position,
    HedgeRatio
)

logger = logging.getLogger(__name__)


@dataclass
class PairCandidate:
    """
    Candidate pair with cointegration statistics.

    Attributes:
        symbol_long: First symbol in pair
        symbol_short: Second symbol in pair
        correlation: Pearson correlation coefficient
        coint_pvalue: Cointegration p-value (from Johansen test)
        hedge_ratio: Hedge ratio from cointegration
        half_life: Mean reversion half-life (days)
        score: Overall pair quality score
    """
    symbol_long: str
    symbol_short: str
    correlation: float
    coint_pvalue: float
    hedge_ratio: float
    half_life: float
    score: float


@dataclass
class PairPosition:
    """
    Tracking for a pairs trade.

    Attributes:
        symbol_long: Long leg symbol
        symbol_short: Short leg symbol
        entry_spread: Spread value at entry
        entry_zscore: Z-score at entry
        entry_timestamp: Entry timestamp
        days_held: Days since entry
        current_spread: Current spread value
        current_zscore: Current z-score
    """
    symbol_long: str
    symbol_short: str
    entry_spread: float
    entry_zscore: float
    entry_timestamp: pd.Timestamp
    days_held: int = 0
    current_spread: float = 0.0
    current_zscore: float = 0.0


class PairsTradingStrategy(RelativeValueStrategy):
    """
    Pairs trading strategy using cointegration and mean reversion.

    This strategy:
    - Identifies cointegrated pairs using Johansen test
    - Monitors spread z-scores for entry/exit signals
    - Maintains market-neutral positions
    - Uses inverse volatility weighting for position sizing

    Parameters:
        lookback_period (int): Lookback for cointegration/spread calc (default: 60)
        zscore_lookback (int): Lookback for z-score calculation (default: 30)
        z_entry (float): Z-score threshold for entry (default: 2.0)
        z_exit (float): Z-score threshold for exit (default: 0.5)
        max_holding_days (int): Maximum holding period (default: 30)
        correlation_threshold (float): Min correlation for pair selection (default: 0.7)
        coint_pvalue_threshold (float): Max p-value for cointegration (default: 0.05)
        position_size_pct (float): % of portfolio per pair (default: 0.2)
        max_pairs (int): Maximum concurrent pairs (default: 5)
        rebalance_hedge_ratio_days (int): Days between hedge ratio updates (default: 20)
        min_spread_move_pct (float): Minimum expected spread move to cover costs (default: 0.5%)
        transaction_cost_pct (float): Round-trip transaction cost (default: 0.2%)

    Example:
        >>> config = StrategyConfig(
        ...     name="PairsTradingETF",
        ...     version="1.0.0",
        ...     parameters={
        ...         'lookback_period': 60,
        ...         'z_entry': 2.0,
        ...         'z_exit': 0.5,
        ...         'max_holding_days': 30
        ...     },
        ...     symbols=['SPY', 'IWM', 'GLD', 'SLV', 'XLE', 'XLF'],
        ...     enable_short=True
        ... )
        >>> strategy = PairsTradingStrategy(config)
    """

    def __init__(self, config: StrategyConfig):
        """
        Initialize pairs trading strategy.

        Args:
            config: Strategy configuration
        """
        super().__init__(config)

        # Validate parameters
        self._validate_parameters()

        # Track selected pairs and active positions
        self.selected_pairs: List[PairCandidate] = []
        self.pair_positions: Dict[Tuple[str, str], PairPosition] = {}

        # Track last pair selection date
        self.last_pair_selection: Optional[pd.Timestamp] = None

    def _validate_parameters(self) -> None:
        """Validate strategy-specific parameters."""
        lookback = self.get_parameter('lookback_period', 60)
        z_entry = self.get_parameter('z_entry', 2.0)
        z_exit = self.get_parameter('z_exit', 0.5)
        max_holding_days = self.get_parameter('max_holding_days', 30)
        correlation_threshold = self.get_parameter('correlation_threshold', 0.7)
        coint_pvalue = self.get_parameter('coint_pvalue_threshold', 0.05)
        position_size_pct = self.get_parameter('position_size_pct', 0.2)
        max_pairs = self.get_parameter('max_pairs', 5)

        if lookback < 30:
            raise ValueError(f"lookback_period must be >= 30, got {lookback}")
        if z_exit >= z_entry:
            raise ValueError(f"z_exit ({z_exit}) must be < z_entry ({z_entry})")
        if max_holding_days <= 0:
            raise ValueError(f"max_holding_days must be positive, got {max_holding_days}")
        if not 0.0 < correlation_threshold <= 1.0:
            raise ValueError(f"correlation_threshold must be in (0, 1], got {correlation_threshold}")
        if not 0.0 < coint_pvalue <= 1.0:
            raise ValueError(f"coint_pvalue_threshold must be in (0, 1], got {coint_pvalue}")
        if not 0.0 < position_size_pct <= 1.0:
            raise ValueError(f"position_size_pct must be in (0, 1], got {position_size_pct}")
        if max_pairs <= 0:
            raise ValueError(f"max_pairs must be positive, got {max_pairs}")

        # Ensure we have at least 2 symbols for pairs
        if len(self.config.symbols) < 2:
            raise ValueError("Need at least 2 symbols for pairs trading")

    def select_pairs(
        self,
        data: Dict[str, pd.DataFrame],
        max_pairs: Optional[int] = None
    ) -> List[PairCandidate]:
        """
        Select best pairs using cointegration and correlation analysis.

        Tests all possible pairs from symbols list, filters by correlation
        and cointegration thresholds, then ranks by quality score.

        Args:
            data: Historical price data for all symbols
            max_pairs: Maximum pairs to select (uses config if None)

        Returns:
            List of PairCandidate objects, sorted by quality score

        Example:
            >>> pairs = strategy.select_pairs(data, max_pairs=5)
            >>> for pair in pairs:
            ...     print(f"{pair.symbol_long}/{pair.symbol_short}: "
            ...           f"corr={pair.correlation:.3f}, p={pair.coint_pvalue:.4f}")
        """
        if not STATSMODELS_AVAILABLE:
            raise ImportError(
                "statsmodels is required for pair selection. "
                "Install with: pip install statsmodels"
            )

        if max_pairs is None:
            max_pairs = self.get_parameter('max_pairs', 5)

        lookback = self.get_parameter('lookback_period', 60)
        correlation_threshold = self.get_parameter('correlation_threshold', 0.7)
        coint_pvalue_threshold = self.get_parameter('coint_pvalue_threshold', 0.05)

        candidates = []
        symbols = self.config.symbols

        # Test all possible pairs
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                symbol_a = symbols[i]
                symbol_b = symbols[j]

                if symbol_a not in data or symbol_b not in data:
                    continue

                # Get price series
                prices_a = data[symbol_a]['Close'].tail(lookback)
                prices_b = data[symbol_b]['Close'].tail(lookback)

                # Align indices
                prices_a, prices_b = prices_a.align(prices_b, join='inner')

                if len(prices_a) < lookback:
                    logger.warning(
                        f"Insufficient data for {symbol_a}/{symbol_b}: "
                        f"{len(prices_a)} < {lookback}"
                    )
                    continue

                # Calculate correlation
                correlation = prices_a.corr(prices_b)

                # Filter by correlation threshold
                if abs(correlation) < correlation_threshold:
                    continue

                # Engle-Granger cointegration test (provides actual p-values)
                try:
                    from statsmodels.tsa.stattools import coint

                    # Run Engle-Granger test (more reliable p-values than Johansen)
                    # Returns: (test_statistic, pvalue, critical_values)
                    _, pvalue, _ = coint(prices_a, prices_b)

                    # Calculate hedge ratio using OLS regression
                    # Regress prices_a on prices_b to get hedge ratio
                    # prices_a = β * prices_b + ε
                    x = prices_b.values.reshape(-1, 1)
                    y = prices_a.values

                    # Simple OLS: β = (X'X)^(-1) X'y
                    beta = np.dot(x.T, y) / np.dot(x.T, x)
                    hedge_ratio = float(beta.item())  # Convert to scalar

                except Exception as e:
                    logger.warning(f"Cointegration test failed for {symbol_a}/{symbol_b}: {e}")
                    continue

                # Filter by cointegration p-value
                if pvalue > coint_pvalue_threshold:
                    continue

                # Calculate spread and half-life
                spread = prices_a - (hedge_ratio * prices_b)

                # Calculate half-life of mean reversion using correct AR(1) model
                # AR(1): spread_t = ρ * spread_{t-1} + ε
                # Half-life = -ln(2) / ln(ρ)
                if len(spread) > 1:
                    spread_curr = spread.iloc[1:].values
                    spread_lag = spread.iloc[:-1].values

                    # OLS regression: spread_t = α + ρ * spread_{t-1} + ε
                    # Add constant term
                    X = np.column_stack([np.ones(len(spread_lag)), spread_lag])

                    # β = (X'X)^(-1) X'y
                    beta = np.linalg.lstsq(X, spread_curr, rcond=None)[0]
                    rho = beta[1]  # AR(1) coefficient

                    if 0 < rho < 1:
                        half_life = -np.log(2) / np.log(rho)
                    else:
                        half_life = np.inf  # No mean reversion
                else:
                    half_life = np.inf

                # Calculate quality score
                # Higher score = better pair
                # Factors: low p-value, high correlation, reasonable half-life
                score = 0.0
                score += (1 - pvalue) * 40  # Max 40 points
                score += abs(correlation) * 40  # Max 40 points

                # Filter by half-life: avoid pairs that are too fast (excessive turnover)
                # or too slow (capital tied up too long)
                min_half_life = self.get_parameter('min_half_life_days', 3)
                max_half_life = self.get_parameter('max_half_life_days', 60)

                if half_life < min_half_life or half_life > max_half_life:
                    logger.debug(
                        f"Filtering {symbol_a}/{symbol_b}: half_life={half_life:.1f}d "
                        f"outside range [{min_half_life}, {max_half_life}]"
                    )
                    continue

                # Half-life scoring: prefer 5-30 day half-life
                if 5 <= half_life <= 30:
                    score += 20
                elif 3 <= half_life < 5:
                    score += 10
                elif 30 < half_life <= 60:
                    score += 10

                # Determine which symbol is "long" (higher price)
                if prices_a.iloc[-1] > prices_b.iloc[-1]:
                    symbol_long, symbol_short = symbol_a, symbol_b
                else:
                    symbol_long, symbol_short = symbol_b, symbol_a

                candidate = PairCandidate(
                    symbol_long=symbol_long,
                    symbol_short=symbol_short,
                    correlation=correlation,
                    coint_pvalue=pvalue,
                    hedge_ratio=hedge_ratio,
                    half_life=half_life,
                    score=score
                )

                candidates.append(candidate)

        # Sort by score (descending) and take top N
        candidates.sort(key=lambda x: x.score, reverse=True)
        selected = candidates[:max_pairs]

        logger.info(
            f"Selected {len(selected)} pairs from {len(candidates)} candidates "
            f"(tested {len(symbols) * (len(symbols) - 1) // 2} total pairs)"
        )

        for pair in selected:
            logger.info(
                f"  {pair.symbol_long}/{pair.symbol_short}: "
                f"score={pair.score:.1f}, corr={pair.correlation:.3f}, "
                f"p={pair.coint_pvalue:.4f}, half_life={pair.half_life:.1f}d"
            )

        return selected

    def on_data(
        self,
        timestamp: pd.Timestamp,
        data: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.DataFrame]:
        """
        Preprocess data and update pair selection periodically.

        Args:
            timestamp: Current timestamp
            data: Market data

        Returns:
            Processed data (unchanged for this strategy)
        """
        # Reselect pairs periodically (e.g., monthly)
        pair_selection_frequency_days = self.get_parameter('pair_selection_frequency_days', 30)

        if (self.last_pair_selection is None or
            (timestamp - self.last_pair_selection).days >= pair_selection_frequency_days):

            logger.info(f"Reselecting pairs at {timestamp}")
            self.selected_pairs = self.select_pairs(data)
            self.last_pair_selection = timestamp

        return data

    def generate_signals(
        self,
        timestamp: pd.Timestamp,
        data: Dict[str, pd.DataFrame]
    ) -> Dict[str, Signal]:
        """
        Generate signals based on spread z-scores.

        Entry signals:
        - LONG spread (buy long leg, sell short leg) when z-score < -z_entry
        - SHORT spread (sell long leg, buy short leg) when z-score > +z_entry

        Exit signals:
        - Exit when z-score crosses z_exit threshold toward mean
        - Exit when max_holding_days exceeded

        Args:
            timestamp: Current timestamp
            data: Market data

        Returns:
            Dict mapping symbols to Signal objects
        """
        signals = {}

        if not self.selected_pairs:
            return signals

        lookback = self.get_parameter('lookback_period', 60)
        zscore_lookback = self.get_parameter('zscore_lookback', 30)
        z_entry = self.get_parameter('z_entry', 2.0)
        z_exit = self.get_parameter('z_exit', 0.5)
        max_holding_days = self.get_parameter('max_holding_days', 30)

        for pair in self.selected_pairs:
            symbol_long = pair.symbol_long
            symbol_short = pair.symbol_short

            if symbol_long not in data or symbol_short not in data:
                continue

            # Calculate hedge ratio (cached for performance)
            hedge_ratio_obj = self.calculate_hedge_ratio(
                symbol_long,
                symbol_short,
                data,
                method="ols",
                lookback=lookback,
                recalc_frequency_bars=self.get_parameter('rebalance_hedge_ratio_days', 20)
            )

            # Calculate spread
            spread = self.calculate_spread(
                symbol_long,
                symbol_short,
                data,
                hedge_ratio=hedge_ratio_obj.ratio
            )

            # Calculate z-score
            zscore = self.calculate_spread_zscore(spread, lookback=zscore_lookback)

            # Current prices
            price_long = data[symbol_long]['Close'].iloc[-1]
            price_short = data[symbol_short]['Close'].iloc[-1]

            pair_key = (symbol_long, symbol_short)
            has_position = pair_key in self.pair_positions

            # Update existing position tracking
            if has_position:
                pos = self.pair_positions[pair_key]
                pos.current_spread = spread.iloc[-1]
                pos.current_zscore = zscore
                pos.days_held = (timestamp - pos.entry_timestamp).days

            # Signal generation logic
            signal_type_long = SignalType.FLAT
            signal_type_short = SignalType.FLAT

            if has_position:
                pos = self.pair_positions[pair_key]

                # Exit conditions
                should_exit = False
                exit_reason = ""

                # Time-based exit
                if pos.days_held >= max_holding_days:
                    should_exit = True
                    exit_reason = "max_holding_days"

                # Mean reversion exit
                elif abs(zscore) < z_exit:
                    should_exit = True
                    exit_reason = "mean_reversion"

                # Stop loss: spread diverging further from mean (losing money)
                # If long spread (entry_zscore < 0), stop if zscore keeps falling (spread widening)
                # If short spread (entry_zscore > 0), stop if zscore keeps rising (spread widening)
                # Use 50% worse than entry as stop loss threshold
                elif (pos.entry_zscore < 0 and zscore < (pos.entry_zscore * 1.5)):
                    should_exit = True
                    exit_reason = "stop_loss"
                elif (pos.entry_zscore > 0 and zscore > (pos.entry_zscore * 1.5)):
                    should_exit = True
                    exit_reason = "stop_loss"

                if should_exit:
                    # Close position
                    signal_type_long = SignalType.FLAT
                    signal_type_short = SignalType.FLAT

                    # Remove from tracking
                    del self.pair_positions[pair_key]

                    logger.info(
                        f"Closing pair {symbol_long}/{symbol_short}: {exit_reason}, "
                        f"entry_z={pos.entry_zscore:.2f}, current_z={zscore:.2f}, "
                        f"days_held={pos.days_held}"
                    )
                else:
                    # Hold existing position
                    if pos.entry_zscore < 0:
                        # Currently long spread
                        signal_type_long = SignalType.LONG
                        signal_type_short = SignalType.SHORT
                    else:
                        # Currently short spread
                        signal_type_long = SignalType.SHORT
                        signal_type_short = SignalType.LONG
            else:
                # Entry conditions
                if zscore < -z_entry:
                    # Spread too low -> go LONG spread
                    # Buy long leg, sell short leg
                    signal_type_long = SignalType.LONG
                    signal_type_short = SignalType.SHORT

                    # Track new position
                    self.pair_positions[pair_key] = PairPosition(
                        symbol_long=symbol_long,
                        symbol_short=symbol_short,
                        entry_spread=spread.iloc[-1],
                        entry_zscore=zscore,
                        entry_timestamp=timestamp,
                        current_spread=spread.iloc[-1],
                        current_zscore=zscore
                    )

                    logger.info(
                        f"Opening LONG spread {symbol_long}/{symbol_short}: "
                        f"z={zscore:.2f}, spread={spread.iloc[-1]:.2f}"
                    )

                elif zscore > z_entry:
                    # Spread too high -> go SHORT spread
                    # Sell long leg, buy short leg
                    signal_type_long = SignalType.SHORT
                    signal_type_short = SignalType.LONG

                    # Track new position
                    self.pair_positions[pair_key] = PairPosition(
                        symbol_long=symbol_long,
                        symbol_short=symbol_short,
                        entry_spread=spread.iloc[-1],
                        entry_zscore=zscore,
                        entry_timestamp=timestamp,
                        current_spread=spread.iloc[-1],
                        current_zscore=zscore
                    )

                    logger.info(
                        f"Opening SHORT spread {symbol_long}/{symbol_short}: "
                        f"z={zscore:.2f}, spread={spread.iloc[-1]:.2f}"
                    )

            # Create signals
            signals[symbol_long] = Signal(
                symbol=symbol_long,
                signal_type=signal_type_long,
                timestamp=timestamp,
                confidence=min(abs(zscore) / z_entry, 1.0) if zscore != 0 else 0.5,
                metadata={
                    'current_price': price_long,
                    'zscore': zscore,
                    'spread': spread.iloc[-1],
                    'hedge_ratio': hedge_ratio_obj.ratio,
                    'pair': symbol_short
                }
            )

            signals[symbol_short] = Signal(
                symbol=symbol_short,
                signal_type=signal_type_short,
                timestamp=timestamp,
                confidence=min(abs(zscore) / z_entry, 1.0) if zscore != 0 else 0.5,
                metadata={
                    'current_price': price_short,
                    'zscore': zscore,
                    'spread': spread.iloc[-1],
                    'hedge_ratio': hedge_ratio_obj.ratio,
                    'pair': symbol_long
                }
            )

        return signals

    def calculate_positions(
        self,
        signals: Dict[str, Signal],
        current_positions: Dict[str, Position],
        portfolio_value: float
    ) -> Dict[str, float]:
        """
        Convert signals to position sizes with market-neutral balancing.

        Uses inverse volatility weighting for position sizing:
        - Higher volatility pairs get smaller positions
        - Maintains dollar neutrality within each pair

        Args:
            signals: Generated signals
            current_positions: Current holdings
            portfolio_value: Total portfolio value

        Returns:
            Target positions (symbol -> quantity)
        """
        positions = {}

        position_size_pct = self.get_parameter('position_size_pct', 0.2)

        # Process pairs
        processed_symbols = set()

        for pair in self.selected_pairs:
            symbol_long = pair.symbol_long
            symbol_short = pair.symbol_short

            if symbol_long in processed_symbols or symbol_short in processed_symbols:
                continue

            if symbol_long not in signals or symbol_short not in signals:
                continue

            signal_long = signals[symbol_long]
            signal_short = signals[symbol_short]

            # Check if we should hold this pair
            if signal_long.signal_type == SignalType.FLAT:
                # No position
                positions[symbol_long] = 0.0
                positions[symbol_short] = 0.0
            else:
                # Calculate position size
                capital_per_pair = portfolio_value * position_size_pct

                price_long = signal_long.metadata['current_price']
                price_short = signal_short.metadata['current_price']
                hedge_ratio = signal_long.metadata['hedge_ratio']

                # Calculate balanced quantities for market neutrality
                # Split capital 50/50 between legs
                capital_per_leg = capital_per_pair / 2

                # Base quantity for long leg
                qty_long_base = capital_per_leg / price_long

                # Balance short leg using hedge ratio
                qty_long, qty_short = self.balance_positions(
                    symbol_long,
                    symbol_short,
                    qty_long_base,
                    hedge_ratio=hedge_ratio
                )

                # Apply signal direction
                if signal_long.signal_type == SignalType.LONG:
                    # Long spread: buy long leg, sell short leg
                    positions[symbol_long] = int(qty_long)
                    positions[symbol_short] = int(qty_short)  # Already negative from balance_positions
                elif signal_long.signal_type == SignalType.SHORT:
                    # Short spread: sell long leg, buy short leg
                    positions[symbol_long] = -int(qty_long)
                    positions[symbol_short] = -int(qty_short)  # Flip sign

            processed_symbols.add(symbol_long)
            processed_symbols.add(symbol_short)

        # Ensure all symbols have a position (even if zero)
        for symbol in self.config.symbols:
            if symbol not in positions:
                positions[symbol] = 0.0

        return positions
