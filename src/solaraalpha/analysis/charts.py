"""Chart images for the agents: the picture the validator reads next to the numbers.

:func:`weekly_chart` draws the last :data:`WEEKS` weekly candles with the 10 / 40-week MAs,
the levels model's support, resistance, trendlines (projected to the coming week), coil lines
and trigger, and the plan's stop and target, over a volume subplot. Headless (Agg) so it runs
in CI and the unattended jobs. Story map S2.5, issue #126.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from solaraalpha.analysis.technical import FAST_MA, SLOW_MA, sma

WEEKS = 104
UP, DOWN = "#2a9d8f", "#c0392b"


def _num(card: Mapping[str, Any] | None, field: str) -> float | None:
    if card is None:
        return None
    v = card.get(field)
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _line(ax: Any, x: np.ndarray, level: float | None, slope: float | None, **kw: Any) -> None:
    """A trendline whose value at the coming week (x = len + 1) is ``level``, falling back by
    ``slope`` per week; a flat line when the slope is unknown."""
    if level is None:
        return
    s = slope or 0.0
    ahead = x[-1] + 1
    ax.plot(np.append(x, ahead), level - s * (ahead - np.append(x, ahead)), linewidth=1.0, **kw)


def weekly_chart(
    weekly: pd.DataFrame,
    levels_card: Mapping[str, Any] | None,
    technical_card: Mapping[str, Any] | None,
    path: Path,
    title: str = "",
) -> Path:
    """Write a PNG of the last :data:`WEEKS` weekly bars (``open high low close volume``,
    optional ``ma10`` / ``ma40``) with the levels and the plan drawn on; returns ``path``."""
    w = weekly.tail(WEEKS).copy()
    if w.empty:
        raise ValueError("weekly_chart: no weekly bars")
    if "ma10" not in w:
        w["ma10"] = sma(weekly["close"], FAST_MA).reindex(w.index)
    if "ma40" not in w:
        w["ma40"] = sma(weekly["close"], SLOW_MA).reindex(w.index)
    x = np.arange(len(w), dtype="float64")
    fig, (ax, av) = plt.subplots(
        2, 1, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [4, 1]}
    )
    up = (w["close"] >= w["open"]).to_numpy()
    colours = np.where(up, UP, DOWN)
    ax.vlines(x, w["low"], w["high"], color=colours, linewidth=1)
    body = (w["close"] - w["open"]).to_numpy()
    ax.bar(x, np.where(body == 0, 1e-9, body), bottom=w["open"], width=0.6, color=colours)
    ax.plot(x, w["ma10"], color="#1f77b4", linewidth=1.2, label="10w MA")
    ax.plot(x, w["ma40"], color="#ff7f0e", linewidth=1.2, label="40w MA")

    lv = levels_card or {}
    for field, colour, style, label in (
        ("support", "#555555", "-", "support"),
        ("resistance", "#555555", "-", "resistance"),
        ("stop", DOWN, "--", "stop"),
        ("target", UP, "--", "target"),
        ("target_2", UP, ":", "target 2"),
        ("trigger", "#8e44ad", "--", "trigger"),
    ):
        v = _num(lv, field)
        if v is not None:
            ax.axhline(v, color=colour, linestyle=style, linewidth=1.0, label=f"{label} {v:,.1f}")
    _line(
        ax,
        x,
        _num(lv, "lower_trend"),
        _num(lv, "lower_slope"),
        color="#2c3e50",
        label="lower trendline",
    )
    _line(
        ax,
        x,
        _num(lv, "upper_trend"),
        _num(lv, "upper_slope"),
        color="#2c3e50",
        label="upper trendline",
    )
    if lv.get("coiled"):
        _line(
            ax,
            x[-12:],
            _num(lv, "coil_upper"),
            _num(lv, "coil_upper_slope"),
            color="#8e44ad",
            linestyle="-.",
        )
        _line(
            ax,
            x[-12:],
            _num(lv, "coil_lower"),
            _num(lv, "coil_lower_slope"),
            color="#8e44ad",
            linestyle="-.",
        )
    tc = technical_card or {}
    bits = [
        f"{k} {_num(tc, k):+.2f}" for k in ("trend", "momentum", "entry") if _num(tc, k) is not None
    ]
    ax.set_title(" | ".join([t for t in (title, "weekly", *bits) if t]))
    ax.legend(loc="upper left", fontsize=8, ncol=3)
    ax.grid(alpha=0.2)
    av.bar(x, w["volume"], width=0.6, color=colours)
    av.set_ylabel("volume")
    av.grid(alpha=0.2)
    ticks = x[:: max(1, len(x) // 12)]
    av.set_xticks(ticks)
    av.set_xticklabels(
        [pd.Timestamp(d).strftime("%Y-%m-%d") for d in w.index[:: max(1, len(x) // 12)]],
        rotation=45,
        fontsize=8,
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path
