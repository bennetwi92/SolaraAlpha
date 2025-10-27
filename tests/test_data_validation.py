"""Unit tests for data validation."""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from src.data.validate.validator import DataValidator, ValidationIssue


@pytest.fixture
def mock_config():
    """Create a mock configuration."""

    class MockConfig:
        def get_validation_config(self):
            return {
                "price_spike_threshold": 0.10,
                "volume_spike_multiplier": 5.0,
                "max_missing_days": 5,
                "max_staleness_days": 7,
            }

    return MockConfig()


@pytest.fixture
def valid_ohlcv_data():
    """Create valid OHLCV data."""
    dates = pd.date_range(start=datetime.now() - timedelta(days=30), periods=30, freq="D")
    data = pd.DataFrame(
        {
            "Open": [100.0] * 30,
            "High": [105.0] * 30,
            "Low": [95.0] * 30,
            "Close": [102.0] * 30,
            "Volume": [1000000] * 30,
        },
        index=dates,
    )
    return data


def test_validator_initialization(mock_config):
    """Test DataValidator initialization."""
    validator = DataValidator(config=mock_config)
    assert validator.validation_config is not None


def test_validate_empty_data(mock_config):
    """Test validation of empty DataFrame."""
    validator = DataValidator(config=mock_config)
    empty_data = pd.DataFrame()

    result = validator.validate(empty_data, "AAPL")

    assert result.passed is False
    assert len(result.issues) == 1
    assert result.issues[0].issue_type == "empty_data"
    assert result.record_count == 0


def test_validate_missing_columns(mock_config):
    """Test validation with missing required columns."""
    validator = DataValidator(config=mock_config)

    dates = pd.date_range(start="2023-01-01", periods=10, freq="D")
    incomplete_data = pd.DataFrame(
        {"Open": [100] * 10, "Close": [101] * 10},
        index=dates,
    )

    result = validator.validate(incomplete_data, "AAPL")

    assert result.passed is False
    errors = result.get_errors()
    assert len(errors) == 1
    assert errors[0].issue_type == "missing_columns"


def test_validate_valid_data(mock_config, valid_ohlcv_data):
    """Test validation of valid data passes."""
    validator = DataValidator(config=mock_config)

    result = validator.validate(valid_ohlcv_data, "AAPL")

    assert result.passed is True
    assert result.record_count == 30


def test_ohlc_violation_high_less_than_low(mock_config):
    """Test detection of High < Low violation."""
    validator = DataValidator(config=mock_config)

    dates = pd.date_range(start="2023-01-01", periods=5, freq="D")
    bad_data = pd.DataFrame(
        {
            "Open": [100, 100, 100, 100, 100],
            "High": [95, 105, 105, 105, 105],  # First row: High < Low
            "Low": [100, 95, 95, 95, 95],
            "Close": [102, 102, 102, 102, 102],
            "Volume": [1000000] * 5,
        },
        index=dates,
    )

    result = validator.validate(bad_data, "AAPL")

    assert result.passed is False
    errors = result.get_errors()
    assert any(e.issue_type == "ohlc_violation" for e in errors)


def test_ohlc_violation_high_less_than_close(mock_config):
    """Test detection of High < Close violation."""
    validator = DataValidator(config=mock_config)

    dates = pd.date_range(start="2023-01-01", periods=5, freq="D")
    bad_data = pd.DataFrame(
        {
            "Open": [100, 100, 100, 100, 100],
            "High": [101, 105, 105, 105, 105],  # First row: High < Close
            "Low": [95, 95, 95, 95, 95],
            "Close": [106, 102, 102, 102, 102],
            "Volume": [1000000] * 5,
        },
        index=dates,
    )

    result = validator.validate(bad_data, "AAPL")

    assert result.passed is False
    errors = result.get_errors()
    assert any(e.issue_type == "ohlc_violation" for e in errors)


def test_price_spike_detection(mock_config):
    """Test detection of price spikes."""
    validator = DataValidator(config=mock_config)

    dates = pd.date_range(start=datetime.now() - timedelta(days=10), periods=10, freq="D")
    data = pd.DataFrame(
        {
            "Open": [100] * 10,
            "High": [105] * 10,
            "Low": [95] * 10,
            "Close": [100, 100, 100, 120, 100, 100, 100, 100, 100, 100],  # 20% spike
            "Volume": [1000000] * 10,
        },
        index=dates,
    )

    result = validator.validate(data, "AAPL")

    # Price spikes are warnings, not errors
    warnings = result.get_warnings()
    assert any(w.issue_type == "price_spike" for w in warnings)


def test_volume_spike_detection(mock_config):
    """Test detection of volume anomalies."""
    validator = DataValidator(config=mock_config)

    dates = pd.date_range(start=datetime.now() - timedelta(days=30), periods=30, freq="D")
    volumes = [1000000] * 30
    volumes[15] = 10000000  # 10x spike

    data = pd.DataFrame(
        {
            "Open": [100] * 30,
            "High": [105] * 30,
            "Low": [95] * 30,
            "Close": [102] * 30,
            "Volume": volumes,
        },
        index=dates,
    )

    result = validator.validate(data, "AAPL")

    # Volume spikes are warnings
    warnings = result.get_warnings()
    assert any(w.issue_type == "volume_spike" for w in warnings)


def test_missing_data_detection(mock_config):
    """Test detection of gaps in time series."""
    validator = DataValidator(config=mock_config)

    # Create data with a 10-day gap
    dates1 = pd.date_range(start="2023-01-01", periods=10, freq="D")
    dates2 = pd.date_range(start="2023-01-20", periods=10, freq="D")
    dates = dates1.union(dates2)

    data = pd.DataFrame(
        {
            "Open": [100] * 20,
            "High": [105] * 20,
            "Low": [95] * 20,
            "Close": [102] * 20,
            "Volume": [1000000] * 20,
        },
        index=dates,
    )

    result = validator.validate(data, "AAPL")

    warnings = result.get_warnings()
    assert any(w.issue_type == "missing_data" for w in warnings)


def test_staleness_detection(mock_config):
    """Test detection of stale data."""
    validator = DataValidator(config=mock_config)

    # Create data that's 30 days old
    dates = pd.date_range(
        start=datetime.now() - timedelta(days=60),
        periods=30,
        freq="D",
    )

    data = pd.DataFrame(
        {
            "Open": [100] * 30,
            "High": [105] * 30,
            "Low": [95] * 30,
            "Close": [102] * 30,
            "Volume": [1000000] * 30,
        },
        index=dates,
    )

    result = validator.validate(data, "AAPL")

    warnings = result.get_warnings()
    assert any(w.issue_type == "stale_data" for w in warnings)


def test_validation_result_get_errors(mock_config):
    """Test ValidationResult.get_errors() method."""
    validator = DataValidator(config=mock_config)

    dates = pd.date_range(start="2023-01-01", periods=5, freq="D")
    bad_data = pd.DataFrame(
        {
            "Open": [100] * 5,
            "High": [95] * 5,  # High < Low (error)
            "Low": [100] * 5,
            "Close": [102] * 5,
            "Volume": [1000000] * 5,
        },
        index=dates,
    )

    result = validator.validate(bad_data, "AAPL")

    errors = result.get_errors()
    warnings = result.get_warnings()

    assert len(errors) > 0
    assert all(e.severity == "error" for e in errors)
    assert all(w.severity == "warning" for w in warnings)


def test_validation_result_date_range(mock_config, valid_ohlcv_data):
    """Test that ValidationResult includes date range."""
    validator = DataValidator(config=mock_config)

    result = validator.validate(valid_ohlcv_data, "AAPL")

    assert result.date_range is not None
    start_date, end_date = result.date_range
    assert start_date == valid_ohlcv_data.index.min()
    assert end_date == valid_ohlcv_data.index.max()


def test_validation_issue_dataclass():
    """Test ValidationIssue dataclass."""
    issue = ValidationIssue(
        issue_type="test_issue",
        severity="error",
        message="Test message",
        timestamp=datetime.now(),
        details={"key": "value"},
    )

    assert issue.issue_type == "test_issue"
    assert issue.severity == "error"
    assert issue.message == "Test message"
    assert issue.timestamp is not None
    assert issue.details == {"key": "value"}
