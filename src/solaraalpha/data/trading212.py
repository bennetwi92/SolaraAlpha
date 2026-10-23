"""Trading212 public API (v0, beta): the owner's own account, the instrument list, and orders.

HTTP Basic with ``settings.t212_key`` / ``t212_secret``; ``settings.t212_env`` picks the practice
(``demo``) or the real (``live``) account, and a key only works on the account it was made for.
Reads (ADR 0030) follow list endpoints through ``nextPagePath``; when a response says no calls
are left in the window (``x-ratelimit-remaining: 0``, or a 429) the next call to that endpoint
first sleeps until ``x-ratelimit-reset`` (a whole-second epoch) instead of retrying into a 429.
Not cached: the point is the broker's state now.

**Orders** (ADR 0031): :func:`place` and :func:`cancel` are called only by
:mod:`solaraalpha.portfolio.execution` (``scripts/execute.py``, the one door with the guards).
They refuse unless ``settings.t212_trading`` names the account in ``t212_env`` (``off`` by
default; ``live`` only when the owner sets it), and they never retry: the API is not idempotent,
so a call that may have reached the broker raises :class:`Unknown` and the caller lists the
orders before ever sending again. A refusal comes back as :class:`T212Error` from the problem
JSON (``type``, ``title``, ``detail``).

Prices of LSE lines are in pence (the instrument's ``currency`` GBX), even where an order's
``currency`` reads GBP; money in ``walletImpact`` / cash / dividends is in GBP. Timestamps are
parsed to UTC. Parsers are pure and pinned to saved responses (``tests/fixtures/t212_*.json``);
a shape they don't know fails loudly. See docs/decisions/0030.
"""

from __future__ import annotations

import re
import time
from functools import cache
from typing import Any

import pandas as pd
import requests

from solaraalpha.config import settings
from solaraalpha.data.http import DEFAULT_TIMEOUT, USER_AGENT, session
from solaraalpha.log import get_logger

log = get_logger(__name__)

BASE = {"demo": "https://demo.trading212.com", "live": "https://live.trading212.com"}
API = "/api/v0"
PAGE = 50  # the largest page the history endpoints allow
LSE = re.compile(r"^[A-Z0-9]+l\d*_EQ$")  # Trading212 LSE lines: AAZl_EQ, BPMl1_EQ
_reset: dict[str, float] = {}  # endpoint -> when its rate-limit window resets (epoch seconds)


def configured() -> bool:
    return bool(settings.t212_key.get_secret_value() and settings.t212_secret.get_secret_value())


class T212Error(Exception):
    """Trading212 refused a call: HTTP ``status`` and its problem JSON. A 4xx means the call did
    nothing at the broker (a 429 only that it came too soon)."""

    def __init__(self, status: int, type: str = "", title: str = "", detail: str = "") -> None:
        self.status, self.type, self.title, self.detail = status, type, title, detail
        super().__init__(f"{status} {type or title}: {detail or title}".strip())

    @property
    def rate_limited(self) -> bool:
        return self.status == 429


class Unknown(Exception):
    """A write that may or may not have reached the broker (timeout, connection lost, 5xx)."""


def parse_error(status: int, payload: Any) -> T212Error:
    """Problem JSON (``type``, ``title``, ``status``, ``detail``; a 429 has ``code`` /
    ``context``) -> :class:`T212Error`."""
    p = payload if isinstance(payload, dict) else {}
    kind = str(p.get("type") or (p.get("context") or {}).get("type") or p.get("code") or "")
    return T212Error(status, kind, str(p.get("title") or ""), str(p.get("detail") or ""))


def _url(path: str) -> tuple[str, str]:
    """``(url, rate-limit key)``; one key per endpoint, an order id counts as one endpoint."""
    env = settings.t212_env
    if env not in BASE:
        raise ValueError(f"SOLARA_T212_ENV must be demo or live, not {env!r}")
    if not configured():
        raise RuntimeError("Trading212 keys not set (SOLARA_T212_KEY / SOLARA_T212_SECRET)")
    url = BASE[env] + (path if path.startswith(API) else API + path)
    return url, re.sub(r"/\d+$", "/{id}", url.split("?")[0])


def _wait(key: str) -> None:
    wait = _reset.pop(key, 0.0) - time.time()
    if wait > 0:
        log.info("Trading212 rate limit: sleeping %.0fs", wait)
        time.sleep(min(wait + 1, 120))


def _limits(key: str, resp: requests.Response) -> None:
    if resp.status_code == 429 or resp.headers.get("x-ratelimit-remaining") == "0":
        _reset[key] = float(resp.headers.get("x-ratelimit-reset", 0) or 0)


def _auth() -> tuple[str, str]:
    return (settings.t212_key.get_secret_value(), settings.t212_secret.get_secret_value())


def _get(path: str, **params: Any) -> Any:
    url, key = _url(path)
    _wait(key)
    resp = session().get(url, params=params or None, auth=_auth(), timeout=DEFAULT_TIMEOUT)
    _limits(key, resp)
    resp.raise_for_status()
    return resp.json()


@cache
def _plain() -> requests.Session:
    """A session with no retries: an order call is sent once (the API is not idempotent)."""
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def trading_allowed() -> str | None:
    """Why orders may not be sent now (None: they may): ``t212_trading`` must name the account
    in ``t212_env`` (ADR 0031: practice only until the owner sets ``live``)."""
    mode, env = settings.t212_trading, settings.t212_env
    if mode == "off":
        return "SOLARA_T212_TRADING is off: nothing is sent to Trading212"
    if mode not in BASE:
        return f"SOLARA_T212_TRADING must be off, demo or live, not {mode!r}"
    if mode != env:
        return f"SOLARA_T212_TRADING is {mode} but the account (SOLARA_T212_ENV) is {env}"
    if not configured():
        return "Trading212 keys not set (SOLARA_T212_KEY / SOLARA_T212_SECRET)"
    return None


def _write(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    why = trading_allowed()
    if why:
        raise RuntimeError(why)
    url, key = _url(path)
    _wait(key)
    try:
        resp = _plain().request(method, url, json=body, auth=_auth(), timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        raise Unknown(f"{method} {path}: {type(e).__name__}: {e}") from e
    _limits(key, resp)
    if resp.status_code >= 500:
        raise Unknown(f"{method} {path}: HTTP {resp.status_code} {resp.text[:200]}")
    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except ValueError:
            payload = {"title": resp.text[:200]}
        raise parse_error(resp.status_code, payload)
    return resp.json() if resp.content else None


def _pages(path: str, **params: Any) -> list[dict[str, Any]]:
    """Every item of a paged list endpoint, oldest page last as served."""
    items: list[dict[str, Any]] = []
    page: Any = _get(path, limit=PAGE, **params)
    while True:
        items += page["items"]
        nxt = page.get("nextPagePath")
        if not nxt:
            return items
        page = _get(str(nxt))


def _utc(v: Any) -> pd.Timestamp | None:
    return pd.Timestamp(v).tz_convert("UTC") if v else None


# ---------------------------------------------------------------------------- parsers


def parse_summary(payload: dict[str, Any]) -> dict[str, float | str]:
    """Account summary -> ``currency, total, free, reserved, invested, unrealised, realised``."""
    c, inv = payload["cash"], payload["investments"]
    return {
        "currency": str(payload["currency"]),
        "total": float(payload["totalValue"]),
        "free": float(c["availableToTrade"]),
        "reserved": float(c["reservedForOrders"]),
        "invested": float(inv["totalCost"]),
        "unrealised": float(inv["unrealizedProfitLoss"]),
        "realised": float(inv["realizedProfitLoss"]),
    }


POSITION_COLS = ["t212", "isin", "currency", "qty", "avg_price", "price", "opened_at"]


def parse_positions(payload: list[dict[str, Any]]) -> pd.DataFrame:
    """Open positions: Trading212 ticker, ISIN, quote currency, shares, average price paid and
    current price (quote currency), first opened (UTC)."""
    rows = [
        {
            "t212": p["instrument"]["ticker"],
            "isin": p["instrument"]["isin"],
            "currency": p["instrument"]["currency"],
            "qty": float(p["quantity"]),
            "avg_price": float(p["averagePricePaid"]),
            "price": float(p["currentPrice"]),
            "opened_at": _utc(p["createdAt"]),
        }
        for p in payload
    ]
    return pd.DataFrame(rows, columns=POSITION_COLS)


ORDER_COLS = [
    "id",
    "t212",
    "currency",
    "side",
    "type",
    "qty",
    "price",
    "status",
    "filled",
    "created_at",
]
TYPES = {"LIMIT": "limit", "STOP": "stop", "MARKET": "market", "STOP_LIMIT": "stop_limit"}


def _order(o: dict[str, Any]) -> dict[str, Any]:
    qty = float(o["quantity"])
    side = {"BUY": 1, "SELL": -1}[o["side"]] if o.get("side") else (1 if qty > 0 else -1)
    kind = TYPES[o["type"]]
    price = o.get("stopPrice") if kind in ("stop", "stop_limit") else o.get("limitPrice")
    return {
        "id": str(o["id"]),
        "t212": o["ticker"],
        "currency": (o.get("instrument") or {}).get("currency"),
        "side": side,
        "type": kind,
        "qty": abs(qty),
        "price": float(price) if price is not None else None,
        "status": str(o["status"]).lower(),
        "filled": abs(float(o.get("filledQuantity") or 0)),
        "created_at": _utc(o["createdAt"]),
    }


def parse_pending(payload: list[dict[str, Any]]) -> pd.DataFrame:
    """Orders working at the broker. ``side`` 1 buy / -1 sell, ``qty`` positive, ``price`` the
    stop price of a stop (stop-limit) order, else the limit price (None: market)."""
    return pd.DataFrame([_order(o) for o in payload], columns=ORDER_COLS)


FILL_COLS = [*ORDER_COLS, "fill_id", "fill_qty", "fill_price", "filled_at", "taxes"]


def parse_history(items: list[dict[str, Any]]) -> pd.DataFrame:
    """Historical orders (filled, cancelled, rejected) with their fill when there is one:
    ``fill_qty`` / ``fill_price`` (quote currency) / ``filled_at`` (UTC) / ``taxes`` (GBP)."""
    rows = []
    for it in items:
        row = _order(it["order"])
        f = it.get("fill") or {}
        taxes = (f.get("walletImpact") or {}).get("taxes") or []
        row |= {
            "fill_id": str(f["id"]) if f else None,
            "fill_qty": abs(float(f["quantity"])) if f else 0.0,
            "fill_price": float(f["price"]) if f else None,
            "filled_at": _utc(f.get("filledAt")),
            "taxes": float(sum(abs(float(t.get("quantity", 0))) for t in taxes)),
        }
        rows.append(row)
    return pd.DataFrame(rows, columns=FILL_COLS)


DIVIDEND_COLS = ["t212", "amount", "per_share", "qty", "paid_at", "ref"]


def parse_dividends(items: list[dict[str, Any]]) -> pd.DataFrame:
    """Dividends paid: Trading212 ticker, net amount (GBP), gross per share, shares, paid (UTC)."""
    rows = [
        {
            "t212": d["ticker"],
            "amount": float(d["amount"]),
            "per_share": float(d.get("grossAmountPerShare") or 0),
            "qty": float(d.get("quantity") or 0),
            "paid_at": _utc(d["paidOn"]),
            "ref": str(d.get("reference", "")),
        }
        for d in items
    ]
    return pd.DataFrame(rows, columns=DIVIDEND_COLS)


TRANSACTION_COLS = ["type", "amount", "currency", "at", "ref"]


def parse_transactions(items: list[dict[str, Any]]) -> pd.DataFrame:
    """Cash in and out of the account: ``type`` (deposit / withdrawal / fee / transfer ...),
    amount (positive, the type gives the direction), currency, time (UTC)."""
    rows = [
        {
            "type": str(t["type"]).lower(),
            "amount": abs(float(t["amount"])),
            "currency": str(t["currency"]),
            "at": _utc(t["dateTime"]),
            "ref": str(t.get("reference", "")),
        }
        for t in items
    ]
    return pd.DataFrame(rows, columns=TRANSACTION_COLS)


INSTRUMENT_COLS = ["t212", "short", "isin", "currency", "type", "name"]


def parse_instruments(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows = [
        {
            "t212": i["ticker"],
            "short": i.get("shortName", ""),
            "isin": i.get("isin", ""),
            "currency": i.get("currencyCode", ""),
            "type": i.get("type", ""),
            "name": i.get("name", ""),
        }
        for i in payload
    ]
    return pd.DataFrame(rows, columns=INSTRUMENT_COLS)


def lse_lines(
    instruments: pd.DataFrame, currencies: tuple[str, ...] | None = ("GBX",)
) -> dict[str, str]:
    """Yahoo ticker (TIDM + ``.L``) -> Trading212 ticker for every LSE share line Trading212
    offers in ``currencies`` (None: any; the book's LSE prices are pence, so GBX only). Matched on
    ``shortName`` (the TIDM); a TIDM with two lines is left out (ambiguous)."""
    lse = instruments[instruments["t212"].map(lambda t: bool(LSE.match(str(t))))]
    lse = lse[lse["type"] == "STOCK"]
    if currencies is not None:
        lse = lse[lse["currency"].isin(currencies)]
    counts = lse["short"].value_counts()
    ok = lse[lse["short"].map(counts) == 1]
    return {f"{s}.L": str(t) for s, t in zip(ok["short"], ok["t212"], strict=True)}


# ---------------------------------------------------------------------------- fetchers


def fetch_summary() -> dict[str, float | str]:
    return parse_summary(_get("/equity/account/summary"))


def fetch_positions() -> pd.DataFrame:
    return parse_positions(_get("/equity/positions"))


def fetch_pending() -> pd.DataFrame:
    return parse_pending(_get("/equity/orders"))


def fetch_history() -> pd.DataFrame:
    return parse_history(_pages("/equity/history/orders"))


def fetch_dividends() -> pd.DataFrame:
    return parse_dividends(_pages("/equity/history/dividends"))


def fetch_transactions() -> pd.DataFrame:
    return parse_transactions(_pages("/equity/history/transactions"))


def fetch_instruments() -> pd.DataFrame:
    """Every instrument Trading212 offers (one call; rate-limited to one per 50 s)."""
    return parse_instruments(_get("/equity/metadata/instruments"))


# ---------------------------------------------------------------------------- orders (ADR 0031)

ORDER_PATHS = {"market": "market", "limit": "limit", "stop": "stop"}


def order_body(type: str, t212: str, qty: int, side: int, price: float | None) -> dict[str, Any]:
    """The request body: ``quantity`` negative for a sell, prices in the quote currency (pence
    for GBX lines, tested 2026-10-21), limit and stop orders good till cancelled."""
    if type not in ORDER_PATHS:
        raise ValueError(f"order type must be one of {tuple(ORDER_PATHS)}, got {type!r}")
    body: dict[str, Any] = {"ticker": t212, "quantity": int(qty) * (1 if side > 0 else -1)}
    if type == "limit":
        body |= {"limitPrice": price, "timeValidity": "GOOD_TILL_CANCEL"}
    elif type == "stop":
        body |= {"stopPrice": price, "timeValidity": "GOOD_TILL_CANCEL"}
    return body


def place(type: str, t212: str, qty: int, side: int, price: float | None) -> dict[str, Any]:
    """Place one order (sent once); returns it parsed as :func:`parse_pending` rows are."""
    body = order_body(type, t212, qty, side, price)
    return _order(_write("POST", f"/equity/orders/{ORDER_PATHS[type]}", body))


def cancel(order_id: str) -> None:
    """Ask Trading212 to cancel a working order. 404: it is not working (gone or never there)."""
    _write("DELETE", f"/equity/orders/{order_id}")


def fetch_order(order_id: str) -> dict[str, Any] | None:
    """A working order by id, None once it is no longer working (404: filled, cancelled ...;
    the history gives its end state)."""
    url, key = _url(f"/equity/orders/{order_id}")
    _wait(key)
    resp = _plain().get(url, auth=_auth(), timeout=DEFAULT_TIMEOUT)
    _limits(key, resp)
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise parse_error(resp.status_code, resp.json() if resp.content else {})
    return _order(resp.json())
