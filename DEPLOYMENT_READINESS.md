# SolaraAlpha Deployment Readiness Checklist

## Purpose
This checklist ensures legal compliance and operational readiness before deploying SolaraAlpha to production environments (live trading with real capital).

**CRITICAL**: Yahoo Finance is ONLY authorized for Phase 1 (R&D/prototype). Commercial trading requires migration to a commercial-friendly data source.

---

## Phase 1: Development/Prototype (Current)
**Status**: ✅ APPROVED for research, backtesting, strategy development

**Authorized Activities**:
- Strategy backtesting on historical data
- Algorithm development and testing
- Performance analysis and optimization
- Paper trading simulations (no real capital)

**Data Source**: Yahoo Finance via yfinance (personal use exemption)

**Environment Variable**: `SOLARA_ENV=development` (default)

---

## Phase 2: Staging/Paper Trading
**Status**: ⚠️ REQUIRES REVIEW before deployment

### Checklist Items
- [ ] All strategies validated via backtesting (Phase 1)
- [ ] Paper trading infrastructure tested (no real capital)
- [ ] Risk management systems validated
- [ ] Performance monitoring in place
- [ ] Data source: Yahoo Finance still acceptable (no real capital deployed)

**Environment Variable**: `SOLARA_ENV=staging`

**Data Source**: Yahoo Finance acceptable (paper trading = non-commercial)

---

## Phase 3: Production/Live Trading
**Status**: ❌ BLOCKED - Data source migration required

### CRITICAL: Data Source Migration Required

**BLOCKER**: Yahoo Finance Terms of Service restrict usage to "personal use only." Live trading with real capital constitutes commercial use and violates Yahoo's ToS.

**Required Action**: Migrate to commercial-friendly data source BEFORE production deployment.

### Pre-Production Checklist

#### 1. Data Source Migration (BLOCKER)
- [ ] **Migrate from Yahoo Finance to Twelve Data** (or other commercial-friendly source)
- [ ] Twelve Data API key obtained (sign up at https://twelvedata.com/register)
- [ ] API key stored in environment variable: `TWELVE_DATA_API_KEY`
- [ ] Update `/src/data/config.py`: Set `DATA_SOURCE = "twelve_data"`
- [ ] Validate data quality matches Yahoo Finance (run validation script)
- [ ] Test rate limiting (800 calls/day) with production symbol list
- [ ] Implement caching strategy to minimize API calls
- [ ] Document any data discrepancies vs. Yahoo Finance

**Alternative Options** (if Twelve Data insufficient):
- Alpaca Markets (free tier, designed for trading firms)
- Polygon.io (free tier limited to 2 years, insufficient for backtesting but acceptable for live trading)
- IEX Cloud (pay-as-you-go, commercial-friendly)

#### 2. Legal/Compliance Review
- [ ] Terms of Service reviewed for selected data provider
- [ ] Commercial use explicitly authorized in data provider's ToS
- [ ] Legal counsel consulted (recommended for live trading)
- [ ] Data licensing agreements documented
- [ ] Regulatory compliance verified (SEC, FINRA if applicable)

#### 3. Infrastructure Readiness
- [ ] Production environment configured: `SOLARA_ENV=production`
- [ ] Runtime safeguard tested (config.py raises error if Yahoo Finance + production)
- [ ] Risk management limits configured and tested
- [ ] Circuit breakers implemented for data source failures
- [ ] Monitoring and alerting configured
- [ ] Backup data source identified (failover strategy)

#### 4. Capital Deployment Authorization
- [ ] Shareholder approval obtained for capital deployment
- [ ] Risk limits defined and enforced
- [ ] Position sizing algorithms validated
- [ ] Maximum drawdown limits configured
- [ ] Emergency shutdown procedures documented

#### 5. Performance & Monitoring
- [ ] Data quality monitoring (detect gaps, anomalies)
- [ ] Latency benchmarks established
- [ ] Failover procedures tested
- [ ] Incident response plan documented

---

## Runtime Safeguards

### Environment Variable: `SOLARA_ENV`
**Purpose**: Prevent accidental production deployment with Yahoo Finance.

**Values**:
- `development` (default): R&D, backtesting, strategy development
- `staging`: Paper trading, pre-production testing
- `production`: Live trading with real capital

**Enforcement**:
`/src/data/config.py` includes runtime check:
```python
if ENVIRONMENT == "production" and DATA_SOURCE == "yahoo_finance":
    raise ValueError("Yahoo Finance not authorized for production use.")
```

### Testing the Safeguard
```bash
# This should FAIL (raises ValueError):
export SOLARA_ENV=production
python -c "from src.data import config"

# This should SUCCEED:
export SOLARA_ENV=development
python -c "from src.data import config"
```

---

## Migration Timeline Recommendation

**Phase 1 (Current - Development)**: 6-12 months
- Strategy development, backtesting, optimization
- Yahoo Finance acceptable under research exemption

**Phase 2 (Staging - Paper Trading)**: 2-3 months
- Paper trading validation with real market conditions
- Yahoo Finance acceptable (no real capital)

**Phase 3 Transition (Data Migration)**: 1-2 weeks before production
- Migrate to Twelve Data or commercial alternative
- Validate data quality and system performance
- Legal review completed

**Phase 3 (Production - Live Trading)**: Only after ALL checklist items complete
- Real capital deployed
- Commercial data source required

---

## Emergency Procedures

### If Yahoo Finance Fails in Phase 1/2
1. Check yfinance version (requires 0.2.66+)
2. Verify internet connectivity and Yahoo Finance service status
3. Check for IP-based rate limiting (wait 1 hour, retry)
4. If persistent failure: Emergency migration to Twelve Data

### If Production Data Source Fails
1. Halt all trading (close positions or maintain existing)
2. Activate backup data source (if configured)
3. Investigate root cause (API quota exceeded, service outage, network issue)
4. Document incident and update runbook

---

## Approval Signatures

### Phase 1 Approval (Development/Prototype)
- **Status**: ✅ APPROVED
- **Data Source**: Yahoo Finance
- **Authorized By**: SolaraAlpha AI Organization
- **Date**: 2025-10-23
- **Valid Until**: Transition to Phase 2 or 12 months (whichever first)

### Phase 2 Approval (Staging/Paper Trading)
- **Status**: ⏳ NOT YET REQUESTED
- **Required**: All Phase 1 backtesting complete

### Phase 3 Approval (Production/Live Trading)
- **Status**: ⏳ NOT YET REQUESTED
- **Required**: All checklist items above completed + shareholder approval

---

## References
- Data Source Documentation: `/src/data/README_DATASOURCE.md`
- Data Configuration: `/src/data/config.py`
- Legal Considerations: `/src/data/README_DATASOURCE.md` (Legal Considerations section)
- Twelve Data Migration Guide: `/src/data/README_DATASOURCE.md` (Future Migration Path section)
