# SolaraAlpha Operations Runbook

**Version**: 1.0.0
**Last Updated**: 2025-10-27
**Audience**: Human operators, IRM team

## Overview

This runbook provides step-by-step procedures for operating the SolaraAlpha AI proprietary trading system. All procedures assume paper trading mode with real market data.

**System Philosophy**: Risk Management > Profitability > Operational Efficiency

**Priority**: Safety first. When in doubt, halt trading and escalate to IRM.

---

## Table of Contents

1. [System Startup](#1-system-startup)
2. [Daily Operations Checklist](#2-daily-operations-checklist)
3. [Shutdown Procedures](#3-shutdown-procedures)
4. [Data Backfill Procedures](#4-data-backfill-procedures)
5. [Incident Response Playbook](#5-incident-response-playbook)
6. [Recovery Procedures](#6-recovery-procedures)
7. [Monitoring & Escalation](#7-monitoring--escalation)
8. [Deployment Checklist](#8-deployment-checklist)
9. [Disaster Recovery Plan](#9-disaster-recovery-plan)

---

## 1. System Startup

### 1.1 Pre-Startup Validation

**Environment Check**:
```bash
cd /Users/williambennett/Github/SolaraAlpha
source ~/anaconda3/etc/profile.d/conda.sh
conda activate solaraalpha

# Verify Python version and dependencies
python --version  # Should be 3.9+
pip list | grep -E "pandas|numpy|requests|fastapi"
```

**Git Status Check**:
```bash
git status
git log -1  # Review latest commit
git pull    # Ensure latest code
```

**Configuration Verification**:
```bash
# Verify risk limits
cat config/risk_limits.yaml

# Verify data sources
cat config/data_sources.yaml

# Check environment variables (if any)
env | grep -E "ALPACA|GITHUB"
```

**Expected Result**:
- Clean conda environment
- Up-to-date codebase
- Valid configuration files
- All required environment variables present

### 1.2 Data Health Check

**Validate Data Freshness**:
```bash
# Check latest data timestamps
python -c "
from src.data.storage.parquet_storage import ParquetStorage
from pathlib import Path
storage = ParquetStorage()

# Check key equities
for symbol in ['SPY', 'AAPL', 'MSFT']:
    try:
        df = storage.load('equities', symbol, '1d')
        print(f'{symbol}: Latest data {df.index[-1]}')
    except Exception as e:
        print(f'{symbol}: ERROR - {e}')

# Check key crypto pairs
for symbol in ['BTC/USDT', 'ETH/USDT']:
    try:
        df = storage.load('crypto', symbol, '1h')
        print(f'{symbol}: Latest data {df.index[-1]}')
    except Exception as e:
        print(f'{symbol}: ERROR - {e}')
"
```

**Expected Result**:
- Equities data within 1 business day
- Crypto data within 2 hours (24/7 markets)
- No ERROR messages

**If Data Stale**: Proceed to [Section 4: Data Backfill](#4-data-backfill-procedures)

### 1.3 Start Monitoring Infrastructure

**Step 1: Start IRM Dashboard API**

Open Terminal 1:
```bash
cd /Users/williambennett/Github/SolaraAlpha
conda activate solaraalpha

# Start API server (runs on http://localhost:8000)
python -m uvicorn src.api.dashboard_api:app --reload --port 8000
```

**Expected Output**:
```
INFO:     Started server process [PID]
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete
```

**Step 2: Access Dashboard**

Open browser to: `http://localhost:8000/dashboard`

**Dashboard Health Indicators**:
- System Status: All components GREEN
- Circuit Breaker: INACTIVE (green)
- Portfolio Value: Displaying current value
- Recent Alerts: No critical alerts
- Real-time updates: Counter incrementing every 5 seconds

**Screenshot Example**:
```
┌─────────────────────────────────────────────┐
│ SolaraAlpha IRM Dashboard                   │
├─────────────────────────────────────────────┤
│ System Status: ✅ OPERATIONAL                │
│ Circuit Breaker: 🟢 INACTIVE                 │
│ Portfolio Value: $100,000.00                │
│ P&L Today: $0.00 (0.00%)                    │
│                                             │
│ [Manual Trigger] [Reset CB]                │
└─────────────────────────────────────────────┘
```

### 1.4 Start Trading Strategies

**Step 1: Start Strategy Runner**

Open Terminal 2:
```bash
cd /Users/williambennett/Github/SolaraAlpha
conda activate solaraalpha

# Start strategy runner daemon
python src/execution/strategy_runner.py
```

**Expected Output**:
```
2025-10-27 09:30:00 [INFO] Strategy Runner: Initializing...
2025-10-27 09:30:01 [INFO] Registered strategy: pairs_trading_1
2025-10-27 09:30:01 [INFO] Registered strategy: stat_arb_1
2025-10-27 09:30:02 [INFO] Strategy Runner: Starting...
2025-10-27 09:30:02 [INFO] pairs_trading_1: Started (status: RUNNING)
2025-10-27 09:30:02 [INFO] stat_arb_1: Started (status: RUNNING)
2025-10-27 09:30:02 [INFO] Next iteration at 09:35:00
```

**Step 2: Verify Strategies via Dashboard**

Dashboard should show:
- Active Strategies: 2 (or configured count)
- Strategy Status: All RUNNING
- Recent activity in execution log

### 1.5 Start Orchestrator (Optional)

**Only if automated task processing desired**:

Open Terminal 3:
```bash
cd /Users/williambennett/Github/SolaraAlpha
conda activate solaraalpha

export GITHUB_TOKEN="ghp_YOUR_TOKEN_HERE"

# Start orchestrator (without CEO poke for manual mode)
python src/orchestrator/orchestrator.py
```

**Expected Output**:
```
================================================================================
GitHub Issue Orchestrator - Lightweight POC
Repository: bennetwi92/SolaraAlpha
Worker timeout: 1200s
Idle sleep: 300s
CEO poke enabled: False
================================================================================

[FETCH] Fetching issues from bennetwi92/SolaraAlpha...
[FETCH] Found 0 ready issues (FIFO order)
[IDLE] No issues to process. Sleeping 300s...
```

### 1.6 Post-Startup Verification

**Checklist**:
- [ ] IRM Dashboard accessible at http://localhost:8000/dashboard
- [ ] Dashboard shows System Status: OPERATIONAL
- [ ] Circuit Breaker: INACTIVE
- [ ] Strategy Runner logs show all strategies RUNNING
- [ ] No ERROR or CRITICAL alerts in logs
- [ ] Orchestrator running (if enabled)

**Startup Complete**: System is now operational in paper trading mode.

---

## 2. Daily Operations Checklist

### Morning Pre-Market (8:00 AM - 9:30 AM ET)

**Data Validation**:
```bash
# Check data freshness
python scripts/validate_strategies.py

# Backfill any gaps (see Section 4)
python scripts/backfill_data.py --all
```

**System Health Check**:
- [ ] Open IRM Dashboard (http://localhost:8000/dashboard)
- [ ] Verify System Status: OPERATIONAL
- [ ] Check Circuit Breaker: INACTIVE
- [ ] Review overnight alerts (Alerts tab)
- [ ] Verify portfolio value matches expected
- [ ] Check data staleness warnings

**Risk Review**:
```bash
# Calculate current risk metrics
python -c "
from src.risk import RiskManager
risk_mgr = RiskManager(portfolio_value=100000)
metrics = risk_mgr.calculate_risk_metrics()
exposure = risk_mgr.get_portfolio_exposure()

print(f'95% VaR: \${metrics.var_95:,.2f}')
print(f'95% CVaR: \${metrics.cvar_95:,.2f}')
print(f'30-day Sharpe: {metrics.sharpe_30d:.2f}')
print(f'Portfolio Beta: {metrics.beta:.2f}')
print(f'Gross Exposure: \${exposure[\"gross\"]:,.2f}')
print(f'Net Exposure: \${exposure[\"net\"]:,.2f}')
"
```

**Strategy Status**:
- [ ] All strategies showing RUNNING status
- [ ] No strategies in ERROR state
- [ ] Recent trade activity visible in logs

### Intraday Monitoring (9:30 AM - 4:00 PM ET)

**Every 30 Minutes**:
- [ ] Check IRM Dashboard for new alerts
- [ ] Monitor P&L: Ensure within expected ranges
- [ ] Verify strategy execution logs show activity
- [ ] Check for data staleness warnings

**Red Flags to Watch**:
- Circuit breaker triggered (🔴 ACTIVE)
- Strategies stuck in ERROR state
- No trade activity for >1 hour (unexpected)
- Data staleness warnings
- Rapid P&L swings (>2% in 30min)
- Critical alerts

**If Red Flag Detected**: Proceed to [Section 5: Incident Response](#5-incident-response-playbook)

### End of Day (4:00 PM - 5:00 PM ET)

**Performance Review**:
- [ ] Review daily P&L in dashboard
- [ ] Check daily performance report: `logs/reports/YYYY-MM-DD.md`
- [ ] Verify all positions reconciled
- [ ] Review execution quality metrics (slippage, fill rate)

**Log Review**:
```bash
# Check for errors/warnings
tail -100 logs/app.log | grep -E "ERROR|WARNING"

# Review circuit breaker triggers (if any)
grep "CIRCUIT BREAKER" logs/app.log
```

**Risk Metrics Update**:
- [ ] Review VaR/CVaR trends
- [ ] Check Sharpe ratio (30/90/365 day)
- [ ] Verify position limits still healthy
- [ ] Document any limit breaches

**Data Preparation for Next Day**:
```bash
# Optional: Pre-fetch next day's data
python scripts/backfill_data.py --asset-class equities --force
```

### Weekly Tasks (Friday EOD)

- [ ] Review weekly performance vs benchmarks
- [ ] Analyze strategy performance attribution
- [ ] Review and acknowledge all alerts from week
- [ ] Archive old logs if disk space low
- [ ] Update risk limits if needed (with IRM approval)
- [ ] Git commit any configuration changes

---

## 3. Shutdown Procedures

### 3.1 Graceful Shutdown (Normal Operations)

**Order of Operations** (reverse of startup):

**Step 1: Stop Strategy Runner**

In Terminal 2 (strategy runner):
```bash
# Send graceful shutdown signal
Ctrl+C
```

**Expected Output**:
```
2025-10-27 16:00:00 [INFO] Shutdown signal received (SIGINT)
2025-10-27 16:00:00 [INFO] Shutting down strategies gracefully...
2025-10-27 16:00:01 [INFO] pairs_trading_1: Shutdown complete
2025-10-27 16:00:01 [INFO] stat_arb_1: Shutdown complete
2025-10-27 16:00:02 [INFO] Strategy Runner: Shutdown complete
```

**Wait for Graceful Completion**: Allow up to 30 seconds for strategies to complete current iteration and persist state.

**Step 2: Stop Orchestrator** (if running)

In Terminal 3 (orchestrator):
```bash
Ctrl+C
```

**Expected Output**:
```
[SHUTDOWN] Received Ctrl+C, shutting down gracefully...
[SHUTDOWN] Orchestrator stopped
```

**Step 3: Stop IRM Dashboard API**

In Terminal 1 (dashboard API):
```bash
Ctrl+C
```

**Expected Output**:
```
INFO:     Shutting down
INFO:     Finished server process [PID]
```

**Step 4: Post-Shutdown Verification**

```bash
# Verify no orphaned processes
ps aux | grep -E "strategy_runner|orchestrator|uvicorn"

# Expected: No SolaraAlpha processes running
```

**Step 5: Final Log Review**

```bash
# Check for shutdown errors
tail -50 logs/app.log | grep -E "ERROR|Shutdown"
```

**Expected**: Clean shutdown messages, no errors.

### 3.2 Emergency Shutdown (Circuit Breaker / Critical Issue)

**When to Use**:
- Circuit breaker triggered and positions need immediate review
- Critical system errors detected
- Data feed failures
- Unexpected market conditions

**Procedure**:

**Step 1: Trigger Circuit Breaker Manually** (if not already active)

IRM Dashboard → Manual Override → Trigger Circuit Breaker
- Operator: YOUR_NAME
- Reason: "Emergency shutdown due to [SPECIFIC REASON]"

**Screenshot**:
```
┌─────────────────────────────────────────────┐
│ Manual Circuit Breaker Trigger              │
├─────────────────────────────────────────────┤
│ Operator: [operator_name]                   │
│ Reason: [Detailed justification ≥20 chars]  │
│                                             │
│ ⚠️  This will halt ALL trading immediately  │
│                                             │
│ [Cancel]  [Trigger Circuit Breaker]        │
└─────────────────────────────────────────────┘
```

**Step 2: Verify Trading Halted**

Dashboard should show:
- Circuit Breaker: 🔴 ACTIVE
- All strategies: HALTED status

**Step 3: Follow Graceful Shutdown** (Section 3.1 Steps 1-5)

**Step 4: Document Incident**

Create incident report:
```bash
echo "Incident Report - $(date)" > logs/incidents/$(date +%Y%m%d_%H%M%S).md
```

Document:
- Timestamp of emergency shutdown
- Reason for shutdown
- Circuit breaker trigger details
- Current portfolio state
- Actions taken

**Step 5: Notify IRM Team**

Contact escalation path (see Section 7.2)

### 3.3 Force Kill (Last Resort)

**Only use if graceful shutdown fails after 60 seconds**:

```bash
# Find all SolaraAlpha processes
ps aux | grep -E "strategy_runner|orchestrator|uvicorn" | grep -v grep

# Kill by PID
kill -9 <PID>

# Or kill all Python processes (DANGEROUS - use with caution)
pkill -9 -f "strategy_runner|orchestrator|uvicorn"
```

**Post-Force Kill**:
- Review logs for state corruption
- Verify data integrity before restart
- Document reason for force kill

---

## 4. Data Backfill Procedures

### 4.1 Routine Backfill (Daily)

**When**: Every morning pre-market, or when data staleness detected

**Backfill All Configured Symbols**:
```bash
cd /Users/williambennett/Github/SolaraAlpha
conda activate solaraalpha

# Backfill all asset classes
python scripts/backfill_data.py --all
```

**Expected Output**:
```
Starting backfill for all configured symbols...
[equities] SPY: Fetching 1d data...
[equities] SPY: ✓ Fetched 252 bars, validated, stored
[equities] AAPL: Fetching 1d data...
[equities] AAPL: ✓ Fetched 252 bars, validated, stored
...
[crypto] BTC/USDT: Fetching 1h data...
[crypto] BTC/USDT: ✓ Fetched 720 bars, validated, stored
...
Backfill complete: 25 symbols processed, 25 successful, 0 failed
```

**Validate Backfill**:
```bash
# Check data inventory
python -c "
from src.data.storage.parquet_storage import ParquetStorage
storage = ParquetStorage()

for asset_class in ['equities', 'crypto', 'forex']:
    print(f'\n{asset_class.upper()}:')
    # List all symbols (implementation-specific)
"
```

### 4.2 Backfill Specific Asset Class

**Equities Only**:
```bash
python scripts/backfill_data.py --asset-class equities
```

**Crypto Only**:
```bash
python scripts/backfill_data.py --asset-class crypto
```

**Forex Only**:
```bash
python scripts/backfill_data.py --asset-class forex
```

### 4.3 Backfill Single Symbol (Force Refresh)

**Use Case**: Data validation failure, suspected bad data

```bash
# Force refresh AAPL daily data
python scripts/backfill_data.py --symbol AAPL --asset-class equities --force

# Force refresh BTC/USDT hourly data
python scripts/backfill_data.py --symbol BTC/USDT --asset-class crypto --force
```

**Force Flag**: Overwrites existing data even if fresh

### 4.4 Historical Data Backfill (One-Time)

**Use Case**: New symbol added, or extending historical window

```bash
# Backfill with extended lookback
python -c "
from src.data.ingest.ingest_manager import IngestManager
from datetime import datetime, timedelta

manager = IngestManager()

# Backfill 2 years of AAPL data
start_date = datetime.now() - timedelta(days=730)
manager.backfill_symbol(
    asset_class='equities',
    symbol='AAPL',
    frequency='1d',
    start_date=start_date,
    force=True
)
"
```

### 4.5 Data Validation Post-Backfill

**Run Validator**:
```bash
python -c "
from src.data.validate.validator import DataValidator
from src.data.storage.parquet_storage import ParquetStorage

validator = DataValidator()
storage = ParquetStorage()

# Validate key symbols
symbols = [('equities', 'SPY'), ('equities', 'AAPL'), ('crypto', 'BTC/USDT')]

for asset_class, symbol in symbols:
    df = storage.load(asset_class, symbol, '1d')
    result = validator.validate(df, asset_class)

    if result.is_valid:
        print(f'✓ {symbol}: VALID')
    else:
        print(f'✗ {symbol}: INVALID - {result.errors}')
"
```

**Expected**: All symbols show VALID

**If Validation Fails**:
1. Review error messages (spikes, gaps, staleness)
2. Check source data availability
3. Re-run backfill with --force
4. If persistent, escalate to data infrastructure review

### 4.6 Data Backfill Troubleshooting

**Issue**: `No data returned from source`

**Solution**:
- Check internet connectivity
- Verify symbol ticker is correct
- Check if market was closed (holidays)
- For crypto: Verify exchange availability

**Issue**: `Validation failed: Price spike detected`

**Solution**:
- Review raw data for correctness
- Check if legitimate corporate action (split, etc)
- Manually inspect: `cat data/{asset_class}/{symbol}/{frequency}/data.parquet`
- If legitimate: Adjust validation thresholds in config
- If bad data: Report to data source

**Issue**: `Validation failed: Data staleness`

**Solution**:
- Check current time vs market hours
- For equities: Market closed → expected
- For crypto: Should be fresh within 2 hours
- Re-run backfill with --force

---

## 5. Incident Response Playbook

### 5.1 Incident Classification

**Severity Levels**:

**CRITICAL** (Red):
- Circuit breaker triggered automatically
- Data feed complete failure (all symbols)
- Strategy crash loop (restarting every <10s)
- Execution errors (orders rejected repeatedly)
- Security breach suspected

**HIGH** (Orange):
- Single strategy persistent errors
- Partial data feed failure (multiple symbols)
- Risk limit breach
- Unusual P&L swing (>5% in 1 hour)

**MEDIUM** (Yellow):
- Single symbol data staleness
- Single strategy temporary error (recovered)
- Minor risk limit warning (approaching threshold)

**LOW** (Blue):
- Informational alerts
- Performance degradation (slow but functional)

### 5.2 Incident Response Protocol

**Step 1: Assess Severity**

Check IRM Dashboard:
- Circuit breaker status
- Active alerts count and severity
- Strategy status
- Recent error logs

**Step 2: Immediate Action (by Severity)**

**CRITICAL**:
1. **HALT TRADING IMMEDIATELY**:
   - Trigger circuit breaker manually (if not already active)
   - Follow Emergency Shutdown (Section 3.2)

2. **Notify IRM Team**:
   - Contact primary on-call (see Section 7.2)
   - Provide dashboard screenshot
   - Share recent logs: `tail -100 logs/app.log`

3. **Preserve Evidence**:
   ```bash
   # Copy logs to incident folder
   mkdir -p logs/incidents/$(date +%Y%m%d_%H%M%S)
   cp logs/app.log logs/incidents/$(date +%Y%m%d_%H%M%S)/
   cp logs/dashboard.html logs/incidents/$(date +%Y%m%d_%H%M%S)/
   ```

4. **Do NOT Restart** until root cause identified

**HIGH**:
1. **Monitor Closely**:
   - Check dashboard every 5 minutes
   - Review live logs: `tail -f logs/app.log`

2. **Investigate Root Cause**:
   - Check recent commits: `git log -5`
   - Review error patterns in logs
   - Validate data integrity

3. **Consider Controlled Shutdown**:
   - If issue persists >30 minutes → escalate to CRITICAL
   - If resolved → document and continue monitoring

4. **Notify IRM Team** (within 1 hour)

**MEDIUM**:
1. **Document Issue**:
   - Screenshot dashboard alert
   - Copy relevant log entries

2. **Attempt Remediation**:
   - Data staleness → Run backfill (Section 4)
   - Strategy error → Check strategy logs for details
   - Risk warning → Review position exposure

3. **Monitor for Escalation**:
   - If issue spreads → escalate to HIGH

**LOW**:
1. **Acknowledge Alert**:
   - Review alert details in dashboard

2. **Schedule Follow-up**:
   - Add to daily review checklist
   - Document in operations log

### 5.3 Common Incident Scenarios

#### Scenario A: Circuit Breaker Triggered (Drawdown)

**Symptoms**:
- Dashboard shows Circuit Breaker: 🔴 ACTIVE
- Alert: "10% drawdown from high-water mark"
- All strategies: HALTED status

**Response**:
1. **Verify Legitimacy**:
   - Check P&L: Is drawdown accurate?
   - Review recent trades: Any execution errors?
   - Check market conditions: Extreme volatility?

2. **Investigate Root Cause**:
   ```bash
   # Review recent P&L
   python -c "
   from src.monitoring.pnl_store import PnLStore
   store = PnLStore()
   recent = store.get_recent_pnl(hours=4)
   print(recent)
   "
   ```

3. **Determine Action**:
   - **If legitimate drawdown**: Review strategy logic, consider parameter adjustments
   - **If data error**: Correct data, recalculate P&L
   - **If execution issue**: Review order logs, contact broker if needed

4. **Reset Circuit Breaker** (ONLY after thorough review):
   - IRM Dashboard → Manual Override → Reset Circuit Breaker
   - Operator: YOUR_NAME
   - Justification: "Thorough review completed. Root cause: [SPECIFIC REASON]. Mitigation: [ACTIONS TAKEN]."

5. **Resume Trading** (if appropriate):
   - Restart strategies (Section 1.4)
   - Monitor closely for 1 hour

#### Scenario B: Strategy Crash Loop

**Symptoms**:
- Strategy logs show repeated startup/crash
- Dashboard shows strategy ERROR status
- Errors like: `Exception in run_iteration()`, `Failed to initialize`

**Response**:
1. **Stop Strategy**:
   ```bash
   # Graceful shutdown to prevent further crashes
   # Ctrl+C in strategy runner terminal
   ```

2. **Review Error Logs**:
   ```bash
   grep "ERROR" logs/app.log | tail -50
   ```

3. **Common Causes**:
   - **Missing data**: Run backfill (Section 4)
   - **Configuration error**: Verify config files
   - **Code bug**: Review recent commits, consider rollback
   - **External API failure**: Check broker/data source status

4. **Test Fix**:
   ```bash
   # Test strategy in isolation
   python -c "
   from src.strategies.pairs_trading.paper_trading import PairsTradingPaperStrategy
   strategy = PairsTradingPaperStrategy(...)
   strategy.run_iteration()  # Should not raise exception
   "
   ```

5. **Restart Strategy** (if fix validated):
   - Restart strategy runner (Section 1.4)
   - Monitor for stability (30 minutes)

6. **If Unresolved**:
   - Keep strategy disabled
   - Create GitHub issue with full error logs
   - Escalate to development team

#### Scenario C: Data Feed Failure

**Symptoms**:
- Alerts: "Data staleness detected"
- Strategies unable to fetch price data
- Backfill scripts failing with network errors

**Response**:
1. **Verify Network Connectivity**:
   ```bash
   ping 8.8.8.8  # Check internet
   curl https://query1.finance.yahoo.com  # Check YFinance
   ```

2. **Check Data Source Status**:
   - YFinance: Often has rate limits or outages
   - Binance (crypto): Check status.binance.com
   - Forex: Check data provider status

3. **Attempt Data Refresh**:
   ```bash
   # Retry backfill with longer timeout
   python scripts/backfill_data.py --all
   ```

4. **Fallback Options**:
   - **Short-term outage (<1 hour)**: Continue with stale data (if acceptable)
   - **Extended outage**: Halt trading until data restored
   - **Permanent source failure**: Switch to alternative data source (requires code change)

5. **Monitor Resolution**:
   - Re-run backfill every 15 minutes
   - Once data restored: Verify quality
   - Resume normal operations

#### Scenario D: Risk Limit Breach

**Symptoms**:
- Alert: "Position limit exceeded"
- Strategies unable to execute new trades
- Dashboard shows exposure warnings

**Response**:
1. **Verify Breach**:
   ```bash
   python -c "
   from src.risk import RiskManager
   risk_mgr = RiskManager(portfolio_value=100000)
   exposure = risk_mgr.get_portfolio_exposure()

   print(f'Gross Exposure: \${exposure[\"gross\"]:,.2f} / \$200,000 limit')
   print(f'Net Exposure: \${exposure[\"net\"]:,.2f} / \$20,000 limit')

   # Check per-strategy exposure
   positions = risk_mgr.position_tracker.get_all_positions()
   for pos in positions:
       print(f'{pos.strategy_id}: \${abs(pos.market_value):,.2f}')
   "
   ```

2. **Determine Cause**:
   - **Legitimate**: Strategies accumulated positions near limit
   - **Bug**: Position tracking error or stale data
   - **Configuration**: Limits too restrictive for current strategy mix

3. **Immediate Action**:
   - **If near soft limit (90%)**: Monitor closely, allow natural position reduction
   - **If at hard limit (100%)**: Halt new entries, allow exits only
   - **If breach is due to bug**: Halt trading, fix tracking

4. **Long-Term Resolution**:
   - **Adjust limits** (requires IRM approval):
     - Edit `config/risk_limits.yaml`
     - Commit change: `git add config/risk_limits.yaml && git commit -m "Risk - Adjust position limits per IRM approval"`
   - **Reduce position sizes**: Adjust strategy sizing parameters
   - **Add strategies**: Diversify to reduce per-strategy concentration

---

## 6. Recovery Procedures

### 6.1 Recovery from Unclean Shutdown

**Symptoms**:
- System crashed without graceful shutdown
- Strategy runner terminated unexpectedly
- Orphaned processes or lock files

**Recovery Steps**:

**Step 1: Verify Clean State**

```bash
# Check for orphaned processes
ps aux | grep -E "strategy_runner|orchestrator|uvicorn"

# Kill if found
kill -9 <PID>
```

**Step 2: Inspect Logs for Corruption**

```bash
# Check for incomplete writes or errors
tail -200 logs/app.log | grep -E "ERROR|CRITICAL|Traceback"
```

**Step 3: Validate Data Integrity**

```bash
# Check for corrupted parquet files
python -c "
from src.data.storage.parquet_storage import ParquetStorage
import pandas as pd

storage = ParquetStorage()

# Test load critical symbols
symbols = [('equities', 'SPY'), ('crypto', 'BTC/USDT')]
for asset_class, symbol in symbols:
    try:
        df = storage.load(asset_class, symbol, '1d')
        print(f'✓ {symbol}: OK ({len(df)} rows)')
    except Exception as e:
        print(f'✗ {symbol}: CORRUPTED - {e}')
"
```

**If Data Corrupted**:
```bash
# Delete corrupted file and re-fetch
rm data/{asset_class}/{symbol}/{frequency}/data.parquet
python scripts/backfill_data.py --symbol {SYMBOL} --asset-class {CLASS} --force
```

**Step 4: Validate Risk State**

```bash
# Check position tracking state
python -c "
from src.risk import RiskManager
risk_mgr = RiskManager(portfolio_value=100000)

# Verify circuit breaker state
if risk_mgr.check_circuit_breaker():
    print('⚠️  Circuit breaker is ACTIVE')
else:
    print('✓ Circuit breaker is INACTIVE')

# Check positions
positions = risk_mgr.position_tracker.get_all_positions()
print(f'Current positions: {len(positions)}')
for pos in positions:
    print(f'  {pos.symbol}: {pos.quantity} @ \${pos.entry_price:.2f}')
"
```

**If Position State Inconsistent**:
- Review execution logs: `grep "ORDER" logs/app.log | tail -50`
- Manually reconcile with broker statements
- Reset position tracker if necessary (requires IRM approval)

**Step 5: Normal Startup**

Once validation complete:
- Follow normal startup procedure (Section 1)
- Monitor closely for 1 hour post-recovery

### 6.2 Recovery from Data Corruption

**Symptoms**:
- Validation errors on multiple symbols
- Strategies failing due to bad price data
- Parquet read errors

**Recovery Steps**:

**Step 1: Identify Corrupted Data**

```bash
# Run full validation
python -c "
from src.data.validate.validator import DataValidator
from src.data.storage.parquet_storage import ParquetStorage
from pathlib import Path

validator = DataValidator()
storage = ParquetStorage()
corrupted = []

# Check all parquet files
for parquet_file in Path('data').rglob('*.parquet'):
    try:
        df = pd.read_parquet(parquet_file)
        # Extract metadata from path
        parts = parquet_file.parts
        asset_class = parts[1]
        symbol = parts[2]

        result = validator.validate(df, asset_class)
        if not result.is_valid:
            corrupted.append((asset_class, symbol, result.errors))
    except Exception as e:
        corrupted.append((str(parquet_file), 'READ_ERROR', str(e)))

print(f'Found {len(corrupted)} corrupted files:')
for item in corrupted:
    print(f'  {item}')
"
```

**Step 2: Backup Corrupted Data** (for forensics)

```bash
mkdir -p data/corrupted_backup/$(date +%Y%m%d_%H%M%S)
# Move corrupted files (from Step 1 output)
mv data/{asset_class}/{symbol}/{frequency}/data.parquet data/corrupted_backup/$(date +%Y%m%d_%H%M%S)/
```

**Step 3: Re-fetch Clean Data**

```bash
# For each corrupted symbol
python scripts/backfill_data.py --symbol {SYMBOL} --asset-class {CLASS} --force
```

**Step 4: Validate Restoration**

Re-run validation (Step 1) - should show 0 corrupted files

**Step 5: Resume Operations**

Follow normal startup (Section 1)

### 6.3 Recovery from Circuit Breaker Lock

**Symptoms**:
- Circuit breaker persists across restarts
- Unable to reset via dashboard
- Trading remains halted

**Recovery Steps**:

**Step 1: Verify Persistent State**

```bash
# Check circuit breaker persistence file
cat src/risk/.circuit_breaker_state

# Expected if triggered:
# {"active": true, "triggered_at": "2025-10-27T14:32:11", "hwm": 100000.0}
```

**Step 2: Determine Legitimacy**

- Was drawdown legitimate? Review P&L history
- Has root cause been addressed?
- Has IRM approved reset?

**Step 3: Manual Reset** (IRM approval required)

```bash
# Option 1: Reset via dashboard (preferred)
# IRM Dashboard → Manual Override → Reset Circuit Breaker
# Justification: "IRM approval: [REFERENCE]. Root cause: [CAUSE]. Mitigation: [ACTIONS]."

# Option 2: Reset via code (if dashboard unavailable)
python -c "
from src.risk.circuit_breaker import CircuitBreaker
cb = CircuitBreaker(portfolio_value=100000, drawdown_threshold=0.10)
cb.reset()
print('Circuit breaker reset complete')
"
```

**Step 4: Verify Reset**

```bash
# Check state file
cat src/risk/.circuit_breaker_state

# Expected:
# {"active": false, "triggered_at": null, "hwm": 100000.0}
```

**Step 5: Resume Trading** (with caution)

- Follow normal startup (Section 1)
- Monitor P&L closely for 2 hours
- Be prepared to re-trigger if issue recurs

---

## 7. Monitoring & Escalation

### 7.1 Monitoring Dashboard Reference

**IRM Dashboard URL**: http://localhost:8000/dashboard

**Dashboard Sections**:

**1. System Status Panel**
- System Status: OPERATIONAL / DEGRADED / DOWN
- Circuit Breaker: ACTIVE / INACTIVE
- Portfolio Value: Current total portfolio value
- P&L Today: Daily profit/loss ($ and %)
- Last Update: Real-time update timestamp

**2. Manual Override Panel**
- **Trigger Circuit Breaker Button**:
  - Use for emergency halt
  - Requires: Operator name (3-50 chars), Reason (≥20 chars)
  - Effect: Immediate trading halt across ALL strategies

- **Reset Circuit Breaker Button**:
  - Use to resume trading after incident resolution
  - Requires: Operator name, Justification (≥20 chars, ≥5 words)
  - Effect: Allows trading to resume

**3. Alerts Panel**
- Recent Alerts: Last 10 alerts with severity color coding
- RED: Critical
- ORANGE: High
- YELLOW: Medium
- BLUE: Low

**4. Positions Panel**
- Active Positions: All open positions across strategies
- Columns: Symbol, Quantity, Entry Price, Current Price, P&L

**5. Strategies Panel**
- Strategy Status: RUNNING / HALTED / ERROR
- Last Activity: Timestamp of last iteration
- Today's P&L: Per-strategy performance

**6. Logs Panel**
- Real-time execution logs (WebSocket streaming)
- Filter by severity

### 7.2 Escalation Matrix

```
⚠️  PRODUCTION READINESS CHECK REQUIRED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ALL CONTACT PLACEHOLDERS (XXX-XXX-XXXX) MUST BE REPLACED BEFORE PRODUCTION DEPLOYMENT
Verify contacts are updated during deployment checklist (Section 8.1).
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Level 1: Automated Alerting**
- Dashboard alerts (in-app)
- Log file entries
- No human notification

**Response Time**: Real-time (operator monitoring)

**Level 2: Operator On-Call**
- For: HIGH severity incidents
- Contact: Primary operator (check schedule)
- Method: Slack/Email/SMS
- Response SLA: 15 minutes

**Level 3: IRM Team Lead**
- For: CRITICAL incidents, or HIGH incidents unresolved after 30 minutes
- Contact: IRM Team Lead
- Method: Phone call + Slack
- Response SLA: 5 minutes

**Level 4: CEO / Principal**
- For: System-wide failure, security breach, regulatory issue
- Contact: CEO
- Method: Phone call
- Response SLA: Immediate

**Escalation Triggers**:
- CRITICAL alert → Auto-escalate to Level 3
- HIGH alert unresolved after 30 min → Escalate to Level 3
- Circuit breaker triggered 2+ times in 24h → Escalate to Level 4
- Suspected security breach → Immediate Level 4

**Contact Information** (Update with actual contacts):
```
Primary Operator: operator@solaraalpha.com | +1-XXX-XXX-XXXX
IRM Team Lead: irm-lead@solaraalpha.com | +1-XXX-XXX-XXXX
CEO: ceo@solaraalpha.com | +1-XXX-XXX-XXXX

Slack Channels:
- #solaraalpha-alerts (automated alerts)
- #solaraalpha-incidents (human coordination)
- #solaraalpha-irm (IRM team only)
```

### 7.3 Log Monitoring

**Primary Log File**: `logs/app.log`

**Real-Time Monitoring**:
```bash
# Watch all logs
tail -f logs/app.log

# Watch errors only
tail -f logs/app.log | grep -E "ERROR|CRITICAL"

# Watch specific component
tail -f logs/app.log | grep "RiskManager"
```

**Log Levels**:
- **DEBUG**: Detailed diagnostic info (verbose)
- **INFO**: General informational messages
- **WARNING**: Warning messages (potential issues)
- **ERROR**: Error messages (definite problems)
- **CRITICAL**: Critical failures (immediate attention)

**Key Log Patterns to Monitor**:

```bash
# Circuit breaker events
grep "CIRCUIT BREAKER" logs/app.log

# Trade executions
grep "ORDER" logs/app.log | grep "FILLED"

# Data validation failures
grep "VALIDATION FAILED" logs/app.log

# Risk limit breaches
grep "LIMIT EXCEEDED" logs/app.log

# Strategy errors
grep "Strategy.*ERROR" logs/app.log
```

**Log Rotation**:
- Logs rotate automatically at 10MB
- Keeps last 5 backup files
- Archive old logs manually if needed:
  ```bash
  mkdir -p logs/archive/$(date +%Y%m)
  mv logs/app.log.* logs/archive/$(date +%Y%m)/
  ```

### 7.4 Performance Monitoring Metrics

**Key Metrics to Track**:

**Risk Metrics** (check every 4 hours):
- 95% VaR (target: <2% of portfolio)
- 95% CVaR (target: <3% of portfolio)
- 30-day Sharpe Ratio (target: >1.0)
- Portfolio Beta (target: 0.0 ± 0.2 for market-neutral)
- Gross Exposure (limit: 200%)
- Net Exposure (limit: 20%)

**Execution Metrics** (check EOD):
- Slippage (target: <10bps per trade)
- Fill Rate (target: >95%)
- Average Latency (target: <500ms)

**Strategy Metrics** (check EOD):
- Win Rate per strategy (target: strategy-dependent)
- Average P&L per trade
- Max drawdown (strategy-level)
- Sharpe ratio (strategy-level)

**System Metrics** (check every 2 hours):
- CPU usage (alert if >80%)
- Memory usage (alert if >80%)
- Disk space (alert if <10% free)
- Network latency to data sources

**Monitoring Commands**:

```bash
# System resources
top -l 1 | grep -E "CPU usage|PhysMem"
df -h

# Risk metrics
python -c "
from src.risk import RiskManager
risk_mgr = RiskManager(portfolio_value=100000)
metrics = risk_mgr.calculate_risk_metrics()
print(f'VaR: \${metrics.var_95:,.2f}')
print(f'Sharpe: {metrics.sharpe_30d:.2f}')
"

# Execution metrics
python -c "
from src.execution.metrics import ExecutionMetrics
from datetime import datetime, timedelta

metrics = ExecutionMetrics()
today = datetime.now().date()
stats = metrics.get_daily_stats(today)
print(f'Fill Rate: {stats[\"fill_rate\"]:.2%}')
print(f'Avg Slippage: {stats[\"avg_slippage\"]*10000:.1f}bps')
"
```

---

## 8. Deployment Checklist

**Use this checklist before deploying ANY code changes to production**

### 8.1 Pre-Deployment Validation

**Code Quality**:
- [ ] All tests passing: `pytest tests/ --cov=src --cov-report=term-missing`
- [ ] Test coverage ≥85%: Review coverage report
- [ ] No linting errors: `pylint src/ --disable=R,C`
- [ ] Code review completed (if multi-developer environment)
- [ ] Git branch up-to-date with main: `git pull origin main`

**Configuration Validation**:
- [ ] Review `config/risk_limits.yaml`: No accidental limit changes
- [ ] Review `config/data_sources.yaml`: All symbols valid
- [ ] No hardcoded API keys or secrets in code
- [ ] Environment variables documented
- [ ] **CRITICAL**: All contact placeholders (XXX-XXX-XXXX) replaced with real contacts in Section 7.2

**Functionality Validation**:
- [ ] Run backtest on new strategy (if applicable): `python scripts/run_*_backtest.py`
- [ ] Validate data pipeline: `python scripts/backfill_data.py --all`
- [ ] Test risk manager: `pytest tests/test_risk_*.py`
- [ ] Test execution system: `pytest tests/test_execution_*.py`

**Documentation**:
- [ ] CLAUDE.md updated (if architecture changed)
- [ ] Docstrings added for new functions/classes
- [ ] Operational runbook updated (if procedures changed)
- [ ] GitHub issue updated with implementation notes

### 8.2 Deployment Steps

**Step 1: Backup Current State**

```bash
# Create backup branch
git branch backup/pre-deploy-$(date +%Y%m%d_%H%M%S)

# Backup data (optional - only if schema changed)
mkdir -p backups/data/$(date +%Y%m%d)
cp -r data/ backups/data/$(date +%Y%m%d)/
```

**Step 2: Graceful Shutdown**

Follow Section 3.1 (Graceful Shutdown)

**Step 3: Deploy Code**

```bash
# Pull latest code
git checkout main
git pull origin main

# Verify correct branch/commit
git log -1

# Reinstall dependencies (if environment.yml changed)
conda env update -f environment.yml --prune
```

**Step 4: Migration Tasks** (if applicable)

```bash
# Run data migrations
# python scripts/migrate_data.py

# Update configuration files
# (manual review if needed)
```

**Step 5: Post-Deployment Validation**

```bash
# Run quick smoke tests
pytest tests/test_integration_*.py -v

# Validate data integrity
python scripts/backfill_data.py --all

# Test system startup (don't start strategies yet)
python -m uvicorn src.api.dashboard_api:app --reload --port 8000 &
sleep 5
curl http://localhost:8000/health  # Should return 200 OK
pkill -f uvicorn
```

**Step 6: Controlled Restart**

- Follow Section 1 (System Startup)
- Monitor dashboard closely for 30 minutes
- Watch logs for errors: `tail -f logs/app.log | grep ERROR`

**Step 7: Validate Operations**

- [ ] Dashboard accessible and showing correct data
- [ ] All strategies RUNNING
- [ ] Circuit breaker INACTIVE
- [ ] Data freshness checks passing
- [ ] No ERROR logs in last 30 minutes
- [ ] Test trade execution (paper trading only)

**Step 8: Commit Deployment**

```bash
# Tag successful deployment
git tag deploy/$(date +%Y%m%d_%H%M%S)
git push origin --tags

# Document deployment
echo "Deployment $(date): SUCCESS" >> logs/deployments.log
```

### 8.3 Rollback Procedure

**If deployment fails validation (Step 7)**:

**Step 1: Emergency Shutdown**

Follow Section 3.2 (Emergency Shutdown)

**Step 2: Revert Code**

```bash
# Rollback to previous commit
git log -5  # Identify last working commit
git checkout <PREVIOUS_COMMIT_HASH>

# Or rollback to backup branch
git checkout backup/pre-deploy-YYYYMMDD_HHMMSS
```

**Step 3: Restore Data** (if backup taken)

```bash
# Only if data schema changed
rm -rf data/
cp -r backups/data/YYYYMMDD/ data/
```

**Step 4: Restart Previous Version**

Follow Section 1 (System Startup)

**Step 5: Validate Rollback**

- [ ] System returns to operational state
- [ ] Dashboard functional
- [ ] Strategies running normally
- [ ] No errors in logs

**Step 6: Document Rollback**

```bash
echo "Deployment $(date): ROLLBACK - Reason: [REASON]" >> logs/deployments.log
```

**Step 7: Create Incident Report**

Document:
- What was deployed
- What failed
- Why rollback was necessary
- Lessons learned
- Prevention strategy

---

## 9. Disaster Recovery Plan

### 9.1 Disaster Scenarios

**Scenario 1: Complete System Failure**
- Hardware failure
- OS corruption
- Catastrophic software bug

**Scenario 2: Data Loss**
- Storage failure
- Accidental deletion
- Corruption across all data

**Scenario 3: Security Breach**
- Unauthorized access
- Code injection
- API key compromise

**Scenario 4: External Dependency Failure**
- Broker API unavailable (extended outage)
- All data sources offline
- GitHub/Git server down

### 9.2 Disaster Recovery: Complete System Failure

**Recovery Time Objective (RTO)**: 4 hours
**Recovery Point Objective (RPO)**: 24 hours (daily backups)

**Prerequisites**:
- Access to backup system or new hardware
- GitHub access (code repository)
- Backup data (if available)

**Recovery Steps**:

**Step 1: Provision New Environment**

```bash
# On new system
# Install Anaconda
wget https://repo.anaconda.com/archive/Anaconda3-latest-MacOSX-x86_64.sh
bash Anaconda3-latest-MacOSX-x86_64.sh

# Clone repository
git clone https://github.com/bennetwi92/SolaraAlpha.git
cd SolaraAlpha

# Create conda environment
conda env create -f environment.yml
conda activate solaraalpha
```

**Step 2: Restore Configuration**

```bash
# Configuration files are in Git - already restored
# Verify:
ls config/*.yaml

# If custom environment variables needed:
# export GITHUB_TOKEN="..."
# export ALPACA_API_KEY="..."
```

**Step 3: Restore Data**

**Option A: Restore from Backup** (if available):
```bash
# Copy backup data to new system
scp -r backup-server:/backups/solaraalpha/data/ ./data/
```

**Option B: Re-fetch from Sources** (if no backup):
```bash
# Fresh backfill of all data
python scripts/backfill_data.py --all

# Validate data integrity
python scripts/validate_strategies.py
```

**Step 4: Validate System**

```bash
# Run full test suite
pytest tests/ --cov=src

# Start system in safe mode (monitoring only, no trading)
python -m uvicorn src.api.dashboard_api:app --reload --port 8000
```

**Step 5: Gradual Resumption**

- **Day 1**: Monitoring only, validate data freshness
- **Day 2**: Enable one strategy (lowest risk)
- **Day 3+**: Enable remaining strategies if all healthy

**Step 6: Post-Recovery Audit**

- Review all logs for anomalies
- Verify position consistency
- Reconcile P&L with broker
- Document incident and recovery

### 9.3 Disaster Recovery: Data Loss

**Recovery Steps**:

**Step 1: Assess Scope**

```bash
# Check data directory
ls -lR data/

# Identify what's missing/corrupted
python -c "
from pathlib import Path
expected_symbols = ['SPY', 'AAPL', 'MSFT', 'BTC/USDT', 'ETH/USDT']  # etc
for symbol in expected_symbols:
    # Check existence
    files = list(Path('data').rglob(f'*{symbol}*/*.parquet'))
    if not files:
        print(f'MISSING: {symbol}')
"
```

**Step 2: Attempt Data Recovery**

```bash
# Check Git history (if data was versioned - NOT recommended for large files)
git log --all --full-history -- data/

# Check system backups (Time Machine, etc)
# ls /Volumes/Backup/SolaraAlpha/data/
```

**Step 3: Re-fetch All Data**

```bash
# Complete refresh
python scripts/backfill_data.py --all --force

# Extend historical window if needed
python -c "
from src.data.ingest.ingest_manager import IngestManager
from datetime import datetime, timedelta

manager = IngestManager()

# Fetch 2 years of historical data for key symbols
start_date = datetime.now() - timedelta(days=730)
for symbol in ['SPY', 'AAPL', 'MSFT']:  # Add all symbols
    manager.backfill_symbol(
        asset_class='equities',
        symbol=symbol,
        frequency='1d',
        start_date=start_date,
        force=True
    )
"
```

**Step 4: Validate Restoration**

```bash
# Run full validation
python scripts/validate_strategies.py

# Verify data completeness
python -c "
from src.data.storage.parquet_storage import ParquetStorage
storage = ParquetStorage()

symbols = [('equities', 'SPY'), ('crypto', 'BTC/USDT')]  # etc
for asset_class, symbol in symbols:
    df = storage.load(asset_class, symbol, '1d')
    print(f'{symbol}: {len(df)} rows, range {df.index[0]} to {df.index[-1]}')
"
```

**Step 5: Resume Operations**

Follow normal startup (Section 1)

### 9.4 Disaster Recovery: Security Breach

**Immediate Response**:

**Step 1: HALT ALL OPERATIONS**

```bash
# Emergency shutdown
# Follow Section 3.2 (Emergency Shutdown)

# Kill all processes
pkill -9 -f "strategy_runner|orchestrator|uvicorn"
```

**Step 2: Isolate System**

```bash
# Disconnect from network (if breach is active)
sudo ifconfig en0 down  # macOS - disables WiFi

# Or physical disconnect
```

**Step 3: Preserve Evidence**

```bash
# Copy all logs
mkdir -p security_incident/$(date +%Y%m%d_%H%M%S)
cp -r logs/ security_incident/$(date +%Y%m%d_%H%M%S)/

# Capture system state
ps aux > security_incident/$(date +%Y%m%d_%H%M%S)/processes.txt
netstat -an > security_incident/$(date +%Y%m%d_%H%M%S)/network.txt
ls -lR > security_incident/$(date +%Y%m%d_%H%M%S)/filesystem.txt

# Git status (check for unauthorized changes)
git status > security_incident/$(date +%Y%m%d_%H%M%S)/git_status.txt
git log -10 > security_incident/$(date +%Y%m%d_%H%M%S)/git_log.txt
```

**Step 4: Revoke Credentials**

```bash
# Revoke GitHub tokens
# Visit: https://github.com/settings/tokens
# Revoke all tokens, regenerate

# Revoke broker API keys
# Visit broker dashboard, revoke and regenerate

# Rotate any other API keys
```

**Step 5: Forensic Analysis**

```bash
# Check for unauthorized code changes
git diff HEAD  # Should be empty if no local changes

# Check for suspicious files
find . -type f -mtime -1  # Files modified in last 24 hours

# Check for unauthorized access
grep "ERROR\|Failed authentication" logs/app.log
```

**Step 6: Clean Rebuild**

```bash
# On clean system:
# 1. Clone fresh from GitHub
git clone https://github.com/bennetwi92/SolaraAlpha.git

# 2. Verify commit hash matches known good state
git log -1

# 3. Rebuild environment from scratch
conda env create -f environment.yml

# 4. Use NEW credentials only
```

**Step 7: Security Hardening**

- Enable 2FA on all accounts
- Review access logs
- Implement IP whitelisting (if available)
- Add intrusion detection monitoring
- Schedule security audit

**Step 8: Controlled Restart**

- Follow Section 1 (System Startup)
- Monitor for 72 hours continuously
- Review logs hourly for anomalies

**Step 9: Incident Report**

Document:
- Timeline of breach discovery
- Attack vector (if identified)
- Systems compromised
- Data accessed
- Credentials rotated
- Security improvements implemented
- Lessons learned

### 9.5 Business Continuity Contacts

**Emergency Contacts**:
```
CEO: ceo@solaraalpha.com | +1-XXX-XXX-XXXX
IRM Lead: irm-lead@solaraalpha.com | +1-XXX-XXX-XXXX
Security Officer: security@solaraalpha.com | +1-XXX-XXX-XXXX
Legal: legal@solaraalpha.com | +1-XXX-XXX-XXXX

External Contacts:
Broker Support: broker-support@example.com | 1-800-XXX-XXXX
Data Provider: data-support@example.com | 1-800-XXX-XXXX
```

**Notification Requirements**:
- Security breach → Notify CEO + Legal immediately
- Data loss → Notify IRM Lead within 1 hour
- System failure → Notify CEO within 4 hours
- Extended outage (>24h) → Notify all stakeholders

---

## Appendix A: Quick Reference Commands

**System Startup**:
```bash
# Terminal 1: IRM Dashboard
conda activate solaraalpha && python -m uvicorn src.api.dashboard_api:app --reload --port 8000

# Terminal 2: Strategy Runner
conda activate solaraalpha && python src/execution/strategy_runner.py

# Terminal 3: Orchestrator (optional)
conda activate solaraalpha && export GITHUB_TOKEN="..." && python src/orchestrator/orchestrator.py
```

**Data Operations**:
```bash
# Backfill all data
python scripts/backfill_data.py --all

# Backfill single symbol
python scripts/backfill_data.py --symbol AAPL --asset-class equities --force

# Validate data
python scripts/validate_strategies.py
```

**Monitoring**:
```bash
# Watch logs
tail -f logs/app.log

# Watch errors
tail -f logs/app.log | grep -E "ERROR|CRITICAL"

# Check circuit breaker status
grep "CIRCUIT BREAKER" logs/app.log | tail -5
```

**Risk Management**:
```bash
# Calculate risk metrics
python -c "from src.risk import RiskManager; rm=RiskManager(portfolio_value=100000); m=rm.calculate_risk_metrics(); print(f'VaR:\${m.var_95:,.2f} Sharpe:{m.sharpe_30d:.2f}')"

# Check exposure
python -c "from src.risk import RiskManager; rm=RiskManager(portfolio_value=100000); e=rm.get_portfolio_exposure(); print(f'Gross:\${e[\"gross\"]:,.2f} Net:\${e[\"net\"]:,.2f}')"
```

**Emergency Shutdown**:
```bash
# Graceful (preferred)
Ctrl+C in each terminal (strategy runner, dashboard, orchestrator)

# Force kill (last resort)
pkill -9 -f "strategy_runner|orchestrator|uvicorn"
```

---

## Appendix B: Troubleshooting FAQ

**Q: Dashboard shows "System Status: DOWN"**
- A: Check if strategy runner is running: `ps aux | grep strategy_runner`
- Solution: Restart strategy runner (Section 1.4)

**Q: Strategies not executing trades**
- A: Check circuit breaker status in dashboard
- Solution: If active, review reason and consider reset (Section 5.3)

**Q: Data staleness warnings**
- A: Run backfill: `python scripts/backfill_data.py --all`
- If persists: Check internet connectivity and data source status

**Q: "Conda environment not found"**
- A: Recreate environment: `conda env create -f environment.yml`

**Q: "Module not found" errors**
- A: Activate environment: `conda activate solaraalpha`
- If persists: Reinstall dependencies: `conda env update -f environment.yml --prune`

**Q: Dashboard not accessible**
- A: Check if API server running: `ps aux | grep uvicorn`
- Check port 8000 not in use: `lsof -i :8000`
- Solution: Restart API server (Section 1.3)

**Q: Git pull fails with merge conflicts**
- A: Stash local changes: `git stash`
- Pull: `git pull origin main`
- Review stash: `git stash show -p`
- Apply if safe: `git stash pop`

**Q: "Permission denied" errors**
- A: Check file permissions: `ls -la`
- Fix: `chmod +x scripts/*.py`

**Q: High memory usage**
- A: Check processes: `top -o MEM`
- Strategies may be accumulating data in memory
- Solution: Restart strategy runner periodically (nightly)

**Q: Slow dashboard updates**
- A: Check WebSocket connection in browser console
- Clear browser cache and refresh
- Restart API server if needed

---

## Appendix C: Configuration Files Reference

**config/risk_limits.yaml**:
```yaml
# NOTE: All percentages stored as whole numbers (5.0 = 5%, 200.0 = 200%)

position_limits:
  per_strategy_pct: 5.0        # Max 5% of portfolio value per strategy
  per_asset_pct: 10.0          # Max 10% of portfolio value per asset
  gross_exposure_pct: 200.0    # Max 200% gross exposure (long + short)
  net_exposure_pct: 20.0       # Max 20% net exposure (for market-neutral)

drawdown:
  max_drawdown_pct: 10.0       # Automatic halt at 10% drawdown from HWM

risk_metrics:
  var_confidence: 0.95         # 95% confidence level for VaR
  var_lookback_days: 252       # 1 year of daily returns
  cvar_confidence: 0.95        # 95% confidence for CVaR
  sharpe_windows: [30, 90, 365]
  benchmark_symbol: "SPY"
  benchmark_asset_class: "equities"
```

**config/data_sources.yaml**:
```yaml
equities:
  source: yfinance
  symbols:
    - SPY
    - AAPL
    - MSFT
    # ... more symbols
  frequencies:
    - 1d
  validation:
    max_spike_pct: 0.10  # 10% max price spike
    max_gap_days: 5
    staleness_hours: 24

crypto:
  source: ccxt_binance
  symbols:
    - BTC/USDT
    - ETH/USDT
    # ... more pairs
  frequencies:
    - 1h
    - 1d
  validation:
    max_spike_pct: 0.20  # 20% for crypto volatility
    max_gap_days: 0      # No gaps in 24/7 markets
    staleness_hours: 2
```

---

## Appendix D: Revision History

| Version | Date       | Author | Changes |
|---------|------------|--------|---------|
| 1.0.0   | 2025-10-27 | System | Initial creation - comprehensive operations runbook |

---

## Appendix E: Related Documentation

- [IRM Dashboard User Guide](./irm_dashboard_guide.md)
- [Data Pipeline Documentation](../src/data/CLAUDE.md)
- [Risk Management Framework](../src/risk/CLAUDE.md)
- [Execution System Documentation](../src/execution/CLAUDE.md)
- [Monitoring & Alerting](../src/monitoring/CLAUDE.md)

**For Questions or Issues**:
- Create GitHub issue: https://github.com/bennetwi92/SolaraAlpha/issues
- Contact IRM Team: irm-lead@solaraalpha.com

---

**END OF RUNBOOK**
