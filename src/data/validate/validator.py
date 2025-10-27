"""Data quality validation and anomaly detection."""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List

import pandas as pd

from ..config.config_manager import ConfigManager

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """Represents a data validation issue."""

    issue_type: str  # e.g., 'price_spike', 'missing_data', 'ohlc_violation'
    severity: str  # 'warning' or 'error'
    message: str
    timestamp: datetime | None = None
    details: dict | None = None


@dataclass
class ValidationResult:
    """Results from data validation."""

    passed: bool
    issues: List[ValidationIssue]
    record_count: int
    date_range: tuple[datetime, datetime] | None = None

    def get_errors(self) -> List[ValidationIssue]:
        """Get only error-level issues."""
        return [issue for issue in self.issues if issue.severity == "error"]

    def get_warnings(self) -> List[ValidationIssue]:
        """Get only warning-level issues."""
        return [issue for issue in self.issues if issue.severity == "warning"]


class DataValidator:
    """Validates market data quality and detects anomalies.

    Performs comprehensive validation:
    - OHLC relationship checks
    - Price spike detection
    - Volume anomaly detection
    - Missing data detection
    - Staleness checks

    Example:
        >>> validator = DataValidator()
        >>> result = validator.validate(df, 'AAPL')
        >>> if not result.passed:
        ...     print(f"Found {len(result.get_errors())} errors")
    """

    def __init__(self, config: ConfigManager | None = None) -> None:
        """Initialize validator.

        Args:
            config: Configuration manager. If None, creates new instance.
        """
        self.config = config or ConfigManager()
        self.validation_config = self.config.get_validation_config()

    def validate(
        self, data: pd.DataFrame, symbol: str
    ) -> ValidationResult:
        """Validate market data for a symbol.

        Args:
            data: DataFrame with datetime index and OHLCV columns.
            symbol: Symbol ticker (for logging).

        Returns:
            ValidationResult with pass/fail status and list of issues.
        """
        issues: List[ValidationIssue] = []

        # Basic checks
        if data.empty:
            issues.append(
                ValidationIssue(
                    issue_type="empty_data",
                    severity="error",
                    message=f"Empty data for {symbol}",
                )
            )
            return ValidationResult(
                passed=False, issues=issues, record_count=0
            )

        # Required columns check
        required_cols = ["Open", "High", "Low", "Close", "Volume"]
        missing_cols = [col for col in required_cols if col not in data.columns]
        if missing_cols:
            issues.append(
                ValidationIssue(
                    issue_type="missing_columns",
                    severity="error",
                    message=f"Missing columns for {symbol}: {missing_cols}",
                )
            )
            return ValidationResult(
                passed=False, issues=issues, record_count=len(data)
            )

        # OHLC validation
        issues.extend(self._validate_ohlc(data, symbol))

        # Price spike detection
        issues.extend(self._detect_price_spikes(data, symbol))

        # Volume anomaly detection
        issues.extend(self._detect_volume_anomalies(data, symbol))

        # Missing data detection
        issues.extend(self._detect_missing_data(data, symbol))

        # Staleness check
        issues.extend(self._check_staleness(data, symbol))

        # Determine if validation passed (no errors, warnings are OK)
        errors = [issue for issue in issues if issue.severity == "error"]
        passed = len(errors) == 0

        date_range = (data.index.min(), data.index.max()) if not data.empty else None

        logger.info(
            f"Validation for {symbol}: "
            f"{'PASSED' if passed else 'FAILED'} "
            f"({len(errors)} errors, {len(issues) - len(errors)} warnings)"
        )

        return ValidationResult(
            passed=passed,
            issues=issues,
            record_count=len(data),
            date_range=date_range,
        )

    def _validate_ohlc(
        self, data: pd.DataFrame, symbol: str
    ) -> List[ValidationIssue]:
        """Validate OHLC relationships.

        Args:
            data: DataFrame with OHLCV columns.
            symbol: Symbol ticker.

        Returns:
            List of validation issues.
        """
        issues = []

        # High >= Low
        violations = data[data["High"] < data["Low"]]
        if not violations.empty:
            for idx in violations.index:
                issues.append(
                    ValidationIssue(
                        issue_type="ohlc_violation",
                        severity="error",
                        message=f"High < Low for {symbol}",
                        timestamp=idx,
                        details={
                            "high": violations.loc[idx, "High"],
                            "low": violations.loc[idx, "Low"],
                        },
                    )
                )

        # High >= Open
        violations = data[data["High"] < data["Open"]]
        if not violations.empty:
            for idx in violations.index[:10]:  # Limit to first 10
                issues.append(
                    ValidationIssue(
                        issue_type="ohlc_violation",
                        severity="error",
                        message=f"High < Open for {symbol}",
                        timestamp=idx,
                    )
                )

        # High >= Close
        violations = data[data["High"] < data["Close"]]
        if not violations.empty:
            for idx in violations.index[:10]:
                issues.append(
                    ValidationIssue(
                        issue_type="ohlc_violation",
                        severity="error",
                        message=f"High < Close for {symbol}",
                        timestamp=idx,
                    )
                )

        # Low <= Open
        violations = data[data["Low"] > data["Open"]]
        if not violations.empty:
            for idx in violations.index[:10]:
                issues.append(
                    ValidationIssue(
                        issue_type="ohlc_violation",
                        severity="error",
                        message=f"Low > Open for {symbol}",
                        timestamp=idx,
                    )
                )

        # Low <= Close
        violations = data[data["Low"] > data["Close"]]
        if not violations.empty:
            for idx in violations.index[:10]:
                issues.append(
                    ValidationIssue(
                        issue_type="ohlc_violation",
                        severity="error",
                        message=f"Low > Close for {symbol}",
                        timestamp=idx,
                    )
                )

        return issues

    def _detect_price_spikes(
        self, data: pd.DataFrame, symbol: str
    ) -> List[ValidationIssue]:
        """Detect abnormal price spikes.

        Args:
            data: DataFrame with OHLCV columns.
            symbol: Symbol ticker.

        Returns:
            List of validation issues.
        """
        issues = []

        threshold = self.validation_config.get("price_spike_threshold", 0.10)

        # Calculate price change percentage
        data = data.copy()
        data["price_change"] = data["Close"].pct_change().abs()

        # Find spikes
        spikes = data[data["price_change"] > threshold]

        for idx in spikes.index[:10]:  # Limit to first 10
            change_pct = spikes.loc[idx, "price_change"] * 100
            issues.append(
                ValidationIssue(
                    issue_type="price_spike",
                    severity="warning",
                    message=(
                        f"Price spike detected for {symbol}: "
                        f"{change_pct:.1f}% change"
                    ),
                    timestamp=idx,
                    details={"change_percent": change_pct},
                )
            )

        return issues

    def _detect_volume_anomalies(
        self, data: pd.DataFrame, symbol: str
    ) -> List[ValidationIssue]:
        """Detect abnormal volume spikes.

        Args:
            data: DataFrame with OHLCV columns.
            symbol: Symbol ticker.

        Returns:
            List of validation issues.
        """
        issues = []

        multiplier = self.validation_config.get("volume_spike_multiplier", 5.0)

        # Calculate rolling average volume
        window = min(20, len(data) // 2)  # 20-day or half the data
        if window < 2:
            return issues  # Not enough data

        data = data.copy()
        data["avg_volume"] = data["Volume"].rolling(window=window).mean()

        # Find volume spikes
        spikes = data[data["Volume"] > data["avg_volume"] * multiplier]

        for idx in spikes.index[:10]:  # Limit to first 10
            ratio = (
                spikes.loc[idx, "Volume"] / spikes.loc[idx, "avg_volume"]
            )
            issues.append(
                ValidationIssue(
                    issue_type="volume_spike",
                    severity="warning",
                    message=(
                        f"Volume spike detected for {symbol}: "
                        f"{ratio:.1f}x average"
                    ),
                    timestamp=idx,
                    details={"volume_ratio": ratio},
                )
            )

        return issues

    def _detect_missing_data(
        self, data: pd.DataFrame, symbol: str
    ) -> List[ValidationIssue]:
        """Detect gaps in time series data.

        Args:
            data: DataFrame with datetime index.
            symbol: Symbol ticker.

        Returns:
            List of validation issues.
        """
        issues = []

        max_missing = self.validation_config.get("max_missing_days", 5)

        # Calculate gaps in days (assuming daily data)
        data = data.sort_index()
        date_diffs = data.index.to_series().diff()

        # Find gaps > 1 day (accounting for weekends)
        # For daily data, 3+ days likely indicates missing data
        gaps = date_diffs[date_diffs > timedelta(days=max_missing)]

        for idx in gaps.index[:10]:  # Limit to first 10
            gap_days = date_diffs.loc[idx].days
            issues.append(
                ValidationIssue(
                    issue_type="missing_data",
                    severity="warning",
                    message=(
                        f"Data gap detected for {symbol}: "
                        f"{gap_days} days missing"
                    ),
                    timestamp=idx,
                    details={"gap_days": gap_days},
                )
            )

        return issues

    def _check_staleness(
        self, data: pd.DataFrame, symbol: str
    ) -> List[ValidationIssue]:
        """Check if data is stale (too old).

        Args:
            data: DataFrame with datetime index.
            symbol: Symbol ticker.

        Returns:
            List of validation issues.
        """
        issues = []

        max_staleness = self.validation_config.get("max_staleness_days", 7)

        # Get most recent date in data
        latest_date = data.index.max()
        age = datetime.now() - latest_date

        if age > timedelta(days=max_staleness):
            issues.append(
                ValidationIssue(
                    issue_type="stale_data",
                    severity="warning",
                    message=(
                        f"Stale data for {symbol}: "
                        f"latest data is {age.days} days old"
                    ),
                    timestamp=latest_date,
                    details={"age_days": age.days},
                )
            )

        return issues
