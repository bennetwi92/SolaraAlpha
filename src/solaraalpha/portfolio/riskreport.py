"""The book's daily risk report: VaR and CVaR, P&L, and where the day's change came from.

Computed by code every weekday in the daily job and saved as a ``risk`` card on ``_BOOK``; the
risk manager agent (``/risk``) interprets it, posts the day's report to the desk thread and
calls the PM's daily review when a limit is crossed (docs/decisions/0036).

- **VaR / CVaR** by historical simulation on today's holdings: each held stock's daily returns
  over the last ``WINDOW`` sessions they all traded (missing sessions carried forward, so a
  stale AIM price counts as no move), applied to today's GBP values. The book's own equity
  curve is far too short. Horizons: one session and ``rules.var_horizon_days`` (overlapping
  compounded returns, not a square-root scaling: AIM gaps are fat-tailed). A second run on
  EWMA-filtered returns (each day rescaled to today's volatility, lambda 0.94) reacts to a
  volatility change at once; the larger headline VaR is held against ``rules.max_var``.
  ``pro_forma``: the same with every pending entry filled at its order price. Stops are
  ignored by VaR, so the heat (loss if every stop is hit) and a stress (every stock's worst
  day in the window, at once) sit beside it.
- **Component CVaR**: each position's average loss in the tail scenarios; they add up to the
  CVaR, so they say which names carry the risk.
- **P&L** for the last session, from the equity curve (cash + fills + closes): per stock the
  price effect on yesterday's shares (split into the market's part, beta x the index, and the
  stock's own), the trading effect of today's fills against the close, income (dividends,
  interest, less fees) and anything left over. Month to date and since inception too.
- **The VaR change**, by re-running the headline VaR in steps: yesterday's book on yesterday's
  window -> today's shares (trades) -> today's prices (drift) -> today's window (new data).
- **Flags**: ``breach`` (calls the PM: VaR over the limit, heat over ``max_heat``, drawdown past
  the circuit breaker) and ``watch`` (VaR or drawdown near its limit, one name carrying most of
  the CVaR, more VaR exceptions than 95% allows, short price history). Only the owner sets the
  limits (``config/portfolio.toml``).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from solaraalpha import store
from solaraalpha.log import get_logger
from solaraalpha.portfolio import book, performance
from solaraalpha.portfolio.performance import _utc_day
from solaraalpha.portfolio.rules import Rules
from solaraalpha.store import Conn

log = get_logger(__name__)

BOOK = "_BOOK"
MODEL = "book_risk"  # the code's card; the risk manager's verdict is model "risk"
WINDOW = 500  # sessions of history (about two years)
MIN_OBS = 120  # fewer: the VaR is flagged as resting on short history
EWMA_LAMBDA = 0.94  # RiskMetrics
BETA_WINDOW = 250
NEAR = 0.8  # a limit this close is a watch flag
CONCENTRATION = 0.5  # one position carrying more than this share of the CVaR
BACKTEST_DAYS = 60  # 1-day VaR exceptions counted over this many reports
BREAKER_NEAR = 0.03  # drawdown within this of the circuit breaker is a watch flag


@dataclass(frozen=True)
class Tail:
    var: float  # GBP, a loss (>= 0)
    es: float  # CVaR / expected shortfall: the average loss beyond the VaR, GBP
    components: dict[str, float]  # each ticker's share of ``es``, GBP


NO_TAIL = Tail(0.0, 0.0, {})


def _close(px: pd.DataFrame, col: str = "close") -> pd.Series:
    s = px[col if col in px else "close"].dropna()
    s.index = pd.DatetimeIndex([_utc_day(t) for t in s.index])
    return s[~s.index.duplicated(keep="last")].sort_index()


def returns_frame(
    prices: Mapping[str, pd.DataFrame],
    tickers: list[str],
    end: pd.Timestamp | None = None,
    window: int = WINDOW,
) -> pd.DataFrame:
    """Daily returns (``adj_close``, else ``close``) of ``tickers`` over the last ``window``
    sessions they all have, up to ``end``; a missing session is carried forward (no move)."""
    if not tickers:
        return pd.DataFrame()
    df = pd.concat({t: _close(prices[t], "adj_close") for t in tickers}, axis=1, sort=True)
    if end is not None:
        df = df[df.index <= end]
    df = df.ffill().dropna()
    return df.pct_change().dropna().iloc[-window:]


def ewma_filtered(r: pd.DataFrame, lam: float = EWMA_LAMBDA) -> pd.DataFrame:
    """Each day's return rescaled from the EWMA volatility of its day to today's forecast
    (filtered historical simulation): an old calm day counts as a calm day at today's vol."""
    if r.empty:
        return r
    var = (r**2).ewm(alpha=1 - lam, adjust=False).mean()
    before = var.shift(1).bfill()  # the variance known before each day
    forecast = var.iloc[-1]
    scale = np.sqrt(forecast / before.where(before > 0))
    return (r * scale).fillna(0.0)


def scenarios(r: pd.DataFrame, horizon: int, filtered: bool = False) -> pd.DataFrame:
    """Return scenarios over ``horizon`` sessions: overlapping compounded returns."""
    if filtered:
        r = ewma_filtered(r)
    if horizon <= 1 or r.empty:
        return r
    logs = pd.DataFrame(np.log1p(r), index=r.index, columns=r.columns)
    return logs.rolling(horizon).sum().dropna().apply(np.expm1)


def var_es(values: Mapping[str, float], scen: pd.DataFrame, conf: float) -> Tail:
    """VaR and CVaR (GBP losses) of holdings worth ``values`` (GBP, by ticker) over the return
    ``scen`` arios, with each ticker's component of the CVaR."""
    cols = [t for t, v in values.items() if v and t in scen]
    if not cols or len(scen) < 2:
        return NO_TAIL
    each = scen[cols] * pd.Series({t: values[t] for t in cols})
    pnl = each.sum(axis=1)
    q = float(np.quantile(pnl, 1 - conf))
    tail = pnl <= q
    return Tail(
        var=max(-q, 0.0),
        es=max(-float(pnl[tail].mean()), 0.0),
        components={t: -float(each.loc[tail, t].mean()) for t in cols},
    )


def _beta(r: pd.Series, bench: pd.Series) -> float:
    both = pd.concat([r, bench], axis=1, sort=True).dropna().iloc[-BETA_WINDOW:]
    if len(both) < 20:
        return math.nan
    v = float(both.iloc[:, 1].var())
    cov = both.cov().to_numpy()
    return float(cov[0, 1]) / v if v > 0 else math.nan


def _num(v: float) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 4)


def risk_history(conn: Conn) -> pd.DataFrame:
    """Earlier risk cards: ``run_id``, ``date``, ``var_1d``, ``pnl_day``, oldest first."""
    df = store.query(
        "SELECT run_id, date, field, value FROM results WHERE ticker = ? AND model = ?"
        " AND field IN ('var_1d', 'pnl_day')",
        conn,
        [BOOK, MODEL],
    )
    if df.empty:
        return pd.DataFrame(columns=["run_id", "date", "var_1d", "pnl_day"])
    wide = df.pivot_table(index=["run_id", "date"], columns="field", values="value").reset_index()
    return wide.sort_values("run_id").drop_duplicates("date", keep="last")


def exceptions(history: pd.DataFrame, days: int = BACKTEST_DAYS) -> tuple[int, int]:
    """1-day VaR backtest over the last ``days`` reports: (sessions whose loss beat the
    previous report's VaR, sessions tested)."""
    if len(history) < 2:
        return 0, 0
    h = history.iloc[-days - 1 :]
    prev = h["var_1d"].shift(1)
    tested = h["pnl_day"].notna() & prev.notna() & (prev > 0)
    return int((h["pnl_day"] < -prev)[tested].sum()), int(tested.sum())


def build_risk(
    conn: Conn,
    prices: Mapping[str, pd.DataFrame],
    bench: pd.DataFrame | None,
    rules: Rules,
) -> dict[str, Any] | None:
    """The risk card for the book at the last session in ``prices`` (cleaned daily frames by
    ticker; any ticker the book ever held that is missing is read from the saved snapshots).
    None when the book has no cash or fills yet."""
    cash = book.cash(conn)
    fl = performance.fills_in_todays_units(conn)
    if cash.empty and fl.empty:
        return None
    px = dict(prices)
    pending = _pending(conn)
    for t in sorted(set(fl["ticker"]) | {p["ticker"] for p in pending}):
        if t not in px:
            saved = store.load_prices(str(t), conn=conn)
            if not saved.empty:
                px[str(t)] = saved
    held_px = {t: px[t] for t in set(fl["ticker"]) if t in px}
    bclose = _close(bench) if bench is not None and not bench.empty else None
    eq = performance.equity_curve(cash, fl, held_px)
    flow = performance.flows(cash)
    if eq.empty:
        return None
    t = eq.index[-1]
    t1 = eq.index[-2] if len(eq) > 1 else t
    equity = float(eq.iloc[-1])

    def shares_at(day: pd.Timestamp) -> dict[str, float]:
        out: dict[str, float] = {}
        for f in fl.to_dict("records"):
            if _utc_day(f["at"]) <= day:
                k = str(f["ticker"])
                out[k] = out.get(k, 0.0) + int(f["side"]) * int(f["qty"]) * float(
                    f.get("split_factor") or 1.0
                )
        return {k: v for k, v in out.items() if abs(v) > 1e-9}

    def close_at(ticker: str, day: pd.Timestamp) -> float:
        c = _close(px[ticker]).loc[:day]
        return float(c.iloc[-1]) if len(c) else math.nan

    def gbp(ticker: str, amount: float) -> float:
        return book.to_account(amount, str(px[ticker].attrs.get("currency") or "GBp"))

    def values(shares: Mapping[str, float], day: pd.Timestamp) -> dict[str, float]:
        return {k: gbp(k, n * close_at(k, day)) for k, n in shares.items() if k in px}

    now_sh, then_sh = shares_at(t), shares_at(t1)
    v_now, v_then, v_mix = values(now_sh, t), values(then_sh, t1), values(now_sh, t1)
    tickers = sorted(set(v_now) | set(v_then))
    missing = sorted((set(now_sh) | set(then_sh)) - set(px))
    r_now = returns_frame(px, tickers, end=t)
    r_then = returns_frame(px, tickers, end=t1)
    h, conf = rules.var_horizon_days, rules.var_confidence

    hist_1d = var_es(v_now, scenarios(r_now, 1), conf)
    hist = var_es(v_now, scenarios(r_now, h), conf)
    filt = var_es(v_now, scenarios(r_now, h, filtered=True), conf)
    steps = [
        var_es(v_then, scenarios(r_then, h), conf).var,
        var_es(v_mix, scenarios(r_then, h), conf).var,
        var_es(v_now, scenarios(r_then, h), conf).var,
        hist.var,
    ]
    v_pending = dict(v_now)
    pending_out = []
    for p in pending:
        k = p["ticker"]
        if k not in px or p["price"] is None:
            continue
        v_pending[k] = v_pending.get(k, 0.0) + gbp(k, p["qty"] * p["price"])
        pending_out.append({**p, "value": round(gbp(k, p["qty"] * p["price"]), 2)})
    pro = (
        var_es(v_pending, scenarios(returns_frame(px, sorted(v_pending), end=t), h), conf)
        if pending_out
        else hist
    )

    # P&L of the last session
    bret = math.nan
    if bclose is not None and len(bclose.loc[:t]) > 1:
        b = bclose.loc[:t]
        bret = float(b.iloc[-1] / b.iloc[-2] - 1) if b.index[-1] == t else 0.0
    bench_r = bclose.pct_change() if bclose is not None else None
    per: dict[str, dict[str, Any]] = {}
    for k in sorted(set(now_sh) | set(then_sh)):
        if k not in px:
            continue
        c_now, c_then = close_at(k, t), close_at(k, t1)
        price = gbp(k, then_sh.get(k, 0.0) * (c_now - c_then)) if t1 != t else 0.0
        beta = (
            _beta(_close(px[k], "adj_close").pct_change(), bench_r)
            if bench_r is not None
            else math.nan
        )
        market = v_then.get(k, 0.0) * beta * bret if not math.isnan(beta * bret) else 0.0
        per[k] = {
            "price": price,
            "market": market,
            "specific": price - market,
            "trading": 0.0,
            "beta": beta,
        }
    for f in fl.to_dict("records"):
        if _utc_day(f["at"]) != t or str(f["ticker"]) not in per:
            continue
        k = str(f["ticker"])
        qty = int(f["side"]) * int(f["qty"])
        per[k]["trading"] += (
            gbp(k, qty * float(f.get("split_factor") or 1.0) * close_at(k, t))
            - book.to_account(qty * float(f["price"]), str(f["currency"]))
            - float(f["commission"])
            - float(f["fees"])
        )
    today_cash = cash[[_utc_day(a) == t for a in cash["at"]]] if len(cash) else cash
    income = sum(
        book.to_account(float(a), str(c)) * (1 if k == "dividend" or k == "interest" else -1)
        for a, c, k in zip(
            today_cash.get("amount", []),
            today_cash.get("currency", []),
            today_cash.get("kind", []),
            strict=True,
        )
        if k in ("dividend", "interest", "fee")
    )
    f_today = float(flow.get(t, 0.0)) if len(flow) else 0.0
    pnl_day = float(eq.iloc[-1] - eq.iloc[-2]) - f_today if len(eq) > 1 else 0.0
    explained = sum(p["price"] + p["trading"] for p in per.values()) + income
    month_start = eq[eq.index < t.replace(day=1)]
    base = float(month_start.iloc[-1]) if len(month_start) else float(eq.iloc[0])
    mtd_flow = (
        float(flow[(flow.index >= t.replace(day=1)) & (flow.index <= t)].sum())
        if len(flow)
        else 0.0
    )
    if not len(month_start):
        mtd_flow = float(flow[flow.index <= t].sum()) if len(flow) else 0.0
        base = 0.0
    summary = performance.summarise(eq, flow, bclose)

    # the book: heat, drawdown, concentration, beta, vol, correlation, liquidity, stress
    states, _ = book.live_states(conn)
    open_states = [s for s in states if s.status == "open" and s.shares > 0]
    heat_gbp = float(sum(s.risk_at_stop for s in open_states))
    invested = float(sum(v_now.values()))
    drawdown = performance.current_drawdown(eq)
    book_beta = (
        sum(v_now[k] * per[k]["beta"] for k in v_now if not math.isnan(per[k]["beta"])) / equity
        if equity > 0 and per
        else math.nan
    )
    daily_pnl = (
        (r_now[list(v_now)] * pd.Series(v_now)).sum(axis=1)
        if v_now and len(r_now)
        else pd.Series(dtype=float)
    )
    book_vol = (
        float(daily_pnl.std() * math.sqrt(252) / equity)
        if len(daily_pnl) > 2 and equity > 0
        else math.nan
    )
    corr = r_now.iloc[-BETA_WINDOW:].corr().to_numpy() if r_now.shape[1] > 1 else None
    avg_corr = float(corr[~np.eye(len(corr), dtype=bool)].mean()) if corr is not None else math.nan
    stress = (
        float(sum(v_now[k] * r_now[k].min() for k in v_now if k in r_now)) if len(r_now) else 0.0
    )
    stops = {s.ticker: s for s in open_states}
    positions = []
    for k in sorted(v_now):
        vol = px[k].get("volume")
        adv = (
            gbp(k, float((px[k]["close"] * vol).iloc[-20:].median()))
            if vol is not None and len(vol)
            else math.nan
        )
        st = stops.get(k)
        positions.append(
            {
                "ticker": k,
                "position_id": st.position_id if st else None,
                "shares": round(now_sh[k], 4),
                "close": round(close_at(k, t), 4),
                "stop": st.stop if st else None,
                "value": round(v_now[k], 2),
                "weight": _num(v_now[k] / equity) if equity > 0 else None,
                "pnl_day": round(per[k]["price"] + per[k]["trading"], 2),
                "market": round(per[k]["market"], 2),
                "specific": round(per[k]["specific"], 2),
                "trading": round(per[k]["trading"], 2),
                "beta": _num(per[k]["beta"]),
                "es_component": round(hist.components.get(k, 0.0), 2),
                "es_share": _num(hist.components.get(k, 0.0) / hist.es) if hist.es > 0 else None,
                "risk_at_stop": round(st.risk_at_stop, 2) if st else None,
                "pct_of_adv": _num(v_now[k] / adv) if adv and not math.isnan(adv) else None,
                "worst_day": _num(float(r_now[k].min())) if k in r_now else None,
            }
        )
    closed_today = [
        {"ticker": k, "pnl_day": round(p["price"] + p["trading"], 2)}
        for k, p in per.items()
        if k not in v_now
    ]

    # flags
    var_used = max(hist.var, filt.var)
    limit = rules.max_var * equity
    flags: list[dict[str, str]] = []

    def flag(level: str, what: str) -> None:
        flags.append({"level": level, "what": what})

    hz = f"{h}-day {conf:.0%}"
    if equity > 0 and var_used > limit:
        flag(
            "breach",
            f"{hz} VaR £{var_used:,.0f} is {var_used / equity:.1%} of equity, over the {rules.max_var:.0%} limit",
        )
    elif equity > 0 and var_used > NEAR * limit:
        flag(
            "watch",
            f"{hz} VaR £{var_used:,.0f} is {var_used / equity:.1%} of equity, near the {rules.max_var:.0%} limit",
        )
    if equity > 0 and heat_gbp / equity > rules.max_heat:
        flag(
            "breach",
            f"heat {heat_gbp / equity:.1%} of equity is over the {rules.max_heat:.0%} limit",
        )
    if rules.circuit_breaker and drawdown < -rules.max_drawdown_new_entries:
        flag(
            "breach",
            f"drawdown {-drawdown:.1%} is past the {rules.max_drawdown_new_entries:.0%} circuit breaker",
        )
    elif drawdown < -(rules.max_drawdown_new_entries - BREAKER_NEAR):
        flag(
            "watch",
            f"drawdown {-drawdown:.1%} is near the {rules.max_drawdown_new_entries:.0%} circuit breaker",
        )
    if len(v_now) > 1 and hist.es > 0:
        top = max(hist.components, key=lambda k: hist.components[k])
        share = hist.components[top] / hist.es
        if share > CONCENTRATION:
            flag("watch", f"{top} carries {share:.0%} of the CVaR")
    hist_cards = risk_history(conn)
    hist_cards = pd.concat(
        [
            hist_cards[hist_cards["date"] != t.strftime("%Y-%m-%d")],
            pd.DataFrame(
                [{"date": t.strftime("%Y-%m-%d"), "var_1d": hist_1d.var, "pnl_day": pnl_day}]
            ),
        ],
        ignore_index=True,
    )
    exc, tested = exceptions(hist_cards)
    if tested >= 20 and exc > max(2.0, 2 * (1 - conf) * tested):
        flag(
            "watch",
            f"{exc} losses beat the 1-day VaR in {tested} sessions (about {(1 - conf) * tested:.0f} expected): the VaR is too low",
        )
    if v_now and len(r_now) < MIN_OBS:
        flag("watch", f"the VaR rests on {len(r_now)} sessions of common history (under {MIN_OBS})")
    if missing:
        flag("watch", f"no prices for {', '.join(missing)}: left out of the VaR")

    detail = {
        "positions": positions,
        "closed_today": closed_today,
        "pending": pending_out,
        "pnl": {
            "day": round(pnl_day, 2),
            "price": round(sum(p["price"] for p in per.values()), 2),
            "market": round(sum(p["market"] for p in per.values()), 2),
            "specific": round(sum(p["specific"] for p in per.values()), 2),
            "trading": round(sum(p["trading"] for p in per.values()), 2),
            "income": round(income, 2),
            "other": round(pnl_day - explained, 2),
            "flows": round(f_today, 2),
        },
        "var_change": {
            "previous": round(steps[0], 2),
            "trades": round(steps[1] - steps[0], 2),
            "prices": round(steps[2] - steps[1], 2),
            "new_data": round(steps[3] - steps[2], 2),
            "now": round(steps[3], 2),
        },
        "flags": flags,
    }
    return {
        "date": t,
        "previous_date": t1.strftime("%Y-%m-%d"),
        "equity": round(equity, 2),
        "cash": round(equity - invested, 2),
        "invested": round(invested, 2),
        "positions": len(v_now),
        "confidence": conf,
        "horizon_days": h,
        "window": len(r_now),
        "var_1d": round(hist_1d.var, 2),
        "es_1d": round(hist_1d.es, 2),
        "var": round(hist.var, 2),
        "es": round(hist.es, 2),
        "var_filtered": round(filt.var, 2),
        "es_filtered": round(filt.es, 2),
        "var_pro_forma": round(pro.var, 2),
        "es_pro_forma": round(pro.es, 2),
        "var_used": round(var_used, 2),
        "var_pct": _num(var_used / equity) if equity > 0 else None,
        "max_var": rules.max_var,
        "heat": _num(heat_gbp / equity) if equity > 0 else None,
        "heat_gbp": round(heat_gbp, 2),
        "stress_worst_days": round(stress, 2),
        "drawdown": _num(drawdown),
        "beta": _num(book_beta),
        "book_vol": _num(book_vol),
        "avg_corr": _num(avg_corr),
        "pnl_day": round(pnl_day, 2),
        "pnl_mtd": round(float(eq.iloc[-1]) - base - mtd_flow, 2),
        # every deposit and withdrawal so far: a weekend one is off the business-day curve
        "pnl_total": round(equity - (float(flow[flow.index <= t].sum()) if len(flow) else 0.0), 2),
        "twr": _num(summary.twr),
        "bench_day": _num(bret),
        "bench_total": _num(summary.benchmark) if summary.benchmark is not None else None,
        "var_exceptions": exc,
        "var_tested": tested,
        "escalate": any(f["level"] == "breach" for f in flags),
        "flags": "; ".join(f"{f['level']}: {f['what']}" for f in flags),
        "detail": json.dumps(detail),
    }


def _pending(conn: Conn) -> list[dict[str, Any]]:
    """Pending entries (draft / approved positions with an open entry order)."""
    pos = book.positions(conn)
    out = []
    for p in pos.loc[pos["status"].isin(["draft", "approved"])].to_dict("records"):
        pe = book.pending_entry(conn, int(p["position_id"]))
        if pe is not None:
            out.append(
                {
                    "position_id": int(p["position_id"]),
                    "ticker": str(p["ticker"]),
                    "qty": int(pe["qty"]) - int(pe.get("filled") or 0),
                    "price": pe["price"],
                }
            )
    return out


def latest_card(conn: Conn, run_id: int | None = None) -> dict[str, Any] | None:
    """The risk card of ``run_id`` (default: the latest saved), ``detail`` parsed."""
    if run_id is None:
        row = conn.execute(
            "SELECT MAX(run_id) FROM results WHERE ticker = ? AND model = ?", (BOOK, MODEL)
        ).fetchone()
        if not row or row[0] is None:
            return None
        run_id = int(row[0])
    card = store.load_cards(run_id, BOOK, conn=conn).get(MODEL)
    if not card:
        return None
    return {**card, "run_id": run_id, "detail": json.loads(str(card.get("detail") or "{}"))}


def _gbp(v: Any) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"-£{-float(v):,.0f}" if float(v) < 0 else f"£{float(v):,.0f}"


def _px(v: Any) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{float(v):,.1f}"


def _pct(v: Any, signed: bool = False) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{float(v):+.1%}" if signed else f"{float(v):.1%}"


def render_risk(card: dict[str, Any], level: int = 2) -> str:
    """The risk card as markdown: headline, flags, P&L and VaR change, positions; its heading
    at ``level`` (the daily report nests it at 3)."""
    d = card["detail"] if isinstance(card["detail"], dict) else json.loads(str(card["detail"]))
    eq = float(card["equity"]) or math.nan
    h, conf = int(card["horizon_days"]), float(card["confidence"])
    date = pd.Timestamp(card["date"]).strftime("%Y-%m-%d")
    top, sub = "#" * level, "#" * (level + 1)
    lines = [f"{top} Risk ({date})", ""]
    lines += [
        f"Equity {_gbp(card['equity'])} (invested {_gbp(card['invested'])}, cash {_gbp(card['cash'])},"
        f" {int(card['positions'])} positions).",
        f"P&L: day {_gbp(card['pnl_day'])} ({_pct(card['pnl_day'] / eq, True)}),"
        f" month {_gbp(card['pnl_mtd'])}, since inception {_gbp(card['pnl_total'])}"
        f" (time-weighted {_pct(card['twr'], True)}; benchmark {_pct(card['bench_total'], True)},"
        f" today {_pct(card['bench_day'], True)}).",
        "",
        f"| {conf:.0%} | VaR | CVaR |",
        "|---|---|---|",
        f"| 1 day | {_gbp(card['var_1d'])} ({_pct(card['var_1d'] / eq)}) | {_gbp(card['es_1d'])} ({_pct(card['es_1d'] / eq)}) |",
        f"| {h} days, historical | {_gbp(card['var'])} ({_pct(card['var'] / eq)}) | {_gbp(card['es'])} ({_pct(card['es'] / eq)}) |",
        f"| {h} days, EWMA-filtered | {_gbp(card['var_filtered'])} ({_pct(card['var_filtered'] / eq)}) | {_gbp(card['es_filtered'])} ({_pct(card['es_filtered'] / eq)}) |",
        f"| {h} days, pending entries filled | {_gbp(card['var_pro_forma'])} ({_pct(card['var_pro_forma'] / eq)}) | {_gbp(card['es_pro_forma'])} ({_pct(card['es_pro_forma'] / eq)}) |",
        "",
        f"Limit: {h}-day VaR {_pct(card['max_var'])} of equity; now {_pct(card['var_pct'])}"
        f" (the larger of historical and filtered). {int(card['window'])} sessions of history.",
        f"Heat (loss if every stop is hit) {_pct(card['heat'])} ({_gbp(card['heat_gbp'])});"
        f" every stock's worst day at once {_gbp(card['stress_worst_days'])};"
        f" drawdown {_pct(card['drawdown'])}; beta to the index {card['beta'] if card['beta'] is not None else '-'};"
        f" book volatility {_pct(card['book_vol'])} a year; average correlation {card['avg_corr'] if card['avg_corr'] is not None else '-'}.",
        f"1-day VaR backtest: {int(card['var_exceptions'])} exceptions in {int(card['var_tested'])} sessions.",
        "",
        f"{sub} Flags",
        "",
    ]
    lines += [f"- **{f['level']}**: {f['what']}" for f in d.get("flags", [])] or ["none"]
    p = d.get("pnl", {})
    lines += [
        "",
        f"{sub} Where the day's P&L came from ({card['previous_date']} → {date})",
        "",
        f"- price moves {_gbp(p.get('price'))}: market {_gbp(p.get('market'))}, stock-specific {_gbp(p.get('specific'))}",
        f"- trading (today's fills vs the close) {_gbp(p.get('trading'))}",
        f"- income (dividends, interest, less fees) {_gbp(p.get('income'))}",
        f"- unexplained {_gbp(p.get('other'))}; deposits / withdrawals (not P&L) {_gbp(p.get('flows'))}",
    ]
    vc = d.get("var_change", {})
    lines += [
        "",
        f"{sub} Where the {h}-day VaR change came from",
        "",
        f"{_gbp(vc.get('previous'))} → {_gbp(vc.get('now'))}: trades {_gbp(vc.get('trades'))},"
        f" price drift {_gbp(vc.get('prices'))}, new market data {_gbp(vc.get('new_data'))}.",
        "",
        f"{sub} Positions",
        "",
        "| Stock (position) | Shares | Close / stop | Value | Weight | Day P&L | market / specific / trading | Beta | CVaR share | At stop | % of ADV | Worst day |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in d.get("positions", []):
        lines.append(
            f"| {x['ticker']} ({x.get('position_id') or '-'}) | {x.get('shares', 0):,.0f} |"
            f" {x.get('close') or 0:,.1f} / {_px(x.get('stop'))} | {_gbp(x['value'])} | {_pct(x['weight'])} | {_gbp(x['pnl_day'])} |"
            f" {_gbp(x['market'])} / {_gbp(x['specific'])} / {_gbp(x['trading'])} |"
            f" {x['beta'] if x['beta'] is not None else '-'} | {_pct(x['es_share'])} |"
            f" {_gbp(x['risk_at_stop'])} | {_pct(x['pct_of_adv'])} | {_pct(x['worst_day'], True)} |"
        )
    if not d.get("positions"):
        lines.append("| none |" + "  |" * 11)
    for x in d.get("closed_today", []):
        lines.append(
            f"\nClosed since the previous session: {x['ticker']} ({_gbp(x['pnl_day'])} today)."
        )
    if d.get("pending"):
        lines += [
            "",
            "Pending entries in the pro-forma: "
            + ", ".join(
                f"{x['ticker']} (position {x['position_id']}) {x['qty']:,} @ {x['price']:,.1f}"
                f" ({_gbp(x['value'])})"
                for x in d["pending"]
            ),
        ]
    lines.append("")
    return "\n".join(lines)


def trend(conn: Conn, last: int = 10) -> pd.DataFrame:
    """The last ``last`` risk cards, one row a session: equity, P&L, VaR, CVaR, heat, drawdown."""
    cols = ["equity", "pnl_day", "var", "var_pct", "es", "heat", "drawdown", "var_exceptions"]
    marks = ", ".join("?" * len(cols))
    df = store.query(
        f"SELECT run_id, date, field, value FROM results WHERE ticker = ? AND model = ?"
        f" AND field IN ({marks})",
        conn,
        [BOOK, MODEL, *cols],
    )
    if df.empty:
        return pd.DataFrame(columns=["run_id", "date", *cols])
    wide = df.pivot_table(index=["run_id", "date"], columns="field", values="value").reset_index()
    return wide.sort_values("run_id").drop_duplicates("date", keep="last").iloc[-last:]


def build_pack(conn: Conn, run_id: int) -> str:
    """The risk manager's pack for daily run ``run_id`` as markdown: the card, the trend, the
    revisions applied since the run started (the monitors'), the market regime, the scheduled
    events on held stocks, the risk manager's previous verdict. ``ValueError`` without a card."""
    from solaraalpha import agents
    from solaraalpha.portfolio import risk

    card = latest_card(conn, run_id)
    if card is None:
        raise ValueError(f"daily run {run_id} has no risk card (no book, or the report failed)")
    from solaraalpha import event_sessions

    es = event_sessions.for_run(conn, run_id)
    lines = [f"> {event_sessions.pack_note(es)}", ""] if es else []
    lines += [render_risk(card), "## Trend (one row a session)", ""]
    tr = trend(conn)
    lines += [
        "| Date | Equity | Day P&L | VaR | VaR % | CVaR | Heat | Drawdown |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['date']} | {_gbp(r.get('equity'))} | {_gbp(r.get('pnl_day'))} | {_gbp(r.get('var'))} |"
        f" {_pct(r.get('var_pct'))} | {_gbp(r.get('es'))} | {_pct(r.get('heat'))} | {_pct(r.get('drawdown'))} |"
        for r in tr.to_dict("records")
    ]
    started = str(store.run_info(run_id, conn=conn)["started_at"])
    revs = book.revisions(conn)
    revs = revs[revs["at"].astype(str) >= started] if len(revs) else revs
    tickers = book.positions(conn).set_index("position_id")["ticker"]
    lines += ["", "## Changes applied today (the monitors' and the PM's)", ""]
    lines += [
        f"- {tickers.get(int(r['position_id']), '?')} position {r['position_id']}: {r['action']}"
        f" {r['old']} → {r['new']} by {r['actor']} ({r['status']}): {r['reason']}"
        for r in revs.to_dict("records")
        if r["action"] != "hold"
    ] or ["none"]
    from solaraalpha.portfolio.rules import load_rules

    regime = store.load_cards(run_id, load_rules().benchmark, conn=conn).get("regime")
    lines += ["", "## Market", ""]
    lines.append(
        f"{regime.get('regime')}: {regime.get('reason')}"
        if regime
        else "no regime card in this run"
    )
    held = {p["ticker"] for p in card["detail"].get("positions", [])}
    events = {t: e for t, e in risk.next_events(conn).items() if t in held}
    lines += ["", "## Scheduled events on held stocks", ""]
    lines += [f"- {t}: {e}" for t, e in sorted(events.items())] or ["none known"]
    lines += ["", "## Event sessions booked (record.py session list)", ""]
    lines += [f"- {event_sessions.describe(s)}" for s in event_sessions.upcoming(conn)] or [
        "none: book one for a dated event before the next daily run that can move the book"
    ]
    prev = agents.prior(conn, BOOK, stage="risk", limit=1).get("verdicts", [])
    lines += ["", "## Your previous report", ""]
    if prev:
        v = prev[0]
        lines += [
            f"run {v['run_id']} ({v['date']}), {v.get('label')}: {v.get('reasons')}",
            "",
            str(v.get("note_md") or ""),
        ]
    else:
        lines.append("none: this is the first")
    lines.append("")
    return "\n".join(lines)
