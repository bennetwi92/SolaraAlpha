# 0019 — Running the agent stages unattended

**Context.** The validator (#118), research (#119) and monitor (#121) skills run by hand in
Claude Code. For Sunday and the weekdays to be hands-off they must run after the scan and the
daily job, read the run from the hosted DB (ADR 0013) and write back through `record.py`
(ADR 0016). ADR 0010 noted this needs a key or subscription token; #92 asked the same
runner question for the Jet2 refresh. The "free data sources" rule covers data, not the Claude
token (#92), but spend must be bounded and nothing may spend until the owner says so.

**Decision.**
- **Runner: GitHub Actions + `anthropics/claude-code-action@v1`**, in `.github/workflows/
  agents.yml`, triggered by `workflow_run` on "Weekly pipeline" and "Daily check" (completed,
  any conclusion) and by `workflow_dispatch` (`mode` weekly | daily, `run_id`). It shares the
  `book` concurrency group with the scan and the daily job. The same runner answers #92: the
  Jet2 refresh is another `claude-code-action` job with its own prompt, PR output and
  no auto-merge; a Claude Code cloud scheduled task would need the same token and would not
  show its log next to the scan's, so Actions wins.
- **Inert until the owner adds one secret.** The `gate` job checks that
  `CLAUDE_CODE_OAUTH_TOKEN` (subscription: `claude setup-token`) or `ANTHROPIC_API_KEY` (pay per
  use: set a spend limit) is set, and that `SOLARA_TURSO_URL` / `SOLARA_TURSO_TOKEN` are set. If
  not, it writes "agents skipped: secrets not configured" to the summary and every other job is
  skipped. **No spend happens otherwise** (§6 Q5).
- **Weekly mode**, in order: `/validate TICKER --run N` for each screen-ACT ticker (a matrix
  job, one invocation per ticker); `/research TICKER --run N` for each ticker whose final label
  is ACT after validation; `plan.py --run N`; `weekly_pack.py --run N --report
  $GITHUB_STEP_SUMMARY`. **Daily mode**: `/monitor --run N --position ID` for each position
  with an agent trigger in the daily run. The run id is the dispatch input, else the latest
  `scan` / `daily` run in the DB (`record.py latest-run`).
- **Cost guardrails.** `max_agent_tickers` (config/portfolio.toml, placeholder 10) caps the
  tickers per stage, taken in `rank_score` order (`record.py queue`); `--max-turns` (60) per
  invocation; a `timeout-minutes` per job (90); allowed tools limited to our scripts via Bash,
  file reads/writes, WebSearch and WebFetch.
- **Failures are reported, not fatal** (S12.6). A failed invocation (error or turn limit)
  records an ERROR verdict for that ticker via `record.py error` and the matrix carries on;
  `record.py outcome` prints the counts to the summary and fails the job only if every item
  of a stage errored.
- The agents write only through `record.py` (ADR 0016): labels can only be downgraded, orders
  are never touched (ADR 0018).

**Owner actions (the build does not wait on them).** Add **one** repository secret:
`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` (with a spend limit at Anthropic). The Turso
secrets already exist. Then dispatch "Agents" once with `mode = weekly` on the latest scan run
and read the summary.

**Consequences.** The weekly run costs up to `max_agent_tickers` validate + research
invocations plus the monitor's daily ones; cost and runtime logging per run is #139,
notifications #132. `claude-code-action` is pinned to its `v1` major tag; bump deliberately.
Until a secret exists the Sunday flow stays: scan → (`/validate`, `/research` by hand) →
`plan.py --run N` → `weekly_pack.py`.
