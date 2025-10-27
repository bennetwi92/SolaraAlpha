# SolaraAlpha Organization Directory

This directory contains the organizational structure, strategy, and operational documentation for SolaraAlpha.

## Structure

### Core Documents
- **vision.md** - Mission, vision, strategic objectives, success metrics
- **strategy.md** - Strategic roadmap, RICE prioritization, quarterly initiatives
- **structure.md** - Organizational architecture, agent roles, interaction protocols
- **metrics.md** - KPIs, performance tracking, leading/lagging indicators
- **decisions.md** - Decision log with timestamp, rationale, alternatives, metrics

### Subdirectories
- **/processes/** - Standard operating procedures, workflow documentation
- **/knowledge/** - Knowledge base, learnings, best practices
- **/knowledge/postmortems/** - Incident analysis, failure learnings
- **/tasks/** - Active task tracking, assignments, statuses (optional - GitHub issues are primary)

## Usage

### Session Initialization
Every CEO session should:
1. Load state: vision.md, strategy.md, structure.md, metrics.md, tasks/*
2. Assess: progress vs objectives, task statuses, metric changes
3. Prioritize: apply RICE to rank work
4. Decide: determine highest-value action
5. Execute/delegate: direct or via Task/Agent tools
6. Update: log decisions, update statuses, refresh metrics

### Decision Making
Use decision framework:
1. **Alignment**: Advances vision.md objectives?
2. **Feasibility**: Within agent constraints?
3. **Priority**: Highest RICE score?
4. **Risk**: What fails? Mitigation in decisions.md
5. **Measurement**: Success metrics defined upfront?

### Metrics Updates
Update metrics.md:
- **Daily** during active development/trading
- **Weekly** during setup phase
- **Monthly** comprehensive review

### Documentation Standards
All decisions in decisions.md need:
- ISO timestamp
- Context (why needed)
- Options considered with pros/cons
- Decision and rationale
- Expected metrics
- Next actions

## Maintenance

**Owner:** CEO (ceo-orchestrator)

**Update Frequency:**
- vision.md: On strategic direction changes
- strategy.md: Per milestone/pivot
- structure.md: On agent/role changes
- metrics.md: Every session
- decisions.md: Per significant decision

**Review Schedule:**
- Weekly: Metrics review, blocker assessment
- Monthly: Strategic review, roadmap updates
- Quarterly: Comprehensive evaluation, pivot decisions
