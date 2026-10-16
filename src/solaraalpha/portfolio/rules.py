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
    risk_per_trade: float  # fraction of equity lost if the stop is hit
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
    trail_atr: float  # chandelier trailing stop, in ATRs
    event_flag_days: int  # a scheduled event within this many days of entry is flagged
    max_agent_tickers: int  # cap on tickers the unattended agents work per run, by rank
    benchmark: str  # Yahoo ticker the equity curve is compared with

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
    "benchmark": str,
}
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
    if r.entry_limit_buffer < 0:
        raise ValueError(f"entry_limit_buffer: must be >= 0, got {r.entry_limit_buffer}")
    if r.trail_atr <= 0:
        raise ValueError(f"trail_atr: must be > 0, got {r.trail_atr}")
    if not r.benchmark:
        raise ValueError("benchmark: must be a Yahoo ticker, e.g. ^FTAS")
