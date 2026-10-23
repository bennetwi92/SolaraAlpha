"""Performance of the book over time: equity curve, drawdown, benchmark comparison, correlation.

The equity curve is rebuilt from the record (cash events, fills, daily closes), never read back
from saved ``portfolio`` cards, so it can be recomputed for any date range. Returns are
time-weighted (deposits and withdrawals are external flows), so adding money does not look like
performance. Money in GBP. Story map S10.4, S10.5.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from solaraalpha.portfolio.book import CASH_IN, to_account

CORRELATION_WEEKS = 26
CORRELATION_FLAG = 0.7  # placeholder, as in portfolio.fit


def _utc_day(ts: object) -> pd.Timestamp:
    t = pd.Timestamp(str(ts))
    t = t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")
    return t.normalize()


def flows(cash: pd.DataFrame) -> pd.Series:
    """External flows per day (deposits - withdrawals, GBP); other cash kinds are performance."""
    if cash.empty:
        return pd.Series(dtype="float64", name="flow")
    ext = cash[cash["kind"].isin(["deposit", "withdrawal"])]
    signed = [
        to_account(float(a), str(c)) * (1 if k == "deposit" else -1)
        for a, c, k in zip(ext["amount"], ext["currency"], ext["kind"], strict=True)
    ]
    s = pd.Series(signed, index=[_utc_day(t) for t in ext["at"]], dtype="float64")
    return s.groupby(level=0).sum().rename("flow")


def equity_curve(
    cash: pd.DataFrame,
    fills: pd.DataFrame,
    prices: Mapping[str, pd.DataFrame],
    end: pd.Timestamp | None = None,
) -> pd.Series:
    """Daily equity (GBP) from the first cash event to ``end`` (default: the last price).

    ``fills`` is :func:`solaraalpha.portfolio.book.fills` joined with each position's ``ticker``
    and ``currency``; ``prices`` maps ticker -> daily frame (``close`` in the quote currency). A
    held ticker with no price on a day carries its last known close forward. An optional
    ``split_factor`` column turns each fill's shares into today's units (Yahoo's closes are
    adjusted back for splits); the cash it moved is unchanged.
    """
    if cash.empty and fills.empty:
        return pd.Series(dtype="float64", name="equity")
    starts = [_utc_day(t) for t in cash["at"]] + [_utc_day(t) for t in fills["at"]]
    last_price = max((p.index[-1] for p in prices.values() if len(p)), default=None)
    stop = end if end is not None else (last_price if last_price is not None else max(starts))
    days = pd.bdate_range(min(starts), _utc_day(stop), tz="UTC", name="date")

    def slot(ts: object) -> int | None:
        """Index of the business day on or after ``ts`` (a weekend booking lands on Monday)."""
        day = max(_utc_day(ts), days[0])
        if day > days[-1]:
            return None
        return int(days.searchsorted(day))

    cash_flow = np.zeros(len(days))
    for a, c, k, t in zip(cash["amount"], cash["currency"], cash["kind"], cash["at"], strict=True):
        i = slot(t)
        if i is None:
            continue
        cash_flow[i] += to_account(float(a), str(c)) * (1 if k in CASH_IN else -1)
    shares: dict[str, np.ndarray] = {}
    for f in fills.to_dict("records"):
        i = slot(f["at"])
        if i is None:
            continue
        money = to_account(float(f["price"]) * int(f["qty"]), str(f["currency"]))
        cash_flow[i] -= int(f["side"]) * money + float(f["commission"]) + float(f["fees"])
        held = shares.setdefault(str(f["ticker"]), np.zeros(len(days)))
        held[i:] += int(f["side"]) * int(f["qty"]) * float(f.get("split_factor") or 1.0)
    cash_level = pd.Series(cash_flow.cumsum(), index=days)

    value = pd.Series(0.0, index=days)
    for ticker, held in shares.items():
        px = prices.get(ticker)
        if px is None or px.empty:
            continue
        close = px["close"].copy()
        close.index = pd.DatetimeIndex([_utc_day(t) for t in close.index])
        close = close.reindex(days).ffill()
        ccy = str(px.attrs.get("currency", "GBp"))
        value += (
            (pd.Series(held, index=days) * close)
            .fillna(0.0)
            .map(lambda v, c=ccy: to_account(float(v), c))
        )
    return (cash_level + value).rename("equity")


@dataclass(frozen=True)
class Drawdown:
    max_drawdown: float  # most negative drawdown, fraction (<= 0)
    peak: pd.Timestamp | None
    trough: pd.Timestamp | None


def drawdown(equity: pd.Series) -> pd.DataFrame:
    """``equity``, its running ``peak`` and ``drawdown`` (fraction, <= 0)."""
    peak = equity.cummax()
    dd = (equity / peak - 1).where(peak > 0, 0.0)
    return pd.DataFrame({"equity": equity, "peak": peak, "drawdown": dd})


def current_drawdown(equity: pd.Series) -> float:
    """Equity now vs its running high, as a fraction (<= 0); 0 for an empty series."""
    if equity.empty:
        return 0.0
    peak = float(equity.cummax().iloc[-1])
    return float(equity.iloc[-1]) / peak - 1 if peak > 0 else 0.0


def max_drawdown(equity: pd.Series) -> Drawdown:
    if equity.empty:
        return Drawdown(0.0, None, None)
    df = drawdown(equity)
    i = int(np.argmin(df["drawdown"].to_numpy()))
    depth = float(df["drawdown"].iloc[i])
    if depth >= 0:
        return Drawdown(0.0, None, None)
    j = int(np.argmax(df["equity"].to_numpy()[: i + 1]))
    return Drawdown(depth, pd.Timestamp(df.index[j]), pd.Timestamp(df.index[i]))


def time_weighted(equity: pd.Series, flow: pd.Series) -> pd.Series:
    """Index of time-weighted returns, starting at 1: each day's return strips out that day's
    external flow, so deposits and withdrawals are not performance."""
    f = flow.reindex(equity.index).fillna(0.0)
    prev = equity.shift(1)
    r = ((equity - f) / prev - 1).where(prev > 0, 0.0).fillna(0.0)
    return (1 + r).cumprod().rename("twr")


def vs_benchmark(equity: pd.Series, bench: pd.Series, flow: pd.Series) -> pd.DataFrame:
    """``twr`` (the book's time-weighted index), ``benchmark`` rebased to 1 at the same start,
    and ``excess`` = twr - benchmark."""
    twr = time_weighted(equity, flow)
    b = bench.copy()
    b.index = pd.DatetimeIndex([_utc_day(t) for t in b.index])
    b = b.reindex(equity.index).ffill().bfill()
    b = (b / b.iloc[0]).rename("benchmark") if len(b) and b.iloc[0] else b
    out = pd.concat([twr, b], axis=1)
    out["excess"] = out["twr"] - out["benchmark"]
    return out


def correlation(
    returns: Mapping[str, pd.Series],
    weeks: int = CORRELATION_WEEKS,
    threshold: float = CORRELATION_FLAG,
) -> tuple[pd.DataFrame, list[tuple[str, str, float]]]:
    """Pairwise correlation of the last ``weeks`` weekly returns, and the pairs above
    ``threshold`` (largest first)."""
    if len(returns) < 2:
        return pd.DataFrame(), []
    frame = pd.concat({k: v.dropna().iloc[-weeks:] for k, v in returns.items()}, axis=1)
    corr = frame.corr(min_periods=8)
    pairs = []
    cols = list(corr.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            v = corr.loc[a, b]
            if (
                isinstance(v, int | float | np.floating)
                and not math.isnan(float(v))
                and float(v) > threshold
            ):
                pairs.append((str(a), str(b), float(v)))
    return corr, sorted(pairs, key=lambda p: -p[2])


@dataclass(frozen=True)
class Summary:
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    equity: float
    net_flows: float  # deposits - withdrawals
    pnl: float  # equity - net flows
    twr: float  # time-weighted return over the span
    max_drawdown: Drawdown
    benchmark: float | None  # benchmark return over the span
    excess: float | None


def summarise(equity: pd.Series, flow: pd.Series, bench: pd.Series | None = None) -> Summary:
    if equity.empty:
        return Summary(None, None, 0.0, 0.0, 0.0, 0.0, Drawdown(0.0, None, None), None, None)
    twr = time_weighted(equity, flow)
    net = float(flow.reindex(equity.index).fillna(0.0).sum())
    bench_ret = None
    if bench is not None and len(bench):
        vb = vs_benchmark(equity, bench, flow)
        bench_ret = float(vb["benchmark"].iloc[-1] - 1)
    twr_ret = float(twr.iloc[-1] - 1)
    return Summary(
        start=equity.index[0],
        end=equity.index[-1],
        equity=float(equity.iloc[-1]),
        net_flows=net,
        pnl=float(equity.iloc[-1]) - net,
        twr=twr_ret,
        max_drawdown=max_drawdown(equity),
        benchmark=bench_ret,
        excess=(twr_ret - bench_ret) if bench_ret is not None else None,
    )


def history_from_book(
    conn: object,
    prices: Mapping[str, pd.DataFrame],
    bench: pd.Series | None = None,
) -> tuple[pd.Series, pd.Series, Summary]:
    """Equity curve, flows and summary for the book on ``conn`` at ``prices``."""
    from solaraalpha.portfolio import book

    cash = book.cash(conn)  # type: ignore[arg-type]
    fl = book.fills_with_tickers(conn)  # type: ignore[arg-type]
    # shares bought before a split count in today's units, as the split-adjusted closes do
    splits = {int(p): book._splits(conn, int(p)) for p in set(fl["position_id"])}  # type: ignore[arg-type]
    fl["split_factor"] = [
        math.prod(f for at, f in splits[int(p)] if at > str(t))
        for p, t in zip(fl["position_id"], fl["at"], strict=True)
    ]
    eq = equity_curve(cash, fl, prices)
    fw = flows(cash)
    return eq, fw, summarise(eq, fw, bench)


def weekly_returns(daily: pd.DataFrame) -> pd.Series:
    """Weekly returns of ``adj_close`` (Friday weeks) for the correlation check."""
    from solaraalpha.data.clean import to_weekly

    w = to_weekly(daily)
    return w["adj_close"].pct_change(fill_method=None).dropna()
