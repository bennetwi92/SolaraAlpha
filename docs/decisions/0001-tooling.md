# 0001 — Tooling

**Status:** accepted

- **uv** for environments and dependencies (fast, single lockfile).
- **ruff** for formatting and linting; **mypy** (non-strict, `check_untyped_defs`) for types.
- **pytest**, with live network tests behind the `network` marker.
- **src layout** (`src/solaraalpha`) so tests and notebooks import the installed package.
- **pandas + parquet** as the default data stack; `@parquet_cache` to avoid re-hitting free APIs.
- **pydantic-settings** for configuration from `.env`.

Rationale: single developer working with Claude Code — minimise setup friction and
make one command (`make check`) the gate for every change.
