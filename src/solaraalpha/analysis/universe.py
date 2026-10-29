"""The AIM research universe point-in-time: liquidity floor and size group per Friday (#290).

Research only (epic #283, research 0010 amendment): nothing in the pipeline imports it. The
universe is ``config/research_universe.toml`` (every AIM company Trading212 offers); the live
watchlist is the control group. Per stock and Friday, from the bars and fundamentals known then:

- ``adv``: median daily traded value over the last 20 sessions, GBP (as ``liquidity.traded_value``);
  a stock-week is in the research universe only when ``adv >= RESEARCH_FLOOR_GBP``;
- ``mcap``: close x the ordinary share count of the latest annual statement public by that Friday
  (``fundamentals.known``). Before the first one is public, the earliest statement's count is used
  and ``size_pit`` is False (it affects only which size group a week is reported in, never a
  signal);
- ``size``: ``aim100`` for today's watchlist (FTSE AIM 100 + AIM UK 50, the owner's control),
  else ``micro`` under ``MICRO_CAP_GBP``, else ``rest``; ``unknown`` with no share count at all.
"""

from __future__ import annotations

import tomllib

import numpy as np
import pandas as pd

from solaraalpha.analysis.fundamentals import usable_from
from solaraalpha.data.announcements import RESEARCH_UNIVERSE
from solaraalpha.pipeline import watchlist
from solaraalpha.portfolio.book import to_account

RESEARCH_FLOOR_GBP = 10_000.0  # owner, #288 (2026-10-28); the live min_adv_gbp stays £25k
MICRO_CAP_GBP = 50e6
SIZES = ("aim100", "rest", "micro")
SHARES = "annualOrdinarySharesNumber"
ADV_SESSIONS = 20


def research_tickers() -> list[str]:
    """Every ticker in ``config/research_universe.toml``, sorted."""
    with RESEARCH_UNIVERSE.open("rb") as f:
        return sorted(tomllib.load(f)["stocks"])


def weekly_adv(daily: pd.DataFrame, fridays: pd.DatetimeIndex) -> pd.Series:
    """20-session median traded value (GBP) as of each Friday, from sessions up to that day."""
    value = (daily["close"] * daily["volume"]).dropna()
    med = value.rolling(ADV_SESSIONS, min_periods=ADV_SESSIONS // 2).median()
    cur = daily.attrs.get("currency", "GBp")
    out = med.reindex(med.index.union(fridays)).ffill().reindex(fridays)
    return out.map(lambda v: to_account(float(v), cur) if np.isfinite(v) else np.nan)


def shares_at(fund: pd.DataFrame | None, fridays: pd.DatetimeIndex) -> pd.DataFrame:
    """Ordinary share count per Friday (``shares``) and whether it was public then (``pit``)."""
    out = pd.DataFrame({"shares": np.nan, "pit": False}, index=fridays)
    if fund is None or fund.empty:
        return out
    f = fund[fund["field"] == SHARES].assign(usable=lambda x: usable_from(x))
    f = f.dropna(subset=["value"]).sort_values("usable")
    if f.empty:
        return out
    known = pd.Series(f["value"].to_numpy(), index=pd.DatetimeIndex(f["usable"]))
    known = known[~known.index.duplicated(keep="last")]
    pit = known.reindex(known.index.union(fridays)).ffill().reindex(fridays)
    out["pit"] = pit.notna().to_numpy()
    first = float(f.sort_values("date")["value"].iloc[0])
    out["shares"] = pit.fillna(first).to_numpy()
    return out


def size_group(control: bool, mcap: float) -> str:
    """``aim100`` / ``micro`` / ``rest`` / ``unknown`` (see the module docstring)."""
    if control:
        return "aim100"
    if not np.isfinite(mcap):
        return "unknown"
    return "micro" if mcap < MICRO_CAP_GBP else "rest"


def weekly_universe(
    daily: pd.DataFrame, fund: pd.DataFrame | None, fridays: pd.DatetimeIndex, control: bool
) -> pd.DataFrame:
    """Per Friday with a bar on or before it: ``adv``, ``floor`` (adv >= the research floor),
    ``mcap`` (GBP), ``size_pit`` and ``size``. Fridays before the first bar are dropped."""
    fridays = fridays[fridays >= daily.index.min()]
    close = daily["close"].reindex(daily.index.union(fridays)).ffill().reindex(fridays)
    close = close.map(lambda v: to_account(float(v), daily.attrs.get("currency", "GBp")))
    sh = shares_at(fund, fridays)
    out = pd.DataFrame(index=fridays)
    out["adv"] = weekly_adv(daily, fridays)
    out["floor"] = out["adv"] >= RESEARCH_FLOOR_GBP
    out["mcap"] = close * sh["shares"]
    out["size_pit"] = sh["pit"]
    out["size"] = [size_group(control, m) for m in out["mcap"]]
    return out


def panel_tickers(universe: str) -> list[str]:
    """The tickers a research panel builds: ``watchlist`` (today's live list) or ``research``
    (the AIM research universe)."""
    return {"watchlist": watchlist, "research": research_tickers}[universe]()


def with_universe(
    rows: pd.DataFrame, daily: pd.DataFrame, fund: pd.DataFrame | None, ticker: str
) -> pd.DataFrame:
    """A panel's per-Friday ``rows`` (a ``week`` column) with ``floor``, ``mcap``, ``size_pit``
    and ``size`` from :func:`weekly_universe` (control = on today's watchlist)."""
    if rows.empty:
        return rows
    weeks = pd.DatetimeIndex(rows["week"])
    u = weekly_universe(daily, fund, weeks, ticker in set(watchlist()))
    u = u.reindex(weeks)[["floor", "mcap", "size_pit", "size"]]
    return pd.concat([rows.reset_index(drop=True), u.reset_index(drop=True)], axis=1)
