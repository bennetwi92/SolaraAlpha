# 0036 — A risk manager reports the book's VaR, CVaR and P&L daily; the PM acts on a breach

**Status:** accepted (owner, 2026-10-27). Builds ADR 0027's phase B (a daily PM review) and ADR
0032's slice 4, triggered by risk rather than run every day.

**Context.** The monitors review each position on its own; nobody looked at the whole book
each day. The owner wants a risk manager who reports to the PM daily and to the owner daily, on
Telegram (the owner never reads the GitHub inbox): VaR and CVaR with the standard risk and P&L
measures, an assessment of where the change since yesterday comes from, and the PM acting on
it when needed.

**Decision.**

- **Code computes, the agent interprets** (as events and research, ADR 0006 / 0016). The daily
  job (`daily.py --save`) saves a `book_risk` card on `_BOOK` (`portfolio/riskreport.py`):
  - **VaR and CVaR** by historical simulation on today's holdings (each stock's returns over the
    last ~500 sessions all held stocks traded; the book's own curve is too short), at
    `var_confidence` (0.95) over 1 session and `var_horizon_days` (5: the book trades weekly;
    overlapping compounded returns, no square-root scaling). A second run on EWMA-filtered returns
    (lambda 0.94) reacts to a volatility change; a third adds the pending entries (pro-forma).
  - **Beside VaR**, which ignores stops: heat, every stock's worst day at once, drawdown vs the
    circuit breaker, beta to the index, book volatility, average correlation, % of ADV; each
    position's component of the CVaR; a 1-day VaR backtest (exceptions vs the 5% expected).
  - **P&L** for the day (price moves split into market = beta x index and stock-specific;
    trading = today's fills vs the close; income; unexplained), month to date, since inception
    vs the benchmark.
  - **The VaR change** by re-running the headline VaR in steps: yesterday's book and window →
    today's shares (trades) → today's prices (drift) → today's window (new data).
- **Limit** (owner, 2026-10-27, "the standard 95 and 5"): `max_var = 0.05`: the larger of the
  historical and filtered 5-day 95% VaR above 5% of equity is a **breach**. So are heat over
  `max_heat` and a drawdown past the circuit breaker. Near a limit, a name over half the CVaR,
  too many backtest exceptions, short history: **watch** flags.
- **The risk manager** (`/risk`, persona `.claude/agents/risk-manager.md`) runs every weekday
  after the monitors when the book holds positions or pending entries. It reads the pack
  (`risk_pack.py`), records one `risk` verdict on `_BOOK` (ACT = the PM must review today,
  WATCH, PASS) with its assessment and recommendations, and posts the daily risk and P&L
  report to the desk thread, i.e. the owner's Telegram. It never trades.
- **The PM's daily review** (`/pm-daily`) runs only when called: a breach on the card (whatever
  the risk manager's label) or a risk ACT (`record.py called`). It records one `pm_daily`
  verdict whose `actions` only cut risk (tighten a stop, take a partial, exit, cancel a pending
  entry), applied at once as agent revisions (ADR 0024; the book refuses a lower stop); the
  execution job sends them. It posts what it did to the thread. No adds or new entries: those
  wait for Sunday.

**Consequences.** One more agent run a weekday with a book (the risk manager), and the PM only
on a call. The limit is the owner's; until the VaR backtest has a few months of exceptions to
judge, the numbers are a guide with known biases (stale AIM prices understate volatility and
correlation; stops make losses smaller in slow falls and gaps make them larger), which the
report states. Not built: the weekly PM pack's risk trend and a candidate's marginal CVaR in
the plan's fit check; a track record for the risk manager (ADR 0035).
