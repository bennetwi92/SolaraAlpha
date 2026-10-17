# 0021 — Owner notifications: only when something needs the owner

**Status:** accepted · 2026-10 · issue #132 (story map S7.8, S12.7; §6 Q7 default taken)

## Context

The pipeline, the daily check and the agents run unattended (ADR 0017, 0019). The owner should
hear from them when there is something to do (approve the week's plans, enter a fill, act on a
stop, decide a revision, look at an error) and otherwise nothing. The delivery channel is an
open question (§6 Q7); nothing may cost money or need a new secret.

## Decision

- **Default channel: a GitHub issue "Solara inbox"**, labelled `inbox`. It is free, reaches the
  owner's phone through GitHub notifications, keeps a history, and needs only the workflow's
  `GITHUB_TOKEN` with `issues: write`. The workflow step finds the open `inbox` issue (or
  creates it) and comments with `gh`.
- **Content** comes from `scripts/notify.py --mode daily|weekly --run N`
  (`solaraalpha.notify`), which writes markdown to the channel and **exits 3 when there is
  nothing to send**; the step then posts nothing.
  - **Daily** (after the daily check and after the monitor agents): sent only if the run has an
    owner-action trigger (`stop_close`, `stop_touched`, `target_hit`, `entry_break`,
    `order_stale`, `event_due`, `dividend`, `split`), a plan revision proposed since the run
    started, or an ERROR label or agent card. One line per item with the command that acts on
    it. The agents job sends only revisions and errors (`--only revisions,errors`), so the
    price triggers are not repeated.
  - **Weekly**: the weekly pack's headline sections (New plans, Open positions, Stale orders,
    Portfolio, Errors) plus a link to the job summary, which holds the full pack and the
    pipeline report.
- **Pluggable**: one `send(markdown)` function per channel in `notify.CHANNELS`; `stdout` and
  `github-issue` are built. Email via SMTP (a free app password in the secrets) is the next
  option and is not built; Notion or a push service would be another `send`.

## Consequences

- Silence is the default: a day without owner actions, proposals or errors posts nothing.
- The inbox issue grows one comment per event; the owner closes and the workflow recreates it
  if they want a fresh one (it looks up the *open* issue with the label).
- Permissions stay minimal: `contents: read`, `issues: write` on the posting jobs only.
