"""Cointegration testing for pairs trading.

Statistical tests to identify and validate cointegrated pairs suitable
for mean-reversion strategies. Implements both Engle-Granger (2-step) and
Johansen multivariate methods.
"""
import logging
from dataclasses import dataclass
from typing import Tuple

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller, coint
from statsmodels.tsa.vector_ar.vecm import coint_johansen

logger = logging.getLogger(__name__)


@dataclass
class CointegrationResult:
    """Result of cointegration test.

    Attributes:
        is_cointegrated: Whether pair passes cointegration test.
        p_value: Test p-value (lower = stronger cointegration).
        test_statistic: Test statistic value.
        critical_values: Critical values at different significance levels.
        hedge_ratio: Optimal hedge ratio for spread construction.
        method: Test method used ('engle_granger' or 'johansen').
    """

    is_cointegrated: bool
    p_value: float
    test_statistic: float
    critical_values: dict
    hedge_ratio: float
    method: str


class CointegrationTester:
    """Statistical cointegration testing for pairs trading.

    Implements multiple methods to validate mean-reversion relationship
    between asset pairs. Primary method is Engle-Granger for pairwise
    testing, with Johansen available for multivariate analysis.

    Example:
        >>> tester = CointegrationTester()
        >>> result = tester.test_pair(prices_a, prices_b)
        >>> if result.is_cointegrated:
        ...     spread = prices_a - result.hedge_ratio * prices_b
    """

    def __init__(self, significance_level: float = 0.05) -> None:
        """Initialize cointegration tester.

        Args:
            significance_level: P-value threshold for cointegration (default 0.05).
        """
        self.significance_level = significance_level

    def test_pair(
        self,
        series1: pd.Series,
        series2: pd.Series,
        method: str = "engle_granger",
    ) -> CointegrationResult:
        """Test cointegration between two price series.

        Args:
            series1: First price series (aligned index).
            series2: Second price series (aligned index).
            method: Test method ('engle_granger' or 'johansen').

        Returns:
            CointegrationResult with test statistics and hedge ratio.

        Raises:
            ValueError: If series lengths don't match or method invalid.
        """
        if len(series1) != len(series2):
            raise ValueError(
                f"Series length mismatch: {len(series1)} vs {len(series2)}"
            )

        if method == "engle_granger":
            return self._engle_granger_test(series1, series2)
        elif method == "johansen":
            return self._johansen_test(series1, series2)
        else:
            raise ValueError(
                f"Invalid method: {method}. Use 'engle_granger' or 'johansen'"
            )

    def _engle_granger_test(
        self, series1: pd.Series, series2: pd.Series
    ) -> CointegrationResult:
        """Engle-Granger two-step cointegration test.

        1. Regress series1 on series2 to get hedge ratio (beta)
        2. Test residuals for stationarity using ADF test

        Args:
            series1: Dependent variable price series.
            series2: Independent variable price series.

        Returns:
            CointegrationResult with hedge ratio and p-value.
        """
        # Step 1: Cointegrating regression to get hedge ratio
        # Uses statsmodels' built-in coint() which does both steps
        test_stat, p_value, crit_values = coint(series1, series2)

        # Calculate hedge ratio via OLS
        # series1 = alpha + beta * series2 + residual
        X = np.vstack([np.ones(len(series2)), series2.values]).T
        beta = np.linalg.lstsq(X, series1.values, rcond=None)[0]
        hedge_ratio = beta[1]

        # Format critical values
        critical_values = {
            "1%": crit_values[0],
            "5%": crit_values[1],
            "10%": crit_values[2],
        }

        is_cointegrated = p_value < self.significance_level

        logger.info(
            f"Engle-Granger test | "
            f"p-value: {p_value:.4f}, "
            f"hedge_ratio: {hedge_ratio:.4f}, "
            f"cointegrated: {is_cointegrated}"
        )

        return CointegrationResult(
            is_cointegrated=is_cointegrated,
            p_value=p_value,
            test_statistic=test_stat,
            critical_values=critical_values,
            hedge_ratio=hedge_ratio,
            method="engle_granger",
        )

    def _johansen_test(
        self, series1: pd.Series, series2: pd.Series
    ) -> CointegrationResult:
        """Johansen multivariate cointegration test.

        Tests for cointegration using maximum likelihood approach.
        More powerful than Engle-Granger for multiple series, though
        we apply it here to pair for comparison.

        Args:
            series1: First price series.
            series2: Second price series.

        Returns:
            CointegrationResult from trace statistic test.
        """
        # Combine series into matrix
        data = pd.DataFrame({"series1": series1, "series2": series2})

        # Johansen test with constant trend
        # det_order: -1 (no deterministic trend), 0 (constant), 1 (linear trend)
        result = coint_johansen(data, det_order=0, k_ar_diff=1)

        # Use trace statistic (test for at least 1 cointegrating vector)
        trace_stat = result.lr1[0]  # Test for r=0 vs r>=1
        critical_value_90 = result.cvt[0, 0]
        critical_value_95 = result.cvt[0, 1]
        critical_value_99 = result.cvt[0, 2]

        # Check if trace stat exceeds 95% critical value
        is_cointegrated = trace_stat > critical_value_95

        # Extract hedge ratio from cointegrating vector
        # eigenvector represents [1, -beta] for spread = series1 - beta * series2
        coint_vector = result.evec[:, 0]
        hedge_ratio = -coint_vector[1] / coint_vector[0]

        # Approximate p-value based on critical values
        # (Johansen doesn't provide exact p-value)
        if trace_stat > critical_value_99:
            p_value_approx = 0.01
        elif trace_stat > critical_value_95:
            p_value_approx = 0.03
        elif trace_stat > critical_value_90:
            p_value_approx = 0.07
        else:
            p_value_approx = 0.15

        critical_values = {
            "1%": critical_value_99,
            "5%": critical_value_95,
            "10%": critical_value_90,
        }

        logger.info(
            f"Johansen test | "
            f"trace_stat: {trace_stat:.4f}, "
            f"hedge_ratio: {hedge_ratio:.4f}, "
            f"cointegrated: {is_cointegrated}"
        )

        return CointegrationResult(
            is_cointegrated=is_cointegrated,
            p_value=p_value_approx,
            test_statistic=trace_stat,
            critical_values=critical_values,
            hedge_ratio=hedge_ratio,
            method="johansen",
        )

    def test_spread_stationarity(
        self, spread: pd.Series, max_lags: int = 1
    ) -> Tuple[bool, float]:
        """Test if spread is stationary using Augmented Dickey-Fuller test.

        Validates that constructed spread exhibits mean-reversion properties.
        Critical for ensuring pairs trading viability.

        Args:
            spread: Price spread series (series1 - hedge_ratio * series2).
            max_lags: Maximum lags for ADF test (default 1 for daily data).

        Returns:
            Tuple of (is_stationary, p_value).
        """
        # ADF test with constant trend (no time trend)
        adf_result = adfuller(spread, maxlag=max_lags, regression="c")

        adf_stat = adf_result[0]
        p_value = adf_result[1]
        critical_values = adf_result[4]

        is_stationary = p_value < self.significance_level

        logger.info(
            f"ADF stationarity test | "
            f"ADF stat: {adf_stat:.4f}, "
            f"p-value: {p_value:.4f}, "
            f"stationary: {is_stationary}"
        )

        return is_stationary, p_value

    def rolling_cointegration(
        self,
        series1: pd.Series,
        series2: pd.Series,
        window: int = 60,
        step: int = 20,
    ) -> pd.DataFrame:
        """Perform rolling window cointegration test.

        Tests parameter stability over time by computing cointegration
        and hedge ratio on successive windows. Helps detect regime changes.

        Args:
            series1: First price series.
            series2: Second price series.
            window: Window size in periods (default 60 days).
            step: Step size between windows (default 20 days).

        Returns:
            DataFrame with columns: [window_end, is_cointegrated, p_value, hedge_ratio].
        """
        results = []

        for i in range(window, len(series1), step):
            window_series1 = series1.iloc[i - window : i]
            window_series2 = series2.iloc[i - window : i]

            try:
                result = self.test_pair(window_series1, window_series2)

                results.append(
                    {
                        "window_end": series1.index[i - 1],
                        "is_cointegrated": result.is_cointegrated,
                        "p_value": result.p_value,
                        "hedge_ratio": result.hedge_ratio,
                    }
                )
            except Exception as e:
                logger.warning(
                    f"Rolling test failed at window ending {series1.index[i-1]}: {e}"
                )
                continue

        return pd.DataFrame(results)
