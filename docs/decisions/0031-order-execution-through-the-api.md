# 0031 — Orders through the Trading212 API, by an execution agent, on the practice account

**Status:** accepted (owner, 2026-10-21; the answers to the open questions are under "Owner's
answers"); **built 2026-10-23** (epic #244, #245-#250; "As built" at the end). Practice checks
"As tested" below; the market-hours checks run in the first practice week. Amends 0026 (the
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

**As tested (2026-10-21, practice account, out of hours).** Run from a throwaway script against
`demo` at 19:56-19:58 UTC (20:56 London, market shut), with the new all-permission key. Each
POST was sent once, with no retry, after listing the working orders. The account held 73 AAZ
(Anglo Asian Mining, `AAZl_EQ`, GBX, last 392p) with the book's stop sell on all 73 at 337p, and
the book's AVG stop buy. Neither was touched. Test orders were 1 share of AAZ, priced so none
could fill.

- **The key works**: account summary and positions read as before (GBP account, £4,997.81).
- **Prices go in pence.** `POST /equity/orders/limit` `{"ticker": "AAZl_EQ", "quantity": 1,
  "limitPrice": 274, "timeValidity": "GOOD_TILL_CANCEL"}` (30% under the last price) → 200,
  status `NEW`, `limitPrice` 274 echoed, `currency` "GBP" on the order and "GBX" on the
  instrument (as in ADR 0030). Reserved cash rose by exactly £2.74, so 274 was read as 274p.
  Integer quantities are accepted.
- **Out of hours, limit and stop orders are accepted and wait.** A limit buy (GTC), a **DAY**
  limit buy and a stop buy at 510p (GTC) all came back 200, `NEW`, `initiatedFrom: API`, each
  with its own id. Nothing marks them as queued for the open. Whether a DAY order placed after
  the close is good for the next session or lapses at once was not seen. Three orders on one
  ticker plus the stop sell were all live together, so the broker does not limit a stock to one
  order.
- **The broker enforces one order per unit on sells.** With the stop sell covering all 73
  shares, a limit sell of 1 share at 510p → **400** `/api-errors/selling-equity-not-owned`,
  "Selling more equities than owned, owned: 0.0". Working sells reserve their shares, so the sum
  of live sell quantities can't exceed the holding. A partial sale has to cut the stop first
  (cancel, confirm, place the smaller stop), then sell. A rejected order gets no id and never
  appears in the order history. Buys alongside the stop are accepted.
- **Cancel out of hours is immediate.** `DELETE /equity/orders/{id}` → 200 with an empty body in
  about 0.65 s. The next `GET /equity/orders/{id}`, under 1 s later, was already **404**
  `entity-not-found`, with no intermediate state seen. In the history the order shows
  `CANCELLED`, without `timeInForce`, `createdAt` cut to whole seconds in UTC. The pending
  endpoint gives the same time with milliseconds and a +03:00 offset (the parser converts to
  UTC). "Gone from pending" therefore means 404 on `GET /{id}`, and the history gives the end
  state. Reserved cash went back to its prior £1,248.20.
- **Rate limits** are as documented, read from the headers (`x-ratelimit-limit / -period /
  -remaining / -reset / -used`): limit and stop 1 per 2 s, the order list 1 per 5 s,
  `GET /{id}` 1 per 1 s, the order history 20 a minute. `x-ratelimit-reset` is a whole-second
  epoch, and a `GET /{id}` sent 1.2 s after the last one still got 429 (`{"code":
  "BusinessException", "context": {"type": "TooManyRequests"}}`) because the window hadn't
  reset. The client must wait for the reset header, not a fixed gap. Errors come as problem
  JSON (`type`, `title`, `status`, `detail`, `traceId`).
- **Not tested yet, in market hours**: a cancel while the market is open (any `CANCELLING`
  state, how long it takes), a marketable order and its fill as the API shows it, whether a
  DAY order expires at the close and with what status, a stop triggering. Run those at the
  next session before the execution job relies on them.
- End state: only the book's two orders working (AAZ stop sell 73 @ 337p, AVG stop buy 158 @
  790p), and nothing of the test left.

**Owner's answers to the build's open questions (2026-10-23).** The price for the 10% band is
Trading212's current price for a stock held, else the last daily close. The in-session deadline
for an unfilled exit is **15:45 London** (45 minutes before the close; 11:45 on a half day). A
GitHub scheduled run starting late is accepted on practice. A stop-limit sell counts like a stop
(any working sell reserves its shares).

**As built (2026-10-23).**

- **The book is the intent; the job makes the broker match it.** New order statuses:
  `sending` (written and committed before the call), `rejected` (Trading212 refused it; the code
  never sends it again, the agent may retry once, then the owner decides) and `cancelling` (an
  order that may be working at the broker, until the broker confirms it gone or filled; a fill
  that races the cancel is booked). `book.cancel_order` picks `cancelling` or `cancelled`. A turn
  cancels at the broker every working order the book is cancelling or has ended, polls
  `GET /{id}` until 404, then books the end state from the history (`reconcile.absorb`, which now
  also confirms cancels and places a `sending` order it finds). Nothing new goes out for a
  position while one of its orders is cancelling.
- **One order per unit** in the book: `book.sell_room` / `check_sell` (the sells that may be
  working never sell more than is held), `protect` sizes the stop to the shares no working exit
  sells, an add or an entry works beside the stop (an add's raised stop replaces the stop at
  once), `exit_position` no longer takes the stop off; `book.swap_for_exit` (and `book.py swap`
  by hand) cuts the stop for a partial or takes it off for a full exit.
- **`scripts/execute.py`** (`portfolio/execution.py`) is the one door: `turn` (code only),
  `send` / `cancel` / `hold` / `note` (the agent's). Guards as decided; the band applies to
  limit orders and buy stops only: a sell stop is below the market by design and is never
  refused on price (a holding without a stop is the worse failure). No daily caps (practice).
  Writes go through `trading212._write`: refused unless `SOLARA_T212_TRADING` names the account
  in `SOLARA_T212_ENV`, a session with no retries, the rate limit waited out from
  `x-ratelimit-reset`, a 4xx parsed from the problem JSON (a 429 did nothing: back to approved),
  a timeout / connection error / 5xx is `Unknown` (the order stays `sending` until the next list
  matches it; one the broker never shows goes back to `approved` the turn after).
- **The agent is an overlay, not a dependency.** With a Claude secret set, a new non-stop order
  waits up to 30 minutes for `/execute` (how and when: send as booked, an exit at market or a
  limit, a buy limit at or below the booked one, hold until a time, let it lapse, one retry after
  a rejection), then the code sends it as booked. Stops never wait. Every action, the code's and
  the agent's, is a row in the append-only `executions` table with its reason.
- **`.github/workflows/execute.yml`**: every 15 minutes 07:00-16:45 UTC on weekdays and after
  each Agents run, in the `book` concurrency group; `SOLARA_T212_TRADING` is `demo` there unless
  the owner sets the repository variable. The inbox (`notify.py --mode execute`) reports each turn
  that sent, booked, cancelled or refused something; a stop not at the broker heads it and goes to
  the desk-errors issue with any rejection or error (`--mode execute-errors`). The other workflows
  set `SOLARA_ORDERS_BY=system` once the keys are there, and the daily / weekly inbox stop giving
  order steps.
- **London calendar** in code (weekdays 08:00-16:30, England & Wales bank holidays and the 12:30
  half days for 2026-2027; a warning once a year is missing).
- **Practice run through the built code (2026-10-23, 16:08 UTC, market shut).** With
  `SOLARA_T212_TRADING=demo` for that one process and the book untouched: `trading212.place`
  limit buy 1 AAZ at 256p (30% under the 365p last) → `new`, id returned, in 0.66 s;
  `fetch_order` found it working; `cancel` answered in 0.22 s and the first `GET /{id}` poll
  (1.2 s later) was already 404; the history showed it `cancelled`, nothing filled. A limit sell
  of 1 AAZ with all 73 under the book's stop → `T212Error` 400
  `/api-errors/selling-equity-not-owned` ("Selling more equities than owned, owned: 0.0"),
  parsed as the code expects. Afterwards only the book's two orders were working (AAZ stop sell
  73 @ 337p, AVG stop buy 158 @ 790p).
- **Still to run in market hours** (the first practice week, before the owner signs the epic
  off): cancel while open, DAY expiry, a fill as the API shows it, a stop triggering.

