"""
Dashboard Data Provider

Aggregates data from risk management, monitoring, and storage layers
for unified dashboard API access. Provides real-time system status,
risk metrics, alerts, and P&L data.
"""

from datetime import datetime
from typing import Dict, List, Optional, Any

from src.risk.risk_manager import RiskManager
from src.monitoring.health_checker import HealthChecker
from src.monitoring.alert_store import AlertStore
from src.monitoring.pnl_store import PnLStore
from src.monitoring.alerting import AlertSeverity


class DashboardDataProvider:
    """Centralized data aggregation for IRM dashboard."""

    def __init__(
        self,
        risk_manager: RiskManager,
        health_checker: HealthChecker,
        alert_store: AlertStore,
        pnl_store: PnLStore
    ):
        """Initialize dashboard data provider.

        Args:
            risk_manager: Risk management orchestrator
            health_checker: System health checker
            alert_store: Alert persistence layer
            pnl_store: P&L time-series storage
        """
        self.risk_manager = risk_manager
        self.health_checker = health_checker
        self.alert_store = alert_store
        self.pnl_store = pnl_store

    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status.

        Returns:
            Dict with circuit breaker, positions, exposure, health
        """
        # Get risk manager system status
        risk_status = self.risk_manager.get_system_status()

        # Get health check results
        health_results = self.health_checker.check_all()
        health_status = {
            "overall": "healthy" if all(r.status.value == "healthy" for r in health_results) else "degraded",
            "components": [
                {
                    "name": r.component,
                    "status": r.status.value,
                    "message": r.message,
                    "metrics": r.metrics
                }
                for r in health_results
            ]
        }

        # Get recent alert summary
        alert_counts = self.alert_store.get_alert_counts_by_severity(hours=1)

        return {
            "timestamp": datetime.now().isoformat(),
            "circuit_breaker": risk_status["circuit_breaker"],
            "positions": risk_status["positions"],
            "exposure": risk_status["exposure"],
            "health": health_status,
            "recent_alerts": alert_counts,
            "total_positions": len(risk_status["positions"])
        }

    def get_risk_metrics(self) -> Dict[str, Any]:
        """Get current risk metrics (VaR, CVaR, Sharpe, etc.).

        Returns:
            Dict with all risk metrics or empty dict if insufficient data
        """
        try:
            metrics = self.risk_manager.calculate_risk_metrics()
            if metrics is None:
                return {
                    "status": "insufficient_data",
                    "message": "Not enough historical data for risk metrics calculation",
                    "timestamp": datetime.now().isoformat()
                }

            return {
                "status": "available",
                "timestamp": metrics.timestamp.isoformat(),
                "var_95": metrics.var_95,
                "cvar_95": metrics.cvar_95,
                "sharpe_30d": metrics.sharpe_30d,
                "sharpe_90d": metrics.sharpe_90d,
                "sharpe_365d": metrics.sharpe_365d,
                "beta": metrics.beta,
                "volatility": metrics.volatility,
                "max_drawdown": metrics.max_drawdown
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def get_circuit_breaker_state(self) -> Dict[str, Any]:
        """Get circuit breaker state with distance to trigger.

        Returns:
            Dict with CB state and trigger distance
        """
        state = self.risk_manager.get_circuit_breaker_state()
        distance = self.risk_manager.circuit_breaker.get_distance_to_trigger()

        return {
            "is_active": state.is_active,
            "high_water_mark": state.high_water_mark,
            "current_value": state.current_value,
            "drawdown_pct": state.drawdown_pct,
            "trigger_timestamp": state.trigger_timestamp.isoformat() if state.trigger_timestamp else None,
            "trigger_reason": state.trigger_reason,
            "distance_to_trigger": distance,
            "timestamp": datetime.now().isoformat()
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all current positions.

        Returns:
            List of position dicts with full details
        """
        positions = self.risk_manager.position_tracker.get_all_positions()

        return [
            {
                "symbol": pos.symbol,
                "asset_class": pos.asset_class,
                "strategy_id": pos.strategy_id,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "current_price": pos.current_price,
                "market_value": pos.market_value,
                "cost_basis": pos.cost_basis,
                "unrealized_pnl": pos.unrealized_pnl,
                "unrealized_pnl_pct": pos.unrealized_pnl_pct,
                "is_long": pos.is_long,
                "is_short": pos.is_short,
                "timestamp": pos.timestamp.isoformat()
            }
            for pos in positions
        ]

    def get_exposure_summary(self) -> Dict[str, Any]:
        """Get portfolio exposure breakdown.

        Returns:
            Dict with gross, net, long, short exposure
        """
        exposure = self.risk_manager.get_portfolio_exposure()

        return {
            "gross_exposure": exposure["gross"],
            "net_exposure": exposure["net"],
            "long_exposure": exposure["long"],
            "short_exposure": exposure["short"],
            "total_pnl": exposure["total_pnl"],
            "timestamp": datetime.now().isoformat()
        }

    def get_limit_utilization(self) -> Dict[str, Any]:
        """Get limit utilization percentages.

        Returns:
            Dict with per-strategy, per-asset, gross, net limit usage
        """
        # Get current exposure
        exposure = self.risk_manager.get_portfolio_exposure()

        # Get portfolio value for percentage calculations
        portfolio_value = self.risk_manager.circuit_breaker.current_value

        if portfolio_value == 0:
            return {
                "per_strategy": {},
                "per_asset": {},
                "gross_exposure": 0,
                "net_exposure": 0,
                "timestamp": datetime.now().isoformat()
            }

        # Calculate gross and net exposure utilization
        gross_limit = self.risk_manager.config.get_gross_exposure_limit_pct()
        net_limit = self.risk_manager.config.get_net_exposure_limit_pct()

        gross_utilization = (exposure["gross"] / portfolio_value * 100) / gross_limit * 100
        net_utilization = abs(exposure["net"] / portfolio_value * 100) / net_limit * 100

        # Get per-strategy utilization
        strategy_limit = self.risk_manager.config.get_per_strategy_limit_pct()
        strategy_exposures = {}
        for pos in self.risk_manager.position_tracker.get_all_positions():
            strategy_exp = self.risk_manager.position_tracker.get_strategy_exposure(pos.strategy_id)
            strategy_pct = abs(strategy_exp["net"] / portfolio_value * 100)
            strategy_utilization = (strategy_pct / strategy_limit) * 100
            strategy_exposures[pos.strategy_id] = {
                "exposure": strategy_exp["net"],
                "exposure_pct": strategy_pct,
                "limit_pct": strategy_limit,
                "utilization_pct": strategy_utilization
            }

        # Get per-asset utilization
        asset_limit = self.risk_manager.config.get_per_asset_limit_pct()
        asset_exposures = {}
        for pos in self.risk_manager.position_tracker.get_all_positions():
            symbol_exp = self.risk_manager.position_tracker.get_symbol_exposure(pos.symbol)
            asset_pct = abs(symbol_exp["net"] / portfolio_value * 100)
            asset_utilization = (asset_pct / asset_limit) * 100
            asset_exposures[pos.symbol] = {
                "exposure": symbol_exp["net"],
                "exposure_pct": asset_pct,
                "limit_pct": asset_limit,
                "utilization_pct": asset_utilization
            }

        return {
            "per_strategy": strategy_exposures,
            "per_asset": asset_exposures,
            "gross_exposure": {
                "current_pct": exposure["gross"] / portfolio_value * 100,
                "limit_pct": gross_limit,
                "utilization_pct": gross_utilization
            },
            "net_exposure": {
                "current_pct": abs(exposure["net"] / portfolio_value * 100),
                "limit_pct": net_limit,
                "utilization_pct": net_utilization
            },
            "timestamp": datetime.now().isoformat()
        }

    def get_recent_alerts(
        self,
        limit: int = 50,
        severity: Optional[AlertSeverity] = None
    ) -> List[Dict[str, Any]]:
        """Get recent alerts with optional severity filter.

        Args:
            limit: Maximum number of alerts
            severity: Filter by severity level

        Returns:
            List of alert dicts
        """
        alerts = self.alert_store.get_recent_alerts(limit=limit, severity=severity)

        return [
            {
                "component": alert.component,
                "severity": alert.severity.value,
                "message": alert.message,
                "context": alert.context,
                "timestamp": alert.timestamp.isoformat()
            }
            for alert in alerts
        ]

    def get_alert_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get alert summary statistics.

        Args:
            hours: Time window in hours

        Returns:
            Dict with counts by severity and component
        """
        by_severity = self.alert_store.get_alert_counts_by_severity(hours=hours)
        by_component = self.alert_store.get_alert_counts_by_component(hours=hours)

        return {
            "by_severity": by_severity,
            "by_component": by_component,
            "time_window_hours": hours,
            "timestamp": datetime.now().isoformat()
        }

    def get_pnl_history(self, hours: int = 24) -> Dict[str, Any]:
        """Get portfolio P&L history.

        Args:
            hours: Time window in hours

        Returns:
            Dict with P&L time series data
        """
        portfolio_pnl = self.pnl_store.get_portfolio_pnl_history(hours=hours)

        return {
            "time_series": portfolio_pnl,
            "time_window_hours": hours,
            "timestamp": datetime.now().isoformat()
        }

    def get_risk_metrics_history(self, hours: int = 24) -> Dict[str, Any]:
        """Get risk metrics history.

        Args:
            hours: Time window in hours

        Returns:
            Dict with risk metrics time series
        """
        metrics_history = self.pnl_store.get_risk_metrics_history(hours=hours)

        return {
            "time_series": metrics_history,
            "time_window_hours": hours,
            "timestamp": datetime.now().isoformat()
        }
