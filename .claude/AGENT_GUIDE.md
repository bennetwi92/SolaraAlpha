# Agent Selection

## Agents

**ceo-orchestrator**: Session start, strategy, priorities, org design, create agents, blockers
**task-builder**: GitHub issues from requirements, decompose features, estimate complexity
**team-builder**: Design/optimize agents, research roles, write agent prompts
**skill-builder**: Capture reusable patterns, document best practices, prevent mistakes
**strategy-researcher**: Research novel trading strategies (quantitative, qualitative, historical analysis)
**strategy-developer**: Implement production-ready strategies with tests, backtesting, risk controls
**infrastructure-engineer**: Build/optimize infrastructure, data pipelines, performance, monitoring, CI/CD
**latency-hunter**: Performance optimization, bottleneck identification, complexity analysis, query optimization, memory profiling
**quant-alchemist**: Quantitative investment analysis, statistical modeling, ML, volatility forecasts, risk-adjusted metrics
**scenario-architect**: Forward-looking scenarios, stress testing, Monte Carlo, VaR/CVaR, tail risk, portfolio resilience
**historical-pattern-seeker**: Historical analysis (50+ years), pattern recognition, sector rotation, regulatory precedents, recency bias prevention
**contrarian-heretic**: Challenge consensus, red-team analysis, counter-evidence, premortem failure scenarios, bias mitigation
**security-sentinel**: SAST, OWASP Top 10 checks, vulnerability detection, secrets auditing, API key management, trading system hardening
**code-aligner**: Code quality enforcement, complexity analysis, refactoring recommendations, documentation validation, anti-pattern detection
**system-cartographer**: Architecture/resilience evaluation, scalability bottleneck identification, IaC design, cost optimization, dependency mapping
**Explore**: Codebase navigation, find files/code, understand architecture (quick/medium/thorough)
**general-purpose**: Multi-step tasks, research, no specialized match

## Decision

Session start/strategy? → **ceo-orchestrator**
Create/optimize agent? → **team-builder**
GitHub issue? → **task-builder**
Reusable pattern? → **skill-builder**
Strategy research? → **strategy-researcher**
Strategy implementation? → **strategy-developer**
Infrastructure/pipelines? → **infrastructure-engineer**
Performance/optimization/bottlenecks? → **latency-hunter**
Quantitative analysis/modeling? → **quant-alchemist**
Scenario planning/stress testing? → **scenario-architect**
Historical patterns/precedents? → **historical-pattern-seeker**
Challenge consensus/identify risks? → **contrarian-heretic**
Security review/vulnerabilities/SAST? → **security-sentinel**
Code review/quality/refactoring? → **code-aligner**
Architecture/scalability/dependencies? → **system-cartographer**
Explore codebase? → **Explore**
Multi-step? → **general-purpose**
Single-step? → Direct tool (Read/Edit/Grep/Glob/Bash)

## Rules

Direct tools > Agents (single-step)
Specialized > general-purpose
Known file? Read, not Explore
Simple edit? Edit, not agent

## Workflow Protocol

**CEO identifies need** → Uses **task-builder** to create issue → Orchestrator processes
**Never**: Create GitHub issues directly (task-builder optimizes)
**Agent/Skill tasks**: task-builder adds Skill reference to issue
