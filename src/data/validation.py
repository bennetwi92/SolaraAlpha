"""
Data Validation Module - Quality control for market data.

Provides real-time validation of market data to prevent bad data from
propagating to trading strategies. Implements multiple validation checks,
quality metrics, and an extensible alert system.

Features:
- Missing data detection (gaps in time series)
- Statistical outlier detection (z-score based)
- Domain-specific outlier detection (price jumps, volume spikes)
- Staleness checks (data age validation)
- Bid-ask spread validation
- Quality metrics: completeness %, outlier rate, average staleness
- Severity-based alerting (info/warning/critical)
- Extensible validator chain pattern
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Literal, Callable
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd
import numpy as np


class AlertSeverity(Enum):
    """Alert severity levels for data quality issues."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class ValidationConfig:
    """
    Configuration for data validation behavior.

    Attributes:
        z_score_threshold: Z-score threshold for outlier detection (default: 3.0)
        price_jump_threshold: Max % price change to flag (default: 0.20 = 20%)
        volume_spike_threshold: Max volume increase multiplier (default: 5.0)
        staleness_thresholds: Max age in seconds by asset class
        min_bid_ask_spread: Minimum realistic bid-ask spread (default: 0.0001)
        max_bid_ask_spread: Maximum realistic bid-ask spread (default: 0.10 = 10%)
        enable_alerts: Enable/disable alert system
        log_metrics: Enable/disable quality metrics logging
    """
    # Statistical thresholds
    z_score_threshold: float = 3.0
    price_jump_threshold: float = 0.20  # 20%
    volume_spike_threshold: float = 5.0

    # Staleness thresholds by asset class (seconds)
    staleness_thresholds: Dict[str, int] = field(default_factory=lambda: {
        'equity': 900,      # 15 minutes
        'etf': 900,         # 15 minutes
        'crypto': 60,       # 1 minute (24/7 market)
        'forex': 60,        # 1 minute
        'economic': 86400,  # 1 day
        'fundamental': 86400  # 1 day
    })

    # Spread validation
    min_bid_ask_spread: float = 0.0001  # 0.01%
    max_bid_ask_spread: float = 0.10    # 10%

    # System config
    enable_alerts: bool = True
    log_metrics: bool = True


@dataclass
class ValidationResult:
    """
    Result of a data validation check.

    Attributes:
        is_valid: Whether data passed validation
        issues: List of validation issues found
        metrics: Quality metrics calculated
        alerts: List of alerts generated
    """
    is_valid: bool
    issues: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    alerts: List[Dict[str, Any]] = field(default_factory=list)

    def add_issue(self, issue: str, severity: AlertSeverity = AlertSeverity.WARNING):
        """Add a validation issue with severity."""
        self.issues.append(issue)
        self.is_valid = False
        if severity in (AlertSeverity.WARNING, AlertSeverity.CRITICAL):
            self.alerts.append({
                'severity': severity.value,
                'message': issue,
                'timestamp': datetime.now().isoformat()
            })

    def add_metric(self, key: str, value: Any):
        """Add a quality metric."""
        self.metrics[key] = value


class DataValidator:
    """
    Market data quality validator with extensible validation chain.

    Performs comprehensive validation checks on market data including:
    - Time series gap detection
    - Statistical outlier detection
    - Domain-specific outlier detection
    - Staleness validation
    - Bid-ask spread validation

    Example:
        >>> validator = DataValidator()
        >>> result = validator.validate_ohlcv(df, symbol="AAPL")
        >>> if not result.is_valid:
        ...     print(f"Issues: {result.issues}")
        >>> print(f"Completeness: {result.metrics['completeness_pct']:.2f}%")
    """

    def __init__(self, config: Optional[ValidationConfig] = None):
        """
        Initialize data validator.

        Args:
            config: Validation configuration (uses defaults if None)
        """
        self.config = config or ValidationConfig()
        self.logger = self._setup_logger()

        # Custom validators (extensible chain)
        self._custom_validators: List[Callable] = []

    def _setup_logger(self) -> logging.Logger:
        """Setup structured logging for data quality."""
        logger = logging.getLogger('data_validator')
        logger.setLevel(logging.INFO)

        # Console handler with JSON formatting
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)

        # Use structured logging
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)

        if not logger.handlers:
            logger.addHandler(handler)

        return logger

    def add_custom_validator(self, validator: Callable[[Any, ValidationResult], None]):
        """
        Add a custom validator to the validation chain.

        Args:
            validator: Function that takes (data, result) and modifies result

        Example:
            >>> def check_min_volume(data, result):
            ...     if (data['Volume'] < 1000).any():
            ...         result.add_issue("Low volume detected")
            >>> validator.add_custom_validator(check_min_volume)
        """
        self._custom_validators.append(validator)

    def validate_ohlcv(
        self,
        data: pd.DataFrame,
        symbol: str,
        asset_class: Literal['equity', 'etf', 'crypto'] = 'equity',
        interval: str = '1d'
    ) -> ValidationResult:
        """
        Validate OHLCV data for quality issues.

        Args:
            data: DataFrame with OHLCV data (DatetimeIndex)
            symbol: Symbol being validated
            asset_class: Type of asset for threshold selection
            interval: Data interval (for gap detection)

        Returns:
            ValidationResult with issues, metrics, and alerts

        Example:
            >>> df = client.get_ohlcv("AAPL", start="2024-01-01")
            >>> result = validator.validate_ohlcv(df, "AAPL", asset_class="equity")
            >>> print(f"Completeness: {result.metrics['completeness_pct']:.2f}%")
        """
        result = ValidationResult(is_valid=True)

        # Basic structure validation
        if not self._validate_structure(data, result):
            return result

        # 1. Check for missing data (gaps)
        self._check_missing_data(data, result, interval, asset_class)

        # 2. Statistical outlier detection
        self._check_statistical_outliers(data, result, asset_class)

        # 3. Domain-specific outlier detection
        self._check_domain_outliers(data, result)

        # 4. Calculate quality metrics
        self._calculate_quality_metrics(data, result, symbol)

        # 5. Run custom validators
        for validator in self._custom_validators:
            validator(data, result)

        # 6. Log and alert
        self._process_results(result, symbol, asset_class)

        return result

    def validate_forex_quote(
        self,
        quote: Dict[str, Any],
        from_currency: str,
        to_currency: str
    ) -> ValidationResult:
        """
        Validate forex quote data.

        Args:
            quote: Quote dict with 'rate', 'bid', 'ask', 'timestamp'
            from_currency: Source currency code
            to_currency: Target currency code

        Returns:
            ValidationResult with issues, metrics, and alerts

        Example:
            >>> quote = client.get_forex_quote("USD", "EUR")
            >>> result = validator.validate_forex_quote(quote, "USD", "EUR")
            >>> if not result.is_valid:
            ...     print(f"Quote issues: {result.issues}")
        """
        result = ValidationResult(is_valid=True)
        pair = f"{from_currency}/{to_currency}"

        # Validate required fields
        required_fields = ['rate', 'bid', 'ask', 'timestamp']
        for field in required_fields:
            if field not in quote:
                result.add_issue(
                    f"Missing required field: {field}",
                    AlertSeverity.CRITICAL
                )
                return result

        # Validate bid/ask are positive (CRITICAL-1 fix)
        if quote['bid'] <= 0 or quote['ask'] <= 0:
            result.add_issue(
                f"Invalid bid/ask prices: bid={quote['bid']}, ask={quote['ask']}",
                AlertSeverity.CRITICAL
            )
            return result

        # Validate bid-ask spread
        self._check_bid_ask_spread(quote, result, pair)

        # Check staleness
        self._check_staleness(quote['timestamp'], result, 'forex')

        # Basic sanity checks
        if quote['rate'] <= 0:
            result.add_issue(
                f"Invalid rate: {quote['rate']}",
                AlertSeverity.CRITICAL
            )

        if quote['bid'] > quote['ask']:
            result.add_issue(
                f"Bid ({quote['bid']}) > Ask ({quote['ask']})",
                AlertSeverity.CRITICAL
            )

        # Log and alert
        self._process_results(result, pair, 'forex')

        return result

    def validate_fundamentals(
        self,
        data: pd.DataFrame,
        symbol: str,
        report_type: str
    ) -> ValidationResult:
        """
        Validate fundamental data.

        Args:
            data: DataFrame with fundamental data
            symbol: Symbol being validated
            report_type: Type of fundamental report

        Returns:
            ValidationResult with issues, metrics, and alerts
        """
        result = ValidationResult(is_valid=True)

        # Basic validation
        if data.empty:
            result.add_issue(
                f"Empty fundamental data for {symbol}",
                AlertSeverity.CRITICAL
            )
            return result

        # Check for null values
        null_pct = (data.isnull().sum().sum() / (data.shape[0] * data.shape[1])) * 100
        result.add_metric('null_percentage', null_pct)

        if null_pct > 50:
            result.add_issue(
                f"High null percentage: {null_pct:.1f}%",
                AlertSeverity.WARNING
            )

        # Log and alert
        self._process_results(result, f"{symbol}:{report_type}", 'fundamental')

        return result

    def validate_economic_indicator(
        self,
        data: pd.Series,
        series_id: str
    ) -> ValidationResult:
        """
        Validate economic indicator data.

        Args:
            data: Series with economic indicator values
            series_id: FRED series ID

        Returns:
            ValidationResult with issues, metrics, and alerts
        """
        result = ValidationResult(is_valid=True)

        # Basic validation
        if data.empty:
            result.add_issue(
                f"Empty economic data for {series_id}",
                AlertSeverity.CRITICAL
            )
            return result

        # Check for null values
        null_count = data.isnull().sum()
        completeness = ((len(data) - null_count) / len(data)) * 100
        result.add_metric('completeness_pct', completeness)

        if completeness < 95:
            result.add_issue(
                f"Low completeness: {completeness:.1f}%",
                AlertSeverity.WARNING
            )

        # Log and alert
        self._process_results(result, series_id, 'economic')

        return result

    def _validate_structure(self, data: pd.DataFrame, result: ValidationResult) -> bool:
        """Validate basic DataFrame structure."""
        if data.empty:
            result.add_issue("Empty DataFrame", AlertSeverity.CRITICAL)
            return False

        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        missing_cols = [col for col in required_cols if col not in data.columns]

        if missing_cols:
            result.add_issue(
                f"Missing columns: {missing_cols}",
                AlertSeverity.CRITICAL
            )
            return False

        if not isinstance(data.index, pd.DatetimeIndex):
            result.add_issue(
                "Index is not DatetimeIndex",
                AlertSeverity.CRITICAL
            )
            return False

        # Validate no zero or negative prices (HIGH-1 fix)
        price_cols = ['Open', 'High', 'Low', 'Close']
        if (data[price_cols] <= 0).any().any():
            result.add_issue(
                "Zero or negative prices detected",
                AlertSeverity.CRITICAL
            )
            return False

        return True

    def _check_missing_data(
        self,
        data: pd.DataFrame,
        result: ValidationResult,
        interval: str,
        asset_class: str = 'equity'
    ) -> None:
        """Check for gaps in time series data."""
        if len(data) < 2:
            return

        # Determine expected frequency (MEDIUM-1 fix: handle crypto 24/7)
        if interval == '1d':
            # Crypto trades 24/7, use calendar days; others use business days
            expected_freq = 'D' if asset_class == 'crypto' else 'B'
        else:
            # Intraday frequencies are the same
            freq_map = {
                '1h': 'H',
                '5m': '5T',
                '1m': 'T'
            }
            expected_freq = freq_map.get(interval, 'B')

        # Create complete date range
        date_range = pd.date_range(
            start=data.index.min(),
            end=data.index.max(),
            freq=expected_freq
        )

        # Find missing dates
        missing_dates = date_range.difference(data.index)

        if len(missing_dates) > 0:
            gap_pct = (len(missing_dates) / len(date_range)) * 100
            result.add_metric('gap_percentage', gap_pct)

            if gap_pct > 5:
                result.add_issue(
                    f"Data gaps detected: {len(missing_dates)} missing points ({gap_pct:.1f}%)",
                    AlertSeverity.WARNING
                )

    def _check_statistical_outliers(
        self,
        data: pd.DataFrame,
        result: ValidationResult,
        asset_class: str
    ) -> None:
        """Detect statistical outliers using z-score."""
        # Adjust threshold for crypto (higher volatility)
        threshold = self.config.z_score_threshold
        if asset_class == 'crypto':
            threshold = threshold * 1.5

        outliers = {}

        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            # Calculate z-scores
            mean = data[col].mean()
            std = data[col].std()

            if std == 0:
                continue

            z_scores = np.abs((data[col] - mean) / std)
            outlier_mask = z_scores > threshold

            if outlier_mask.any():
                outlier_count = outlier_mask.sum()
                outliers[col] = outlier_count

        if outliers:
            # MEDIUM-2 fix: Use actual column count instead of hardcoded 5
            validated_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
            total_cells = len(data) * len(validated_cols)
            outlier_rate = sum(outliers.values()) / total_cells * 100
            result.add_metric('outlier_rate_pct', outlier_rate)

            if outlier_rate > 2:
                result.add_issue(
                    f"High outlier rate: {outlier_rate:.2f}% - {outliers}",
                    AlertSeverity.WARNING
                )

    def _check_domain_outliers(
        self,
        data: pd.DataFrame,
        result: ValidationResult
    ) -> None:
        """Check for domain-specific outliers (price jumps, volume spikes)."""
        if len(data) < 2:
            return

        # Price jump detection
        price_changes = data['Close'].pct_change().abs()
        large_jumps = price_changes > self.config.price_jump_threshold

        if large_jumps.any():
            jump_count = large_jumps.sum()
            try:
                # Filter out inf values and get max
                finite_changes = price_changes[price_changes != np.inf]
                max_jump = finite_changes.values.max() * 100 if len(finite_changes) > 0 else 0
            except (TypeError, AttributeError):
                # Fallback for compatibility issues
                max_jump = 0

            result.add_issue(
                f"Large price jumps detected: {jump_count} instances (max: {max_jump:.1f}%)",
                AlertSeverity.WARNING
            )

        # Volume spike detection
        volume_ratios = data['Volume'] / data['Volume'].rolling(window=20, min_periods=1).mean()
        volume_spikes = volume_ratios > self.config.volume_spike_threshold

        if volume_spikes.any():
            spike_count = volume_spikes.sum()
            result.add_issue(
                f"Volume spikes detected: {spike_count} instances",
                AlertSeverity.INFO
            )

        # OHLC consistency
        invalid_ohlc = (
            (data['High'] < data['Low']) |
            (data['High'] < data['Open']) |
            (data['High'] < data['Close']) |
            (data['Low'] > data['Open']) |
            (data['Low'] > data['Close'])
        )

        if invalid_ohlc.any():
            result.add_issue(
                f"Invalid OHLC relationships: {invalid_ohlc.sum()} instances",
                AlertSeverity.CRITICAL
            )

    def _check_bid_ask_spread(
        self,
        quote: Dict[str, Any],
        result: ValidationResult,
        pair: str
    ) -> None:
        """Validate bid-ask spread is realistic."""
        bid = quote['bid']
        ask = quote['ask']
        mid = (bid + ask) / 2

        if mid == 0:
            result.add_issue(
                f"Zero mid price for {pair}",
                AlertSeverity.CRITICAL
            )
            return

        spread_pct = (ask - bid) / mid
        result.add_metric('bid_ask_spread_pct', spread_pct * 100)

        if spread_pct < self.config.min_bid_ask_spread:
            result.add_issue(
                f"Unrealistically tight spread: {spread_pct*100:.4f}%",
                AlertSeverity.WARNING
            )

        if spread_pct > self.config.max_bid_ask_spread:
            result.add_issue(
                f"Unrealistically wide spread: {spread_pct*100:.2f}%",
                AlertSeverity.WARNING
            )

    def _check_staleness(
        self,
        timestamp: str,
        result: ValidationResult,
        asset_class: str
    ) -> None:
        """Check if data is stale (too old)."""
        try:
            # Parse timestamp (handle various formats)
            if isinstance(timestamp, str):
                data_time = pd.to_datetime(timestamp)
            else:
                data_time = timestamp

            age_seconds = (datetime.now() - data_time.to_pydatetime()).total_seconds()
            result.add_metric('staleness_seconds', age_seconds)

            threshold = self.config.staleness_thresholds.get(asset_class, 900)

            if age_seconds > threshold:
                result.add_issue(
                    f"Stale data: {age_seconds/60:.1f} minutes old (threshold: {threshold/60:.1f} min)",
                    AlertSeverity.WARNING
                )
        except Exception as e:
            result.add_issue(
                f"Could not parse timestamp: {e}",
                AlertSeverity.WARNING
            )

    def _calculate_quality_metrics(
        self,
        data: pd.DataFrame,
        result: ValidationResult,
        symbol: str
    ) -> None:
        """Calculate overall data quality metrics."""
        # Completeness (non-null percentage)
        total_cells = data.shape[0] * data.shape[1]
        non_null_cells = total_cells - data.isnull().sum().sum()
        completeness = (non_null_cells / total_cells) * 100
        result.add_metric('completeness_pct', completeness)

        # Data points
        result.add_metric('data_points', len(data))

        # Date range
        result.add_metric('start_date', data.index.min().isoformat())
        result.add_metric('end_date', data.index.max().isoformat())

        # If completeness is low, flag it
        if completeness < 95:
            result.add_issue(
                f"Low completeness: {completeness:.1f}%",
                AlertSeverity.WARNING
            )

    def _process_results(
        self,
        result: ValidationResult,
        identifier: str,
        asset_class: str
    ) -> None:
        """Process validation results: log metrics and emit alerts."""
        # Log quality metrics
        if self.config.log_metrics and result.metrics:
            metrics_msg = json.dumps({
                'identifier': identifier,
                'asset_class': asset_class,
                'metrics': result.metrics,
                'timestamp': datetime.now().isoformat()
            })
            self.logger.info(f"Quality metrics: {metrics_msg}")

        # Emit alerts
        if self.config.enable_alerts and result.alerts:
            for alert in result.alerts:
                severity = alert['severity']
                message = alert['message']

                log_msg = f"[{identifier}] [{severity.upper()}] {message}"

                if severity == AlertSeverity.CRITICAL.value:
                    self.logger.error(log_msg)
                elif severity == AlertSeverity.WARNING.value:
                    self.logger.warning(log_msg)
                else:
                    self.logger.info(log_msg)
