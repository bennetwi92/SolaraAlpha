# 0012 — Relaxed price-quality rules for thinly traded AIM stocks

**Context.** The blocking rules in `data/clean.py` were set on Jet2. With the watchlist widened
to the FTSE AIM UK 50, `trim_to_consistent` cut 6 illiquid stocks (CAM, GHH, RKH, ARBB, BTG,
BIG) to between 0 and 233 days of history, so they got NaN scores or crashed (#94). The causes
were real trading, not bad data: days with no trades, prices that didn't move for a week, and
today's bar with no close.

**Decision.**
- `zero_volume` no longer blocks. A day with no trades is normal on AIM; long no-trade spells
  still show up as `stale`.
- `stale` blocks only after **20** unchanged closes (~4 weeks), up from 5. A flat week is
  normal for an illiquid stock traded weekly; a flat month still points to a suspension or
  filler data (e.g. Afentra's 2021 suspension is still cut).
- Issues on rows that `clean_prices` drops (e.g. a no-trade bar with no close) don't cut.

All three are reported by `check_prices` as before; only the trim changes.

**Consequences.** All 50 watchlist stocks except two keep their full 5 years: GMET listed in
2023, and AET's 2021 suspension is still cut. Jet2 is unchanged (it had no blocking issues).
Illiquid stocks now pass the data checks; whether they are tradeable is a separate question
(no liquidity filter yet).

**Amendment (2026-10-23).** A `duplicate_date` or `missing` issue on the *newest* bar (Yahoo's
live or partial session: a repeated date, a null volume beside the close) no longer cuts the
history: `trim_to_consistent` used to keep only rows after it, which left nothing and turned
the stock into an ERROR. `clean_prices` keeps the last copy of a repeated date. The same issues
mid-history still cut (`LATEST_BAR_OK` in `data/clean.py`).
