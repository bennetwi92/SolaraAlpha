"""Each agent's own track record from the results DB, in plain words (ADR 0035, step 1).

One section per stage an agent owns: what it decided, what happened next, where it was overruled,
how reliable and costly it was. Built only from what is already recorded (labels, cards, the book,
executions, agent runs); nothing new is stored. Forward returns (``fwd``, from
:func:`solaraalpha.analysis.scorecard.forward_returns`) are hindsight by construction: they say
what the stock did after the decision, which the agent could not know. Groups smaller than
``MIN_GROUP`` are reported but flagged "too few to conclude".
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from solaraalpha import agents, store
from solaraalpha.analysis.scorecard import (
    MIN_GROUP,
    agent_value,
    calibration,
    toolkit_scorecard,
)
from solaraalpha.data.clean import clean_prices
from solaraalpha.data.prices import fetch_daily
from solaraalpha.log import get_logger
from solaraalpha.portfolio import book
from solaraalpha.portfolio.journal import closed_trades
from solaraalpha.store import Conn, query
from solaraalpha.validation import disagreement_stats, disagreements

log = get_logger(__name__)

HORIZONS = (4, 8)  # inside the 1-10 week holding window (CLAUDE.md strategy)
BURST = 0.15  # the strategy's minimum burst

# desk area (.claude/agents) -> the stages it reviews; CLAUDE.md "Desk ownership"
AREAS: dict[str, tuple[str, ...]] = {
    "senior-quant": ("screen",),
    "fundamental-strategist": ("research", "macro"),
    "portfolio-manager": ("pm", "validate", "monitor", "postmortem"),
    "execution-trader": ("execute",),
}


@dataclass(frozen=True)
class Inputs:
    """What the sections share: labels with their card week, their forward returns, the window."""

    labels: pd.DataFrame  # run_id, ticker, stage, label, confidence, date
    fwd: pd.DataFrame  # forward_returns(labels, ...)
    since: str | None = None


def labels_with_dates(conn: Conn, since: str | None) -> pd.DataFrame:
    """Labels joined with the screen card's week (``date``) for the run and ticker."""
    sql = (
        "SELECT l.run_id, l.ticker, l.stage, l.label, l.confidence, (SELECT MIN(x.date) FROM results x"
        " WHERE x.run_id = l.run_id AND x.ticker = l.ticker"
        " AND x.model IN ('technical', 'levels', 'relative_strength', 'fundamentals')) AS date"
        " FROM labels l ORDER BY l.run_id, l.rowid"
    )
    df = query(sql, conn)
    df = df.dropna(subset=["date"])
    if since:
        df = df[df["date"] >= since]
    return df.reset_index(drop=True)


def prices_for(conn: Conn, tickers: list[str], fetch: object = None) -> dict[str, pd.DataFrame]:
    """Daily prices per ticker from the saved snapshots; a ticker with none is fetched."""
    fetch = fetch or fetch_daily
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        px = store.load_prices(t, conn=conn)
        if px.empty:
            try:
                px = clean_prices(fetch(t))  # type: ignore[operator]
            except Exception as e:
                log.warning("%s: no saved history and the fetch failed (%s)", t, e)
                continue
        out[t] = px
    return out


def stages_for(name: str) -> tuple[str, ...]:
    """A desk area's stages, a single stage, or every stage for ``all``."""
    if name == "all":
        return tuple(s for area in AREAS.values() for s in area)
    if name in AREAS:
        return AREAS[name]
    if name in SECTIONS:
        return (name,)
    raise ValueError(f"unknown agent {name!r}: one of {', '.join([*AREAS, *SECTIONS, 'all'])}")


def report(conn: Conn, name: str, inputs: Inputs) -> str:
    """The markdown track record for a desk area, a stage, or ``all``."""
    conn.executescript(book.BOOK_SCHEMA)
    title = f"# Track record: {name}" + (f" (since {inputs.since})" if inputs.since else "")
    lines = [
        title,
        "",
        "What each decision was, what happened next (hindsight: the agent could not know it),"
        f" and where it was overruled. Groups under {MIN_GROUP} are too few to conclude anything.",
    ]
    for stage in stages_for(name):
        lines += ["", f"## {stage}", ""]
        lines += SECTIONS[stage](conn, inputs) or ["- Nothing recorded yet."]
        lines += _reliability(conn, stage, inputs.since)
    return "\n".join(lines) + "\n"


# --- formatting --------------------------------------------------------------------------------


def _pct(v: Any) -> str:
    return "-" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.1%}"


def _few(n: int) -> str:
    return " (too few to conclude)" if n < MIN_GROUP else ""


def _returns_line(what: str, r: pd.Series) -> str:
    """``what``: n, mean, hit rate and share ending ≥ BURST, over the scored rows of ``r``."""
    r = r.dropna()
    if r.empty:
        return f"- {what}: none scored yet."
    burst = float((r >= BURST).mean())
    return (
        f"- {what}: n={len(r)}, average {_pct(float(r.mean()))}, up in {float((r > 0).mean()):.0%},"
        f" ended {BURST:.0%}+ up in {burst:.0%}{_few(len(r))}"
    )


# --- shared readers ----------------------------------------------------------------------------


def _cards(conn: Conn, stage: str, since: str | None) -> pd.DataFrame:
    """One row per (run, ticker) card of ``stage``, its fields as columns (text wins over value)."""
    sql = (
        "SELECT x.run_id, x.ticker, x.field, x.value, x.text FROM results x JOIN runs r USING"
        " (run_id) WHERE x.model = ?" + (" AND r.started_at >= ?" if since else "")
    )
    df = query(sql + " ORDER BY x.run_id, x.rowid", conn, [stage, since] if since else [stage])
    if df.empty:
        return pd.DataFrame(columns=["run_id", "ticker"])
    df["v"] = df["text"].where(df["text"].notna(), df["value"])
    out = df.pivot_table(index=["run_id", "ticker"], columns="field", values="v", aggfunc="last")
    return out.reset_index()


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def _avg(df: pd.DataFrame, col: str) -> str:
    v = [x for x in (_num(x) for x in df.get(col, [])) if not math.isnan(x)]
    return f"{np.mean(v):+.2f}" if v else "-"


def _reliability(conn: Conn, stage: str, since: str | None) -> list[str]:
    cards = _cards(conn, stage, since) if stage != "execute" else pd.DataFrame()
    errors = int((cards.get("label", pd.Series(dtype=str)) == "ERROR").sum())
    runs = store.load_agent_runs(since, conn=conn)
    runs = runs[runs["stage"] == stage] if not runs.empty else runs
    if runs.empty and not errors:
        return []
    out = ["", "Reliability and cost:"]
    if not runs.empty:
        failed = int((runs["status"] != "success").sum())
        cost = pd.to_numeric(runs["cost_usd"], errors="coerce").sum()
        minutes = pd.to_numeric(runs["seconds"], errors="coerce").mean() / 60
        out.append(
            f"- {len(runs)} invocations, {failed} failed, {minutes:.1f} min each on average"
            + (f", ${cost:.2f} in all" if cost else "")
        )
    out.append(f"- {errors} ERROR verdicts recorded")
    return out


def _agent_value_lines(inputs: Inputs, stage: str) -> list[str]:
    """Kept vs downgraded, then calibration, for an agent that relabels the screen's ACTs."""
    if inputs.labels.empty:
        return []
    av = agent_value(inputs.labels, inputs.fwd)
    av = av[(av["stage"] == stage) & av["horizon"].isin(HORIZONS)] if not av.empty else av
    out = []
    for r in av.to_dict("records"):
        n = min(int(r["n_kept"]), int(r["n_downgraded"]))
        out.append(
            f"- {int(r['horizon'])} weeks on: kept (n={r['n_kept']}) {_pct(r['kept_mean'])},"
            f" downgraded (n={r['n_downgraded']}) {_pct(r['downgraded_mean'])}; difference"
            f" {_pct(r['diff_mean'])} (90%: {_pct(r['diff_lo90'])} to {_pct(r['diff_hi90'])}){_few(n)}"
        )
    if out:
        out.insert(0, "Did its downgrades avoid the losers? (screen ACTs it kept vs cut)")
    cal = calibration(inputs.labels, inputs.fwd)
    cal = cal[(cal["stage"] == stage) & (cal["n"] > 0)] if not cal.empty else cal
    if not cal.empty:
        out += [
            *([""] if out else []),
            "Is its confidence honest? (its ACTs by confidence, 8 weeks on)",
        ]
        out += [
            f"- confidence {r['bucket']}: n={r['n']}, up in {r['hit_rate']:.0%},"
            f" average {_pct(r['mean_return'])}{_few(int(r['n']))}"
            for r in cal.to_dict("records")
        ]
    return out


def _trade_lines(trades: pd.DataFrame, what: str) -> list[str]:
    if trades.empty:
        return [f"- {what}: no closed trades yet."]
    r = trades["r_multiple"].dropna()
    win = float((r > 0).mean()) if len(r) else math.nan
    out = [
        f"- {what}: {len(trades)} closed, won {win:.0%}, average {r.mean():+.2f}R,"
        f" £{trades['net_pnl'].sum():+,.0f} net{_few(len(trades))}"
    ]
    for reason, g in trades.groupby("exit_reason"):
        out.append(f"  - exit by {reason}: {len(g)}, average {g['r_multiple'].mean():+.2f}R")
    return out


# --- sections ----------------------------------------------------------------------------------


def _screen(conn: Conn, inputs: Inputs) -> list[str]:
    fwd = inputs.fwd[inputs.fwd["stage"] == "screen"] if not inputs.fwd.empty else inputs.fwd
    if fwd.empty:
        return []
    out = ["What the screen's labels did next:"]
    for n in HORIZONS:
        for label in ("ACT", "WATCH", "PASS"):
            if not (fwd["label"] == label).any():
                continue
            out.append(
                _returns_line(f"{label}, {n} weeks on", fwd.loc[fwd["label"] == label, f"ret_{n}w"])
            )
    steps = query(
        "SELECT run_id, ticker, toolkit, status FROM steps WHERE stage IN ('screen', 'fundamentals')",
        conn,
    )
    tk = toolkit_scorecard(steps, inputs.fwd, horizons=(8,))
    if not tk.empty:
        out += ["", "By toolkit band, 8 weeks on:"]
        out += [
            f"- {r['toolkit']} {r['label']}: n={int(r['n_8w'])}, average {_pct(r['mean_8w'])},"
            f" up in {_pct(r['hit_8w'])}{_few(int(r['n_8w']))}"
            for r in tk.to_dict("records")
        ]
    return out


def _validate(conn: Conn, inputs: Inputs) -> list[str]:
    out = _agent_value_lines(inputs, "validate")
    cards = _cards(conn, "validate", inputs.since)
    by_key: dict[tuple[int, str], dict[str, Any]] = {
        (int(r["run_id"]), str(r["ticker"])): {str(k): v for k, v in r.items()}
        for r in cards.to_dict("records")
    }
    lab = inputs.labels[inputs.labels["stage"].isin(["screen", "validate"])]
    stats = disagreement_stats(disagreements(lab, by_key), lab)
    n_val = int((lab["stage"] == "validate").sum())
    if n_val:
        out += ["", f"It disagreed with the screen {stats['total']} times in {n_val} checks."]
        blamed = sorted(((v, k) for k, v in stats["by_toolkit"].items() if v), reverse=True)
        out += [f"- toolkit not warranted: {k} ({v})" for v, k in blamed]
    return out


def _research(conn: Conn, inputs: Inputs) -> list[str]:
    out = _agent_value_lines(inputs, "research")
    trades = closed_trades(conn)
    pos = book.positions(conn)
    from_runs = set(pos.loc[pos["opened_from_run_id"].notna(), "position_id"].astype(int))
    trades = trades[trades["position_id"].isin(from_runs)]
    if inputs.since and not trades.empty:
        trades = trades[trades["closed_at"] >= inputs.since]
    out += ["", "Trades built on its entry and stop:"]
    out += _trade_lines(trades, "research trades")
    stopped = trades[trades["exit_reason"] == "stop"].dropna(subset=["mfe_r"])
    if not stopped.empty:
        out.append(
            f"- stopped out: {len(stopped)}; best point before the stop averaged"
            f" {stopped['mfe_r'].mean():+.2f}R"
        )
    return out


def _macro(conn: Conn, inputs: Inputs) -> list[str]:
    cards = _cards(conn, "macro", inputs.since)
    if cards.empty:
        return []
    return [f"- {len(cards)} sector notes written over {cards['run_id'].nunique()} runs."]


def _pm(conn: Conn, inputs: Inputs) -> list[str]:
    out: list[str] = []
    fwd = inputs.fwd
    acts = fwd[(fwd["stage"] == "research") & (fwd["label"] == "ACT")] if not fwd.empty else fwd
    funded_rows = []
    for run_id in sorted(set(acts["run_id"])) if not acts.empty else []:
        d = agents.pm_decision(conn, int(run_id))
        if d is None:
            continue
        chosen = {a["ticker"] for a in d["allocation"]}
        g = acts[acts["run_id"] == run_id]
        funded_rows.append(g.assign(funded=g["ticker"].isin(chosen)))
    if funded_rows:
        f = pd.concat(funded_rows)
        out.append("Did it fund the better research ideas? (research ACTs, funded vs not)")
        for n in HORIZONS:
            out.append(_returns_line(f"funded, {n} weeks on", f.loc[f["funded"], f"ret_{n}w"]))
            out.append(_returns_line(f"not funded, {n} weeks on", f.loc[~f["funded"], f"ret_{n}w"]))
    cards = _cards(conn, "pm", inputs.since)
    rotations = sum(len(json.loads(str(x))) for x in cards.get("rotations", pd.Series()).dropna())
    if not cards.empty:
        out += ["", f"- {len(cards)} weekly allocations, {rotations} rotations."]
    trades = closed_trades(conn)
    if inputs.since and not trades.empty:
        trades = trades[trades["closed_at"] >= inputs.since]
    out += ["", "The book as a whole:", *_trade_lines(trades, "all trades")]
    rejected = query(
        "SELECT COUNT(*) AS n FROM position_events WHERE kind = 'rejected' AND actor = ?",
        conn,
        [book.OWNER],
    )
    out.append(f"- plans the owner rejected: {int(rejected['n'].iloc[0])}")
    return out


def _monitor(conn: Conn, inputs: Inputs) -> list[str]:
    rev = book.revisions(conn)
    if not rev.empty:
        rev = rev[rev["actor"].astype(str).str.startswith("agent:")]
        if inputs.since:
            rev = rev[rev["at"] >= inputs.since]
    out: list[str] = []
    if not rev.empty:
        out.append("What it changed:")
        for (action, status), g in rev.groupby(["action", "status"]):
            out.append(f"- {action}: {len(g)} {status}")
        undone = rev[rev["status"] == "undone"]
        if not undone.empty:
            out += ["", "Undone by the owner:"]
            out += [
                f"- #{r['revision_id']} {r['action']} (position {r['position_id']}): {r['decision_reason']}"
                for r in undone.to_dict("records")
            ]
    trades = closed_trades(conn)
    if inputs.since and not trades.empty:
        trades = trades[trades["closed_at"] >= inputs.since]
    if not trades.empty:
        give = (trades["mfe_r"] - trades["r_multiple"]).dropna()
        if len(give):
            out += [
                "",
                f"- closed trades gave back {give.mean():.2f}R on average from their best point"
                f" (n={len(give)}){_few(len(give))}",
            ]
    return out


def _execute(conn: Conn, inputs: Inputs) -> list[str]:
    sql = "SELECT * FROM executions" + (" WHERE at >= ?" if inputs.since else "")
    ex = query(sql, conn, [inputs.since] if inputs.since else [])
    out: list[str] = []
    if not ex.empty:
        out.append(
            "What it did: " + ", ".join(f"{a} {n}" for a, n in ex["action"].value_counts().items())
        )
        bad = ex[ex["action"].isin(["refused", "rejected", "error", "unknown"])]
        for (action, reason), g in bad.groupby(["action", "reason"]):
            out.append(f"- {action} x{len(g)}: {reason}")
    fills = query(
        "SELECT f.at, f.price AS fill, o.price AS planned, o.side, o.purpose, o.type FROM fills f"
        " JOIN orders o USING (order_id) WHERE o.price IS NOT NULL"
        + (" AND f.at >= ?" if inputs.since else ""),
        conn,
        [inputs.since] if inputs.since else [],
    )
    if not fills.empty:
        # positive = worse than planned: paid more on a buy, got less on a sell
        fills["slip"] = (fills["fill"] - fills["planned"]) / fills["planned"] * fills["side"]
        out += ["", "Fill price against the order price (positive = worse than planned):"]
        for (purpose, kind), g in fills.groupby(["purpose", "type"]):
            out.append(
                f"- {purpose} {kind}: n={len(g)}, average {g['slip'].mean() * 1e4 + 0.0:+.0f} bp,"
                f" worst {g['slip'].max() * 1e4 + 0.0:+.0f} bp"
            )
    return out


def _postmortem(conn: Conn, inputs: Inputs) -> list[str]:
    cards = _cards(conn, "postmortem", inputs.since)
    if cards.empty:
        return []
    cards = cards.loc[cards["label"] != "ERROR"]
    word = {"ACT": "repeat", "WATCH": "mixed", "PASS": "avoid"}
    counts = cards["label"].map(word).value_counts()
    out = [
        f"- {len(cards)} post-mortems: " + ", ".join(f"{k} {v}" for k, v in counts.items()),
        f"- average set-up quality {_avg(cards, 'setup_quality')}, execution quality"
        f" {_avg(cards, 'execution_quality')} (-1 to +1)",
        "",
        "Lessons:",
    ]
    out += [
        f"- {r['ticker']} ({word.get(str(r['label']), r['label'])}): {r.get('reasons', '')}"
        for r in cards.to_dict("records")
    ]
    return out


SECTIONS: dict[str, Callable[[Conn, Inputs], list[str]]] = {
    "screen": _screen,
    "validate": _validate,
    "research": _research,
    "macro": _macro,
    "pm": _pm,
    "monitor": _monitor,
    "execute": _execute,
    "postmortem": _postmortem,
}
