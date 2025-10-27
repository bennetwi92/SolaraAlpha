"""PCA-based factor model for statistical arbitrage.

Implements principal component analysis to extract common factors from stock returns,
then calculates residuals (idiosyncratic returns) for mean-reversion signals.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler


@dataclass
class FactorResult:
    """Result of factor model estimation."""

    n_factors: int
    explained_variance: float
    factor_loadings: pd.DataFrame  # Stocks × Factors
    factor_returns: pd.DataFrame  # Time × Factors
    residuals: pd.DataFrame  # Time × Stocks
    fitted_returns: pd.DataFrame  # Time × Stocks (predicted from factors)


class FactorModel:
    """PCA-based factor model for extracting common factors from stock returns.

    The model decomposes stock returns into:
    - Systematic component (explained by factors)
    - Idiosyncratic component (residuals for mean-reversion)

    Uses rolling estimation to adapt to changing market conditions.
    """

    def __init__(
        self,
        n_factors: int = 5,
        estimation_window: int = 60,
        min_explained_variance: float = 0.3,
    ):
        """Initialize factor model.

        Args:
            n_factors: Number of principal components to extract
            estimation_window: Lookback period for factor estimation (days)
            min_explained_variance: Minimum variance explained (0-1)
        """
        self.n_factors = n_factors
        self.estimation_window = estimation_window
        self.min_explained_variance = min_explained_variance

        self._pca: Optional[PCA] = None
        self._scaler: Optional[StandardScaler] = None
        self._factor_loadings: Optional[pd.DataFrame] = None

    def fit(self, returns: pd.DataFrame) -> FactorResult:
        """Estimate factor model on stock returns.

        Args:
            returns: DataFrame with returns (time × stocks)

        Returns:
            FactorResult with factor loadings, factor returns, and residuals

        Raises:
            ValueError: If insufficient data or variance explained too low
        """
        if len(returns) < self.estimation_window:
            raise ValueError(
                f"Insufficient data: need {self.estimation_window} periods, "
                f"got {len(returns)}"
            )

        if returns.isnull().any().any():
            raise ValueError("Returns contain missing values")

        # Use most recent data for estimation
        estimation_data = returns.iloc[-self.estimation_window :]

        # Standardize returns (mean=0, std=1)
        self._scaler = StandardScaler()
        returns_scaled = self._scaler.fit_transform(estimation_data)

        # Extract principal components
        self._pca = PCA(n_components=self.n_factors)
        factor_returns = self._pca.fit_transform(returns_scaled)

        # Check variance explained
        explained_variance = self._pca.explained_variance_ratio_.sum()
        if explained_variance < self.min_explained_variance:
            raise ValueError(
                f"Insufficient variance explained: {explained_variance:.2%} "
                f"< {self.min_explained_variance:.2%}"
            )

        # Factor loadings (stocks × factors)
        self._factor_loadings = pd.DataFrame(
            self._pca.components_.T,
            index=returns.columns,
            columns=[f"Factor{i+1}" for i in range(self.n_factors)],
        )

        # Factor returns (time × factors)
        factor_returns_df = pd.DataFrame(
            factor_returns,
            index=estimation_data.index,
            columns=[f"Factor{i+1}" for i in range(self.n_factors)],
        )

        # Fitted returns (predicted by factors)
        fitted = self._pca.inverse_transform(factor_returns)
        fitted = self._scaler.inverse_transform(fitted)
        fitted_returns = pd.DataFrame(
            fitted, index=estimation_data.index, columns=returns.columns
        )

        # Residuals (idiosyncratic returns)
        residuals = estimation_data - fitted_returns

        return FactorResult(
            n_factors=self.n_factors,
            explained_variance=explained_variance,
            factor_loadings=self._factor_loadings,
            factor_returns=factor_returns_df,
            residuals=residuals,
            fitted_returns=fitted_returns,
        )

    def transform(self, returns: pd.DataFrame) -> pd.DataFrame:
        """Calculate residuals for new returns using fitted model.

        Args:
            returns: DataFrame with returns (time × stocks)

        Returns:
            DataFrame with residuals (idiosyncratic returns)

        Raises:
            ValueError: If model not fitted or columns mismatch
        """
        if self._pca is None or self._scaler is None:
            raise ValueError("Model not fitted. Call fit() first.")

        if not set(returns.columns).issubset(set(self._factor_loadings.index)):
            raise ValueError("Returns columns do not match fitted model")

        # Ensure column order matches
        returns_aligned = returns[self._factor_loadings.index]

        # Standardize
        returns_scaled = self._scaler.transform(returns_aligned)

        # Project onto factors
        factor_returns = self._pca.transform(returns_scaled)

        # Reconstruct fitted returns
        fitted = self._pca.inverse_transform(factor_returns)
        fitted = self._scaler.inverse_transform(fitted)
        fitted_returns = pd.DataFrame(
            fitted, index=returns_aligned.index, columns=returns_aligned.columns
        )

        # Calculate residuals
        residuals = returns_aligned - fitted_returns

        return residuals

    def fit_transform(self, returns: pd.DataFrame) -> FactorResult:
        """Fit model and return full results.

        Convenience method that combines fit() and full result extraction.

        Args:
            returns: DataFrame with returns (time × stocks)

        Returns:
            FactorResult with all model outputs
        """
        return self.fit(returns)

    def get_factor_loadings(self) -> pd.DataFrame:
        """Get current factor loadings (betas).

        Returns:
            DataFrame with factor loadings (stocks × factors)

        Raises:
            ValueError: If model not fitted
        """
        if self._factor_loadings is None:
            raise ValueError("Model not fitted. Call fit() first.")

        return self._factor_loadings.copy()

    def get_explained_variance_ratio(self) -> np.ndarray:
        """Get variance explained by each factor.

        Returns:
            Array with variance ratios for each factor

        Raises:
            ValueError: If model not fitted
        """
        if self._pca is None:
            raise ValueError("Model not fitted. Call fit() first.")

        return self._pca.explained_variance_ratio_


def calculate_rolling_residuals(
    returns: pd.DataFrame,
    estimation_window: int = 60,
    n_factors: int = 5,
) -> pd.DataFrame:
    """Calculate rolling residuals using walk-forward factor estimation.

    This function re-estimates the factor model at each time step using a rolling
    window, mimicking real-world conditions where future data is unavailable.

    Args:
        returns: DataFrame with returns (time × stocks)
        estimation_window: Lookback period for each estimation (days)
        n_factors: Number of factors to extract

    Returns:
        DataFrame with rolling residuals (time × stocks)
        First `estimation_window` rows will be NaN (no out-of-sample data)
    """
    if len(returns) <= estimation_window:
        raise ValueError(
            f"Need more data than estimation window: "
            f"{len(returns)} <= {estimation_window}"
        )

    residuals_list = []

    for i in range(estimation_window, len(returns)):
        # Estimation window: [i - estimation_window : i]
        train_data = returns.iloc[i - estimation_window : i]

        # Test point: i
        test_data = returns.iloc[[i]]

        # Fit model on training window
        model = FactorModel(n_factors=n_factors, estimation_window=estimation_window)
        try:
            model.fit(train_data)
            # Calculate residual for test point
            residual = model.transform(test_data)
            residuals_list.append(residual)
        except ValueError:
            # If model fails (e.g., low variance), use NaN
            residuals_list.append(
                pd.DataFrame(
                    np.nan, index=test_data.index, columns=test_data.columns
                )
            )

    # Concatenate all residuals
    all_residuals = pd.concat(residuals_list)

    # Prepend NaN for estimation window
    nan_df = pd.DataFrame(
        np.nan,
        index=returns.iloc[:estimation_window].index,
        columns=returns.columns,
    )
    result = pd.concat([nan_df, all_residuals])

    return result
