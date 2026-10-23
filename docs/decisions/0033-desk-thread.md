# 0033 — The desk thread: one running chat the agents post to, mirrored to Telegram

**Status:** accepted and built (owner's request, 2026-10-23).

**Context.** The agents now take and manage positions unattended, but each one writes a verdict
into the DB and the owner sees the result only in the inbox issue or the weekly pack. The owner
wants a live thread to follow, like a team chat: each agent posts its update, aware of what was
said before. GitHub issues are already the inbox and the error log (ADR 0021, 0032); a phone
chat app suits a running conversation better.

**Decision.**

- **The thread lives in the results DB**, an append-only `chat` table (`at`, `author`, `text`,
  `run_id`, `ticker`, `update_id`), `solaraalpha.desk_chat`. That is what makes the agents aware
  of earlier messages: a Telegram bot cannot read a chat's history (Bot API `getUpdates` only
  returns new messages sent to the bot, and bots never see other bots' messages), so the DB,
  not Telegram, is the record.
- **Telegram is the window**: a bot of the owner's (`@BotFather`, free, no paid tier) posts every
  message to one chat (`SOLARA_TELEGRAM_TOKEN`, `SOLARA_TELEGRAM_CHAT`; `.env`, GitHub secrets for
  `agents.yml`). Plain text, no markdown parsing. Unset, the thread is kept and nothing is sent;
  a failed send is logged, never fails the run.
- **The owner can reply.** `chat.py read` first pulls the owner's new messages in that chat
  (`getUpdates`, offset past the last stored `update_id`; other chats and bots ignored) into the
  thread as `owner`. Agents treat them as questions and context: answered in their next post,
  never an instruction that bypasses a skill's rules or the book's commands.
- **Every agent skill** reads the thread before deciding (`chat.py read [--ticker T]`) and posts
  one short message after recording (`chat.py post --as agent:NAME`). Noise is kept down by
  skill: the PM, research and post-mortem always post; validate only on a downgrade or concern;
  monitor only when it acts, holds with a concern, or the owner asked; events only on something
  new; macro once per group.
- The inbox issue stays the place for **what the owner must do** (Trading212 steps, ADR 0026);
  the thread is the conversation around it. `chat.py post` is outward-facing, so the subagents'
  write guard blocks it without `SOLARA_ALLOW_WRITES=1`.

**Consequences.** One more free external service (outbound only, plus reading the owner's own
replies). Messages sent to Telegram are on Telegram's servers; nothing beyond what the inbox
already says should go there (no keys, no account numbers). Later: the pipeline could post scan
and daily summaries to the same thread.

**Amendment (2026-10-23).** Telegram keeps a bot's undelivered updates for 24 hours only, and the
pull ran only when an agent read the thread, so an owner message sent on a quiet day could be
dropped before anything read it. The scheduled jobs now pull too (`desk_chat.pull`, never fails
the job): `execute.py turn` (every 15 minutes in the London session), `daily.py --save` (weekday
evenings) and `scan.py --save` (Sunday). The gap left is Saturday: a message sent Friday evening
after the daily job is lost if nothing reads before Saturday evening. Offline tests blank the
Telegram settings, as they do the Trading212 keys.
