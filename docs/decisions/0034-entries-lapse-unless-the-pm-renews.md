# 0034 — Buy orders expire on their date; a pending entry lapsing before the next weekly run is kept only if the PM renews it

**Status:** accepted and built (owner's request, 2026-10-24).

**Context.** Every entry order carries a `valid_until` (the research's `valid_days`, else
`entry_validity_days`), but nothing ended it: `book.py expire` had to be run by hand, the daily
job only reported the order as stale a day later, and the execution job cancels at Trading212
only orders whose broker reference it holds, so an entry the owner placed by hand (no ref) stayed
working there, good till cancelled, after the book had written it off. The PM, meanwhile,
allocated each Sunday counting an order that would die mid-week as if it held its slot, heat and
cash all week (AVG.L, position 9: valid until Monday 26 Oct, still working on Saturday 24 Oct).

**Decision.**

- **Expiry is automatic.** A buy (entry or add) is good through the session of its `valid_until`
  day. Once that session has closed (`execution.expiry_as_of`), `book.expire_orders` ends it:
  in every execution turn (after the fills are booked, so a fill before the close keeps it) and
  in the daily job's saved run (before the pending entries are read, so the monitor never
  reviews a dead order). The execution turn's settle then cancels it at Trading212 and confirms
  it gone. While the owner places the orders, the inbox tells them to cancel it (`broker.py`).
- **An order placed by hand is the book's too.** Settle cancels an ended order matched on its
  terms when the book shows it `placed` with no broker ref. An order the book ended before it
  was ever placed is still never touched (it may be the owner's own).
- **Renewal is the PM's call, made weekly.** At each weekly run every pending entry whose order
  is valid until before the next run (`agents.lapsing`: the run's start + 7 days) is shown as
  `lapses` in the PM pack. The PM lists the ones still worth building in `renewals`; they stay
  working, at their entry and stop, until the next run (a `renew` revision recording a
  `renewed` position event; order rows are never mutated). **Every other lapsing entry is
  cancelled as the PM's verdict is recorded**, so the planner allocates the week's room without
  it. To re-level an entry, the PM lets it lapse and funds the stock's new research ACT. An
  ERROR verdict renews and cancels nothing; entries then run to their own dates.
- The monitor cannot renew (it can say "worth renewing" in its reasons); the execution agent
  cannot hold an order past its validity, re-send or rescue an expired one. The owner can undo a
  renewal or a lapse like any agent revision (`book.py undo`); a cancelled entry is re-planned
  with `plan.py --replan`.

**Consequences.** A lapsing entry the PM lets go loses the days left on its order (AVG.L would
not get Monday): the price of a book that is what the allocation assumes. Renewals keep the old
levels for another week, so a stale breakout trigger is the PM's to spot (the pack's last close
against the entry). Add orders expire too; the stop on the shares already held stays.
