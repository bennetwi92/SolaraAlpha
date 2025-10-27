# Operations Documentation

**Purpose**: Comprehensive operational procedures for running SolaraAlpha trading systems in production.

**Primary Document**: `RUNBOOK.md` - Complete operational reference covering startup, daily operations, incident response, and disaster recovery.

**Contents**:
- System startup/shutdown procedures (normal and emergency)
- Daily operations checklists (pre-market, intraday, EOD)
- Data backfill and validation procedures
- Incident response playbook (by severity)
- Recovery procedures (system failure, data corruption, circuit breaker)
- Monitoring dashboard reference and escalation matrix
- Deployment checklist (pre/post validation)
- Disaster recovery plan (RTO: 4h, RPO: 24h)

**Audience**: Human operators, IRM team, on-call engineers

**Philosophy**: Safety first - when in doubt, halt trading and escalate to IRM.

**Quick Start**: Open `RUNBOOK.md` and follow Section 1 (System Startup).
