"""The daily job: prices, triggers and new filings for open positions and the watch list.

Every weekday evening (``daily.yml``): fetch daily prices for the open positions, the latest
scan's WATCH tickers and the pending entries (draft / approved positions with an unfilled entry
order); run the checks in :mod:`solaraalpha.portfolio.triggers`; evaluate the machine thesis
conditions of open positions and pending entries on fresh cards; look for dividends and splits
the book has not recorded, and for new filings (and headlines) since the last daily run; first,
book what Trading212 shows (fills, orders, dividends) when its keys are set (ADR 0030); report
owner actions and agent reviews. Every open position and pending entry gets a ``daily_review``
trigger, so the monitor agent re-evaluates each one every weekday (ADR 0024). One ticker's
failure never stops the others. With ``save``: one ``daily`` run, a price snapshot per ticker
and the triggers in the ``triggers`` table (the monitor agent reads them). See
docs/decisions/0017.
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
from solaraalpha.analysis.regime import market_regime
from solaraalpha.data import announcements as ann_mod
from solaraalpha.data import news as news_mod
from solaraalpha.data import prices as prices_mod
from solaraalpha.data.clean import check_prices, clean_prices, trim_to_consistent
from solaraalpha.log import get_logger
from solaraalpha.pipeline import Condition, run_toolkit
from solaraalpha.portfolio import book, risk
from solaraalpha.portfolio.rules import load_rules
from solaraalpha.portfolio.triggers import (
    AGENT_KINDS,
    OWNER_KINDS,
    Trigger,
    _session,
    action_triggers,
    daily_review,
    entry_break,
    event_due,
    filing_triggers,
    market_trigger,
    news_triggers,
    price_triggers,
    stale_order_triggers,
    thesis_hits,
    watch_break,
    watch_pullback,
)
from solaraalpha.store import Conn
from solaraalpha.toolkits import Inputs, owner_of

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
    broker: Callable[[], Any] | None = None  # Trading212 state (ADR 0030); None: when keys set


@dataclass
class DailyResult:
    since: str
    tickers: dict[str, list[str]]  # ticker -> why it was checked (open / watch / pending)
    triggers: list[Trigger] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    run_id: int | None = None
    regime: Any = None  # the market RegimeCard (#142), None when the index could not be read
    drawdown: float | None = None  # the book's drawdown from stored snapshots (#143)
    breaker_limit: float = 0.0
    breaker_on: bool = False
    booked: list[str] = field(default_factory=list)  # taken in from Trading212 (ADR 0030)
    resized: list[str] = field(default_factory=list)  # rotation buys sized to the cash freed

    def of(self, kinds: tuple[str, ...]) -> list[Trigger]:
        return [t for t in self.triggers if t.kind in kinds]


def last_daily_run(conn: Conn) -> str | None:
    """When the last daily run that finished started: one that saved its triggers (a run that
    crashed first saved none, and its window is looked at again rather than lost)."""
    row = conn.execute(
        "SELECT started_at FROM runs WHERE script = 'daily' AND run_id IN"
        " (SELECT run_id FROM triggers) ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else None


def latest_watch(conn: Conn) -> dict[str, dict[str, Any]]:
    """WATCH tickers of the latest full weekly scan with a screen label (a one-stock scan is not
    the week's watch list): their ``watch_on`` list (what each WATCH toolkit waits for, #125),
    levels and technical cards."""
    labelled = {
        int(r[0]) for r in conn.execute("SELECT DISTINCT run_id FROM labels WHERE stage = 'screen'")
    }
    runs = [r for r in store.weekly_scans(conn) if r in labelled]
    if not runs:
        return {}
    run_id = runs[-1]
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


def pending_entries(conn: Conn) -> list[dict[str, Any]]:
    """Pending entries: draft / approved positions with an unfilled entry order (draft,
    approved, placed or part filled), with ``wait_for_break`` from the plan (``trigger`` = the
    order price; the ``entry_break`` check applies to those only)."""
    pos = book.positions(conn)
    pos = pos[pos["status"].isin(["draft", "approved"])]
    out = []
    for p in pos.to_dict("records"):
        pid = int(p["position_id"])
        ev = book.position_events(conn, pid)
        drafted = [x for x in ev.loc[ev["kind"] == "drafted", "payload"]]
        wait = bool(drafted and drafted[-1].get("wait_for_break"))
        pe = book.pending_entry(conn, pid)
        if pe is None:
            continue
        out.append(
            {
                "ticker": str(p["ticker"]),
                "position_id": pid,
                "order_id": pe["order_id"],
                "order_status": pe["status"],
                "trigger": pe["price"],
                "wait_for_break": wait,
            }
        )
    return out


def pending_breaks(conn: Conn) -> list[dict[str, Any]]:
    """Pending entries whose plan waits for a break (not yet at the broker: draft / approved)."""
    return [
        p
        for p in pending_entries(conn)
        if p["wait_for_break"] and p["order_status"] in ("draft", "approved")
    ]


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

    # the book takes in what Trading212 shows (fills, placed / ended orders, dividends) before
    # anything else reads it, so a stop that sold overnight is closed before the checks; the
    # rest of the differences are owner triggers (ADR 0030 amendment). Skipped without keys.
    from solaraalpha.data import trading212
    from solaraalpha.portfolio import reconcile

    get_broker = fetch.broker or (reconcile.fetch_broker if trading212.configured() else None)
    broker_diffs: list[Any] = []
    booked: list[str] = []
    broker_errors: list[tuple[str, str]] = []
    if get_broker is not None:
        try:
            done, broker_diffs, _ = reconcile.absorb(conn, get_broker())
            booked = [f"{d.ticker}: {reconcile.booked_text(d)}" for d in done]
            for line in booked:
                log.info("booked from Trading212: %s", line)
        except Exception as e:
            log.exception("Trading212 reconcile failed")
            broker_errors.append(("Trading212", f"reconcile: {type(e).__name__}: {e}"))

    resized = release_rotations(conn, load_rules())

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
    pending = pending_entries(conn)
    pending_states = {
        pe["position_id"]: book.position_state(conn, pe["position_id"]) for pe in pending
    }

    why: dict[str, list[str]] = {}
    for st in states.values():
        why.setdefault(st.ticker, []).append("open")
    for t in watch:
        why.setdefault(t, []).append("watch")
    for pb in pending:
        why.setdefault(pb["ticker"], []).append("pending")
    result = DailyResult(since=since, tickers=why, run_id=run_id, booked=booked, resized=resized)
    result.triggers += [broker_trigger(d, now.strftime("%Y-%m-%d")) for d in broker_diffs]
    result.errors += broker_errors

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
            hits, issues = check_thesis(conn, pid, st, raw, daily)
            result.triggers += [Trigger(**{**h.__dict__, "at": _session(daily)}) for h in hits]
            result.errors += [(ticker, f"thesis: {i}") for i in issues]
        if ticker in watch:
            result.triggers += watch_checks(ticker, daily, watch[ticker])
        for pb in pending:
            if pb["ticker"] != ticker:
                continue
            pid = pb["position_id"]
            if pb["wait_for_break"] and pb["order_status"] in ("draft", "approved"):
                eb = entry_break(ticker, daily, pid, pb["order_id"], pb["trigger"])
                if eb:
                    result.triggers.append(eb)
            result.triggers += price_triggers(ticker, daily, pid, None)  # a gap, for the agent
            hits, issues = check_thesis(conn, pid, pending_states[pid], raw, daily)
            result.triggers += [Trigger(**{**h.__dict__, "at": _session(daily)}) for h in hits]
            result.errors += [(ticker, f"thesis: {i}") for i in issues]

    # the market regime from the benchmark index (#142): advisory, at the top of the report
    rules = load_rules()
    try:
        idx_raw = fetch.daily(rules.benchmark)
        result.regime = market_regime(clean_prices(idx_raw), rules.benchmark)
        if run_id is not None:
            store.save_ticker(
                run_id, rules.benchmark, {"regime": result.regime}, daily=idx_raw, conn=conn
            )
        mt = market_trigger(result.regime)
        if mt:
            result.triggers.append(mt)
    except Exception as e:
        log.exception("%s: market regime failed", rules.benchmark)
        result.errors.append((rules.benchmark, f"regime: {type(e).__name__}: {e}"))

    # the book's drawdown vs the circuit breaker (#143): shown, never acted on here
    from solaraalpha.portfolio.weekly_pack import drawdown_from_store

    try:
        result.drawdown = drawdown_from_store(conn, now.isoformat(), rules)
    except Exception as e:
        log.warning("drawdown unavailable (%s)", e)
    result.breaker_limit = rules.max_drawdown_new_entries
    result.breaker_on = rules.circuit_breaker

    # orders past their validity with no fill entered (#130)
    result.triggers += stale_order_triggers(
        book.stale_orders(conn, now.isoformat()), pd.Timestamp(now)
    )

    # scheduled events two sessions away on open positions (policy decided in advance, #129)
    for ticker, nxt in risk.next_events(conn).items():
        for pid, st in states.items():
            if st.ticker == ticker:
                trig = event_due(ticker, pid, nxt, pd.Timestamp(now), rules.event_policy)
                if trig:
                    result.triggers.append(trig)

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
            found = action_triggers(
                ticker,
                actions,
                pid,
                st.shares,
                since,
                st.currency,
                frozenset(str(a)[:10] for a in divs["at"]),
                frozenset(str(a)[:10] for a in ev.loc[ev["kind"] == "split", "at"]),
            )
            if get_broker is not None:  # Trading212 books the dividend when it is paid
                found = [t for t in found if t.kind != "dividend"]
            result.triggers += found

    # filings and headlines for open positions and pending entries
    reviewed = {**states, **pending_states}
    for ticker in sorted({st.ticker for st in reviewed.values()}):
        pids = [pid for pid, st in reviewed.items() if st.ticker == ticker]
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

    # the weekday agent review of every open position and pending entry (ADR 0024)
    today = now.strftime("%Y-%m-%d")
    order_of = {pe["position_id"]: pe["order_id"] for pe in pending}
    for pid, st in reviewed.items():
        fired = [
            t
            for t in result.triggers
            if t.position_id == pid or (t.kind == "news" and t.ticker == st.ticker)
        ]
        status = "pending" if pid in pending_states else "open"
        result.triggers.append(
            daily_review(st.ticker, pid, status, fired, today, order_of.get(pid))
        )

    if run_id is not None:
        # the errors too (kind ``error``), so the desk errors report them without the job failing
        errs = [Trigger(t, "error", None, None, {"reason": e}, today) for t, e in result.errors]
        store.save_triggers(run_id, result.triggers + errs, conn=conn)
    return result


def free_cash(conn: Conn, rules: Any, but: int | None = None) -> float:
    """Cash (GBP) a new buy can use now: the book's cash, less what the other live buy orders
    will take (not the ones still waiting for a sale) and the cash buffer (on equity at cost)."""
    states = [book.position_state(conn, int(p)) for p in book.positions(conn)["position_id"]]
    e = risk.equity(book.cash(conn), states, pd.DataFrame())
    ccy = book.positions(conn).set_index("position_id")["currency"]
    od = book.orders(conn)
    live = od[
        od["purpose"].isin(list(book.BUY_PURPOSES))
        & od["status"].isin(list(book.LIVE_ORDER))
        & od["price"].notna()
        & (od["position_id"] != but)
    ]
    committed = sum(
        book.to_account(
            (int(r["qty"]) - int(r["filled"])) * float(r["price"]), ccy[r["position_id"]]
        )
        for r in live.to_dict("records")
        if not book.waiting_on(conn, int(r["position_id"]))
    )
    return e.cash - committed - rules.cash_buffer * (e.cash + e.open_cost)


def release_rotations(conn: Conn, rules: Any) -> list[str]:
    """A buy the PM paid for with a sale (``waits_for``, ADR 0027) waits until that sale has
    filled; then its quantity is cut to the cash actually there (the sale may fill below the
    close it was sized on), or the entry is cancelled when nothing is left. The owner places it
    from the inbox as any other buy. Returns one line per change."""
    out = []
    pos = book.positions(conn, status="approved")
    for pid, ticker, ccy in zip(pos["position_id"], pos["ticker"], pos["currency"], strict=True):
        pid = int(pid)
        if not book._drafted_plan(conn, pid).get("waits_for") or book.waiting_on(conn, pid):
            continue
        pe = book.pending_entry(conn, pid)
        if pe is None or pe["status"] != "approved" or pe["price"] is None:
            continue
        cash = free_cash(conn, rules, but=pid)
        qty = min(pe["qty"], math.floor(cash / book.to_account(pe["price"], str(ccy))))
        if qty == pe["qty"]:
            continue
        why = f"the sale paying for it left £{max(cash, 0):,.2f} to spend"
        if qty <= 0:
            book.end_order(conn, pe["order_id"], "cancelled", why, actor=book.PIPELINE)
            out.append(f"{ticker}: entry cancelled ({why})")
        else:
            book.amend_position(conn, pid, qty=qty, reason=why, actor=book.PIPELINE)
            out.append(f"{ticker}: buy cut from {pe['qty']:,} to {qty:,} shares ({why})")
    for line in out:
        log.info("rotation: %s", line)
    return out


def broker_trigger(d: Any, day: str) -> Trigger:
    """A :class:`solaraalpha.portfolio.reconcile.Difference` as an owner trigger."""
    detail = {"what": d.kind, "text": d.text, "command": d.command}
    return Trigger(d.ticker, "broker", d.position_id, d.order_id, detail, day)


def check_thesis(
    conn: Conn,
    position_id: int,
    state: book.PositionState,
    raw: pd.DataFrame,
    daily: pd.DataFrame,
) -> tuple[list[Trigger], list[str]]:
    """Evaluate the position's ``machine`` thesis conditions on fresh cards (#131): the toolkit
    that owns each field (:func:`solaraalpha.toolkits.owner_of`) runs once on the same prices;
    ``close`` is the latest daily close. A condition already acted on (a revision accepted or
    declined after its first hit) is not reported again."""
    conds = book.thesis_conditions(conn, position_id)
    machine = conds[conds["kind"] == "machine"] if not conds.empty else conds
    if machine.empty:
        return [], []
    needed: set[str] = set()
    issues: list[str] = []
    for r in machine.to_dict("records"):
        try:
            field = Condition.parse(str(r["condition"])).field
        except ValueError:
            continue  # reported by thesis_hits
        if field == "close":
            continue
        owner = owner_of(field)
        if owner is not None:
            needed.add(owner)
    cards: dict[str, Any] = {}
    x = Inputs(state.ticker, daily=raw)
    entry = pd.Timestamp(state.opened_at).strftime("%Y-%m-%d") if state.opened_at else None
    for tk in sorted(needed):
        params = (
            {"entry_date": entry, "entry_price": state.entry_avg}
            if tk == "technical" and entry
            else {}
        )
        step = run_toolkit(tk, x, params, stage="daily")
        if step.status == "error":
            issues.append(f"{tk} failed: {step.reason}")
            continue
        cards.update(step.cards)
    close = float(daily["close"].iloc[-1]) if len(daily) else None
    hits, more = thesis_hits(
        state.ticker, position_id, machine, cards, close, suppressed_conditions(conn, position_id)
    )
    return hits, issues + more


def suppressed_conditions(conn: Conn, position_id: int) -> frozenset[int]:
    """Condition ids whose first ``thesis_hit`` was followed by a revision decision (accepted
    or declined): acted on, so not reported again."""
    hits = store.load_triggers(kind="thesis_hit", conn=conn)
    if hits.empty:
        return frozenset()
    hits = hits[hits["position_id"] == position_id]
    if hits.empty:
        return frozenset()
    ev = book.position_events(conn, position_id)
    decided = [
        str(a) for a in ev.loc[ev["kind"].isin(["revision_accepted", "revision_declined"]), "at"]
    ]
    if not decided:
        return frozenset()
    last_decision = max(decided)
    out = set()
    for cid, g in hits.groupby(hits["detail"].map(lambda d: int(d["condition_id"]))):
        first = min(str(a) for a in g["at"])
        if first[:10] <= last_decision[:10]:
            out.add(int(str(cid)))
    return frozenset(out)


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
    if r.regime is not None:
        c = r.regime
        if c.regime == "calm":
            lines += [f"Market ({c.index}): **calm** — {c.reason}", ""]
        else:
            lines += [
                f"### Market: **{c.regime.upper()}** ({c.index} {c.date:%Y-%m-%d}): {c.reason}",
                "",
                "Consider tightening stops and pausing new entries today (advisory, ADR 0017).",
                "",
            ]

    if r.drawdown is not None:
        tripped = r.breaker_on and r.drawdown < -r.breaker_limit
        lines += [
            f"Book drawdown {-r.drawdown:.1%} vs circuit-breaker limit {r.breaker_limit:.0%}; breaker "
            + ("**ACTIVE: no new entries**" if tripped else ("on" if r.breaker_on else "off")),
            "",
        ]

    if r.booked:
        lines += ["### Booked from Trading212", ""]
        lines += [f"- {b}" for b in r.booked] + [""]
    if r.resized:
        lines += ["### Rotation buys sized to the cash freed", ""]
        lines += [f"- {b}" for b in r.resized] + [""]

    lines += ["### Owner actions", ""]
    owner = r.of(OWNER_KINDS)
    for t in owner:
        d = t.detail
        if t.kind == "stop_close":
            lines.append(
                f"- **{t.ticker}** closed {_px(d['close'])} below the stop {_px(d['stop'])} (position {t.position_id}): the stop order should have sold it — record its fill (`book.py fill STOP_ORDER --qty N --price P`)"
            )
        elif t.kind == "stop_touched":
            lines.append(
                f"- **{t.ticker}** traded through the stop {_px(d['stop'])} (low {_px(d['low'])}) but closed {_px(d['close'])} (position {t.position_id}): check whether the broker stop filled"
            )
        elif t.kind == "target_hit":
            lines.append(
                f"- **{t.ticker}** reached the target {_px(d['target'])} (high {_px(d['high'])}) (position {t.position_id}): nothing to do — the stop and the daily review manage the exit"
            )
        elif t.kind == "entry_break":
            lines.append(
                f"- **{t.ticker}** closed {_px(d['close'])} above the trigger {_px(d['trigger'])}: place entry order {t.order_id} (position {t.position_id}) now — `book.py placed {t.order_id} --ref ...`"
            )
        elif t.kind == "order_stale":
            lines.append(
                f"- **{t.ticker}** order {t.order_id} ({d['purpose']} {d['qty']} @ {_px(d['price'])}, {d['status']}) was valid until {d['valid_until']} (position {t.position_id}):"
                f" enter the fill (`book.py fill {t.order_id} --qty N --price P`), or run `book.py unfilled {t.order_id}` / `book.py expire`"
            )
        elif t.kind == "event_due":
            lines.append(
                f"- **{t.ticker}** scheduled event {d['event']} in {d['sessions']} session(s) (position {t.position_id}): policy **{d['policy']}** — "
                + (
                    f"the weekly review applied the {d['policy']} (`book.py revisions`)"
                    if d["policy"] in ("trim", "exit")
                    else "decide: hold through, trim or exit (set `event_policy` in config/portfolio.toml)"
                )
            )
        elif t.kind == "dividend":
            lines.append(
                f"- **{t.ticker}** ex-dividend {t.at}: {book.fmt_price(d['per_share'], d['currency'])} x {d['shares']} shares = £{d['amount_gbp']:,.2f} (position {t.position_id}): confirm against the broker and record — `book.py cash dividend {t.ticker} {d['amount_gbp']:.2f} --at {t.at}`"
            )
        elif t.kind == "split":
            lines.append(
                f"- **{t.ticker}** split {d['ratio']} on {t.at} (position {t.position_id}): record it — `book.py split {t.position_id} --ratio {d['ratio']} --at {t.at}`"
            )
        elif t.kind == "broker":
            cmd = f" — `book.py {d['command']}`" if d["command"] else ""
            lines.append(f"- **{t.ticker}** Trading212: {d['text']}{cmd}")
    lines += ([] if owner else ["none"]) + [""]

    lines += ["### Agent review needed", ""]
    agent = [t for t in r.of(AGENT_KINDS) if t.kind != "daily_review"]
    for t in agent:
        d = t.detail
        if t.kind == "gap":
            lines.append(
                f"- **{t.ticker}** gapped {d['gap_atr']:+.1f} ATR at the open ({_px(d['prev_close'])} → {_px(d['open'])}), position {t.position_id}"
            )
        elif t.kind == "thesis_hit":
            lines.append(
                f"- **{t.ticker}** thesis condition hit: `{d['condition']}` ({d['field']} = {_px(d['value']) if isinstance(d['value'], int | float) else d['value']}), position {t.position_id}"
            )
        else:
            lines.append(
                f"- **{t.ticker}** new filing {t.at[:16]} `{d['type_code']}` {d['headline']} (position {t.position_id}) {d['url']}"  # noqa: PD008
            )
    lines += ([] if agent else ["none"]) + [""]
    reviews = r.of(("daily_review",))
    lines += ["### Daily agent review (ADR 0024)", ""]
    lines += [
        f"- **{t.ticker}** position {t.position_id} ({t.detail['status']})"
        + (f": {', '.join(t.detail['fired'])}" if t.detail["fired"] else ": nothing fired")
        for t in reviews
    ] or ["none"]
    if reviews and r.run_id is not None:
        lines.append(
            f"\nRun `/monitor --run {r.run_id}` (or `uv run python scripts/monitor_pack.py --run {r.run_id}`)."
        )
    lines.append("")

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
