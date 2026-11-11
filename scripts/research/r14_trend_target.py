"""Research 0016: trend entries with a take-profit and a stop (the owner's hypothesis, 2026-11-11).

uv run python scripts/research/r14_trend_target.py count PANEL_DIR  # trades and costs; no outcome
uv run python scripts/research/r14_trend_target.py run PANEL_DIR    # the pre-registered test

PANEL_DIR is research 0015's panel (``r13_trend_rotation.py fetch`` then ``build``). Each Friday
the book's empty slots (8) take the highest ``s26 > 0`` names, bought at the next open; each
position exits at a stop (entry - 3 daily ATR), a target (+5 / +10 / +15%, or none) or after 50
sessions, simulated day by day on adjusted bars up to 2025-10-17 (the in-sample end). Everything
follows research 0016 as pre-registered. Research only: nothing in the pipeline imports it.
"""

from __future__ import annotations

import math
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from r13_trend_rotation import FLOOR, HALVES, daily_bars

from solaraalpha.analysis.technical import atr

FIRST, ENTRY_LAST = pd.Timestamp("2022-12-16", tz="UTC"), pd.Timestamp("2025-08-08", tz="UTC")
END = pd.Timestamp("2025-10-17", tz="UTC")  # in-sample end: no bar after it is read
SIG = "s26"
SLOTS = 8
STOP_ATR = 3.0
MAX_SESSIONS = 50
TARGETS: dict[str, float | None] = {"T5": 0.05, "T10": 0.10, "T15": 0.15, "none": None}
BLOCK, REPS, SEED = 16, 2000, 16
TESTS = 4  # Bonferroni: 97.5% intervals
BAR_R = 0.2


@dataclass
class Bars:
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    atr: np.ndarray
    idx: pd.DatetimeIndex


def adjusted(panel: Path, t: str) -> Bars | None:
    try:
        d = daily_bars(panel, t)
    except Exception:
        return None
    d = d[d.index <= END]
    if len(d) < 30:
        return None
    f = d["adj_close"] / d["close"]
    a = pd.DataFrame(
        {"open": d["open"] * f, "high": d["high"] * f, "low": d["low"] * f, "close": d["adj_close"]}
    )
    return Bars(
        a["open"].to_numpy(float),
        a["high"].to_numpy(float),
        a["low"].to_numpy(float),
        a["close"].to_numpy(float),
        atr(a).to_numpy(float),
        pd.DatetimeIndex(a.index),
    )


def _load(args: tuple[Path, str]) -> tuple[str, Bars | None]:
    return args[1], adjusted(*args)


def load_bars(panel: Path, tickers: list[str]) -> dict[str, Bars]:
    with ProcessPoolExecutor(4) as ex:
        out = dict(ex.map(_load, [(panel, t) for t in tickers]))
    return {t: b for t, b in out.items() if b is not None}


def candidates(panel: Path) -> pd.DataFrame:
    f = pd.read_parquet(panel / "r13.parquet", columns=["ticker", "week", SIG, "adv", "spread"])
    f = f[(f["week"] >= FIRST) & (f["week"] <= ENTRY_LAST) & (f["adv"] >= FLOOR) & (f[SIG] > 0)]
    return f.sort_values(["week", SIG], ascending=[True, False])


def simulate(
    cands: pd.DataFrame,
    bars: dict[str, Bars],
    target: float | None,
    cost_mult: float,
    exclude: frozenset[str] = frozenset(),
) -> tuple[pd.DataFrame, pd.Series]:
    """Trades (one row each) and the book's value at every session's close."""
    cal = pd.DatetimeIndex(sorted(set().union(*(b.idx for b in bars.values()))))
    cal = cal[(cal > FIRST) & (cal <= END)]
    pos_of = {t: {d: i for i, d in enumerate(b.idx)} for t, b in bars.items()}
    weeks = {w: g for w, g in cands.groupby("week")}
    entry_day: dict[pd.Timestamp, pd.Timestamp] = {}
    for w in weeks:
        nxt = cal[cal > w]
        if len(nxt):
            entry_day[nxt[0]] = w
    cash, nav, last_nav_fri = 1.0, 1.0, 1.0
    open_: dict[str, dict] = {}
    trades: list[dict] = []
    curve = {}
    for d in cal:
        if d in entry_day and len(open_) < SLOTS:
            w = entry_day[d]
            for row in weeks[w].itertuples():
                if len(open_) >= SLOTS:
                    break
                t = row.ticker
                if t in open_ or t in exclude or t not in bars:
                    continue
                b, i = bars[t], pos_of[t].get(d)
                if i is None:
                    continue
                prior = np.searchsorted(b.idx, w, side="right") - 1
                if prior < 0:
                    continue
                e, a = b.o[i], b.atr[prior]
                stop = e - STOP_ATR * a
                if not (math.isfinite(e) and e > 0 and math.isfinite(a) and a > 0 and stop > 0):
                    continue
                spend = min(last_nav_fri / SLOTS, cash)
                if spend <= 0:
                    break
                side = cost_mult * float(np.nan_to_num(row.spread)) / 2
                cash -= spend
                open_[t] = {
                    "ticker": t,
                    "week": w,
                    "entry": e,
                    "stop": stop,
                    "target": e * (1 + target) if target is not None else math.inf,
                    "side": side,
                    "shares": spend * (1 - side) / e,
                    "start": i,
                    "last": e,
                }
        for t in list(open_):
            p, b = open_[t], bars[t]
            i = pos_of[t].get(d)
            if i is None:
                continue
            px, why = None, None
            if b.lo[i] <= p["stop"]:
                px, why = min(b.o[i], p["stop"]), "stop"
            elif b.h[i] >= p["target"]:
                px, why = max(b.o[i], p["target"]), "target"
            elif i - p["start"] + 1 >= MAX_SESSIONS:
                px, why = b.c[i], "time"
            p["last"] = b.c[i]
            if px is None:
                continue
            cash += p["shares"] * px * (1 - p["side"])
            risk = (p["entry"] - p["stop"]) / p["entry"]
            net = (px * (1 - p["side"]) * (1 - p["side"])) / p["entry"] - 1
            trades.append(
                {
                    "ticker": t,
                    "week": p["week"],
                    "exit": why,
                    "ret": net,
                    "R": net / risk,
                    "days": i - p["start"] + 1,
                }
            )
            del open_[t]
        nav = cash + sum(p["shares"] * p["last"] for p in open_.values())
        curve[d] = nav
        if d.weekday() == 4 or d + pd.Timedelta(days=1) not in cal:  # last session of the week
            last_nav_fri = nav
    return pd.DataFrame(trades), pd.Series(curve)


def boot(tr: pd.DataFrame) -> tuple[float, float]:
    """97.5% moving-block bootstrap of expectancy (mean R), blocks of 16 entry weeks."""
    g = tr.groupby("week")["R"].agg(["sum", "count"])
    allw = pd.date_range(FIRST, ENTRY_LAST, freq="W-FRI")
    a = g.reindex(allw, fill_value=0).to_numpy(float)
    rng = np.random.default_rng(SEED)
    k = math.ceil(len(a) / BLOCK)
    draws = []
    for _ in range(REPS):
        ix = np.concatenate([np.arange(s, s + BLOCK) for s in rng.integers(0, len(a) - BLOCK, k)])
        s = a[ix[: len(a)]].sum(axis=0)
        if s[1]:
            draws.append(s[0] / s[1])
    q = 100 * 0.1 / TESTS / 2
    lo, hi = np.percentile(draws, [q, 100 - q])
    return float(lo), float(hi)


def halves(tr: pd.DataFrame) -> list[float]:
    out = []
    for _, a, b in HALVES:
        m = (tr["week"] >= pd.Timestamp(a, tz="UTC")) & (tr["week"] <= pd.Timestamp(b, tz="UTC"))
        out.append(float(tr.loc[m, "R"].mean()) if m.any() else math.nan)
    return out


def curve_stats(c: pd.Series) -> tuple[float, float]:
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return float(c.iloc[-1] ** (1 / yrs) - 1), float((c / c.cummax() - 1).min())


def bench_weekly(panel: Path, bars: dict[str, Bars]) -> pd.Series:
    """Equal-weight weekly return (Friday close to Friday close) of every stock past the floor."""
    f = pd.read_parquet(panel / "r13.parquet", columns=["ticker", "week", "adv"])
    f = f[(f["adv"] >= FLOOR) & (f["week"] >= FIRST)]
    rets = []
    for t, b in bars.items():
        c = pd.Series(b.c, index=b.idx).resample("W-FRI").last()
        r = (c.shift(-1) / c - 1).rename("r").to_frame().assign(ticker=t)
        rets.append(r.rename_axis("week").reset_index())
    r = pd.concat(rets).merge(f, on=["ticker", "week"])
    return r.groupby("week")["r"].mean()


def count(panel: Path) -> None:
    cands = candidates(panel)
    print(f"candidate stock-weeks (s26 > 0, past £{FLOOR:,.0f}): {len(cands):,}")
    print(f"candidates per Friday: median {cands.groupby('week').size().median():.0f}")
    print(
        f"median spread of the top 8 a week: "
        f"{cands.groupby('week').head(SLOTS)['spread'].median():.2%} (full spread)"
    )


def run(panel: Path) -> None:
    cands = candidates(panel)
    bars = load_bars(panel, sorted(cands["ticker"].unique()))
    bench = bench_weekly(panel, bars)
    print("## The test: expectancy after 2x costs\n")
    print(
        "| variant | trades | expectancy (R) | 97.5% interval | positive half-years | halves (R) "
        "| without best 3 | pass |"
    )
    print("|---|---|---|---|---|---|---|---|")
    money = []
    for name, tgt in TARGETS.items():
        tr, c = simulate(cands, bars, tgt, 2.0)
        lo, hi = boot(tr)
        hs = halves(tr)
        pos = sum(1 for h in hs if np.isfinite(h) and h > 0)
        best = frozenset(tr.groupby("ticker")["R"].sum().nlargest(3).index)
        tr3, _ = simulate(cands, bars, tgt, 2.0, exclude=best)
        e, e3 = float(tr["R"].mean()), float(tr3["R"].mean())
        ok = e > BAR_R and lo > 0 and pos >= 5 and e3 > BAR_R
        print(
            f"| {name} | {len(tr)} | {e:+.3f} | {lo:+.3f} to {hi:+.3f} | {pos} of 6 | "
            + " / ".join(f"{h:+.2f}" for h in hs)
            + f" | {e3:+.3f} | {'yes' if ok else 'no'} |"
        )
        row = {"variant": name, "trades": tr}
        for m in (2.0, 1.0, 0.5):
            _, cm = (tr, c) if m == 2.0 else simulate(cands, bars, tgt, m)
            row[m] = curve_stats(cm)
            if m == 2.0:
                wk = cm.resample("W-FRI").last().pct_change().dropna()
                row["excess"] = float((wk - bench.reindex(wk.index).shift(1)).mean())
        tr0, _ = simulate(cands, bars, tgt, 0.0)
        side_cost = float(cands.groupby("week").head(SLOTS)["spread"].median()) / 2
        row["gross_ret"] = float(tr0["ret"].mean())
        row["breakeven"] = row["gross_ret"] / 2 if row["gross_ret"] > 0 else math.nan
        row["side_cost"] = side_cost
        money.append(row)
    print("\n## In money terms (reported, not the bar)\n")
    print(
        "| variant | a year, 2x / 1x / 0.5x costs | max drawdown 2x / 0.5x | at target / stop / "
        "10 weeks | win rate | avg win / avg loss (2x) | days held | gross return a trade | "
        "break-even cost per side |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for r in money:
        tr = r["trades"]
        sh = tr["exit"].value_counts(normalize=True)
        win = tr["ret"] > 0
        print(
            f"| {r['variant']} | {r[2.0][0]:+.1%} / {r[1.0][0]:+.1%} / {r[0.5][0]:+.1%} | "
            f"{r[2.0][1]:.1%} / {r[0.5][1]:.1%} | {sh.get('target', 0):.0%} / "
            f"{sh.get('stop', 0):.0%} / {sh.get('time', 0):.0%} | {win.mean():.0%} | "
            f"{tr.loc[win, 'ret'].mean():+.1%} / {tr.loc[~win, 'ret'].mean():+.1%} | "
            f"{tr['days'].median():.0f} | {r['gross_ret']:+.2%} | {r['breakeven']:.2%} |"
        )
    print(f"\nmedian estimated cost per side of the names bought (1x): {money[0]['side_cost']:.2%}")
    bq = (1 + bench.dropna()).cumprod()
    bq = bq[bq.index <= END]
    print(
        f"equal-weight eligible universe: {curve_stats(bq)[0]:+.1%} a year, "
        f"max drawdown {curve_stats(bq)[1]:.1%}"
    )


if __name__ == "__main__":
    cmd, panel = sys.argv[1], Path(sys.argv[2])
    {"count": count, "run": run}[cmd](panel)
