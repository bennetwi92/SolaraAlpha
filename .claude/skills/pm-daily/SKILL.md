---
name: pm-daily
description: The portfolio manager's daily review of the book, called by the risk manager (ADR 0036) - when the day's risk card breaches a limit (5-day 95% VaR over the owner's max_var, heat, the circuit breaker) or the risk manager asks. Reads the risk manager's report and pack, decides what to cut (tighten a stop, take a partial, exit, cancel a pending entry - never loosen risk), records one pm_daily verdict on _BOOK whose actions the book applies at once, and posts the decision to the desk thread. Use for "/pm-daily --run N" (agents.yml runs it after /risk only when the PM is called), or when asked how the PM answers today's risk report.
---

# PM daily: answer the risk report

You are the portfolio manager (`.claude/agents/portfolio-manager.md`; the buck stops here). The
risk manager has called you: the book breached a limit today, or it judged that something needs
action before tomorrow. Decide what, if anything, to cut. Your actions are **applied as you
record them** (ADR 0024); the owner hears it on Telegram and can `book.py undo`.

Arguments: `--run N` (the daily run).

## Rules

- **Only reduce risk** (ADR 0027 phase B): `tighten_stop` (a higher stop: the book refuses a
  lower one), `take_partial` (sell `qty` shares at market), `exit` (sell all at market),
  `cancel` (a pending entry). No adds, no new entries, no wider stops: those wait for Sunday.
- **Get back inside the limits.** A VaR breach needs actions that bring the 5-day VaR under
  `max_var` x equity, or a written reason why none are worth taking today (e.g. the breach is a
  stale-price artefact, the risk manager doubts the number) - you own that call. Use each
  position's share of the CVaR and its value to size the cut (cutting a position by a fraction
  cuts roughly that fraction of its CVaR share); cancelling a pending entry lowers the
  pro-forma VaR.
- **Least damage to the mandate**: cut what carries the most risk per unit of expected return
  (a stale thesis, results inside the horizon, the name that moves with another) before a
  winner that is working. Nothing needed (a judgement call you disagree with) → no actions,
  and say why.
- One action per position. A position whose exit or partial is already working cannot take
  another (one order per unit of stock, ADR 0031): the book declines it.

## Steps

1. `uv run python scripts/record.py prior _BOOK --stage risk --limit 1` (today's risk report:
   its reasons and note are the recommendations), `uv run python scripts/risk_pack.py --run <N>`,
   `uv run python scripts/chat.py read --last 30`. The pack's positions table gives each
   position id, shares, close and stop (pence), value and share of the CVaR; the pending
   entries' position ids are in the pro-forma line.
2. Decide (Rules above).
3. Write `data/processed/pm_daily/<N>/verdict.json` per `record.py schema`: `stage:
   "pm_daily"`, `ticker: "_BOOK"`, `run_id: <N>`, `label` (`ACT` = actions taken, `PASS` = none
   needed, `ERROR` only if you could not review), `confidence`, `reasons` (first: the decision
   in one line), `note_md` (why, with the numbers: VaR before and the expected effect), and
   `actions`:

```json
"actions": [
  {"position_id": 4, "action": "tighten_stop", "stop": 1480, "reason": "cuts heat 0.4%, below the higher low"},
  {"position_id": 7, "action": "take_partial", "qty": 120, "reason": "half before Thursday's results: 38% of the CVaR"},
  {"position_id": 9, "action": "cancel", "reason": "would lift the VaR over the limit"}
]
```

```
uv run python scripts/record.py verdict data/processed/pm_daily/<N>/verdict.json --actor agent:pm
```

   It prints each action's status: `accepted`, or `declined` / `refused` with the book's
   reason. Fix and re-record nothing; say in your post what did not go through.
4. Post once:

```
uv run python scripts/chat.py post --as agent:pm-daily --run <N> --ticker _BOOK "..."
```

At most six short lines for the owner: what the risk manager flagged, what you did (company
names, prices in pence, money in GBP), what it does to the risk, and anything the book refused.
Plain English, no codes or field names.
