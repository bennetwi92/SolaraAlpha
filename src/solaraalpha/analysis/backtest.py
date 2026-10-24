"""Replay the screen point-in-time and sweep its thresholds (issue #138, story map S11.3).

:func:`replay` recomputes the technical, levels, relative-strength and fundamentals cards for
each Friday using only the weekly bars (and benchmark bars, and fundamentals public by then)
up to that week, and labels each week with the current bands. :func:`forward` gives the
forward returns per week; :func:`sweep` relabels the replay with one threshold changed at a
time and reports the ACT count, forward return, excess and hit rate; :func:`report` writes it
up with the survivorship caveat and a **suggested** band table. Nothing here changes the bands:
the owner decides from the report (ADR 0014).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from solaraalpha.analysis.fundamentals import fundamentals_card, fx_ticker, reporting_currency
from solaraalpha.analysis.levels import levels_card
from solaraalpha.analysis.relative_strength import Comparisons, compare, rs_card, weekly_close
from solaraalpha.analysis.technical import scorecard
from solaraalpha.data.clean import CLOSE_ONLY_BLOCKING, check_prices, clean_prices, to_weekly
from solaraalpha.log import get_logger
from solaraalpha.pipeline import Clause, combine
from solaraalpha.toolkits import get

log = get_logger(__name__)

TOOLKITS = ("technical", "levels", "relative_strength", "fundamentals")
HORIZONS = (4, 8, 16)
MIN_WEEKS = 60  # weeks of history before the first replayed card (warm-up for the 52w fields)
MIN_N = 30  # ACT weeks a sweep cell needs before its numbers are read
# relative_strength has no ACT clause since #210 phase 0 (context only), so nothing to sweep.
GRID: dict[tuple[str, str], tuple[float, ...]] = {
    ("technical", "trend"): (0.0, 0.1, 0.25, 0.4),
}
Fetch = Callable[[str], pd.DataFrame]


def _flat(model: str, card: Any) -> dict[str, Any]:
    out = {}
    for k, v in asdict(card).items():
        if k == "date":
            continue
        out[f"{model}.{k}"] = v.isoformat() if isinstance(v, pd.Timestamp) else v
    return out


def _label(toolkit: str, card: Mapping[str, Any], act: Iterable[str], watch: Iterable[str]) -> str:
    """ACT / WATCH / PASS of ``card`` (its fields without the model prefix) under ``act`` and
    ``watch`` clauses (the registered ones by default)."""
    acts = [Clause.parse(c) for c in act]
    watches = [Clause.parse(c) for c in watch]
    if all(c.check(dict(card)) for c in acts):
        return "ACT"
    if all(c.check(dict(card)) for c in watches):
        return "WATCH"
    return "PASS"


def _card_fields(row: Mapping[str, Any], model: str) -> dict[str, Any]:
    pre = model + "."
    return {k[len(pre) :]: v for k, v in row.items() if k.startswith(pre)}


def replay(
    ticker: str,
    weekly: pd.DataFrame,
    fund: pd.DataFrame | None = None,
    comp: Comparisons | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    step: int = 1,
    fetch: Fetch | None = None,
) -> pd.DataFrame:
    """One row per replayed Friday: every card field (``model.field``) plus ``label_<toolkit>``
    and the combined ``label``, computed from the bars up to that week only.

    ``weekly`` is the stock's completed weekly bars (``to_weekly`` of checked, cleaned, trimmed
    prices); ``fund`` the raw fundamentals frame (``known()`` lags applied per week by
    ``fundamentals_card``); ``comp`` the comparisons and ``fetch`` their daily prices, sliced
    to the week inside. Weeks whose cards cannot be computed (warm-up) are skipped.
    """
    idx = weekly.index
    lo = pd.Timestamp(start) if start is not None else idx[0]
    hi = pd.Timestamp(end) if end is not None else idx[-1]
    lo = lo.tz_localize("UTC") if lo.tz is None else lo
    hi = hi.tz_localize("UTC") if hi.tz is None else hi
    weeks = [w for i, w in enumerate(idx) if lo <= w <= hi and i >= MIN_WEEKS and (i % step == 0)]
    rows = []
    others: dict[str, pd.DataFrame] = {}
    fx = None  # accounts in another currency than the price: FX via fetch (as-of per week)
    pair = (
        fx_ticker(reporting_currency(fund), weekly.attrs.get("currency"))
        if fund is not None
        else None
    )
    if pair and fetch is not None:
        try:
            fx = fetch(pair)["close"]
        except Exception as e:
            log.warning("%s: no FX %s (%s); valuation NaN", ticker, pair, e)

    def full(t: str) -> pd.DataFrame:
        if t not in others:
            if fetch is None:
                raise ValueError("no fetch for comparisons")
            others[t] = fetch(t)
        return others[t]

    def sliced_fetch(week: pd.Timestamp) -> Fetch:
        def f(t: str) -> pd.DataFrame:
            df = full(t)
            return df[df.index <= week + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)]

        return f

    prepared: dict[str, tuple[pd.DataFrame, pd.Series, pd.Series]] = {}

    def closes_at(week: pd.Timestamp) -> Callable[[str], pd.Series]:
        """The comparison's weekly close as of ``week``, as weekly_close of the daily bars up to
        it would give: checked and cleaned once, sliced per week; only a week a blocking issue
        reaches is worked out again from its own bars (the cut must not see the future)."""
        end = week + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)

        def f(t: str) -> pd.Series:
            if t not in prepared:
                daily = full(t)
                issues = check_prices(daily)
                clean = clean_prices(daily)
                block = issues.loc[issues["check"].isin(CLOSE_ONLY_BLOCKING), "date"].dropna()
                block = block[block.isin(clean.index)]
                prepared[t] = (daily, block, to_weekly(clean)["adj_close"])
            daily, block, weekly_full = prepared[t]
            if (block <= end).any():
                return weekly_close(daily[daily.index <= end])
            return weekly_full[weekly_full.index <= week]

        return f

    for week in weeks:
        w = weekly[weekly.index <= week]
        w.attrs = dict(weekly.attrs)
        row: dict[str, Any] = {"week": week, "ticker": ticker}
        labels: dict[str, str] = {}
        try:
            row |= _flat("technical", scorecard(w))
            row |= _flat("levels", levels_card(w))
        except (ValueError, IndexError, KeyError) as e:
            log.debug("%s %s: skipped (%s)", ticker, week.date(), e)
            continue
        if comp is not None and fetch is not None:
            try:
                cmp = compare(w, comp, sliced_fetch(week), closes_at(week))
                rs = rs_card(cmp, row["technical.trend"])
                row |= _flat("relative_strength", rs)
            except (ValueError, IndexError, KeyError) as e:
                log.debug("%s %s: no relative strength (%s)", ticker, week.date(), e)
        if fund is not None and not fund.empty:
            try:
                row |= _flat("fundamentals", fundamentals_card(fund, w, as_of=week, fx=fx))
            except (ValueError, IndexError, KeyError) as e:
                log.debug("%s %s: no fundamentals (%s)", ticker, week.date(), e)
        for tk in TOOLKITS:
            fields = _card_fields(row, tk)
            if fields:
                t = get(tk)
                labels[tk] = _label(tk, fields, t.act, t.watch)
                row[f"label_{tk}"] = labels[tk]
        row["label"] = combine(list(labels.values()))
        rows.append(row)
    return pd.DataFrame(rows)


def entry_and_close(weekly: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Per signal Friday: the entry (the next week's first open, adjusted like ``adj_close``)
    and the week's ``adj_close``."""
    adj_open = weekly["open"] * weekly["adj_close"] / weekly["close"]
    return adj_open.shift(-1), weekly["adj_close"]


def forward(
    weekly: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS, bench: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Per week: ``ret_{N}w`` from **Monday's open** after the signal Friday (the next week's
    first open: the card is only known after Friday's close, which cannot be traded; ADR 0029)
    to the close N weeks after the signal, adjusted like ``adj_close``; ``exc_{N}w`` over
    ``bench`` (same shape) when given; NaN where the future is not there."""
    out = pd.DataFrame(index=weekly.index)
    entry, close = entry_and_close(weekly)
    if bench is not None:
        b_entry, b_close = (s.reindex(weekly.index).ffill() for s in entry_and_close(bench))
    for n in horizons:
        out[f"ret_{n}w"] = close.shift(-n) / entry - 1
        if bench is not None:
            out[f"exc_{n}w"] = out[f"ret_{n}w"] - (b_close.shift(-n) / b_entry - 1)
        else:
            out[f"exc_{n}w"] = np.nan
    out.index.name = "week"
    return out.reset_index()


_THRESH = re.compile(r"^(\s*)(\w+)(\s*(?:>=|<=|>|<|==|!=)\s*)(-?\d+(?:\.\d+)?)(\s*)$")


def _with_threshold(clauses: Iterable[str], field: str, value: float) -> tuple[str, ...]:
    """The ``act`` clauses with the numeric threshold of the first condition on ``field``
    replaced by ``value`` (a clause ``field >= 0.25`` becomes ``field >= 0.4``)."""
    out = []
    for c in clauses:
        parts = re.split(r"(\s+or\s+)", c)
        for i, p in enumerate(parts):
            m = _THRESH.match(p)
            if m and m.group(2) == field:
                parts[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{value:g}{m.group(5)}"
        out.append("".join(parts))
    return tuple(out)


def _stats(g: pd.DataFrame, horizons: tuple[int, ...]) -> dict[str, float]:
    out: dict[str, float] = {"n_act": float(len(g))}
    for n in horizons:
        r = g[f"ret_{n}w"].dropna()
        e = g[f"exc_{n}w"].dropna()
        out[f"ret_{n}w"] = float(r.mean()) if len(r) else math.nan
        out[f"exc_{n}w"] = float(e.mean()) if len(e) else math.nan
        out[f"hit_{n}w"] = float((r > 0).mean()) if len(r) else math.nan
    return out


def sweep(
    replayed: pd.DataFrame,
    fwd: pd.DataFrame,
    grid: Mapping[tuple[str, str], Iterable[float]] = GRID,
    horizons: tuple[int, ...] = HORIZONS,
) -> pd.DataFrame:
    """One row per (toolkit, field, threshold): the combined label recomputed with that one ACT
    threshold changed and the others at their current values, then the ACT weeks' count, mean
    forward return, mean excess and hit rate per horizon. The first row per key is the current
    threshold (``current`` True). ``fwd`` is :func:`forward` for the same ticker(s), joined on
    ``ticker`` (when present) and ``week``."""
    if replayed.empty:
        return pd.DataFrame()
    on = ["ticker", "week"] if "ticker" in fwd.columns else ["week"]
    joined = replayed.merge(fwd, on=on, how="left")
    rows = []
    for (toolkit, field), values in grid.items():
        tk = get(toolkit)
        current = _current(tk.act, field)
        if current is None:
            continue
        for v in [current, *[x for x in values if x != current]]:
            act = _with_threshold(tk.act, field, v)
            labels = []
            for raw in joined.to_dict("records"):
                r = {str(k): v for k, v in raw.items()}
                per: list[str] = []
                for other in TOOLKITS:
                    fields = _card_fields(r, other)
                    if not fields:
                        continue
                    t = get(other)
                    per.append(_label(other, fields, act if other == toolkit else t.act, t.watch))
                labels.append(combine(per) if per else "ERROR")
            acts = joined[[lab == "ACT" for lab in labels]]
            rows.append(
                {
                    "toolkit": toolkit,
                    "field": field,
                    "threshold": v,
                    "current": v == current,
                    **_stats(acts, horizons),
                }
            )
    return pd.DataFrame(rows)


def _current(clauses: Iterable[str], field: str) -> float | None:
    for c in clauses:
        for p in re.split(r"\s+or\s+", c):
            m = _THRESH.match(p)
            if m and m.group(2) == field:
                return float(m.group(4))
    return None


def suggest(sw: pd.DataFrame, horizon: int = 8, min_n: int = MIN_N) -> pd.DataFrame:
    """Per (toolkit, field): the current threshold and the one with the best mean
    ``ret_{horizon}w`` among cells with at least ``min_n`` ACT weeks (NaN when none qualify).
    A suggestion, not a change."""
    rows = []
    if sw.empty:
        return pd.DataFrame(
            columns=["toolkit", "field", "current", "suggested", "n_act", f"ret_{horizon}w"]
        )
    for (tk, field), g in sw.groupby(["toolkit", "field"], sort=False):
        cur = g.loc[g["current"], "threshold"]
        ok = g[g["n_act"] >= min_n].dropna(subset=[f"ret_{horizon}w"])
        best = ok.sort_values(f"ret_{horizon}w", ascending=False).iloc[0] if len(ok) else None
        rows.append(
            {
                "toolkit": tk,
                "field": field,
                "current": float(cur.iloc[0]) if len(cur) else math.nan,
                "suggested": float(best["threshold"]) if best is not None else math.nan,
                "n_act": float(best["n_act"]) if best is not None else math.nan,
                f"ret_{horizon}w": float(best[f"ret_{horizon}w"]) if best is not None else math.nan,
            }
        )
    return pd.DataFrame(rows)


def report(
    replayed: pd.DataFrame,
    sw: pd.DataFrame,
    tickers: Iterable[str],
    years: float,
    horizons: tuple[int, ...] = HORIZONS,
) -> str:
    """Markdown: the caveat, sample sizes, label counts, the sweep table and the suggested bands."""
    tickers = list(tickers)
    lines = [
        "# Screen backtest (point-in-time replay)",
        "",
        "> **Caveat.** The universe is today's watchlist (the current FTSE AIM 100 + AIM UK 50 snapshot), so the"
        " replay has **survivorship bias**: stocks that left the index are missing and the numbers"
        " are indicative, not a performance estimate. Every band stays a placeholder until the owner"
        " decides (ADR 0014); nothing here changes the code.",
        "",
        f"- stocks: {len(tickers)} ({', '.join(tickers[:12])}{', ...' if len(tickers) > 12 else ''})",
        f"- history: {years:g} years of weekly bars; replayed weeks: {len(replayed)}",
    ]
    if not replayed.empty:
        counts = replayed["label"].value_counts()
        lines.append("- weeks by label: " + ", ".join(f"{k} {int(v)}" for k, v in counts.items()))
        lines += ["", "## Weeks per toolkit label", ""]
        for tk in TOOLKITS:
            col = f"label_{tk}"
            if col in replayed:
                c = replayed[col].value_counts()
                lines.append(f"- {tk}: " + ", ".join(f"{k} {int(v)}" for k, v in c.items()))
    lines += ["", "## Threshold sweep (one ACT threshold changed at a time)", ""]
    if sw.empty:
        lines.append("no replayed weeks to sweep")
    else:
        cols = ["toolkit", "field", "threshold", "current", "n_act"]
        cols += [f"{k}_{n}w" for n in horizons for k in ("ret", "exc", "hit")]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for r in sw.to_dict("records"):
            cells = []
            for name in cols:
                v = r.get(name)
                if name in ("toolkit", "field"):
                    cells.append(str(v))
                elif name == "current":
                    cells.append("yes" if v else "")
                elif name in ("threshold", "n_act"):
                    cells.append(f"{float(v):g}" if v is not None else "-")
                else:
                    cells.append(
                        "-"
                        if v is None or (isinstance(v, float) and math.isnan(v))
                        else f"{float(v):+.1%}"
                    )
            lines.append("| " + " | ".join(cells) + " |")
        lines += [
            "",
            f"## Suggested bands (best mean {horizons[1] if len(horizons) > 1 else horizons[0]}-week return with at least {MIN_N} ACT weeks; a proposal)",
            "",
        ]
        sg = suggest(sw, horizons[1] if len(horizons) > 1 else horizons[0])
        lines.append("| toolkit | field | current | suggested | ACT weeks | mean return |")
        lines.append("|---|---|---|---|---|---|")
        hcol = next(c for c in sg.columns if c.startswith("ret_"))
        for r in sg.to_dict("records"):
            sug = "-" if math.isnan(float(r["suggested"])) else f"{float(r['suggested']):g}"
            n = "-" if math.isnan(float(r["n_act"])) else f"{float(r['n_act']):g}"
            ret = "-" if math.isnan(float(r[hcol])) else f"{float(r[hcol]):+.1%}"
            lines.append(
                f"| {r['toolkit']} | {r['field']} | {float(r['current']):g} | {sug} | {n} | {ret} |"
            )
    lines += [
        "",
        "Change bands in `solaraalpha/toolkits.py` only after reading this with the caveat in mind.",
    ]
    return "\n".join(lines) + "\n"
