# 0026 — No owner approval; one order per stock; the inbox says what to do in Trading212

**Status:** accepted (owner, 2026-10-20). Amends 0015 (trade plans), 0021 (notifications), 0024
(auto-applied revisions) and 0025 (agent adds: how they are worked at the broker). Amended by
0030: the daily job reads Trading212 and tells the owner what the book is missing.

**Context.** The owner wants the system low- to no-touch: no approving positions or pipeline
proposals, only being told which orders to submit, then reporting back when they are placed and
whether they filled. Trading212 (Stocks & Shares ISA) offers market, limit, stop (market) and
stop-limit orders and price alerts, and holds **one open order per stock**, so a protective
stop cannot sit next to a target, an exit or an add order. The inbox messages were hard to
follow.

**Decision.**

- **No approvals.** The planner approves each plan as it drafts it (actor `pipeline`), and the
  pipeline's own revisions (the weekly review's pre-results trims and exits) are applied as
  recorded, like the agents' (ADR 0024). `book.py reject` / `undo` remain as overrides; nothing waits for them.
- **One order per stock.** The book never holds two live orders for a stock: an exit, a partial
  exit or an add cancels the live stop order first (its level stays the position's stop for
  heat and the daily triggers); a partial exit's fill, an add's fill, or either ending unfilled
  puts the stop back (approved, to place) for the shares held; a part fill on a buy cancels the
  rest of the order so the shares held get their stop at once; a stop cannot be tightened while
  an exit or add is working. No target orders are placed: a target is a price alert.
- **Breakout entries stay stop market** (`stop_entry_orders = true`, confirmed). Stop-limit
  orders are not used: a capped buy-stop can miss the trade, and a protective stop-limit can fail
  to fill on a gap down.
- **Adds (the monitor's, ADR 0025) are worked as a sequence**, not left at the broker: cancel
  the stop, place the add, then the stop back on everything at the raised level; if the add
  hasn't filled by the close, cancel it and put the stop back.
- **The inbox is instructions in plain words** (`portfolio/broker.py`, `notify.py`): per stock,
  numbered steps in Trading212's terms (*Buy 120 · Stop order · stop price 1,540p*), each
  replaced order's cancellation first, what to place once it fills, the price alerts to set
  (targets), why, and what to tell back (`placed N`, `fill N --qty --price`). Then what to check
  and report (a stop that should have sold, an order past its date, a dividend), alert changes,
  information needing nothing, errors, and the undo commands folded away. Every approved order
  not yet marked placed stays listed until it is.

**Consequences.** The owner's touchpoints are placing orders, setting alerts and reporting
placements and fills. Each automatic change is still auditable and reversible (`book.py undo`).
While an exit or add is worked the stock has no stop order at the broker, for as long as the
owner takes between the steps; the instructions keep that window to minutes.
