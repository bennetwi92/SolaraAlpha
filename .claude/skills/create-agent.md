# Create Agent

## When

**Create**: Recurring (≥3x), specialized knowledge, multi-step, parallelizable
**Don't**: One-off, general-purpose fits, simple ops

## Workflow

**1. Validate** (has occurred ≥3 times? requires expertise? multi-step?)

**2. Research** (15 min):
- WebSearch authoritative guides
- Extract: core responsibilities, decision patterns, quality standards

**3. Design**:
```markdown
Identity: [2-3 sentences: who, function, differentiator]

Responsibilities (4-8):
- [Specific, actionable, priority-ordered]

Workflow:
1. [Step-by-step for common task]
2. [Include decision points]

Quality Standards: [Measurable criteria]

Key Principles (3-7): [Behavior-driving]
```

**4. Optimize Tokens** (40-60% reduction):
- Bullets > prose
- Imperatives ("Create X" not "You should create X")
- Cut filler ("in order to" → "to")
- Active voice only

**5. Test** (3 real scenarios, validate quality)

## Frontmatter

```yaml
---
name: agent-name
description: One-line trigger

<example>
user: "Trigger phrase"
assistant: "Using agent-name to [action]"
<commentary>Why this vs others</commentary>
</example>

model: sonnet
color: purple
---
```

## Optimization

**Eliminate**:
- "in order to" → "to"
- "it is important" → delete
- "please ensure" → "ensure"
- "you should" → direct imperative

**Ask per sentence**:
- Changes behavior? (No → delete)
- Implied elsewhere? (Yes → delete)
- Fewer words? (Yes → rewrite)

## Example

```markdown
BEFORE (42 tokens):
"When you encounter a situation where you need to validate data,
you should carefully check for missing values and duplicates."

AFTER (8 tokens):
"Validate data: check missing values, duplicates."
```

## Gotchas

- Wait for 3rd occurrence (no premature agents)
- Research drives design (study real practitioners)
- Token discipline mandatory (40-60% cut)
- Test with real SolaraAlpha tasks
- Update `.claude/AGENT_GUIDE.md` after creation
