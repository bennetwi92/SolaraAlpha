"""The daily job: prices, triggers and new filings for open positions and the watch list.

Every weekday evening (``daily.yml``): fetch daily prices for the open positions, the latest
scan's WATCH tickers and the tickers with a live entry order waiting for a break; run the
checks in :mod:`solaraalpha.portfolio.triggers`; look for dividends and splits the book has not
recorded, and for new filings (and headlines) since the last daily run; report owner actions and agent reviews. One ticker's failure never stops
the others. With ``save``: one ``daily`` run, a price snapshot per ticker and the triggers in
the ``triggers`` table (the monitor agent reads them). See docs/decisions/0017.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.analysis.events import _short_name
from solaraalpha.data import announcements as ann_mod
from solaraalpha.data import news as news_mod
from solaraalpha.data import prices as prices_mod
from solaraalpha.data.clean import check_prices, clean_prices, trim_to_consistent
from solaraalpha.log import get_logger
from solaraalpha.portfolio import book
from solaraalpha.portfolio.triggers import (
    AGENT_KINDS,
    OWNER_KINDS,
    Trigger,
    action_triggers,
    entry_break,
    filing_triggers,
    news_triggers,
    price_triggers,
    watch_break,
    watch_pullback,
)
from solaraalpha.store import Conn

log = get_logger(__name__)

DEFAULT_LOOKBACK_DAYS = 3  # "since the last daily run" when there is none


@dataclass
class Fetchers:
    """The live sources, swappable in tests."""

    daily: Callable[[str], pd.DataFrame] = prices_mod.fetch_daily
    actions: Callable[[str], pd.DataFrame] = prices_mod.fetch_corporate_actions
    announcements: Callable[..., pd.DataFrame] = ann_mod.fetch_announcements
    issuer: Callable[[str], Any] = ann_mod.resolve_issuer
    news: Callable[..., pd.DataFrame] = news_mod.fetch_news


@dataclass
class DailyResult:
    since: str
    tickers: dict[str, list[str]]  # ticker -> why it was checked (open / watch / pending)
    triggers: list[Trigger] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    run_id: int | None = None

    def of(self, kinds: tuple[str, ...]) -> list[Trigger]:
        return [t for t in self.triggers if t.kind in kinds]


def last_daily_run(conn: Conn) -> str | None:
    row = conn.execute(
        "SELECT started_at FROM runs WHERE script = 'daily' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else None


def latest_watch(conn: Conn) -> dict[str, dict[str, Any]]:
    """WATCH tickers of the latest scan run with a screen label: their ``watch_on`` list (what
    each WATCH toolkit waits for, #125), levels and technical cards."""
    row = conn.execute("SELECT MAX(run_id) FROM labels WHERE stage = 'screen'").fetchone()
    if not row or row[0] is None:
        return {}
    run_id = int(row[0])
    df = store.query(
        "SELECT ticker, watch_on FROM labels WHERE run_id = ? AND stage = 'screen'"
        " AND label = 'WATCH'",
        conn,
        [run_id],
    )
    out = {}
    for t, w in zip(df["ticker"], df["watch_on"], strict=True):
        cards = store.load_cards(run_id, str(t), conn=conn)
        out[str(t)] = {
            "run_id": run_id,
            "levels": cards.get("levels", {}),
            "technical": cards.get("technical", {}),
            "watch_on": json.loads(w) if isinstance(w, str) else [],
        }
    return out


def watch_checks(ticker: str, daily: pd.DataFrame, w: dict[str, Any]) -> list[Trigger]:
    """The watch triggers for one WATCH ticker: a ``break`` above the coil trigger (from
    ``watch_on``, else the levels card's pending coil) and a ``pullback`` to the 10w MA level
    while the close holds above the 40w MA (from the technical card)."""
    items = list(w.get("watch_on") or [])
    lv, tech = w.get("levels") or {}, w.get("technical") or {}
    if not any(i.get("kind") == "break" for i in items) and lv.get("coil_pending"):
        items.append({"kind": "break", "trigger": lv.get("trigger")})
    out: list[Trigger] = []
    for item in items:
        trig: Trigger | None = None
        if item.get("kind") == "break":
            v = item.get("trigger")
            trig = watch_break(ticker, daily, float(v) if v is not None else None)
        elif item.get("kind") == "pullback":
            v = item.get("level")
            ma40 = tech.get("ma40")
            trig = watch_pullback(
                ticker,
                daily,
                float(v) if v is not None else None,
                float(ma40) if ma40 is not None else None,
            )
        if trig:
            out.append(
                Trigger(**{**trig.__dict__, "detail": {**trig.detail, "run_id": w["run_id"]}})
            )
    return out


def pending_breaks(conn: Conn) -> list[dict[str, Any]]:
    """Draft / approved entry orders on positions whose plan waits for a break."""
    pos = book.positions(conn)
    pos = pos[pos["status"].isin(["draft", "approved"])]
    out = []
    for p in pos.to_dict("records"):
        ev = book.position_events(conn, int(p["position_id"]))
        payloads = [x for x in ev.loc[ev["kind"].isin(["drafted", "amended"]), "payload"]]
        plan = payloads[-1] if payloads else {}
        if not plan.get("wait_for_break"):
            continue
        od = book.orders(conn, int(p["position_id"]))
        live = od[(od["purpose"] == "entry") & od["status"].isin(["draft", "approved"])]
        for o in live.to_dict("records"):
            out.append(
                {
                    "ticker": str(p["ticker"]),
                    "position_id": int(p["position_id"]),
                    "order_id": int(o["order_id"]),
                    "trigger": float(o["price"])
                    if o["price"] is not None and not pd.isna(o["price"])
                    else None,
                }
            )
    return out


def _clean(daily: pd.DataFrame) -> pd.DataFrame:
    return trim_to_consistent(clean_prices(daily), check_prices(daily))


def run_daily(
    conn: Conn,
    fetch: Fetchers | None = None,
    save: bool = False,
    args: dict[str, Any] | None = None,
) -> DailyResult:
    fetch = fetch or Fetchers()
    now = datetime.now(UTC)
    since = last_daily_run(conn) or (now - pd.Timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()
    run_id = store.start_run("daily", args or {}, conn=conn) if save else None

    open_pos = book.positions(conn, status="open")
    states = {int(p): book.position_state(conn, int(p)) for p in open_pos["position_id"]}
    targets = {}
    for pid in states:
        ev = book.position_events(conn, pid)
        payloads = [
            x for x in ev.loc[ev["kind"].isin(["drafted", "amended", "revised"]), "payload"]
        ]
        targets[pid] = next(
            (p.get("target") for p in reversed(payloads) if p.get("target") is not None), None
        )
    watch = latest_watch(conn)
    pending = pending_breaks(conn)

    why: dict[str, list[str]] = {}
    for st in states.values():
        why.setdefault(st.ticker, []).append("open")
    for t in watch:
        why.setdefault(t, []).append("watch")
    for pb in pending:
        why.setdefault(pb["ticker"], []).append("pending")
    result = DailyResult(since=since, tickers=why, run_id=run_id)

    for ticker in why:
        try:
            raw = fetch.daily(ticker)
            daily = _clean(raw)
        except Exception as e:
            log.exception("%s: prices failed", ticker)
            result.errors.append((ticker, f"prices: {type(e).__name__}: {e}"))
            continue
        if run_id is not None:
            store.save_ticker(run_id, ticker, daily=raw, conn=conn)
        for pid, st in states.items():
            if st.ticker != ticker:
                continue
            raw_target = targets.get(pid)
            tgt = float(raw_target) if raw_target is not None else None
            result.triggers += price_triggers(ticker, daily, pid, st.stop, tgt, st.side)
        if ticker in watch:
            result.triggers += watch_checks(ticker, daily, watch[ticker])
        for pb in pending:
            if pb["ticker"] == ticker:
                eb = entry_break(ticker, daily, pb["position_id"], pb["order_id"], pb["trigger"])
                if eb:
                    result.triggers.append(eb)

    # dividends and splits on open positions that the book does not have yet
    cash = book.cash(conn)
    for ticker in {st.ticker for st in states.values()}:
        try:
            actions = fetch.actions(ticker)
        except Exception as e:
            log.exception("%s: corporate actions failed", ticker)
            result.errors.append((ticker, f"actions: {type(e).__name__}: {e}"))
            continue
        for pid, st in states.items():
            if st.ticker != ticker:
                continue
            divs = cash[(cash["kind"] == "dividend") & (cash["position_id"] == pid)]
            ev = book.position_events(conn, pid)
            result.triggers += action_triggers(
                ticker,
                actions,
                pid,
                st.shares,
                since,
                st.currency,
                frozenset(str(a)[:10] for a in divs["at"]),
                frozenset(str(a)[:10] for a in ev.loc[ev["kind"] == "split", "at"]),
            )

    # filings and headlines for open positions
    for ticker in {st.ticker for st in states.values()}:
        pids = [pid for pid, st in states.items() if st.ticker == ticker]
        try:
            issuer = fetch.issuer(ticker)
            ann = fetch.announcements(issuer.lei, start=since[:10])
            for pid in pids:
                result.triggers += filing_triggers(ticker, pid, ann, since)
        except Exception as e:
            log.exception("%s: filings failed", ticker)
            result.errors.append((ticker, f"filings: {type(e).__name__}: {e}"))
            issuer = None
        try:
            name = (
                _short_name(issuer.name)
                if issuer is not None and issuer.name
                else ticker.removesuffix(".L")
            )
            days = max(1, math.ceil((now - pd.Timestamp(since)).total_seconds() / 86400))
            result.triggers += news_triggers(ticker, fetch.news(f'"{name}"', days=days), since)
        except Exception as e:
            log.warning("%s: news unavailable (%s)", ticker, e)
            result.errors.append((ticker, f"news: {type(e).__name__}: {e}"))

    if run_id is not None:
        store.save_triggers(run_id, result.triggers, conn=conn)
    return result


def _px(v: Any) -> str:
    return (
        f"{float(v):,.1f}"
        if v is not None and not (isinstance(v, float) and math.isnan(v))
        else "-"
    )


def render_daily(r: DailyResult) -> str:
    lines = [f"## Daily check ({datetime.now(UTC):%Y-%m-%d}) — since {r.since[:16]}", ""]
    checked = ", ".join(f"{t} ({'/'.join(w)})" for t, w in r.tickers.items()) or "none"
    lines += [f"Checked: {checked}", ""]
    if r.run_id is not None:
        lines += [f"saved run {r.run_id}", ""]

    lines += ["### Owner actions", ""]
    owner = r.of(OWNER_KINDS)
    for t in owner:
        d = t.detail
        if t.kind == "stop_close":
            lines.append(
                f"- **{t.ticker}** closed {_px(d['close'])} below the stop {_px(d['stop'])} (position {t.position_id}): exit — `book.py exit {t.position_id} --reason stop`, then `fill` the stop order if the broker filled it"
            )
        elif t.kind == "stop_touched":
            lines.append(
                f"- **{t.ticker}** traded through the stop {_px(d['stop'])} (low {_px(d['low'])}) but closed {_px(d['close'])} (position {t.position_id}): check whether the broker stop filled"
            )
        elif t.kind == "target_hit":
            lines.append(
                f"- **{t.ticker}** reached the target {_px(d['target'])} (high {_px(d['high'])}) (position {t.position_id}): take profit or trail — `book.py exit {t.position_id} --reason target`"
            )
        elif t.kind == "entry_break":
            lines.append(
                f"- **{t.ticker}** closed {_px(d['close'])} above the trigger {_px(d['trigger'])}: place entry order {t.order_id} (position {t.position_id}) now — `book.py placed {t.order_id} --ref ...`"
            )
        elif t.kind == "dividend":
            lines.append(
                f"- **{t.ticker}** ex-dividend {t.at}: {book.fmt_price(d['per_share'], d['currency'])} x {d['shares']} shares = £{d['amount_gbp']:,.2f} (position {t.position_id}): confirm against the broker and record — `book.py cash dividend {t.ticker} {d['amount_gbp']:.2f} --at {t.at}`"
            )
        elif t.kind == "split":
            lines.append(
                f"- **{t.ticker}** split {d['ratio']} on {t.at} (position {t.position_id}): record it — `book.py split {t.position_id} --ratio {d['ratio']} --at {t.at}`"
            )
    lines += ([] if owner else ["none"]) + [""]

    lines += ["### Agent review needed", ""]
    agent = r.of(AGENT_KINDS)
    for t in agent:
        d = t.detail
        if t.kind == "gap":
            lines.append(
                f"- **{t.ticker}** gapped {d['gap_atr']:+.1f} ATR at the open ({_px(d['prev_close'])} → {_px(d['open'])}), position {t.position_id}"
            )
        else:
            lines.append(
                f"- **{t.ticker}** new filing {t.at[:16]} `{d['type_code']}` {d['headline']} (position {t.position_id}) {d['url']}"  # noqa: PD008
            )
    if agent and r.run_id is not None:
        lines.append(
            f"\nRun `/monitor --run {r.run_id}` (or `uv run python scripts/monitor_pack.py --run {r.run_id}`)."
        )
    lines += ([] if agent else ["none"]) + [""]

    lines += ["### Watch breaks and pullbacks", ""]
    wb = r.of(("watch_break", "watch_pullback"))
    lines += [
        (
            f"- **{t.ticker}** closed {_px(t.detail['close'])} above its coil trigger {_px(t.detail['trigger'])}: promote candidate — `uv run python scripts/scan.py {t.ticker} --save`"
            if t.kind == "watch_break"
            else f"- **{t.ticker}** closed {_px(t.detail['close'])}, {t.detail['distance_atr']:+.1f} ATR from its 10w MA {_px(t.detail['level'])} with the trend intact: pullback entry candidate — `uv run python scripts/scan.py {t.ticker} --save`"
        )
        for t in wb
    ] or ["none"]
    lines.append("")

    lines += ["### Headlines", ""]
    news = r.of(("news",))
    lines += [
        f"- {t.at[:10]} {t.ticker}: {t.detail['headline']} ({t.detail['source']})"  # noqa: PD008
        for t in news
    ] or ["none"]
    lines.append("")

    lines += ["### Errors", ""]
    lines += [f"- {t}: {e}" for t, e in r.errors] or ["none"]
    lines.append("")
    return "\n".join(lines)


def triggers_json(triggers: list[Trigger]) -> str:
    return json.dumps([t.__dict__ for t in triggers], default=str)
