# 0031 — Orders through the Trading212 API, by an execution agent, on the practice account

**Status:** accepted (owner, 2026-10-21; the answers to the open questions are under "Owner's
answers"), not built yet. Amends 0026 (the
owner places every order), 0030 ("never an order") and the one-open-order rule of 0026 / 0030.

**Context.** Since ADR 0026 the owner places every order the book approves, from the inbox.
ADR 0030 gave the system read access and, since its amendment, the book takes in the fills
itself. The owner is still the slowest step: an entry that fills leaves its shares without a
stop until the evening job drafts one and the owner places it, a monitor's raised stop waits
for the owner, a rotation needs the sale placed, then the buy, by hand. Trading212's public API
(docs.trading212.com/api, read 2026-10-21, status **Beta**) also places and cancels orders:

- `POST /equity/orders/market | limit | stop | stop_limit` (sell = negative quantity;
  `timeValidity` `DAY` or `GOOD_TILL_CANCEL`; a market order sent while the market is shut
  waits for the open), `DELETE /equity/orders/{id}`, `GET /equity/orders[/{id}]`. Enabled for
  **Invest and Stocks ISA** accounts; orders only in the account's main currency. Nothing in the
  docs restricts an order type on live.
- **No modify**: an order is changed by cancel + place. "Cancellation is not guaranteed if the
  order is already in the process of being filled."
- **Not idempotent**: "Sending the same request multiple times may result in duplicate orders";
  no client order id.
- **Rate limits per account** (shared by every key on the account): limit / stop / stop-limit 1 per 2 s,
  market and cancel 50 a minute, the order list 1 per 5 s, `GET /{id}` 1 per s.
- Scope `orders:execute` to place and cancel; no price alerts; no fill notifications (poll);
  stops trigger on the last traded price; up to 50 pending orders per ticker.

**Decision.**

- **Practice account only, indefinitely.** Orders go to `demo` until the owner instructs
  otherwise. The code refuses to send to `live` unless a separate setting
  (`SOLARA_T212_TRADING=live`) is set, and that setting is the owner's to add, never an agent's
  or a workflow default. `SOLARA_T212_TRADING=off` (the default) sends nothing.
- **One key with every permission** (the read scopes and `orders:execute`), in the existing
  `SOLARA_T212_KEY` / `SOLARA_T212_SECRET`, replacing the read-only key (owner, 2026-10-21),
  allowed from all IP addresses (GitHub's runners have no fixed address). Trade-off accepted for
  the practice account: every workflow holding the key, agent runs included, *could* place an
  order by calling the API directly, past `execute.py`; only `SOLARA_T212_TRADING` (set in the
  execution job alone) keeps ordinary code from sending. **Before live**, the order permission
  goes back to a key only the execution job gets, the other workflows keeping a read-only one.
- **One order per unit of stock, not per stock** (the owner's rule, restated). Trading212 has no
  one-cancels-other orders, so the sell orders working on a holding never add up to more shares
  than it holds: a stop and a target sell are never both live on the same shares. Buys don't
  compete with the stop: an add or a new entry can work while the stop protects what is held.
  A partial sale cuts the stop to the shares left (cancel + place) and sells the rest; it no
  longer takes the whole stop off. The book enforces it as an invariant (`sum of live sell
  quantities <= shares held`) and refuses an order that breaks it.
- **An execution agent owns every order.** A new subagent (`.claude/agents/execution-trader.md`)
  and skill (`/execute`) is the only path to the broker. It works from an execution pack: the
  orders the book has approved and not sent, the working orders, the fills since its last turn,
  and each stock's last price, spread proxy and liquidity. Per order it decides how and when
  (send now or wait for the open, limit or market, the limit price within the research's entry,
  stop or stop-limit on a thin stock, one try again after a rejection) and records why. It
  **never decides what to trade**: it can send, cancel or let lapse an order the book approved,
  never create one, change a quantity or move a price outside the guards. It reports what it did
  and what it couldn't do to the owner and the PM.
- **One door in code.** The agent sends through one script (`scripts/execute.py send ORDER_ID
  [--type T] [--price P]` / `cancel ORDER_ID`), which holds the key and the guards, so a guard
  can't be argued past:
  - the order is approved in the book, long-only, its quantity and side as booked;
  - the per-unit rule above; never sell more than is held;
  - the price within a band of the last trade (placeholder 10%) and, for a buy, inside the
    position cap and the free cash (`daily.free_cash`);
  - a cap on orders a day and on money committed a day (none on practice; set before live);
  - nothing new while the drawdown circuit breaker is on.
- **No duplicates.** The intent is written to the book (`sending`, with a time) before the call.
  After a timeout or an error the script never sends again blind: it lists the working orders
  (and the history) and matches by ticker, side, type, quantity and price (the reconcile
  matcher) before deciding the order wasn't placed. A placed order is recorded with its
  Trading212 id (`broker_ref`), so reconcile ties it exactly.
- **Cancel, confirm, then place.** A replacement (a stop moved or resized, an entry amended) is
  placed only after `GET /equity/orders/{id}` no longer shows the old order working. If the old
  one filled meanwhile, the replacement is dropped and the fill is booked.
- **An execution job in market hours.** Besides the evening run, a short job runs through the
  London session every 15 minutes: book the fills (ADR 0030 amendment), put the stop
  on new shares, release and send rotation buys (ADR 0027 amendment), send what the agents
  approved. The evening run prepares the next open.
- **The inbox reports instead of instructing.** It says what was sent, filled, cancelled or
  refused, and how to reverse it (`undo` now cancels at the broker too). It still asks for the
  owner when a guard refused an order, the broker rejected one, or reconcile finds a difference.

**Owner's answers (2026-10-21).**

1. **The stop after a fill goes out from code at once**, in the job turn that sees the fill (no
   agent turn); the execution agent reviews it afterwards.
2. **Every 15 minutes** through the London session (about 08:00-16:30). The agent is called only
   when a turn has something for it; the other turns are code only.
3. **Targets stay price levels, not orders.** Reaching one is information for the monitor and
   the PM. When a position is to be closed (target, thesis, rotation, any exit), the execution
   agent swaps the protection for the exit: cancel the stop, confirm, send the exit sell. It does
   the swap **only while the market is open**: an exit decided out of hours keeps its stop
   overnight and is swapped after the open, so a gap down never meets an unprotected holding. An
   exit that hasn't filled by the agent's deadline in the session (a limit not reached) is
   cancelled and the stop put back before the close. The holding is never left without a stop
   across a close.
4. **Stops are stop (market) orders**, on every stock; the thin-stock gap risk is accepted.
5. **Guards**: price band 10% of the last trade; **no daily caps on the practice account** (the
   practice run shows what a real week needs; caps are set before live).
6. **No orders by hand.** Everything goes through the system; an order or fill reconcile finds
   that the book didn't send is an error for the owner to resolve, never adopted.
7. **On live, the agent sends everything the book approved without asking**, as on practice.
   Live itself still waits for the owner's instruction.

**Consequences.** The owner leaves the order loop; a decision reaches the broker within one
job turn, and the book and the broker agree by construction, with reconcile as the check. The
cost: an unattended agent and code now trade, so the guards, the duplicate check and the
practice-only rule carry the risk, and with one key on practice those guards are a convention
any run holding the key could bypass; the API is beta and may change under us; a leaked trading
key could place orders on the practice account (no withdrawal endpoint is documented). Work
starts on the practice account: check an order in pence-quoted AIM lines is accepted in a GBP
account, how a limit / stop order behaves out of hours, and the order states a cancel passes
through, before anything is built on them. The rule text in CLAUDE.md and ADR 0026 changes to
"one order per unit" when this is built.
