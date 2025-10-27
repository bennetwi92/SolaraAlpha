"""Portfolio-level risk metrics and analysis.

Calculates portfolio-wide correlation, factor exposure, and diversification
metrics to support allocation decisions.
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PortfolioRiskMetrics:
    """Portfolio-level risk metrics.

    Attributes:
        portfolio_volatility: Annualized portfolio volatility
        portfolio_sharpe: Portfolio Sharpe ratio
        avg_correlation: Average pairwise correlation
        effective_strategies: Effective number of independent strategies
        concentration_risk: Concentration measure (Herfindahl index)
        diversification_ratio: Diversification ratio (sum of vols / portfolio vol)
    """

    portfolio_volatility: float = 0.0
    portfolio_sharpe: float = 0.0
    avg_correlation: float = 0.0
    effective_strategies: float = 0.0
    concentration_risk: float = 0.0
    diversification_ratio: float = 0.0


@dataclass
class PortfolioMetrics:
    """Complete portfolio metrics.

    Attributes:
        total_value: Total portfolio value
        allocated_capital: Total capital allocated to strategies
        unallocated_capital: Remaining unallocated capital
        strategy_count: Number of active strategies
        risk_metrics: Portfolio risk metrics
        correlation_matrix: Strategy correlation matrix
        allocation_weights: Current allocation weights by strategy
    """

    total_value: float
    allocated_capital: float
    unallocated_capital: float
    strategy_count: int
    risk_metrics: PortfolioRiskMetrics
    correlation_matrix: pd.DataFrame
    allocation_weights: Dict[str, float]


class PortfolioRiskCalculator:
    """Calculates portfolio-level risk metrics.

    Provides correlation analysis, diversification metrics, and factor
    exposure calculations to support allocation decisions.
    """

    def __init__(self) -> None:
        """Initialize portfolio risk calculator."""
        logger.info("Portfolio risk calculator initialized")

    def calculate_portfolio_metrics(
        self,
        strategy_returns: Dict[str, pd.Series],
        allocation_weights: Dict[str, float],
    ) -> PortfolioRiskMetrics:
        """Calculate comprehensive portfolio risk metrics.

        Args:
            strategy_returns: Dict mapping strategy_id to return series
            allocation_weights: Dict mapping strategy_id to allocation weight

        Returns:
            PortfolioRiskMetrics object
        """
        if len(strategy_returns) == 0:
            return PortfolioRiskMetrics()

        # Build returns dataframe
        returns_df = pd.DataFrame(strategy_returns).dropna()

        if len(returns_df) == 0:
            return PortfolioRiskMetrics()

        # Calculate portfolio returns using weights
        weights = pd.Series(allocation_weights)
        weights = weights / weights.sum()  # Normalize to sum to 1

        portfolio_returns = (returns_df * weights).sum(axis=1)

        # Calculate metrics
        metrics = PortfolioRiskMetrics()

        # Portfolio volatility
        metrics.portfolio_volatility = portfolio_returns.std() * np.sqrt(252)

        # Portfolio Sharpe
        mean_return = portfolio_returns.mean()
        if portfolio_returns.std() > 1e-10:
            metrics.portfolio_sharpe = (mean_return / portfolio_returns.std()) * np.sqrt(252)

        # Correlation metrics
        corr_matrix = returns_df.corr()
        metrics.avg_correlation = self._calculate_avg_correlation(corr_matrix)
        metrics.effective_strategies = self._calculate_effective_n(corr_matrix)

        # Concentration risk (Herfindahl index)
        metrics.concentration_risk = self._calculate_herfindahl(weights)

        # Diversification ratio
        metrics.diversification_ratio = self._calculate_diversification_ratio(
            returns_df, weights, metrics.portfolio_volatility
        )

        return metrics

    def _calculate_avg_correlation(self, corr_matrix: pd.DataFrame) -> float:
        """Calculate average pairwise correlation.

        Args:
            corr_matrix: Correlation matrix

        Returns:
            Average correlation (excluding diagonal)
        """
        if len(corr_matrix) < 2:
            return 0.0

        # Get upper triangle (excluding diagonal)
        mask = np.triu(np.ones_like(corr_matrix, dtype=bool), k=1)
        correlations = corr_matrix.values[mask]

        if len(correlations) == 0:
            return 0.0

        return correlations.mean()

    def _calculate_effective_n(self, corr_matrix: pd.DataFrame) -> float:
        """Calculate effective number of independent strategies.

        Uses eigenvalue decomposition to estimate diversification.

        Args:
            corr_matrix: Correlation matrix

        Returns:
            Effective number of independent strategies
        """
        if len(corr_matrix) < 2:
            return float(len(corr_matrix))

        try:
            # Calculate eigenvalues
            eigenvalues = np.linalg.eigvalsh(corr_matrix.values)
            eigenvalues = eigenvalues[eigenvalues > 1e-10]  # Filter near-zero

            # Shannon entropy of eigenvalues
            eigenvalues = eigenvalues / eigenvalues.sum()
            entropy = -np.sum(eigenvalues * np.log(eigenvalues + 1e-10))

            # Convert to effective number
            effective_n = np.exp(entropy)

            return effective_n

        except Exception as e:
            logger.warning(f"Failed to calculate effective N: {e}")
            return float(len(corr_matrix))

    def _calculate_herfindahl(self, weights: pd.Series) -> float:
        """Calculate Herfindahl concentration index.

        Higher values indicate more concentration (less diversification).
        Range: 1/N (fully diversified) to 1 (fully concentrated).

        Args:
            weights: Allocation weights

        Returns:
            Herfindahl index
        """
        if len(weights) == 0:
            return 0.0

        # Normalize weights
        weights = weights / weights.sum()

        # Sum of squared weights
        herfindahl = (weights ** 2).sum()

        return herfindahl

    def _calculate_diversification_ratio(
        self,
        returns_df: pd.DataFrame,
        weights: pd.Series,
        portfolio_vol: float,
    ) -> float:
        """Calculate diversification ratio.

        Ratio of weighted average volatility to portfolio volatility.
        Higher values indicate better diversification.

        Args:
            returns_df: DataFrame of strategy returns
            weights: Allocation weights
            portfolio_vol: Portfolio volatility

        Returns:
            Diversification ratio
        """
        if portfolio_vol < 1e-10:
            return 0.0

        # Calculate individual volatilities (annualized)
        individual_vols = returns_df.std() * np.sqrt(252)

        # Normalize weights
        weights = weights / weights.sum()

        # Weighted average volatility
        weighted_avg_vol = (individual_vols * weights).sum()

        # Diversification ratio
        div_ratio = weighted_avg_vol / portfolio_vol

        return div_ratio

    def calculate_marginal_contribution(
        self,
        strategy_returns: Dict[str, pd.Series],
        allocation_weights: Dict[str, float],
        strategy_id: str,
    ) -> Dict[str, float]:
        """Calculate marginal contribution of a strategy to portfolio risk.

        Args:
            strategy_returns: Dict mapping strategy_id to return series
            allocation_weights: Current allocation weights
            strategy_id: Strategy to analyze

        Returns:
            Dict with marginal risk contribution metrics
        """
        if strategy_id not in strategy_returns:
            return {}

        # Build returns dataframe
        returns_df = pd.DataFrame(strategy_returns).dropna()

        if len(returns_df) == 0 or strategy_id not in returns_df.columns:
            return {}

        # Current portfolio metrics
        weights = pd.Series(allocation_weights)
        weights = weights / weights.sum()

        portfolio_returns = (returns_df * weights).sum(axis=1)
        portfolio_vol = portfolio_returns.std() * np.sqrt(252)

        # Calculate covariance between strategy and portfolio
        strategy_returns_series = returns_df[strategy_id]
        cov = strategy_returns_series.cov(portfolio_returns) * 252  # Annualize

        # Marginal contribution to volatility
        if portfolio_vol > 1e-10:
            marginal_vol = cov / portfolio_vol
        else:
            marginal_vol = 0.0

        # Calculate contribution to portfolio variance
        weight = weights.get(strategy_id, 0.0)
        contrib_to_variance = weight * marginal_vol

        return {
            "marginal_volatility": marginal_vol,
            "contribution_to_variance": contrib_to_variance,
            "weight": weight,
        }

    def calculate_factor_exposure(
        self,
        strategy_returns: Dict[str, pd.Series],
        factor_returns: Dict[str, pd.Series],
        allocation_weights: Dict[str, float],
    ) -> Dict[str, float]:
        """Calculate portfolio exposure to common factors.

        Args:
            strategy_returns: Dict mapping strategy_id to return series
            factor_returns: Dict mapping factor_name to return series
            allocation_weights: Current allocation weights

        Returns:
            Dict mapping factor_name to portfolio beta
        """
        if len(strategy_returns) == 0 or len(factor_returns) == 0:
            return {}

        # Build returns dataframe
        returns_df = pd.DataFrame(strategy_returns).dropna()

        if len(returns_df) == 0:
            return {}

        # Calculate portfolio returns
        weights = pd.Series(allocation_weights)
        weights = weights / weights.sum()

        portfolio_returns = (returns_df * weights).sum(axis=1)

        # Calculate beta for each factor
        factor_betas = {}

        for factor_name, factor_series in factor_returns.items():
            # Align dates
            aligned = pd.DataFrame(
                {"portfolio": portfolio_returns, "factor": factor_series}
            ).dropna()

            if len(aligned) < 30:  # Need minimum history
                continue

            # Calculate beta (covariance / factor variance)
            cov = aligned["portfolio"].cov(aligned["factor"])
            factor_var = aligned["factor"].var()

            if factor_var > 1e-10:
                beta = cov / factor_var
                factor_betas[factor_name] = beta

        return factor_betas
