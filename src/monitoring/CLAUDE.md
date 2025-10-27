# Monitoring & Observability

**Purpose**: System health monitoring, alerting, reporting, and dashboard generation.

**Components**:
- `logging_config.py`: Centralized JSON logging with rotating file handlers
- `health_checker.py`: System health validation (data, risk, execution, resources)
- `dashboard.py`: HTML dashboard generator (auto-refresh every 5 minutes)
- `alerting.py`: Alert management with deduplication and email delivery
- `reporting.py`: Daily markdown performance reports

**Integration**: Used by all components (data, risk, execution, strategies, orchestrator).
**Output**: `logs/app.log`, `logs/dashboard.html`, `logs/reports/YYYY-MM-DD.md`
