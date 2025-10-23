# Create Skill

## When
Capturing reusable pattern (≥3x), documenting best practices, preventing mistakes

## Structure

```markdown
# Skill Name

## When
[One-line trigger]

## Decision Tree (if applicable)
- If X → do A
- If Y → do B

## Commands/Workflow
```bash
# Copy-paste ready commands
```

## Patterns (if applicable)
[Code patterns with inline comments]

## Example (ONE minimal)
[Bare minimum working example]

## Gotchas
- [One-liner critical issue]
- [One-liner fix]
```

## Workflow

**1. Validate** (occurred ≥3x? causes errors if wrong? reusable?)

**2. Extract Pattern**:
- What's the recurring operation?
- What decisions must be made?
- What commands/code are needed?

**3. Write Skill** (target 50-80 lines):
- When: One-line trigger
- Decision tree: If/then logic
- Commands: Copy-paste ready
- ONE example (minimal)
- Gotchas: Critical issues only

**4. Optimize** (eliminate prose):
- Bullets > paragraphs
- Commands > explanations
- Inline comments > separate docs
- One example > multiple

**5. Test** (3 scenarios, validate agent can execute)

## Example

**BEFORE** (verbose tutorial):
```markdown
# Data Validation

## Overview
Data validation is important because...

## Prerequisites
You will need...

## Step-by-Step Guide
1. First, you should...
2. Then, you must...
[20+ lines of explanation]
```

**AFTER** (executable checklist):
```markdown
# Data Validation

## When
Validating market data (OHLCV)

## Commands
```python
validator = DataValidator(interval_minutes=1)
gaps = validator.find_missing_bars(df)
assert len(gaps) == 0, f"Missing bars: {gaps}"
```

## Gotchas
- Use `interval_minutes` not `frequency`
- Check both missing AND duplicate timestamps
```

## Gotchas

- Wait for 3rd occurrence (no premature Skills)
- Agents need decisions + commands, not tutorials
- Target 50-80 lines (not 200+)
- One example maximum
- Test with real agent execution
