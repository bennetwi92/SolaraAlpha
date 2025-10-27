# SolaraAlpha Organizational Structure

**Last Updated:** 2025-10-27

## Architecture Model: Hybrid AI-Native

Based on fund_structure_ceo_insights.md, SolaraAlpha implements a four-layer hybrid architecture balancing centralized control with decentralized innovation.

## Four-Layer Architecture

### 1. Governance Layer (Centralized Control)

**Independent Risk Management (IRM)**
- Real-time position monitoring across all strategies
- Automated circuit breakers and kill switches
- Override authority for any strategy breaching limits
- VaR/CVaR calculation and stress testing
- Status: NOT IMPLEMENTED

**Portfolio Allocation Committee (PAC)**
- Dynamic, factor-based capital allocation
- Real-time correlation analysis
- Performance vs. risk budget tracking
- Immediate reallocation authority
- Status: NOT IMPLEMENTED

**Risk & Trading Oversight Committee (RTOC)**
- Strategic kill switch authority
- Human-in-the-loop (HITL) escalation
- Compliance and regulatory oversight
- Final authority on strategy deployment
- Status: NOT IMPLEMENTED (Human role)

### 2. Infrastructure Layer (Centralized Utility)

**Technology & Data Services**
- Standardized data ingestion (yfinance, alpha-vantage, fredapi)
- Centralized data storage and validation
- Common libraries and utilities
- Status: MINIMAL (basic environment.yml exists)

**Execution Research Team**
- Optimized trade execution across all strategies
- Minimize slippage and market impact
- Centralized broker integration
- Order routing and fills management
- Status: NOT IMPLEMENTED

**Monitoring & Observability**
- System health monitoring
- Performance dashboards
- Logging and alerting infrastructure
- Incident response automation
- Status: NOT IMPLEMENTED

### 3. Alpha Generation Layer (Decentralized Innovation)

**Strategy Development Teams (SDTs/Pods)**

Independent, specialized teams focusing on narrow mandates. Each pod operates independently with standardized interfaces to infrastructure.

**Current Pods:**
- None deployed yet

**Target Pod Structure (15+ strategies):**

*Relative Value Pods (Primary Focus):*
- Pairs Trading Pod (Equities)
- Statistical Arbitrage Pod (Multi-asset)
- Spread Trading Pod (Futures/Options)
- Index Arbitrage Pod (ETFs)

*Factor Pods:*
- Momentum Pod (Cross-asset)
- Value Pod (Equities/ETFs)
- Quality Pod (Equities)
- Low Volatility Pod (Multi-asset)

*Time-Series Pods:*
- Mean Reversion Pod (Crypto/Forex)
- Trend Following Pod (Commodities/Futures)
- Volatility Trading Pod (Options)

*Asset-Specific Pods:*
- Crypto Microstructure Pod
- Fixed Income Arbitrage Pod
- FX Carry Pod

### 4. Interface Layer (Human-AI Collaboration)

**CEO (ceo-orchestrator)**
- Strategic direction and planning
- Organizational design
- Resource allocation and prioritization
- Blocker resolution
- Status: ACTIVE (this role)

**Task Builder (task-builder agent)**
- Decomposes requirements into atomic tasks
- Creates optimized GitHub issues
- Estimates complexity
- Status: AVAILABLE

**Team Builder (team-builder agent)**
- Designs and optimizes agents
- Writes agent prompts
- Researches role requirements
- Status: AVAILABLE

**Orchestrator (orchestrator.py)**
- Automated task execution
- GitHub issue processing (FIFO)
- Status label management
- Status: IMPLEMENTED (src/orchestrator/orchestrator.py)

**Specialized Agents:**
- Available per AGENT_GUIDE.md
- Used as needed for specialized work

## Agent Roles & Responsibilities

### Strategic Leadership

**ceo-orchestrator (THIS ROLE)**
- Define vision, mission, strategic objectives
- Design organizational structure
- Prioritize initiatives via RICE framework
- Create tasks via task-builder
- Monitor metrics and KPIs
- Resolve blockers
- Document all decisions

### Operational Execution

**task-builder**
- Convert requirements → GitHub issues
- Decompose complex features
- Estimate effort and complexity
- Reference appropriate Skills for agent/skill creation

**team-builder**
- Design specialized agents for new capabilities
- Optimize existing agent prompts
- Research role requirements
- Document agent specifications

**orchestrator.py**
- Process type:task issues (no status: labels)
- Pure FIFO order (oldest first)
- Manage status: labels (in-progress, blocked)
- Post execution logs to issues
- Close completed tasks

### Specialized Functions

**quant-alchemist**
- Quantitative investment analysis
- Statistical modeling, ML
- Risk-adjusted metrics, volatility forecasts

**scenario-architect**
- Stress testing, Monte Carlo simulation
- VaR/CVaR calculation
- Tail risk analysis

**historical-pattern-seeker**
- Historical analysis (50+ years)
- Pattern recognition
- Regulatory precedent research

**contrarian-heretic**
- Challenge consensus assumptions
- Red-team analysis
- Premortem failure scenarios
- Bias mitigation

**security-sentinel**
- SAST, OWASP Top 10 checks
- Secrets auditing
- Trading system hardening

**test-oracle**
- Test coverage verification (>85%)
- Edge case validation
- Regression protection

**code-aligner**
- Code quality enforcement
- Complexity analysis
- Refactoring recommendations

**latency-hunter**
- Performance optimization
- Bottleneck identification
- Query optimization

**system-cartographer**
- Architecture evaluation
- Scalability analysis
- Dependency mapping

## Interaction Protocols

### Task Creation Flow
1. CEO identifies need → uses task-builder
2. task-builder creates GitHub issue (type:task, priority:*)
3. Orchestrator fetches issues (no status: labels)
4. Orchestrator processes FIFO (oldest first)
5. Orchestrator updates status: (in-progress → [no label]/closed OR blocked)

### Agent Creation Flow
1. CEO identifies capability gap → uses task-builder
2. task-builder creates issue referencing create-agent Skill
3. Orchestrator assigns to appropriate agent
4. New agent documented in /org/structure.md

### Decision Escalation
- Operational decisions: CEO autonomous
- Strategic pivots: Document in /org/decisions.md
- Technical blockers: Assign to specialized agent
- Risk breaches: IRM override (when implemented)

## Current State Assessment

**Implemented:**
- CEO role (active)
- Orchestrator (functional)
- Specialized agents (available via AGENT_GUIDE.md)
- Basic Python environment

**Minimal:**
- Data infrastructure (only environment.yml)
- Testing framework (pytest installed, minimal tests)

**Missing:**
- All governance layer components (IRM, PAC, RTOC)
- Infrastructure layer (data services, execution, monitoring)
- All strategy pods (alpha generation)
- Risk management systems
- Execution systems
- Performance tracking

## Next Steps

Per RICE prioritization (see /org/strategy.md):
1. Build data infrastructure (foundation)
2. Implement risk management framework
3. Create first pairs trading strategy
4. Deploy monitoring and logging
5. Implement execution layer
6. Scale to multiple strategies
