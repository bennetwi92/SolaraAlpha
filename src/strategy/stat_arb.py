"""
Statistical Arbitrage Strategy

Market-neutral strategy identifying temporary mispricings in baskets of related
securities using factor models and mean reversion. Exploits factor-adjusted
residuals (alpha) while maintaining market neutrality.

Strategy Logic:
1. Universe: Sector ETFs or correlated stocks within sectors
2. Factor Model: PCA-based factor decomposition or sector index regression
3. Residual Calculation: Returns minus factor exposure (alpha signals)
4. Signal Generation: Z-score of residuals for mean reversion
5. Portfolio Construction: Dollar-neutral or beta-neutral positions
6. Risk Controls: Position limits, sector concentration, correlation breakdown detection

References:
- Avellaneda & Lee (2010) "Statistical Arbitrage in the U.S. Equities Market"
- Pole (2007) "Statistical Arbitrage: Algorithmic Trading Insights and Techniques"
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
import logging
from sklearn.decomposition import PCA

from src.strategy.base import (
    RelativeValueStrategy,
    StrategyConfig,
    Signal,
    SignalType,
    Position
)

logger = logging.getLogger(__name__)


@dataclass
class FactorModel:
    """
    Factor model state for PCA-based decomposition.

    Attributes:
        n_components: Number of principal components
        explained_variance_ratio: Variance explained by each component
        loadings: Factor loadings (shape: n_securities x n_factors)
        mean_returns: Mean returns for centering
        last_updated: Last model update timestamp
    """
    n_components: int
    explained_variance_ratio: np.ndarray
    loadings: np.ndarray
    mean_returns: np.ndarray
    last_updated: pd.Timestamp


@dataclass
class SecurityState:
    """
    Per-security tracking for residuals and signals.

    Attributes:
        symbol: Ticker symbol
        current_residual: Current factor-adjusted residual
        residual_history: Rolling window of residuals
        zscore: Current residual z-score
        volatility: Residual volatility (for position sizing)
        last_signal_type: Last signal generated
    """
    symbol: str
    current_residual: float = 0.0
    residual_history: List[float] = field(default_factory=list)
    zscore: float = 0.0
    volatility: float = 0.0
    last_signal_type: SignalType = SignalType.FLAT


class StatArbStrategy(RelativeValueStrategy):
    """
    Statistical arbitrage strategy using PCA factor model.

    This strategy:
    - Decomposes returns into systematic factors (PCA) and residuals
    - Generates mean reversion signals on factor-adjusted residuals
    - Maintains market-neutral (dollar or beta) portfolio
    - Implements risk controls for position limits and correlation breakdown

    Parameters:
        factor_model_type (str): Factor model type ('pca' or 'sector_index') (default: 'pca')
        n_factors (int): Number of factors for PCA (default: 3)
        lookback_period (int): Lookback for factor model estimation (default: 60)
        residual_lookback (int): Lookback for residual z-score (default: 30)
        z_entry (float): Z-score threshold for entry (default: 2.0)
        z_exit (float): Z-score threshold for exit (default: 0.5)
        max_holding_days (int): Maximum holding period (default: 30)
        max_positions (int): Maximum concurrent positions (default: 10)
        position_size_pct (float): % of portfolio per position (default: 0.1)
        neutrality_type (str): 'dollar' or 'beta' (default: 'dollar')
        max_sector_exposure (float): Max sector exposure as % of portfolio (default: 0.3)
        min_correlation (float): Min average correlation (breakdown detection) (default: 0.3)
        rebalance_frequency_days (int): Days between factor model updates (default: 5)
        transaction_cost_pct (float): Round-trip transaction cost (default: 0.001)
        min_variance_explained (float): Min total variance explained by factors (default: 0.7)

    Example:
        >>> config = StrategyConfig(
        ...     name="StatArb_SectorETFs",
        ...     version="1.0.0",
        ...     parameters={
        ...         'factor_model_type': 'pca',
        ...         'n_factors': 3,
        ...         'lookback_period': 60,
        ...         'z_entry': 2.0,
        ...         'z_exit': 0.5,
        ...         'max_positions': 10
        ...     },
        ...     symbols=['XLF', 'XLE', 'XLK', 'XLV', 'XLI', 'XLP', 'XLY', 'XLU', 'XLB'],
        ...     enable_short=True
        ... )
        >>> strategy = StatArbStrategy(config)
    """

    def __init__(self, config: StrategyConfig):
        """
        Initialize statistical arbitrage strategy.

        Args:
            config: Strategy configuration
        """
        super().__init__(config)

        # Validate parameters
        self._validate_parameters()

        # Factor model state
        self.factor_model: Optional[FactorModel] = None
        self.last_model_update: Optional[pd.Timestamp] = None

        # Security state tracking
        self.security_states: Dict[str, SecurityState] = {}

        # Initialize security states
        for symbol in self.config.symbols:
            self.security_states[symbol] = SecurityState(symbol=symbol)

    def _validate_parameters(self) -> None:
        """Validate strategy-specific parameters."""
        # Check minimum symbols first
        if len(self.config.symbols) < 3:
            raise ValueError("Need at least 3 symbols for statistical arbitrage")

        factor_model_type = self.get_parameter('factor_model_type', 'pca')
        n_factors = self.get_parameter('n_factors', 3)
        lookback_period = self.get_parameter('lookback_period', 60)
        residual_lookback = self.get_parameter('residual_lookback', 30)
        z_entry = self.get_parameter('z_entry', 2.0)
        z_exit = self.get_parameter('z_exit', 0.5)
        max_holding_days = self.get_parameter('max_holding_days', 30)
        max_positions = self.get_parameter('max_positions', 10)
        position_size_pct = self.get_parameter('position_size_pct', 0.1)
        neutrality_type = self.get_parameter('neutrality_type', 'dollar')
        max_sector_exposure = self.get_parameter('max_sector_exposure', 0.3)
        min_correlation = self.get_parameter('min_correlation', 0.3)
        min_variance_explained = self.get_parameter('min_variance_explained', 0.7)

        if factor_model_type not in ['pca', 'sector_index']:
            raise ValueError(f"factor_model_type must be 'pca' or 'sector_index', got {factor_model_type}")

        if n_factors <= 0 or n_factors >= len(self.config.symbols):
            raise ValueError(
                f"n_factors must be between 1 and {len(self.config.symbols) - 1}, got {n_factors}"
            )

        if lookback_period < 30:
            raise ValueError(f"lookback_period must be >= 30, got {lookback_period}")

        if residual_lookback < 10:
            raise ValueError(f"residual_lookback must be >= 10, got {residual_lookback}")

        if z_exit >= z_entry:
            raise ValueError(f"z_exit ({z_exit}) must be < z_entry ({z_entry})")

        if max_holding_days <= 0:
            raise ValueError(f"max_holding_days must be positive, got {max_holding_days}")

        if max_positions <= 0:
            raise ValueError(f"max_positions must be positive, got {max_positions}")

        if not 0.0 < position_size_pct <= 1.0:
            raise ValueError(f"position_size_pct must be in (0, 1], got {position_size_pct}")

        if neutrality_type not in ['dollar', 'beta']:
            raise ValueError(f"neutrality_type must be 'dollar' or 'beta', got {neutrality_type}")

        if not 0.0 < max_sector_exposure <= 1.0:
            raise ValueError(f"max_sector_exposure must be in (0, 1], got {max_sector_exposure}")

        if not 0.0 <= min_correlation <= 1.0:
            raise ValueError(f"min_correlation must be in [0, 1], got {min_correlation}")

        if not 0.0 < min_variance_explained <= 1.0:
            raise ValueError(f"min_variance_explained must be in (0, 1], got {min_variance_explained}")

    def _estimate_factor_model(
        self,
        data: Dict[str, pd.DataFrame],
        timestamp: pd.Timestamp
    ) -> FactorModel:
        """
        Estimate PCA factor model from returns data.

        Args:
            data: Historical price data
            timestamp: Current timestamp

        Returns:
            FactorModel object with PCA decomposition

        Raises:
            ValueError: If insufficient data or poor model quality
        """
        lookback_period = self.get_parameter('lookback_period', 60)
        n_factors = self.get_parameter('n_factors', 3)
        min_variance_explained = self.get_parameter('min_variance_explained', 0.7)

        # Collect returns for all symbols
        returns_dict = {}

        for symbol in self.config.symbols:
            if symbol not in data:
                raise ValueError(f"Missing data for {symbol}")

            df = data[symbol]

            if len(df) < lookback_period + 1:
                raise ValueError(
                    f"Insufficient data for {symbol}: {len(df)} < {lookback_period + 1}"
                )

            # Calculate returns
            prices = df['Close'].tail(lookback_period + 1)
            returns = prices.pct_change().dropna()

            returns_dict[symbol] = returns

        # Align all returns to common index
        returns_df = pd.DataFrame(returns_dict)
        returns_df = returns_df.dropna()

        if len(returns_df) < lookback_period:
            raise ValueError(
                f"Insufficient aligned returns: {len(returns_df)} < {lookback_period}"
            )

        # Center returns (mean = 0)
        mean_returns = returns_df.mean().values
        centered_returns = returns_df - returns_df.mean()

        # Fit PCA model
        pca = PCA(n_components=n_factors)
        pca.fit(centered_returns)

        # Check variance explained
        total_variance_explained = pca.explained_variance_ratio_.sum()

        if total_variance_explained < min_variance_explained:
            logger.warning(
                f"Factor model explains only {total_variance_explained:.1%} of variance "
                f"(threshold: {min_variance_explained:.1%})"
            )

        logger.info(
            f"Factor model estimated: {n_factors} factors explain "
            f"{total_variance_explained:.1%} of variance"
        )

        for i, var_ratio in enumerate(pca.explained_variance_ratio_):
            logger.info(f"  Factor {i + 1}: {var_ratio:.1%}")

        factor_model = FactorModel(
            n_components=n_factors,
            explained_variance_ratio=pca.explained_variance_ratio_,
            loadings=pca.components_.T,  # Transpose to get (n_securities x n_factors)
            mean_returns=mean_returns,
            last_updated=timestamp
        )

        return factor_model

    def _calculate_residuals(
        self,
        data: Dict[str, pd.DataFrame],
        factor_model: FactorModel
    ) -> Dict[str, float]:
        """
        Calculate factor-adjusted residuals for all securities.

        Residual = Actual Return - Factor Model Predicted Return

        Args:
            data: Current price data
            factor_model: Fitted factor model

        Returns:
            Dict mapping symbols to current residuals
        """
        residuals = {}

        # Get current returns
        current_returns = {}

        for symbol in self.config.symbols:
            if symbol not in data:
                continue

            df = data[symbol]

            if len(df) < 2:
                continue

            # Calculate current return
            current_price = df['Close'].iloc[-1]
            previous_price = df['Close'].iloc[-2]
            current_return = (current_price - previous_price) / previous_price

            current_returns[symbol] = current_return

        # Align symbols with factor model
        symbols_aligned = self.config.symbols
        returns_array = np.array([current_returns.get(s, 0.0) for s in symbols_aligned])

        # Center returns
        centered_returns = returns_array - factor_model.mean_returns

        # Project onto factor space and reconstruct
        # Factor exposures: beta = loadings^T * centered_returns
        factor_exposures = np.dot(factor_model.loadings.T, centered_returns)

        # Predicted returns: predicted = loadings * factor_exposures
        predicted_returns = np.dot(factor_model.loadings, factor_exposures)

        # Residuals: actual - predicted
        residuals_array = centered_returns - predicted_returns

        # Map back to symbols
        for i, symbol in enumerate(symbols_aligned):
            residuals[symbol] = float(residuals_array[i])

        return residuals

    def _update_security_states(
        self,
        residuals: Dict[str, float],
        timestamp: pd.Timestamp
    ) -> None:
        """
        Update security states with new residuals and calculate z-scores.

        Args:
            residuals: Current residuals for all securities
            timestamp: Current timestamp
        """
        residual_lookback = self.get_parameter('residual_lookback', 30)

        for symbol, residual in residuals.items():
            if symbol not in self.security_states:
                self.security_states[symbol] = SecurityState(symbol=symbol)

            state = self.security_states[symbol]

            # Update residual
            state.current_residual = residual

            # Add to history
            state.residual_history.append(residual)

            # Trim history to lookback window
            if len(state.residual_history) > residual_lookback:
                state.residual_history = state.residual_history[-residual_lookback:]

            # Calculate z-score if we have enough history
            if len(state.residual_history) >= residual_lookback:
                mean = np.mean(state.residual_history)
                std = np.std(state.residual_history)

                if std > 0:
                    state.zscore = (residual - mean) / std
                    state.volatility = std
                else:
                    state.zscore = 0.0
                    state.volatility = 0.0
            else:
                state.zscore = 0.0
                state.volatility = 0.0

    def _check_correlation_breakdown(
        self,
        data: Dict[str, pd.DataFrame]
    ) -> bool:
        """
        Check for correlation breakdown (risk control).

        Returns True if average correlation drops below threshold.

        Args:
            data: Historical price data

        Returns:
            True if correlation breakdown detected
        """
        min_correlation = self.get_parameter('min_correlation', 0.3)
        lookback_period = self.get_parameter('lookback_period', 60)

        # Collect recent returns
        returns_dict = {}

        for symbol in self.config.symbols:
            if symbol not in data:
                continue

            df = data[symbol]

            if len(df) < lookback_period + 1:
                continue

            prices = df['Close'].tail(lookback_period + 1)
            returns = prices.pct_change().dropna()

            returns_dict[symbol] = returns

        if len(returns_dict) < 2:
            return True  # Not enough data to assess

        # Calculate correlation matrix
        returns_df = pd.DataFrame(returns_dict)
        returns_df = returns_df.dropna()

        if len(returns_df) < 20:
            return True  # Not enough data

        corr_matrix = returns_df.corr()

        # Calculate average correlation (excluding diagonal)
        n = len(corr_matrix)
        avg_correlation = (corr_matrix.values.sum() - n) / (n * (n - 1))

        if avg_correlation < min_correlation:
            logger.warning(
                f"Correlation breakdown detected: avg_corr={avg_correlation:.3f} < "
                f"threshold={min_correlation:.3f}"
            )
            return True

        return False

    def on_data(
        self,
        timestamp: pd.Timestamp,
        data: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.DataFrame]:
        """
        Update factor model and calculate residuals.

        Args:
            timestamp: Current timestamp
            data: Market data

        Returns:
            Processed data (unchanged)
        """
        rebalance_frequency_days = self.get_parameter('rebalance_frequency_days', 5)

        # Update factor model periodically
        should_update = (
            self.last_model_update is None or
            (timestamp - self.last_model_update).days >= rebalance_frequency_days
        )

        if should_update:
            try:
                logger.info(f"Updating factor model at {timestamp}")
                self.factor_model = self._estimate_factor_model(data, timestamp)
                self.last_model_update = timestamp
            except Exception as e:
                logger.error(f"Failed to update factor model: {e}")
                # Continue with old model if available

        # Calculate residuals if we have a factor model
        if self.factor_model is not None:
            try:
                residuals = self._calculate_residuals(data, self.factor_model)
                self._update_security_states(residuals, timestamp)
            except Exception as e:
                logger.error(f"Failed to calculate residuals: {e}")

        return data

    def generate_signals(
        self,
        timestamp: pd.Timestamp,
        data: Dict[str, pd.DataFrame]
    ) -> Dict[str, Signal]:
        """
        Generate trading signals from residual z-scores.

        Entry signals:
        - LONG when residual z-score < -z_entry (oversold)
        - SHORT when residual z-score > +z_entry (overbought)

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

        # Check for factor model
        if self.factor_model is None:
            logger.warning("No factor model available, skipping signal generation")
            return signals

        # Check for correlation breakdown
        if self._check_correlation_breakdown(data):
            logger.warning("Correlation breakdown detected, no new signals")
            # Exit all positions
            for symbol in self.config.symbols:
                signals[symbol] = Signal(
                    symbol=symbol,
                    signal_type=SignalType.FLAT,
                    timestamp=timestamp,
                    confidence=0.0,
                    metadata={'reason': 'correlation_breakdown'}
                )
            return signals

        z_entry = self.get_parameter('z_entry', 2.0)
        z_exit = self.get_parameter('z_exit', 0.5)
        max_holding_days = self.get_parameter('max_holding_days', 30)

        for symbol in self.config.symbols:
            if symbol not in data or symbol not in self.security_states:
                continue

            state = self.security_states[symbol]

            # Need sufficient history for z-score
            residual_lookback = self.get_parameter('residual_lookback', 30)
            if len(state.residual_history) < residual_lookback:
                continue

            # Get current price
            current_price = data[symbol]['Close'].iloc[-1]

            # Check if we have a position
            has_position = self.has_position(symbol)

            signal_type = SignalType.FLAT
            confidence = 0.0
            metadata = {
                'current_price': current_price,
                'zscore': state.zscore,
                'residual': state.current_residual,
                'volatility': state.volatility
            }

            if has_position:
                pos = self.get_position(symbol)
                days_held = (timestamp - pos.entry_time).days

                # Exit conditions
                should_exit = False
                exit_reason = ""

                # Time-based exit
                if days_held >= max_holding_days:
                    should_exit = True
                    exit_reason = "max_holding_days"

                # Mean reversion exit
                elif abs(state.zscore) < z_exit:
                    should_exit = True
                    exit_reason = "mean_reversion"

                # Stop loss: z-score moving against us (50% worse than entry)
                entry_metadata = pos.metadata.get('entry_zscore', 0.0)
                if pos.quantity > 0:  # Long position (entered on negative z-score)
                    if state.zscore < (entry_metadata * 1.5):
                        should_exit = True
                        exit_reason = "stop_loss"
                elif pos.quantity < 0:  # Short position (entered on positive z-score)
                    if state.zscore > (entry_metadata * 1.5):
                        should_exit = True
                        exit_reason = "stop_loss"

                if should_exit:
                    signal_type = SignalType.FLAT
                    metadata['exit_reason'] = exit_reason
                    logger.info(
                        f"Closing {symbol}: {exit_reason}, z={state.zscore:.2f}, days={days_held}"
                    )
                else:
                    # Hold position
                    signal_type = state.last_signal_type
                    confidence = min(abs(state.zscore) / z_entry, 1.0)
            else:
                # Entry conditions
                if state.zscore < -z_entry:
                    # Oversold -> go LONG
                    signal_type = SignalType.LONG
                    confidence = min(abs(state.zscore) / z_entry, 1.0)
                    metadata['entry_zscore'] = state.zscore

                    logger.info(
                        f"Opening LONG {symbol}: z={state.zscore:.2f}, "
                        f"residual={state.current_residual:.4f}"
                    )

                elif state.zscore > z_entry:
                    # Overbought -> go SHORT
                    signal_type = SignalType.SHORT
                    confidence = min(abs(state.zscore) / z_entry, 1.0)
                    metadata['entry_zscore'] = state.zscore

                    logger.info(
                        f"Opening SHORT {symbol}: z={state.zscore:.2f}, "
                        f"residual={state.current_residual:.4f}"
                    )

            # Update last signal type
            state.last_signal_type = signal_type

            signals[symbol] = Signal(
                symbol=symbol,
                signal_type=signal_type,
                timestamp=timestamp,
                confidence=confidence,
                metadata=metadata
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

        Uses inverse volatility weighting:
        - Higher volatility securities get smaller positions
        - Maintains dollar neutrality (sum of long = sum of short)
        - Respects max_positions and max_sector_exposure constraints

        Args:
            signals: Generated signals
            current_positions: Current holdings
            portfolio_value: Total portfolio value

        Returns:
            Target positions (symbol -> quantity)
        """
        positions = {}

        max_positions = self.get_parameter('max_positions', 10)
        position_size_pct = self.get_parameter('position_size_pct', 0.1)
        neutrality_type = self.get_parameter('neutrality_type', 'dollar')

        # Separate long and short signals
        long_signals = []
        short_signals = []

        for symbol, signal in signals.items():
            if signal.signal_type == SignalType.LONG:
                long_signals.append(signal)
            elif signal.signal_type == SignalType.SHORT:
                short_signals.append(signal)

        # Limit to max_positions (take highest confidence)
        long_signals.sort(key=lambda s: s.confidence, reverse=True)
        short_signals.sort(key=lambda s: s.confidence, reverse=True)

        max_per_side = max_positions // 2
        long_signals = long_signals[:max_per_side]
        short_signals = short_signals[:max_per_side]

        # Calculate total capital for long and short sides
        capital_per_side = portfolio_value / 2

        # Calculate inverse volatility weights for position sizing
        def calculate_positions_side(signals_list: List[Signal], side_capital: float) -> Dict[str, float]:
            if not signals_list:
                return {}

            # Get volatilities
            volatilities = {}
            for signal in signals_list:
                symbol = signal.symbol
                if symbol in self.security_states:
                    vol = self.security_states[symbol].volatility
                    # Avoid division by zero
                    volatilities[symbol] = max(vol, 1e-6)
                else:
                    volatilities[symbol] = 1.0

            # Inverse volatility weights
            inv_vols = {s: 1.0 / v for s, v in volatilities.items()}
            total_inv_vol = sum(inv_vols.values())

            weights = {s: iv / total_inv_vol for s, iv in inv_vols.items()}

            # Calculate quantities
            pos = {}
            for signal in signals_list:
                symbol = signal.symbol
                price = signal.metadata['current_price']

                # Capital allocated to this position
                capital = side_capital * weights[symbol]

                # Quantity
                qty = int(capital / price)

                if qty > 0:
                    pos[symbol] = qty

            return pos

        # Calculate long positions
        long_positions = calculate_positions_side(long_signals, capital_per_side)

        # Calculate short positions
        short_positions = calculate_positions_side(short_signals, capital_per_side)

        # Apply short sign
        short_positions = {s: -q for s, q in short_positions.items()}

        # Combine
        positions.update(long_positions)
        positions.update(short_positions)

        # Ensure all symbols have a position (even if zero)
        for symbol in self.config.symbols:
            if symbol not in positions:
                positions[symbol] = 0.0

        # Enforce dollar neutrality (if enabled)
        if neutrality_type == 'dollar':
            # Calculate total dollar exposure
            long_value = sum(
                abs(qty) * signals[s].metadata['current_price']
                for s, qty in positions.items()
                if qty > 0 and s in signals
            )

            short_value = sum(
                abs(qty) * signals[s].metadata['current_price']
                for s, qty in positions.items()
                if qty < 0 and s in signals
            )

            # Adjust to make them equal
            if long_value > 0 and short_value > 0:
                imbalance_ratio = min(long_value, short_value) / max(long_value, short_value)

                if long_value > short_value:
                    # Scale down long positions
                    for symbol in positions:
                        if positions[symbol] > 0:
                            positions[symbol] = int(positions[symbol] * imbalance_ratio)
                else:
                    # Scale down short positions
                    for symbol in positions:
                        if positions[symbol] < 0:
                            positions[symbol] = int(positions[symbol] * imbalance_ratio)

        return positions
