"""Tests for PCA factor model."""

import numpy as np
import pandas as pd
import pytest

from src.strategies.stat_arb.factor_model import FactorModel, calculate_rolling_residuals


@pytest.fixture
def sample_returns():
    """Generate sample returns data."""
    np.random.seed(42)
    n_periods = 100
    n_stocks = 10

    # Generate returns with factor structure
    returns = pd.DataFrame(
        np.random.randn(n_periods, n_stocks) * 0.01,
        columns=[f"STOCK{i}" for i in range(n_stocks)],
    )

    return returns


def test_factor_model_init():
    """Test factor model initialization."""
    model = FactorModel(n_factors=3, estimation_window=60)

    assert model.n_factors == 3
    assert model.estimation_window == 60
    assert model._pca is None


def test_factor_model_fit(sample_returns):
    """Test factor model fitting."""
    model = FactorModel(n_factors=3, estimation_window=60)

    result = model.fit(sample_returns)

    assert result.n_factors == 3
    assert result.explained_variance > 0
    assert result.factor_loadings.shape == (10, 3)
    assert len(result.residuals) == len(sample_returns)


def test_factor_model_transform(sample_returns):
    """Test factor model transformation."""
    model = FactorModel(n_factors=3)

    # Fit model
    model.fit(sample_returns.iloc[:80])

    # Transform new data
    residuals = model.transform(sample_returns.iloc[80:])

    assert len(residuals) == 20
    assert residuals.shape[1] == 10


def test_factor_model_insufficient_data():
    """Test error on insufficient data."""
    model = FactorModel(n_factors=3, estimation_window=60)

    short_data = pd.DataFrame(np.random.randn(50, 10))

    with pytest.raises(ValueError, match="Insufficient data"):
        model.fit(short_data)


def test_calculate_rolling_residuals(sample_returns):
    """Test rolling residuals calculation."""
    residuals = calculate_rolling_residuals(
        sample_returns, estimation_window=60, n_factors=3
    )

    assert len(residuals) == len(sample_returns)
    # First estimation_window rows should be NaN
    assert residuals.iloc[:60].isna().all().all()
    # After that should have values
    assert not residuals.iloc[60:].isna().all().all()
