"""Daily performance reporting for SolaraAlpha.

Generates comprehensive daily reports with P&L breakdown, risk metrics,
execution quality, and system uptime. Reports saved as markdown files.
"""

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional

from .health_checker import HealthChecker

logger = logging.getLogger(__name__)


class PerformanceReporter:
    """Generates daily performance reports.

    Creates comprehensive markdown reports including:
    - P&L breakdown by strategy and asset class
    - Risk metrics (drawdown, VaR, Sharpe)
    - Execution quality (fill rates, costs, slippage)
    - System uptime and error count
    - Top positions and exposures

    Example:
        >>> reporter = PerformanceReporter()
        >>> reporter.generate_daily_report()
        >>> print("Report saved to logs/reports/2025-10-27.md")
    """

    def __init__(
        self,
        reports_dir: str = "logs/reports",
        smtp_config: Optional[Dict] = None,
    ) -> None:
        """Initialize performance reporter.

        Args:
            reports_dir: Directory to save reports
            smtp_config: Optional SMTP config for email delivery
        """
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.smtp_config = smtp_config

        self.health_checker = HealthChecker()

    def generate_daily_report(
        self,
        risk_manager: Optional[object] = None,
        exec_metrics: Optional[object] = None,
        send_email: bool = False,
    ) -> Path:
        """Generate daily performance report.

        Args:
            risk_manager: Risk manager instance for metrics
            exec_metrics: Execution metrics instance
            send_email: Whether to email the report

        Returns:
            Path to generated report file
        """
        try:
            timestamp = datetime.now(timezone.utc)
            date_str = timestamp.strftime("%Y-%m-%d")

            # Collect data
            health_results = self.health_checker.check_all()

            risk_data = {}
            positions = []
            if risk_manager:
                risk_data = self._get_risk_metrics(risk_manager)
                positions = self._get_top_positions(risk_manager, limit=5)

            exec_data = {}
            if exec_metrics:
                exec_data = self._get_execution_metrics(exec_metrics)

            system_data = self._get_system_metrics(health_results)

            # Generate markdown
            markdown = self._generate_markdown(
                timestamp=timestamp,
                risk_data=risk_data,
                exec_data=exec_data,
                system_data=system_data,
                positions=positions,
            )

            # Save report
            report_path = self.reports_dir / f"{date_str}.md"
            report_path.write_text(markdown, encoding="utf-8")

            logger.info(
                "Daily report generated",
                extra={
                    "context": {
                        "report_path": str(report_path),
                        "date": date_str,
                    }
                },
            )

            # Send email if requested
            if send_email and self.smtp_config:
                self._send_email_report(markdown, date_str)

            return report_path

        except Exception as e:
            logger.error(
                "Daily report generation failed",
                extra={"context": {"error": str(e)}},
                exc_info=True,
            )
            raise

    def _get_risk_metrics(self, risk_manager: object) -> Dict:
        """Extract risk metrics.

        Args:
            risk_manager: Risk manager instance

        Returns:
            Dict of risk metrics
        """
        try:
            metrics = risk_manager.calculate_risk_metrics()

            return {
                "total_exposure": metrics.total_exposure,
                "net_exposure": metrics.net_exposure,
                "gross_exposure": metrics.gross_exposure,
                "var_95": metrics.var_95,
                "var_99": metrics.var_99,
                "max_drawdown": metrics.max_drawdown,
                "sharpe_ratio": metrics.sharpe_ratio,
                "portfolio_value": risk_manager.portfolio_tracker.portfolio_value,
            }
        except Exception as e:
            logger.warning(
                "Failed to get risk metrics", extra={"context": {"error": str(e)}}
            )
            return {}

    def _get_top_positions(
        self, risk_manager: object, limit: int = 5
    ) -> List[Dict]:
        """Get top positions by absolute value.

        Args:
            risk_manager: Risk manager instance
            limit: Number of positions to return

        Returns:
            List of position dicts
        """
        try:
            positions = risk_manager.get_all_positions()

            # Sort by absolute market value
            sorted_positions = sorted(
                positions,
                key=lambda p: abs(p.quantity * p.average_price),
                reverse=True,
            )

            return [
                {
                    "symbol": p.symbol,
                    "asset_class": p.asset_class,
                    "quantity": p.quantity,
                    "avg_price": p.average_price,
                    "market_value": p.quantity * p.average_price,
                    "unrealized_pnl": p.unrealized_pnl,
                }
                for p in sorted_positions[:limit]
            ]

        except Exception as e:
            logger.warning(
                "Failed to get top positions", extra={"context": {"error": str(e)}}
            )
            return []

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
                "cancelled_orders": summary.get("cancelled_orders", 0),
                "rejected_orders": summary.get("rejected_orders", 0),
                "fill_rate_pct": summary.get("fill_rate_pct", 0.0),
                "avg_fill_time_ms": summary.get("avg_fill_time_ms", 0.0),
                "avg_slippage_bps": summary.get("avg_slippage_bps", 0.0),
                "total_commissions": summary.get("total_commissions", 0.0),
            }
        except Exception as e:
            logger.warning(
                "Failed to get execution metrics", extra={"context": {"error": str(e)}}
            )
            return {}

    def _get_system_metrics(self, health_results: List) -> Dict:
        """Extract system metrics from health checks.

        Args:
            health_results: Health check results

        Returns:
            Dict of system metrics
        """
        from .health_checker import HealthStatus

        unhealthy_count = sum(
            1 for r in health_results if r.status == HealthStatus.UNHEALTHY
        )
        degraded_count = sum(
            1 for r in health_results if r.status == HealthStatus.DEGRADED
        )
        healthy_count = sum(
            1 for r in health_results if r.status == HealthStatus.HEALTHY
        )

        total = len(health_results)
        uptime_pct = (healthy_count / total * 100) if total > 0 else 0.0

        return {
            "uptime_pct": uptime_pct,
            "healthy_count": healthy_count,
            "degraded_count": degraded_count,
            "unhealthy_count": unhealthy_count,
        }

    def _generate_markdown(
        self,
        timestamp: datetime,
        risk_data: Dict,
        exec_data: Dict,
        system_data: Dict,
        positions: List[Dict],
    ) -> str:
        """Generate markdown report.

        Args:
            timestamp: Report generation time
            risk_data: Risk metrics
            exec_data: Execution metrics
            system_data: System health metrics
            positions: Top positions

        Returns:
            Markdown report string
        """
        date_str = timestamp.strftime("%Y-%m-%d")
        time_str = timestamp.strftime("%H:%M:%S UTC")

        # Build P&L section
        pnl_section = ""
        if risk_data:
            portfolio_value = risk_data.get("portfolio_value", 0.0)
            total_exposure = risk_data.get("total_exposure", 0.0)
            net_exposure = risk_data.get("net_exposure", 0.0)

            pnl_section = f"""## P&L Overview

- **Portfolio Value**: ${portfolio_value:,.2f}
- **Total Exposure**: ${total_exposure:,.2f}
- **Net Exposure**: ${net_exposure:,.2f}
"""

        # Build risk metrics section
        risk_section = ""
        if risk_data:
            risk_section = f"""## Risk Metrics

- **VaR (95%)**: ${risk_data.get('var_95', 0):,.2f}
- **VaR (99%)**: ${risk_data.get('var_99', 0):,.2f}
- **Max Drawdown**: {risk_data.get('max_drawdown', 0):.2%}
- **Sharpe Ratio**: {risk_data.get('sharpe_ratio', 0):.3f}
- **Gross Exposure**: ${risk_data.get('gross_exposure', 0):,.2f}
"""

        # Build execution section
        exec_section = ""
        if exec_data:
            exec_section = f"""## Execution Quality

- **Total Orders**: {exec_data.get('total_orders', 0)}
- **Filled**: {exec_data.get('filled_orders', 0)}
- **Cancelled**: {exec_data.get('cancelled_orders', 0)}
- **Rejected**: {exec_data.get('rejected_orders', 0)}
- **Fill Rate**: {exec_data.get('fill_rate_pct', 0):.1f}%
- **Avg Fill Time**: {exec_data.get('avg_fill_time_ms', 0):.1f}ms
- **Avg Slippage**: {exec_data.get('avg_slippage_bps', 0):.2f} bps
- **Total Commissions**: ${exec_data.get('total_commissions', 0):.2f}
"""

        # Build positions section
        positions_section = ""
        if positions:
            positions_section = "## Top 5 Positions\n\n"
            positions_section += "| Symbol | Asset Class | Quantity | Avg Price | Market Value | Unrealized P&L |\n"
            positions_section += "|--------|-------------|----------|-----------|--------------|----------------|\n"

            for pos in positions:
                positions_section += (
                    f"| {pos['symbol']} "
                    f"| {pos['asset_class']} "
                    f"| {pos['quantity']:,} "
                    f"| ${pos['avg_price']:.2f} "
                    f"| ${pos['market_value']:,.2f} "
                    f"| ${pos['unrealized_pnl']:,.2f} |\n"
                )

        # Build system health section
        system_section = f"""## System Health

- **Uptime**: {system_data.get('uptime_pct', 0):.1f}%
- **Healthy Components**: {system_data.get('healthy_count', 0)}
- **Degraded Components**: {system_data.get('degraded_count', 0)}
- **Unhealthy Components**: {system_data.get('unhealthy_count', 0)}
"""

        # Combine sections
        markdown = f"""# SolaraAlpha Daily Report - {date_str}

Generated: {time_str}

---

{pnl_section}

{risk_section}

{exec_section}

{positions_section}

{system_section}

---

*This report is automatically generated by SolaraAlpha monitoring system.*
"""

        return markdown

    def _send_email_report(self, markdown: str, date_str: str) -> None:
        """Send report via email.

        Args:
            markdown: Report markdown content
            date_str: Report date string
        """
        try:
            # Validate SMTP config
            required_keys = [
                "host",
                "port",
                "username",
                "password",
                "from_email",
                "to_emails",
            ]
            if not all(k in self.smtp_config for k in required_keys):
                logger.warning(
                    "Email report skipped: incomplete SMTP configuration",
                    extra={"context": {"missing_keys": required_keys}},
                )
                return

            # Build email
            msg = MIMEMultipart()
            msg["From"] = self.smtp_config["from_email"]
            msg["To"] = ", ".join(self.smtp_config["to_emails"])
            msg["Subject"] = f"SolaraAlpha Daily Report - {date_str}"

            msg.attach(MIMEText(markdown, "plain"))

            # Send email
            with smtplib.SMTP(
                self.smtp_config["host"], self.smtp_config["port"]
            ) as server:
                server.starttls()
                server.login(
                    self.smtp_config["username"], self.smtp_config["password"]
                )
                server.send_message(msg)

            logger.info(
                "Daily report emailed",
                extra={
                    "context": {
                        "date": date_str,
                        "recipients": self.smtp_config["to_emails"],
                    }
                },
            )

        except Exception as e:
            logger.error(
                "Failed to send email report",
                extra={"context": {"error": str(e)}},
                exc_info=True,
            )
