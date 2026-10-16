# 0020 — Corporate actions in the book

**Status:** accepted · 2026-10 · issue #133

## Context

The book (ADR 0007, 0015) is append-only and event-sourced: a position's state is derived from
its fills and events. Without dividends received, share-count changes and takeovers /
delistings its P&L is wrong, and a position can end without an ordinary exit fill.

## Decision

- **Dividends** are `cash` rows of kind `dividend` tagged with the `position_id` that was open on
  the ex-date (`book.record_dividend`; `book.py cash dividend TICKER AMOUNT --at EXDATE`). They
  count in equity (as before) and in the close summary: `CloseSummary.dividends`, and
  `net_pnl = gross - costs + dividends`, so the R-multiple includes them.
- **The daily job proposes, the owner records.** `fetch_corporate_actions` (the Yahoo chart
  endpoint with `events=div,split`) is read for each open position; an ex-date since the last
  run with no dividend row on that date becomes an owner trigger (`dividend`, with per-share
  amount x shares held in GBP) and a `book.py cash dividend ...` line in the report. The job
  never writes cash itself: the table has no draft state and no correction path, and the
  broker's figure (withholding, fractional shares, payment date) is the one to book.
- **Splits / consolidations** are a `split` position event (`factor` = new / old shares,
  `book.py split ID --ratio NEW:OLD`). `position_state` replays fills and splits in time order:
  at a split the shares held and the entry shares scale by the factor (fractions dropped, as
  the broker pays them in cash) and the average cost scales so the cost is conserved; the
  planned initial stop scales too, so R stays in money terms. Every open order of the position
  is replaced at the scaled quantity and price (never mutated).
- **Takeover / delisting** is `book.corporate_close` (`book.py corporate-close ID --price P
  --reason takeover|delisting`): an approved market exit order for every share, approved with
  reason `corporate_action: <kind>`, then a synthetic fill at the offer price (0 if worthless,
  no commission). The fill closes the position as usual with exit reason `corporate_action`;
  the kind stays on the order's approval event.

## Consequences

- Prices stay in the quote currency; the proposed dividend amount is converted with
  `book.to_account`. A dividend declared in another currency is booked by hand from the broker.
- Yahoo lists ex-dates, not payment dates: the cash row dates the entitlement, and the equity
  curve shows it from the ex-date.
- The Yahoo events feed is unofficial like the prices (docs/data-sources.md); a missing feed is
  an error line in the daily report, not a stop.
