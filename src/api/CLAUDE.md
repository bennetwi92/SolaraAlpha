# IRM Dashboard API

Real-time monitoring dashboard for Integrated Risk Management with manual override capabilities.

## Purpose
- Real-time monitoring of positions, risk limits, circuit breaker status
- Manual override authority for emergency situations (kill switch, CB reset)
- Comprehensive audit trail of all override actions
- WebSocket-based auto-refresh every 10 seconds

## Components
- `dashboard_api.py`: FastAPI backend with REST + WebSocket endpoints
- `dashboard.html`: Frontend with real-time monitoring and override controls

## Running the Dashboard

```bash
# Activate environment
source ~/anaconda3/etc/profile.d/conda.sh && conda activate solaraalpha

# Run server
uvicorn src.api.dashboard_api:app --reload --host 127.0.0.1 --port 8000

# Access dashboard
open http://localhost:8000
```

## Security Considerations

### CRITICAL - Production Deployment
This dashboard includes **MANUAL OVERRIDE CAPABILITIES** that can halt all trading. Before production:

1. **Authentication Required**: Add API key / JWT authentication
2. **Authorization Required**: Role-based access control for override endpoints
3. **Rate Limiting**: Prevent abuse of override endpoints
4. **CORS Configuration**: Restrict `allow_origins` to production domain
5. **HTTPS Only**: Never serve over HTTP in production
6. **Network Isolation**: Place behind VPN/firewall

### Input Validation
- Operator: 3-50 chars, alphanumeric + underscore/hyphen only
- Reason: Minimum 20 chars, minimum 5 words
- All inputs validated by Pydantic models

### Audit Trail
All override actions logged to `logs/audit.db` with:
- Operator identity
- Justification
- Previous/new state
- Timestamp
- Success/failure status
- Error messages

## API Endpoints

### Monitoring (Read-Only)
- `GET /api/health` - API health check
- `GET /api/status` - Comprehensive system status
- `GET /api/risk/metrics` - VaR, CVaR, Sharpe ratios
- `GET /api/circuit-breaker` - Circuit breaker state
- `GET /api/positions` - Current positions
- `GET /api/exposure` - Portfolio exposure
- `GET /api/limits` - Risk limit utilization
- `GET /api/alerts/recent` - Recent alerts
- `GET /api/performance/pnl` - P&L history

### Manual Override (Write)
- `POST /api/override/circuit-breaker/trigger` - Emergency kill switch
- `POST /api/override/circuit-breaker/reset` - Reset circuit breaker

### WebSocket
- `WS /ws` - Real-time updates (10-second intervals)

## Data Storage
- `logs/alerts.db` - Alert history
- `logs/pnl.db` - P&L time-series
- `logs/audit.db` - Override action audit trail

## Integration
Must initialize components before use:
```python
from src.api.dashboard_api import initialize_components

initialize_components(
    risk_manager=rm,
    health_checker=hc,
    alert_manager=am
)
```
