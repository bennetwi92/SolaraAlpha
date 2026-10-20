"""Portfolio rules from ``config/portfolio.toml``: capital, risk per trade and the limits.

One loader, one frozen dataclass, so sizing (:mod:`solaraalpha.portfolio.plan`), the fit check
and the risk view all use the same numbers. Every value is a placeholder until the owner sets it
and flips ``confirmed``; reports warn while it is false. See epic #104 and the story map §6.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from solaraalpha.config import REPO_ROOT

RULES = REPO_ROOT / "config" / "portfolio.toml"


@dataclass(frozen=True)
class Rules:
    confirmed: bool  # False: placeholders; reports warn
    currency: str  # account currency; only "GBP" is supported
    capital: float  # starting capital, account currency
    risk_per_trade: float  # fraction of equity lost if the stop is hit (no PM allocation)
    risk_per_trade_range: tuple[float, float]  # the PM's band for risk per trade (ADR 0027)
    max_positions: int
    max_position_pct: float  # position value / equity
    max_sector_pct: float  # sector value / equity
    max_heat: float  # sum of open risk to the stops / equity
    max_adv_pct: float  # position value / median daily traded value
    cash_buffer: float  # fraction of equity never deployed
    long_only: bool
    entry_validity_days: int  # an unfilled entry order expires after this
    entry_limit_buffer: float  # limit entries at close x (1 + buffer)
    stop_entry_orders: bool  # broker accepts buy-stop orders on AIM
    horizon_weeks: tuple[int, int]  # holding horizon (min, max)
    target_moves: tuple[float, float]  # plan targets as moves above the entry (ADR 0022)
    trail_atr: float  # chandelier trailing stop, in ATRs
    event_flag_days: int  # a scheduled event within this many days of entry is flagged
    max_agent_tickers: int  # cap on tickers the unattended agents work per run, by rank
    event_policy: str  # results inside event_flag_days: flag / hold / trim / exit (#129)
    event_trim_fraction: float  # the share of the position a trim proposes to sell
    circuit_breaker: bool  # stop new entries in a drawdown (#143); off until the owner confirms
    max_drawdown_new_entries: float  # equity this far below its high stops new entries
    benchmark: str  # Yahoo ticker the equity curve is compared with
    shortlist_size: int  # top-ranked tickers the screen sends to the agents (#192)
    shortlist_catalysts: int  # at most this many more join the shortlist on a catalyst
    min_adv_gbp: float  # the screen's liquidity floor: median daily traded value, GBP

    @property
    def warning(self) -> str | None:
        """The banner reports print while the rules are placeholders, else None."""
        if self.confirmed:
            return None
        return "WARNING: placeholder portfolio rules (config/portfolio.toml, confirmed = false)"


_TYPES = {
    "confirmed": bool,
    "currency": str,
    "capital": float,
    "risk_per_trade": float,
    "max_positions": int,
    "max_position_pct": float,
    "max_sector_pct": float,
    "max_heat": float,
    "max_adv_pct": float,
    "cash_buffer": float,
    "long_only": bool,
    "entry_validity_days": int,
    "entry_limit_buffer": float,
    "stop_entry_orders": bool,
    "trail_atr": float,
    "event_flag_days": int,
    "max_agent_tickers": int,
    "event_policy": str,
    "event_trim_fraction": float,
    "circuit_breaker": bool,
    "max_drawdown_new_entries": float,
    "benchmark": str,
    "shortlist_size": int,
    "shortlist_catalysts": int,
    "min_adv_gbp": float,
}
EVENT_POLICIES = ("flag", "hold", "trim", "exit")
_FRACTIONS = ("max_position_pct", "max_sector_pct", "max_heat", "max_adv_pct", "cash_buffer")


def load_rules(path: Path = RULES) -> Rules:
    """Read and validate the rules file; ``ValueError`` names the offending field."""
    with path.open("rb") as f:
        raw = tomllib.load(f)
    names = {f.name for f in fields(Rules)}
    unknown = sorted(set(raw) - names)
    if unknown:
        raise ValueError(f"unknown keys in {path.name}: {', '.join(unknown)}")
    missing = sorted(names - set(raw))
    if missing:
        raise ValueError(f"missing keys in {path.name}: {', '.join(missing)}")

    values: dict[str, object] = {}
    for name, typ in _TYPES.items():
        v = raw[name]
        if typ is float and isinstance(v, int) and not isinstance(v, bool):
            v = float(v)
        if not isinstance(v, typ) or (typ is int and isinstance(v, bool)):
            raise ValueError(f"{name}: expected {typ.__name__}, got {v!r}")
        values[name] = v
    hw = raw["horizon_weeks"]
    if not (isinstance(hw, list) and len(hw) == 2 and all(isinstance(w, int) for w in hw)):
        raise ValueError(f"horizon_weeks: expected two integers, got {hw!r}")
    values["horizon_weeks"] = (hw[0], hw[1])
    tm = raw["target_moves"]
    if not (
        isinstance(tm, list)
        and len(tm) == 2
        and all(isinstance(m, int | float) and not isinstance(m, bool) for m in tm)
        and 0 < tm[0] < tm[1]
    ):
        raise ValueError(f"target_moves: expected two increasing moves > 0, got {tm!r}")
    values["target_moves"] = (float(tm[0]), float(tm[1]))
    rr = raw["risk_per_trade_range"]
    if not (
        isinstance(rr, list)
        and len(rr) == 2
        and all(isinstance(m, int | float) and not isinstance(m, bool) for m in rr)
        and 0 < rr[0] <= rr[1] <= 0.05
    ):
        raise ValueError(f"risk_per_trade_range: expected [low, high] in (0, 0.05], got {rr!r}")
    values["risk_per_trade_range"] = (float(rr[0]), float(rr[1]))
    rules = Rules(**values)  # type: ignore[arg-type]
    _validate(rules)
    return rules


def _validate(r: Rules) -> None:
    if r.currency != "GBP":
        raise ValueError(f"currency: only GBP is supported, got {r.currency!r}")
    if not r.capital > 0:
        raise ValueError(f"capital: must be > 0, got {r.capital}")
    if not 0 < r.risk_per_trade <= 0.05:
        raise ValueError(f"risk_per_trade: must be in (0, 0.05], got {r.risk_per_trade}")
    lo, hi = r.risk_per_trade_range
    if not lo <= r.risk_per_trade <= hi:
        raise ValueError(
            f"risk_per_trade_range: {list(r.risk_per_trade_range)} must contain risk_per_trade"
            f" {r.risk_per_trade}"
        )
    if r.event_policy not in EVENT_POLICIES:
        raise ValueError(f"event_policy: must be one of {EVENT_POLICIES}, got {r.event_policy!r}")
    if not 0 < r.event_trim_fraction <= 1:
        raise ValueError(f"event_trim_fraction: must be in (0, 1], got {r.event_trim_fraction}")
    if not 0 < r.max_drawdown_new_entries <= 1:
        raise ValueError(
            f"max_drawdown_new_entries: must be in (0, 1], got {r.max_drawdown_new_entries}"
        )
    for name in _FRACTIONS:
        v = getattr(r, name)
        if not 0 < v <= 1:
            raise ValueError(f"{name}: must be in (0, 1], got {v}")
    if r.max_positions < 1:
        raise ValueError(f"max_positions: must be >= 1, got {r.max_positions}")
    if not r.horizon_weeks[0] < r.horizon_weeks[1]:
        raise ValueError(f"horizon_weeks: min must be < max, got {list(r.horizon_weeks)}")
    for name in ("entry_validity_days", "event_flag_days", "max_agent_tickers"):
        if getattr(r, name) < 1:
            raise ValueError(f"{name}: must be >= 1, got {getattr(r, name)}")
    if r.shortlist_size < 1:
        raise ValueError(f"shortlist_size: must be >= 1, got {r.shortlist_size}")
    if r.shortlist_catalysts < 0:
        raise ValueError(f"shortlist_catalysts: must be >= 0, got {r.shortlist_catalysts}")
    if r.min_adv_gbp < 0:
        raise ValueError(f"min_adv_gbp: must be >= 0, got {r.min_adv_gbp}")
    if r.entry_limit_buffer < 0:
        raise ValueError(f"entry_limit_buffer: must be >= 0, got {r.entry_limit_buffer}")
    if r.trail_atr <= 0:
        raise ValueError(f"trail_atr: must be > 0, got {r.trail_atr}")
    if not r.benchmark:
        raise ValueError("benchmark: must be a Yahoo ticker, e.g. ^FTAS")
