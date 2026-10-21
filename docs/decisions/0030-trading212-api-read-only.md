# 0030 — Trading212 API, read-only: reconcile the book against the broker

**Status:** proposed (owner chose read-only first, 2026-10-21). Amends 0026 (the owner's
touchpoints) once built. Issue #234; supersedes the CSV route of #141. Order placement through the API is
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
  `SOLARA_T212_SECRET` (`.env`, the cloud environment, GitHub secrets for `daily.yml`). Live
  base URL `https://live.trading212.com/api/v0`. Nothing in the code calls a write endpoint.
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
  ended).
- **The daily job reconciles** after the close, when the keys are set (it skips silently
  otherwise). The differences go into the daily report and the inbox as "check" items, in plain
  words. **It never corrects the book by itself** in this phase: fills and dividends the broker
  shows are offered as the exact `book.py` command to run (or confirmed by telling Claude).
- **Tradable universe.** The instrument list (one call, refreshed every 10 minutes by Trading212,
  rate-limited to one per 50 s) maps each watchlist ticker to its Trading212 ticker by ISIN. The
  scan marks stocks Trading212 doesn't offer, and the shortlist skips them. Trading212 tickers
  are always taken from the list, never built by hand.
- **Free source, recorded** in `docs/data-sources.md`. Account data never goes into the repo.

**Consequences.** The owner keeps placing orders and setting alerts, but no longer has to
remember every fill: the inbox says what the broker shows and gives the command to book it. A
broker-only fill (a stop that sold) is caught the evening it happens. The API is beta and its
docs leave some formats unstated (fill and dividend timestamps, cursor types), so the parser is
pinned to saved responses and fails loudly on a shape it doesn't know. The docs allow up to 50
pending orders per ticker, against the one-order-per-stock rule ADR 0026 took from the app. The
rule stays until it is tested on the demo account. A later ADR may let the reconciler book exact
matches itself (actor `broker`), and a separate one would cover placing orders.
