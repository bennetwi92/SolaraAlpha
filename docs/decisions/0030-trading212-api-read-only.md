# 0030 — Trading212 API, read-only: reconcile the book against the broker

**Status:** accepted and built (owner chose read-only first, 2026-10-21; built in #234, see
"As built" below). Amends 0026 (the owner's touchpoints) and 0027 ("no broker API" now means
no orders through it). Issue #234; supersedes the CSV route of #141. Order placement through the API is
**out of scope** and needs its own ADR.

**Context.** Since ADR 0026 the owner places every order in Trading212 and reports back by
hand (`book.py placed`, `fill`, `cash dividend`). Hand-typed fills will contain errors (#141),
the book only learns of a fill when the owner says so, and a stop that sold overnight shows as
open until then. Not every AIM stock is offered by Trading212, and nothing checks that before
research spends agent time on a name. Trading212's public API (v0, beta) is free with an Invest
or Stocks ISA account. Its read endpoints cover the account summary, open positions, pending
orders, historical orders with their fills, dividends, cash transactions and instrument
metadata. It authenticates by HTTP Basic with an API key and secret, and a key can be given
read scopes only and restricted to IP addresses.

**Decision.**

- **Read-only key.** The key is created with read scopes only (`account`, `portfolio`,
  `orders:read`, `history:orders`, `history:dividends`, `history:transactions`, `metadata`),
  never with order placement. It's read through `settings` as `SOLARA_T212_KEY` /
  `SOLARA_T212_SECRET` (`.env`, the cloud environment, GitHub secrets for `daily.yml`).
  **Practice or live** is a setting, `SOLARA_T212_ENV` = `demo` (default:
  `https://demo.trading212.com/api/v0`, the practice account the first key is for) or `live`
  (`https://live.trading212.com/api/v0`); a key only works on the account it was made for.
  Nothing in the code calls a write endpoint.
- **Fetcher** `solaraalpha.data.trading212`: one function per endpoint, each returning a tidy
  DataFrame. List endpoints are followed through `nextPagePath`, and the client sleeps on
  `x-ratelimit-reset` rather than retrying into a 429. Timestamps are parsed to UTC. Not
  cached, since the point is the broker's state now. Tests parse saved, redacted responses.
- **Reconcile** `solaraalpha.portfolio.reconcile`: the broker's positions, pending orders, fills,
  dividends and cash against the book. Book order ids and Trading212 order ids are tied by a
  `broker_ref` on the book's `placed` event (from `book.py placed ORDER_ID --ref T212_ID`, or
  matched by ticker, side, type, quantity and price when unique). It lists matches, book-only
  items, broker-only items and mismatches (quantity; price beyond 0.5%; a position the book
  thinks open but the broker holds none of; an order working at the broker that the book has
  ended). **Prices are compared in pence**: Trading212 quotes LSE stocks in GBX (positions'
  `averagePrice` / `currentPrice`, orders' `stopPrice` / `limitPrice`) even where an order's
  `currency` reads GBP, and the book stores pence, so no conversion is made; the instrument's
  own currency (GBX) is checked and anything else fails loudly.
- **The daily job reconciles** after the close, when the keys are set (it skips silently
  otherwise). The differences go into the daily report and the inbox as "check" items, in plain
  words. **It never corrects the book by itself** in this phase: fills and dividends the broker
  shows are offered as the exact `book.py` command to run (or confirmed by telling Claude).
- **Tradable universe.** The instrument list (one call, refreshed every 10 minutes by Trading212,
  rate-limited to one per 50 s) maps each watchlist ticker to its Trading212 ticker. The
  scan marks stocks Trading212 doesn't offer, and the shortlist skips them. Trading212 tickers
  are always taken from the list, never built by hand (LSE lines look like `AAZl_EQ`).
- **Free source, recorded** in `docs/data-sources.md`. Account data never goes into the repo.

**Consequences.** The owner keeps placing orders and setting alerts, but no longer has to
remember every fill: the inbox says what the broker shows and gives the command to book it. A
broker-only fill (a stop that sold) is caught the evening it happens. The API is beta and its
docs leave some formats unstated (fill and dividend timestamps, cursor types), so the parser is
pinned to saved responses and fails loudly on a shape it doesn't know. The docs allow up to 50
pending orders per ticker, against the one-order-per-stock rule ADR 0026 took from the app. The
rule stays until it is tested on the demo account. A later ADR may let the reconciler book exact
matches itself (actor `broker`), and a separate one would cover placing orders.

**As built (#234, 2026-10-21).** Tried first on the practice account (`demo`), which matched
the book exactly.

- Endpoints read: `/equity/account/summary`, `/equity/positions`, `/equity/orders` (pending),
  `/equity/history/orders` (each item an order and its fill), `/equity/history/dividends`,
  `/equity/history/transactions`, `/equity/metadata/instruments`. When a response says the
  window is spent, the **next call to the same endpoint** waits for `x-ratelimit-reset`, so the
  one-per-50 s instrument list costs nothing unless it is called twice.
- **Tickers are matched on `shortName`** (the TIDM) among the LSE share lines (`…l_EQ`,
  `…l1_EQ`), not by ISIN: the watchlist holds no ISINs, and fetching them is one LSE call per
  stock. A TIDM with two lines is left out as ambiguous. On 2026-10-21 all 104 watchlist stocks
  matched; Greencoat Renewables (`GRP.L`) is quoted in EUR, so it is screened out ("quotes it in
  another currency") until the book handles FX. Only GBX lines count as tradable, and
  reconcile refuses a broker line in any other currency.
- The scan's floor drops untradable stocks (`pipeline.screen(untradable=...)`), so they are
  never shortlisted. The practice account's instrument list may differ from the ISA's; check it
  again when the key moves to `live`.
- Deposits and withdrawals are compared as the **net flow**, so a correction in the book (a
  deposit, its withdrawal, the deposit again) is not a difference. Dividends are matched by
  ticker and amount.
- Each difference is an owner trigger `broker` in the daily run (report "Owner actions", the
  inbox "Check and tell me"), with the `book.py` command when one fixes it. `book.py reconcile`
  prints the same by hand. Workflows read `SOLARA_T212_KEY` / `SOLARA_T212_SECRET` from secrets
  and `SOLARA_T212_ENV` from repository variables (default `demo`).

