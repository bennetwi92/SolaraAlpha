# 0031 — Orders through the Trading212 API, by an execution agent, on the practice account

**Status:** proposed (owner, 2026-10-21: the direction, the practice-only rule, the key and the
execution agent are decided; the open questions below wait for the owner). Amends 0026 (the
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
- **Rate limits per account** (shared with the read key): limit / stop / stop-limit 1 per 2 s,
  market and cancel 50 a minute, the order list 1 per 5 s, `GET /{id}` 1 per s.
- Scope `orders:execute` to place and cancel; no price alerts; no fill notifications (poll);
  stops trigger on the last traded price; up to 50 pending orders per ticker.

**Decision.**

- **Practice account only, indefinitely.** Orders go to `demo` until the owner instructs
  otherwise. The code refuses to send to `live` unless a separate setting
  (`SOLARA_T212_TRADING=live`) is set, and that setting is the owner's to add, never an agent's
  or a workflow default. `SOLARA_T212_TRADING=off` (the default) sends nothing.
- **A second key** with `orders:execute` (`SOLARA_T212_TRADE_KEY` / `SOLARA_T212_TRADE_SECRET`),
  separate from the read-only one, allowed from all IP addresses (the owner accepts this; GitHub's
  runners have no fixed address). Only the execution workflow gets it.
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
  - a cap on orders a day and on money committed a day (placeholders);
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
  London session (cadence an open question): book the fills (ADR 0030 amendment), put the stop
  on new shares, release and send rotation buys (ADR 0027 amendment), send what the agents
  approved. The evening run prepares the next open.
- **The inbox reports instead of instructing.** It says what was sent, filled, cancelled or
  refused, and how to reverse it (`undo` now cancels at the broker too). It still asks for the
  owner when a guard refused an order, the broker rejected one, or reconcile finds a difference.

**Open questions (owner).**

1. **The stop after a fill.** Should the protective stop go out from code at once (seconds, no
   agent turn, no token cost), with the agent reviewing it afterwards, or wait for the agent's
   next turn like every other order? Recommended: at once, from code.
2. **Cadence** of the market-hours job (every 15 or 30 minutes; GitHub's schedule is not
   exact) and whether a turn calls the agent only when there is something to send.
3. **Targets.** With the per-unit rule a target could be a working limit sell on part of the
   holding (say half at the first target, the stop on the other half) instead of a price alert.
   Wanted, or keep targets as alerts the system watches?
4. **Stop or stop-limit** on thin AIM stocks (a stop triggers on one odd trade; a stop-limit
   may not fill at all).
5. **The guards' numbers**: price band, orders a day, money a day.
6. **Orders placed by hand** in the app: still allowed (reconcile reports them) or not?
7. **What the agent may do without asking** once on live (not needed while on practice).

**Consequences.** The owner leaves the order loop; a decision reaches the broker within one
job turn, and the book and the broker agree by construction, with reconcile as the check. The
cost: an unattended agent and code now trade, so the guards, the duplicate check and the
practice-only rule carry the risk; the API is beta and may change under us; a leaked trading
key could place orders on the practice account (no withdrawal endpoint is documented). Work
starts on the practice account: check an order in pence-quoted AIM lines is accepted in a GBP
account, how a limit / stop order behaves out of hours, and the order states a cancel passes
through, before anything is built on them. The rule text in CLAUDE.md and ADR 0026 changes to
"one order per unit" when this is built.
