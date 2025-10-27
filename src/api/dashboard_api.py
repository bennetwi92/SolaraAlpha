"""
IRM Dashboard API

FastAPI backend providing REST endpoints and WebSocket support for
real-time monitoring dashboard. Includes manual override capabilities
with full audit trail.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from pathlib import Path
import asyncio

from src.risk.risk_manager import RiskManager
from src.monitoring.health_checker import HealthChecker
from src.monitoring.alerting import AlertManager, AlertSeverity
from src.monitoring.alert_store import AlertStore
from src.monitoring.pnl_store import PnLStore
from src.monitoring.dashboard_data_provider import DashboardDataProvider
from src.monitoring.audit_logger import AuditLogger, OverrideAction


# Request/Response Models
class CircuitBreakerTriggerRequest(BaseModel):
    """Request to manually trigger circuit breaker."""
    operator: str = Field(..., description="Operator performing action", min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_-]+$')
    reason: str = Field(..., description="Justification for manual trigger", min_length=20, max_length=500)

    @validator('reason')
    def reason_must_be_substantive(cls, v):
        if len(v.split()) < 5:
            raise ValueError('Reason must contain at least 5 words')
        return v


class CircuitBreakerResetRequest(BaseModel):
    """Request to reset circuit breaker."""
    operator: str = Field(..., description="Operator performing action", min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_-]+$')
    justification: str = Field(..., description="Justification for reset", min_length=20, max_length=500)

    @validator('justification')
    def justification_must_be_substantive(cls, v):
        if len(v.split()) < 5:
            raise ValueError('Justification must contain at least 5 words')
        return v


# Initialize FastAPI app
app = FastAPI(
    title="SolaraAlpha IRM Dashboard API",
    description="Integrated Risk Management monitoring and control interface",
    version="1.0.0"
)

# CORS middleware for frontend access
# NOTE: In production, restrict to specific domains:
# allow_origins=["https://dashboard.solaraalpha.internal"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],  # Restrict to localhost for development
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

# Global instances (initialized on startup)
risk_manager: Optional[RiskManager] = None
health_checker: Optional[HealthChecker] = None
alert_manager: Optional[AlertManager] = None
alert_store: Optional[AlertStore] = None
pnl_store: Optional[PnLStore] = None
data_provider: Optional[DashboardDataProvider] = None
audit_logger: Optional[AuditLogger] = None

# WebSocket connection manager
class ConnectionManager:
    """Manage WebSocket connections for real-time updates."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                # Connection lost, mark for removal
                dead_connections.append(connection)

        # Clean up dead connections
        for conn in dead_connections:
            if conn in self.active_connections:
                self.active_connections.remove(conn)


manager = ConnectionManager()


def initialize_components(
    rm: RiskManager,
    hc: HealthChecker,
    am: AlertManager
):
    """Initialize global component instances.

    Args:
        rm: Risk manager instance
        hc: Health checker instance
        am: Alert manager instance
    """
    global risk_manager, health_checker, alert_manager
    global alert_store, pnl_store, data_provider, audit_logger

    risk_manager = rm
    health_checker = hc
    alert_manager = am

    # Initialize storage layers
    alert_store = AlertStore()
    pnl_store = PnLStore()
    audit_logger = AuditLogger()

    # Initialize data provider
    data_provider = DashboardDataProvider(
        risk_manager=risk_manager,
        health_checker=health_checker,
        alert_store=alert_store,
        pnl_store=pnl_store
    )


# Health check endpoint
@app.get("/api/health")
async def get_health():
    """API health check."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# System status endpoints
@app.get("/api/status")
async def get_system_status():
    """Get comprehensive system status."""
    if data_provider is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    return data_provider.get_system_status()


@app.get("/api/risk/metrics")
async def get_risk_metrics():
    """Get current risk metrics (VaR, CVaR, Sharpe, etc.)."""
    if data_provider is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    return data_provider.get_risk_metrics()


@app.get("/api/circuit-breaker")
async def get_circuit_breaker_state():
    """Get circuit breaker state."""
    if data_provider is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    return data_provider.get_circuit_breaker_state()


@app.get("/api/positions")
async def get_positions():
    """Get all current positions."""
    if data_provider is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    return data_provider.get_positions()


@app.get("/api/exposure")
async def get_exposure():
    """Get portfolio exposure summary."""
    if data_provider is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    return data_provider.get_exposure_summary()


@app.get("/api/limits")
async def get_limit_utilization():
    """Get risk limit utilization."""
    if data_provider is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    return data_provider.get_limit_utilization()


# Alert endpoints
@app.get("/api/alerts/recent")
async def get_recent_alerts(limit: int = 50, severity: Optional[str] = None):
    """Get recent alerts."""
    if data_provider is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    severity_filter = AlertSeverity(severity) if severity else None
    return data_provider.get_recent_alerts(limit=limit, severity=severity_filter)


@app.get("/api/alerts/summary")
async def get_alert_summary(hours: int = 24):
    """Get alert summary statistics."""
    if data_provider is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    return data_provider.get_alert_summary(hours=hours)


# P&L and performance endpoints
@app.get("/api/performance/pnl")
async def get_pnl_history(hours: int = 24):
    """Get portfolio P&L history."""
    if data_provider is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    return data_provider.get_pnl_history(hours=hours)


@app.get("/api/performance/metrics")
async def get_metrics_history(hours: int = 24):
    """Get risk metrics history."""
    if data_provider is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    return data_provider.get_risk_metrics_history(hours=hours)


# Audit endpoints
@app.get("/api/audit/recent")
async def get_recent_overrides(limit: int = 50, hours: Optional[int] = None):
    """Get recent override actions."""
    if audit_logger is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    return audit_logger.get_recent_overrides(limit=limit, hours=hours)


@app.get("/api/audit/summary")
async def get_audit_summary(hours: int = 24):
    """Get audit summary statistics."""
    if audit_logger is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    by_type = audit_logger.get_override_counts_by_type(hours=hours)
    by_operator = audit_logger.get_override_counts_by_operator(hours=hours)

    return {
        "by_type": by_type,
        "by_operator": by_operator,
        "time_window_hours": hours,
        "timestamp": datetime.now().isoformat()
    }


# Manual override endpoints (CRITICAL - require audit logging)
@app.post("/api/override/circuit-breaker/trigger")
async def trigger_circuit_breaker(request: CircuitBreakerTriggerRequest):
    """Manually trigger circuit breaker (EMERGENCY KILL SWITCH)."""
    if risk_manager is None or audit_logger is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    # Get state before override
    previous_state = data_provider.get_circuit_breaker_state()

    try:
        # Execute manual trigger
        risk_manager.manual_circuit_breaker_trigger(reason=request.reason)

        # Get state after override
        new_state = data_provider.get_circuit_breaker_state()

        # Log to audit trail
        audit_logger.log_override(
            action_type=OverrideAction.CIRCUIT_BREAKER_TRIGGER,
            operator=request.operator,
            reason=request.reason,
            previous_state=previous_state,
            new_state=new_state,
            success=True
        )

        # Broadcast to WebSocket clients
        await manager.broadcast({
            "type": "circuit_breaker_triggered",
            "operator": request.operator,
            "reason": request.reason,
            "timestamp": datetime.now().isoformat()
        })

        return {
            "success": True,
            "message": "Circuit breaker triggered manually",
            "new_state": new_state
        }

    except Exception as e:
        # Log failure to audit trail
        audit_logger.log_override(
            action_type=OverrideAction.CIRCUIT_BREAKER_TRIGGER,
            operator=request.operator,
            reason=request.reason,
            previous_state=previous_state,
            success=False,
            error_message=str(e)
        )

        raise HTTPException(status_code=500, detail=f"Failed to trigger circuit breaker: {str(e)}")


@app.post("/api/override/circuit-breaker/reset")
async def reset_circuit_breaker(request: CircuitBreakerResetRequest):
    """Reset circuit breaker (requires strong justification)."""
    if risk_manager is None or audit_logger is None:
        raise HTTPException(status_code=503, detail="System not initialized")

    # Get state before override
    previous_state = data_provider.get_circuit_breaker_state()

    try:
        # Execute reset
        risk_manager.circuit_breaker.reset(justification=request.justification)

        # Get state after override
        new_state = data_provider.get_circuit_breaker_state()

        # Log to audit trail
        audit_logger.log_override(
            action_type=OverrideAction.CIRCUIT_BREAKER_RESET,
            operator=request.operator,
            reason=request.justification,
            previous_state=previous_state,
            new_state=new_state,
            success=True
        )

        # Broadcast to WebSocket clients
        await manager.broadcast({
            "type": "circuit_breaker_reset",
            "operator": request.operator,
            "justification": request.justification,
            "timestamp": datetime.now().isoformat()
        })

        return {
            "success": True,
            "message": "Circuit breaker reset successfully",
            "new_state": new_state
        }

    except Exception as e:
        # Log failure to audit trail
        audit_logger.log_override(
            action_type=OverrideAction.CIRCUIT_BREAKER_RESET,
            operator=request.operator,
            reason=request.justification,
            previous_state=previous_state,
            success=False,
            error_message=str(e)
        )

        raise HTTPException(status_code=500, detail=f"Failed to reset circuit breaker: {str(e)}")


# WebSocket endpoint for real-time updates
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time dashboard updates."""
    await manager.connect(websocket)

    try:
        while True:
            # Send periodic updates (every 10 seconds)
            if data_provider is not None:
                status = data_provider.get_system_status()
                await websocket.send_json({
                    "type": "status_update",
                    "data": status,
                    "timestamp": datetime.now().isoformat()
                })

            # Wait 10 seconds before next update
            await asyncio.sleep(10)

    except WebSocketDisconnect:
        manager.disconnect(websocket)


# Serve frontend
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve dashboard HTML."""
    # Read dashboard HTML from file (using relative path)
    dashboard_path = Path(__file__).parent / "dashboard.html"
    try:
        with open(dashboard_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return """
        <html>
        <body>
            <h1>IRM Dashboard</h1>
            <p>Dashboard HTML not found. Please ensure dashboard.html exists at: """ + str(dashboard_path) + """</p>
        </body>
        </html>
        """
