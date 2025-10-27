"""Risk configuration manager for SolaraAlpha.

Loads and validates risk limits, circuit breaker thresholds, and metric
calculation parameters from YAML configuration files.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)


class RiskConfigManager:
    """Manages risk management configuration.

    Loads risk limits, drawdown thresholds, and metric calculation parameters
    from YAML files. Provides validation and accessor methods.

    Example:
        >>> config = RiskConfigManager()
        >>> max_dd = config.get_max_drawdown_pct()
        >>> print(f"Circuit breaker at {max_dd}% drawdown")
    """

    def __init__(self, config_path: str | None = None) -> None:
        """Initialize risk configuration manager.

        Args:
            config_path: Path to risk_limits.yaml. If None, uses default.
        """
        if config_path is None:
            repo_root = Path(__file__).parent.parent.parent.parent
            config_path = repo_root / "config" / "risk_limits.yaml"
        else:
            config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(
                f"Risk configuration file not found: {config_path}"
            )

        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        logger.info(f"Loaded risk configuration from {config_path}")
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate required configuration keys exist."""
        required_sections = [
            "position_limits",
            "drawdown",
            "risk_metrics",
            "alerts",
        ]

        for section in required_sections:
            if section not in self.config:
                raise ValueError(
                    f"Missing required config section: {section}"
                )

    # Position Limits
    def get_per_strategy_limit_pct(self) -> float:
        """Get per-strategy position limit as percentage of portfolio."""
        return self.config["position_limits"]["per_strategy_pct"]

    def get_per_asset_limit_pct(self) -> float:
        """Get per-asset position limit as percentage of portfolio."""
        return self.config["position_limits"]["per_asset_pct"]

    def get_gross_exposure_limit_pct(self) -> float:
        """Get gross exposure limit (long + short) as percentage."""
        return self.config["position_limits"].get(
            "gross_exposure_pct", 200.0
        )

    def get_net_exposure_limit_pct(self) -> float:
        """Get net exposure limit (long - short) as percentage."""
        return self.config["position_limits"].get("net_exposure_pct", 20.0)

    # Drawdown Controls
    def get_max_drawdown_pct(self) -> float:
        """Get maximum drawdown threshold for circuit breaker."""
        return self.config["drawdown"]["max_drawdown_pct"]

    def get_hwm_persist_enabled(self) -> bool:
        """Check if high-water mark persistence is enabled."""
        return self.config["drawdown"].get("high_water_mark_persist", True)

    def get_hwm_storage_path(self) -> Path:
        """Get high-water mark storage path."""
        repo_root = Path(__file__).parent.parent.parent.parent
        path_str = self.config["drawdown"].get(
            "hwm_storage_path", "data/risk/high_water_mark.json"
        )
        return repo_root / path_str

    # Risk Metrics
    def get_var_confidence(self) -> float:
        """Get Value at Risk confidence level."""
        return self.config["risk_metrics"]["var_confidence"]

    def get_var_lookback_days(self) -> int:
        """Get VaR lookback window in days."""
        return self.config["risk_metrics"]["var_lookback_days"]

    def get_cvar_confidence(self) -> float:
        """Get Conditional VaR (Expected Shortfall) confidence level."""
        return self.config["risk_metrics"].get("cvar_confidence", 0.95)

    def get_sharpe_windows(self) -> List[int]:
        """Get Sharpe ratio calculation windows in days."""
        return self.config["risk_metrics"]["sharpe_windows"]

    def get_benchmark_symbol(self) -> str:
        """Get market benchmark symbol for beta calculation."""
        return self.config["risk_metrics"]["benchmark_symbol"]

    def get_benchmark_asset_class(self) -> str:
        """Get market benchmark asset class."""
        return self.config["risk_metrics"].get(
            "benchmark_asset_class", "equities"
        )

    def get_min_history_days(self) -> int:
        """Get minimum historical data required for calculations."""
        return self.config["risk_metrics"].get("min_history_days", 30)

    # Alerting
    def get_log_limit_breaches(self) -> bool:
        """Check if limit breaches should be logged."""
        return self.config["alerts"].get("log_all_limit_breaches", True)

    def get_log_risk_metrics(self) -> bool:
        """Check if risk metrics should be logged."""
        return self.config["alerts"].get("log_risk_metrics", True)

    def get_alert_on_circuit_breaker(self) -> bool:
        """Check if circuit breaker activation should trigger alert."""
        return self.config["alerts"].get("alert_on_circuit_breaker", True)

    # Full config access
    def get_config(self) -> Dict[str, Any]:
        """Get full configuration dictionary."""
        return self.config
