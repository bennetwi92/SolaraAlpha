"""Tests for portfolio-level risk metrics."""
import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timedelta

from src.portfolio.portfolio_metrics import PortfolioRiskCalculator, PortfolioRiskMetrics


class TestPortfolioRiskCalculator:
    """Test suite for PortfolioRiskCalculator."""

    @pytest.fixture
    def calculator(self):
        """Create a portfolio risk calculator instance."""
        return PortfolioRiskCalculator()

    @pytest.fixture
    def sample_strategy_returns(self):
        """Generate sample strategy returns."""
        np.random.seed(42)
        dates = pd.date_range(end=datetime.now(), periods=100, freq='D')

        return {
            "strategy_1": pd.Series(np.random.normal(0.001, 0.02, 100), index=dates),
            "strategy_2": pd.Series(np.random.normal(0.001, 0.015, 100), index=dates),
            "strategy_3": pd.Series(np.random.normal(0.001, 0.025, 100), index=dates),
        }

    @pytest.fixture
    def sample_weights(self):
        """Generate sample allocation weights."""
        return {
            "strategy_1": 0.4,
            "strategy_2": 0.3,
            "strategy_3": 0.3,
        }

    def test_initialization(self, calculator):
        """Test calculator initialization."""
        assert calculator is not None

    def test_calculate_portfolio_metrics(self, calculator, sample_strategy_returns, sample_weights):
        """Test comprehensive portfolio metrics calculation."""
        metrics = calculator.calculate_portfolio_metrics(
            sample_strategy_returns, sample_weights
        )

        assert isinstance(metrics, PortfolioRiskMetrics)
        assert metrics.portfolio_volatility > 0.0
        assert metrics.portfolio_sharpe != 0.0
        assert -1.0 <= metrics.avg_correlation <= 1.0
        assert metrics.effective_strategies > 0.0
        assert 0.0 <= metrics.concentration_risk <= 1.0
        assert metrics.diversification_ratio > 0.0

    def test_empty_returns(self, calculator):
        """Test behavior with empty returns."""
        metrics = calculator.calculate_portfolio_metrics({}, {})

        assert metrics.portfolio_volatility == 0.0
        assert metrics.portfolio_sharpe == 0.0

    def test_avg_correlation(self, calculator):
        """Test average correlation calculation."""
        # Create perfectly correlated strategies
        dates = pd.date_range(end=datetime.now(), periods=50, freq='D')
        base_returns = np.random.normal(0.001, 0.02, 50)

        strategy_returns = {
            "strategy_1": pd.Series(base_returns, index=dates),
            "strategy_2": pd.Series(base_returns, index=dates),  # Perfect correlation
        }

        weights = {"strategy_1": 0.5, "strategy_2": 0.5}

        metrics = calculator.calculate_portfolio_metrics(strategy_returns, weights)

        # Average correlation should be close to 1.0
        assert metrics.avg_correlation > 0.95

    def test_effective_strategies(self, calculator):
        """Test effective number of strategies calculation."""
        dates = pd.date_range(end=datetime.now(), periods=50, freq='D')
        np.random.seed(42)

        # Three uncorrelated strategies
        strategy_returns = {
            "strategy_1": pd.Series(np.random.normal(0.001, 0.02, 50), index=dates),
            "strategy_2": pd.Series(np.random.normal(0.001, 0.02, 50), index=dates),
            "strategy_3": pd.Series(np.random.normal(0.001, 0.02, 50), index=dates),
        }

        weights = {"strategy_1": 1/3, "strategy_2": 1/3, "strategy_3": 1/3}

        metrics = calculator.calculate_portfolio_metrics(strategy_returns, weights)

        # Effective N should be close to 3 for uncorrelated strategies
        assert 2.0 <= metrics.effective_strategies <= 3.0

    def test_herfindahl_concentration(self, calculator):
        """Test Herfindahl concentration index."""
        # Fully concentrated
        weights_concentrated = pd.Series({"strategy_1": 1.0})
        herfindahl_concentrated = calculator._calculate_herfindahl(weights_concentrated)
        assert herfindahl_concentrated == pytest.approx(1.0)

        # Fully diversified (4 strategies)
        weights_diversified = pd.Series({
            "strategy_1": 0.25,
            "strategy_2": 0.25,
            "strategy_3": 0.25,
            "strategy_4": 0.25,
        })
        herfindahl_diversified = calculator._calculate_herfindahl(weights_diversified)
        assert herfindahl_diversified == pytest.approx(0.25, abs=0.01)

    def test_diversification_ratio(self, calculator):
        """Test diversification ratio calculation."""
        dates = pd.date_range(end=datetime.now(), periods=50, freq='D')
        np.random.seed(42)

        # Two uncorrelated strategies
        returns_df = pd.DataFrame({
            "strategy_1": np.random.normal(0.001, 0.02, 50),
            "strategy_2": np.random.normal(0.001, 0.02, 50),
        }, index=dates)

        weights = pd.Series({"strategy_1": 0.5, "strategy_2": 0.5})

        portfolio_returns = (returns_df * weights).sum(axis=1)
        portfolio_vol = portfolio_returns.std() * np.sqrt(252)

        div_ratio = calculator._calculate_diversification_ratio(
            returns_df, weights, portfolio_vol
        )

        # Diversification ratio should be > 1 for uncorrelated assets
        assert div_ratio >= 1.0

    def test_marginal_contribution(self, calculator, sample_strategy_returns, sample_weights):
        """Test marginal risk contribution calculation."""
        contribution = calculator.calculate_marginal_contribution(
            sample_strategy_returns,
            sample_weights,
            "strategy_1"
        )

        assert "marginal_volatility" in contribution
        assert "contribution_to_variance" in contribution
        assert "weight" in contribution

        assert contribution["weight"] == sample_weights["strategy_1"]

    def test_factor_exposure(self, calculator):
        """Test factor exposure calculation."""
        dates = pd.date_range(end=datetime.now(), periods=100, freq='D')
        np.random.seed(42)

        # Create strategy returns
        market_factor = np.random.normal(0.001, 0.02, 100)
        strategy_returns = {
            "strategy_1": pd.Series(
                0.5 * market_factor + np.random.normal(0, 0.01, 100),
                index=dates
            ),
        }

        # Create factor returns
        factor_returns = {
            "market": pd.Series(market_factor, index=dates),
        }

        weights = {"strategy_1": 1.0}

        factor_betas = calculator.calculate_factor_exposure(
            strategy_returns, factor_returns, weights
        )

        assert "market" in factor_betas
        # Beta should be close to 0.5 (our coefficient)
        assert 0.3 <= factor_betas["market"] <= 0.7

    def test_zero_volatility_case(self, calculator):
        """Test handling of zero volatility."""
        dates = pd.date_range(end=datetime.now(), periods=50, freq='D')

        # Constant returns (zero volatility)
        strategy_returns = {
            "strategy_1": pd.Series([0.01] * 50, index=dates),
        }

        weights = {"strategy_1": 1.0}

        metrics = calculator.calculate_portfolio_metrics(strategy_returns, weights)

        # Should handle gracefully
        assert metrics.portfolio_sharpe == 0.0  # Division by zero handled

    def test_single_strategy(self, calculator):
        """Test metrics with single strategy."""
        dates = pd.date_range(end=datetime.now(), periods=50, freq='D')
        np.random.seed(42)

        strategy_returns = {
            "strategy_1": pd.Series(np.random.normal(0.001, 0.02, 50), index=dates),
        }

        weights = {"strategy_1": 1.0}

        metrics = calculator.calculate_portfolio_metrics(strategy_returns, weights)

        # Should work with single strategy
        assert metrics.portfolio_volatility > 0.0
        assert metrics.effective_strategies == 1.0
        assert metrics.concentration_risk == pytest.approx(1.0)

    def test_weight_normalization(self, calculator):
        """Test that weights are normalized."""
        dates = pd.date_range(end=datetime.now(), periods=50, freq='D')
        np.random.seed(42)

        strategy_returns = {
            "strategy_1": pd.Series(np.random.normal(0.001, 0.02, 50), index=dates),
            "strategy_2": pd.Series(np.random.normal(0.001, 0.02, 50), index=dates),
        }

        # Non-normalized weights
        weights = {"strategy_1": 100.0, "strategy_2": 100.0}

        # Should handle non-normalized weights
        metrics = calculator.calculate_portfolio_metrics(strategy_returns, weights)

        assert metrics.portfolio_volatility > 0.0

    def test_negative_correlation(self, calculator):
        """Test with negatively correlated strategies."""
        dates = pd.date_range(end=datetime.now(), periods=50, freq='D')
        np.random.seed(42)

        base_returns = np.random.normal(0.001, 0.02, 50)

        strategy_returns = {
            "strategy_1": pd.Series(base_returns, index=dates),
            "strategy_2": pd.Series(-base_returns, index=dates),  # Negative correlation
        }

        weights = {"strategy_1": 0.5, "strategy_2": 0.5}

        metrics = calculator.calculate_portfolio_metrics(strategy_returns, weights)

        # Average correlation should be negative
        assert metrics.avg_correlation < 0.0

        # Portfolio volatility should be low due to hedging
        strategy_1_vol = strategy_returns["strategy_1"].std() * np.sqrt(252)
        assert metrics.portfolio_volatility < strategy_1_vol
