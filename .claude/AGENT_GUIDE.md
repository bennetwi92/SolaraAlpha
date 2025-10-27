# Agent Selection

## Agents

**ceo-orchestrator**: Session start, strategy, priorities, org design, create agents, blockers
**task-builder**: GitHub issues from requirements, decompose features, estimate complexity
**team-builder**: Design/optimize agents, research roles, write agent prompts
**skill-builder**: Capture reusable patterns, document best practices, prevent mistakes
**strategy-researcher**: Research novel trading strategies (quantitative, qualitative, historical analysis)
**strategy-developer**: Implement production-ready strategies with tests, backtesting, risk controls
**infrastructure-engineer**: Build/optimize infrastructure, data pipelines, performance, monitoring, CI/CD
**quant-alchemist**: Quantitative investment analysis, statistical modeling, ML, volatility forecasts, risk-adjusted metrics
**scenario-architect**: Forward-looking scenarios, stress testing, Monte Carlo, VaR/CVaR, tail risk, portfolio resilience
**contrarian-heretic**: Challenge consensus, red-team analysis, counter-evidence, premortem failure scenarios, bias mitigation
**Explore**: Codebase navigation, find files/code, understand architecture (quick/medium/thorough)
**general-purpose**: Multi-step tasks, research, no specialized match

## Decision

Session start/strategy? → **ceo-orchestrator**
Create/optimize agent? → **team-builder**
GitHub issue? → **task-builder**
Reusable pattern? → **skill-builder**
Strategy research? → **strategy-researcher**
Strategy implementation? → **strategy-developer**
Infrastructure/pipelines/performance? → **infrastructure-engineer**
Quantitative analysis/modeling? → **quant-alchemist**
Scenario planning/stress testing? → **scenario-architect**
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
