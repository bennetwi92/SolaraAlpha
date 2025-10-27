"""Performance dashboard generator for SolaraAlpha.

Generates HTML-based dashboard displaying system health, P&L, risk metrics,
and execution statistics. Refreshed every 5 minutes for real-time monitoring.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .health_checker import HealthCheckResult, HealthChecker, HealthStatus

logger = logging.getLogger(__name__)


class DashboardGenerator:
    """Generates HTML performance dashboard.

    Creates a live dashboard showing:
    - System health status
    - Active strategies and positions
    - P&L metrics (daily, weekly, monthly)
    - Risk metrics (exposure, VaR, utilization)
    - Execution statistics (fill rates, latency, slippage)

    Example:
        >>> generator = DashboardGenerator()
        >>> generator.generate_dashboard()
        >>> print("Dashboard available at logs/dashboard.html")
    """

    def __init__(self, output_path: str = "logs/dashboard.html") -> None:
        """Initialize dashboard generator.

        Args:
            output_path: Path to output HTML file
        """
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        self.health_checker = HealthChecker()

    def generate_dashboard(
        self,
        risk_manager: Optional[object] = None,
        exec_metrics: Optional[object] = None,
    ) -> None:
        """Generate and save HTML dashboard.

        Args:
            risk_manager: Risk manager instance for metrics
            exec_metrics: Execution metrics instance for statistics
        """
        try:
            # Collect data
            health_results = self.health_checker.check_all()
            timestamp = datetime.now(timezone.utc)

            # Get risk metrics if available
            risk_data = {}
            if risk_manager:
                risk_data = self._get_risk_metrics(risk_manager)

            # Get execution metrics if available
            exec_data = {}
            if exec_metrics:
                exec_data = self._get_execution_metrics(exec_metrics)

            # Generate HTML
            html = self._generate_html(timestamp, health_results, risk_data, exec_data)

            # Write to file
            self.output_path.write_text(html, encoding="utf-8")

            logger.info(
                "Dashboard generated successfully",
                extra={
                    "context": {
                        "output_path": str(self.output_path),
                        "timestamp": timestamp.isoformat(),
                    }
                },
            )

        except Exception as e:
            logger.error(
                "Dashboard generation failed",
                extra={"context": {"error": str(e)}},
                exc_info=True,
            )

    def _get_risk_metrics(self, risk_manager: object) -> Dict:
        """Extract risk metrics from risk manager.

        Args:
            risk_manager: Risk manager instance

        Returns:
            Dict of risk metrics
        """
        try:
            metrics = risk_manager.calculate_risk_metrics()
            positions = risk_manager.get_all_positions()

            return {
                "total_exposure": metrics.total_exposure,
                "var_95": metrics.var_95,
                "max_drawdown": metrics.max_drawdown,
                "sharpe_ratio": metrics.sharpe_ratio,
                "position_count": len(positions),
                "circuit_breaker_active": (
                    risk_manager.circuit_breaker.get_state().name != "TRIPPED"
                ),
            }
        except Exception as e:
            logger.warning(
                "Failed to get risk metrics", extra={"context": {"error": str(e)}}
            )
            return {}

    def _get_execution_metrics(self, exec_metrics: object) -> Dict:
        """Extract execution metrics.

        Args:
            exec_metrics: Execution metrics instance

        Returns:
            Dict of execution metrics
        """
        try:
            summary = exec_metrics.get_daily_summary()

            return {
                "total_orders": summary.get("total_orders", 0),
                "filled_orders": summary.get("filled_orders", 0),
                "fill_rate_pct": summary.get("fill_rate_pct", 0.0),
                "avg_slippage_bps": summary.get("avg_slippage_bps", 0.0),
                "total_commissions": summary.get("total_commissions", 0.0),
            }
        except Exception as e:
            logger.warning(
                "Failed to get execution metrics", extra={"context": {"error": str(e)}}
            )
            return {}

    def _generate_html(
        self,
        timestamp: datetime,
        health_results: List[HealthCheckResult],
        risk_data: Dict,
        exec_data: Dict,
    ) -> str:
        """Generate HTML dashboard.

        Args:
            timestamp: Dashboard generation time
            health_results: Health check results
            risk_data: Risk metrics
            exec_data: Execution metrics

        Returns:
            HTML string
        """
        # Calculate overall health
        unhealthy_count = sum(
            1 for r in health_results if r.status == HealthStatus.UNHEALTHY
        )
        degraded_count = sum(
            1 for r in health_results if r.status == HealthStatus.DEGRADED
        )

        if unhealthy_count > 0:
            overall_status = "UNHEALTHY"
            status_color = "#dc3545"
        elif degraded_count > 0:
            overall_status = "DEGRADED"
            status_color = "#ffc107"
        else:
            overall_status = "HEALTHY"
            status_color = "#28a745"

        # Build health status rows
        health_rows = ""
        for result in health_results:
            if result.status == HealthStatus.HEALTHY:
                badge_color = "#28a745"
                badge_text = "HEALTHY"
            elif result.status == HealthStatus.DEGRADED:
                badge_color = "#ffc107"
                badge_text = "DEGRADED"
            else:
                badge_color = "#dc3545"
                badge_text = "UNHEALTHY"

            metrics_str = ""
            if result.metrics:
                metrics_str = " | ".join(
                    f"{k}: {v:.2f}" for k, v in result.metrics.items()
                )

            health_rows += f"""
                <tr>
                    <td>{result.component}</td>
                    <td><span class="badge" style="background-color: {badge_color};">{badge_text}</span></td>
                    <td>{result.message}</td>
                    <td style="font-size: 0.85em; color: #666;">{metrics_str}</td>
                </tr>
            """

        # Build risk metrics section
        risk_section = ""
        if risk_data:
            risk_section = f"""
                <h2>Risk Metrics</h2>
                <div class="card">
                    <div class="metric-grid">
                        <div class="metric">
                            <div class="metric-label">Total Exposure</div>
                            <div class="metric-value">${risk_data.get('total_exposure', 0):,.2f}</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">VaR (95%)</div>
                            <div class="metric-value">${risk_data.get('var_95', 0):,.2f}</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Max Drawdown</div>
                            <div class="metric-value">{risk_data.get('max_drawdown', 0):.2%}</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Sharpe Ratio</div>
                            <div class="metric-value">{risk_data.get('sharpe_ratio', 0):.2f}</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Active Positions</div>
                            <div class="metric-value">{risk_data.get('position_count', 0)}</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Circuit Breaker</div>
                            <div class="metric-value">{'ACTIVE' if risk_data.get('circuit_breaker_active') else 'TRIPPED'}</div>
                        </div>
                    </div>
                </div>
            """

        # Build execution metrics section
        exec_section = ""
        if exec_data:
            fill_rate = exec_data.get("fill_rate_pct", 0.0)
            fill_color = "#28a745" if fill_rate >= 95 else "#ffc107"

            exec_section = f"""
                <h2>Execution Statistics</h2>
                <div class="card">
                    <div class="metric-grid">
                        <div class="metric">
                            <div class="metric-label">Total Orders</div>
                            <div class="metric-value">{exec_data.get('total_orders', 0)}</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Filled Orders</div>
                            <div class="metric-value">{exec_data.get('filled_orders', 0)}</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Fill Rate</div>
                            <div class="metric-value" style="color: {fill_color};">{fill_rate:.1f}%</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Avg Slippage</div>
                            <div class="metric-value">{exec_data.get('avg_slippage_bps', 0):.2f} bps</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Total Commissions</div>
                            <div class="metric-value">${exec_data.get('total_commissions', 0):.2f}</div>
                        </div>
                    </div>
                </div>
            """

        # Generate HTML
        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="300">
    <title>SolaraAlpha Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: #f8f9fa;
            color: #212529;
            padding: 20px;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .header {{
            background: white;
            padding: 30px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .header h1 {{
            font-size: 2em;
            margin-bottom: 10px;
        }}
        .header .timestamp {{
            color: #6c757d;
            font-size: 0.9em;
        }}
        .status-banner {{
            display: inline-block;
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
            margin-left: 20px;
            background-color: {status_color};
            color: white;
        }}
        .card {{
            background: white;
            padding: 25px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h2 {{
            font-size: 1.5em;
            margin-bottom: 15px;
            color: #495057;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            text-align: left;
            padding: 12px;
            border-bottom: 1px solid #dee2e6;
        }}
        th {{
            background: #f8f9fa;
            font-weight: 600;
            color: #495057;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.85em;
            font-weight: bold;
            color: white;
        }}
        .metric-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
        }}
        .metric {{
            text-align: center;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 6px;
        }}
        .metric-label {{
            font-size: 0.85em;
            color: #6c757d;
            margin-bottom: 8px;
            text-transform: uppercase;
            font-weight: 600;
        }}
        .metric-value {{
            font-size: 1.8em;
            font-weight: bold;
            color: #212529;
        }}
        .auto-refresh {{
            text-align: center;
            padding: 10px;
            color: #6c757d;
            font-size: 0.85em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>
                SolaraAlpha Dashboard
                <span class="status-banner">{overall_status}</span>
            </h1>
            <div class="timestamp">
                Last updated: {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}
            </div>
        </div>

        <h2>System Health</h2>
        <div class="card">
            <table>
                <thead>
                    <tr>
                        <th>Component</th>
                        <th>Status</th>
                        <th>Message</th>
                        <th>Metrics</th>
                    </tr>
                </thead>
                <tbody>
                    {health_rows}
                </tbody>
            </table>
        </div>

        {risk_section}

        {exec_section}

        <div class="auto-refresh">
            Auto-refreshes every 5 minutes
        </div>
    </div>
</body>
</html>
        """

        return html
