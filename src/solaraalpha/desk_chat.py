"""The desk thread: one running chat the agents post their updates to (docs/decisions/0033).

The thread lives in the results DB (append-only ``chat`` table), so every agent reads the same
history before it posts, wherever it runs. Telegram is only the window onto it: with
``SOLARA_TELEGRAM_TOKEN`` / ``SOLARA_TELEGRAM_CHAT`` set, each post is also sent to that chat, and
the owner's replies there are pulled into the thread (author ``owner``) before it is read.
Unset, the thread is still kept; nothing is sent. A failed send is logged, never raised: the
post is already in the thread.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import pandas as pd

from solaraalpha import store
from solaraalpha.config import REPO_ROOT, settings
from solaraalpha.data.http import DEFAULT_TIMEOUT, session
from solaraalpha.log import get_logger

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat (
    at        TEXT NOT NULL,     -- ISO-8601 UTC
    author    TEXT NOT NULL,     -- agent:<name> / owner / pipeline
    text      TEXT NOT NULL,
    run_id    INTEGER,           -- the run the post is about, if any
    ticker    TEXT,              -- the stock the post is about, if any (_BOOK for the book)
    update_id INTEGER            -- owner messages: the Telegram update (dedupes the pull)
);
"""
API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4096  # Telegram's message length cap


def connect(path: Path | None = None) -> store.Conn:
    """The results DB (see :func:`solaraalpha.store.connect`) with the chat table present."""
    conn = store.connect(path)
    conn.executescript(SCHEMA)
    return conn


def enabled() -> bool:
    return bool(settings.telegram_token.get_secret_value() and settings.telegram_chat)


def _call(method: str, payload: dict[str, Any]) -> Any:
    url = API.format(token=settings.telegram_token.get_secret_value(), method=method)
    resp = session().post(url, json=payload, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["result"]


def _label(author: str, ticker: str | None) -> str:
    name = author.removeprefix("agent:")
    return f"{name} · {ticker}" if ticker else name


# The owner reads Telegram on a phone: each post is headed in plain words with who is writing,
# the process it comes from and what it is about, not the thread's author / ticker codes.
WHO = {
    "validate": "Screen checker",
    "macro": "Sector & economy analyst",
    "research": "Research analyst",
    "pm": "Portfolio manager",
    "monitor": "Position monitor",
    "execute": "Execution trader",
    "events": "News analyst",
    "postmortem": "Trade reviewer",
    "pipeline": "Solara (automatic)",
    "owner": "You",
}
PROCESS = {
    "validate": "Weekly check of the screen's picks",
    "macro": "Weekly sector & economy note",
    "research": "Weekly research on a candidate",
    "pm": "Weekly portfolio plan",
    "monitor": "Daily check of positions and pending buys",
    "execute": "Sending orders to Trading212",
    "events": "News and announcements review",
    "postmortem": "Review of a closed trade",
    "retro": "Desk self-review: how its work has gone",
    "pipeline": "Automatic job",
}
AREAS = {
    "senior-quant": "Senior quant",
    "fundamental-strategist": "Fundamental strategist",
    "portfolio-manager": "Portfolio manager",
    "execution-trader": "Execution trader",
}
OBSERVATION = "observation:"


@cache
def _names() -> dict[str, str]:
    """Ticker -> company name, from the watchlist's comments (``"JET2.L",  # Jet2``)."""
    try:
        text = (REPO_ROOT / "config" / "watchlist.toml").read_text()
    except OSError:
        return {}
    return dict(re.findall(r'"([^"]+)",\s*#\s*(.+?)\s*$', text, re.M))


def _about(ticker: str) -> str:
    if ticker == "_BOOK":
        return "the portfolio as a whole"
    if ticker.startswith("_SECTOR:"):
        return f"the {ticker.removeprefix('_SECTOR:')} sector"
    if ticker.startswith("_DESK:"):
        return "how its own decisions have worked out"
    code = ticker.removesuffix(".L")
    name = _names().get(ticker)
    return f"{name} ({code})" if name else code


def heading(author: str, ticker: str | None, run_id: int | None, text: str) -> tuple[str, str]:
    """The Telegram header (who · process, then what it is about) and the text to send under
    it (an ``observation:`` post loses its prefix: the header says what it is)."""
    role = author.removeprefix("agent:")
    who = WHO.get(role, role.replace("-", " ").capitalize())
    if ticker and ticker.startswith("_DESK:"):
        area = ticker.removeprefix("_DESK:")
        who = AREAS.get(area, area.replace("-", " ").capitalize())
    process = PROCESS.get(role, "")
    if role == "pm" and run_id is None:
        process, ticker = "Reply to your message", None
    if text[: len(OBSERVATION)].lower() == OBSERVATION:
        process, text = "Note for the next desk self-review", text[len(OBSERVATION) :].strip()
    lines = [f"{who} · {process}" if process else who]
    if ticker:
        lines.append(f"About: {_about(ticker)}")
    return "\n".join(lines), text


def post(
    author: str,
    text: str,
    run_id: int | None = None,
    ticker: str | None = None,
    conn: store.Conn | None = None,
    path: Path | None = None,
) -> bool:
    """Add a message to the thread and send it to Telegram when configured. True if sent."""
    text = text.strip()
    if not text:
        raise ValueError("empty message")
    own = conn is None
    conn = conn or connect(path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO chat (at, author, text, run_id, ticker) VALUES (?, ?, ?, ?, ?)",
                (datetime.now(UTC).isoformat(), author, text, run_id, ticker),
            )
    finally:
        if own:
            conn.close()
    if not enabled():
        return False
    head, shown = heading(author, ticker, run_id, text)
    body = f"{head}\n\n{shown}"
    try:
        _call("sendMessage", {"chat_id": settings.telegram_chat, "text": body[:LIMIT]})
    except Exception as e:  # the thread already has it; a lost send is not worth a failed run
        log.warning("telegram send failed: %s", e)
        return False
    return True


def pull_owner(conn: store.Conn) -> int:
    """Copy the owner's new Telegram messages in the configured chat into the thread (author
    ``owner``). Other chats and bots are ignored. Returns how many were added."""
    if not enabled():
        return 0
    last = store.query("SELECT MAX(update_id) AS u FROM chat", conn)["u"].iloc[0]
    params: dict[str, Any] = {"timeout": 0, "allowed_updates": ["message"]}
    if pd.notna(last):
        params["offset"] = int(last) + 1
    try:
        updates = _call("getUpdates", params)
    except Exception as e:
        log.warning("telegram pull failed: %s", e)
        return 0
    rows = []
    for u in updates:
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(settings.telegram_chat):
            continue
        if m.get("from", {}).get("is_bot") or not m.get("text"):
            continue
        at = datetime.fromtimestamp(m["date"], UTC).isoformat()
        rows.append((at, "owner", m["text"], None, None, u["update_id"]))
    if rows:
        with conn:  # another job may have pulled the same update meanwhile: once each
            conn.executemany(
                "INSERT INTO chat SELECT ?, ?, ?, ?, ?, ?"
                " WHERE NOT EXISTS (SELECT 1 FROM chat WHERE update_id = ?)",
                [(*r, r[-1]) for r in rows],
            )
    return len(rows)


def pull(path: Path | None = None) -> int:
    """Pull the owner's new Telegram messages into the thread from a scheduled job, so none
    outlives Telegram's 24-hour hold on undelivered updates. Never raises: a job's own work
    matters more than the pull. Returns how many were added."""
    if not enabled():
        return 0
    try:
        conn = connect(path)
        try:
            return pull_owner(conn)
        finally:
            conn.close()
    except Exception as e:
        log.warning("desk thread pull failed: %s", e)
        return 0


def unanswered(conn: store.Conn, by: str = "agent:pm") -> pd.DataFrame:
    """The owner's messages since ``by`` last answered (oldest first): what the desk reply job
    (``desk-reply.yml``) wakes the PM for. Pull first (:func:`pull_owner`). An answer is a post
    with no run (the weekly ``/pm`` update carries its run and answers nobody); "since" is the
    order the thread received them in, so a message pulled while the PM was writing still
    waits even though Telegram dates it before the reply."""
    return store.query(
        "SELECT at, text FROM chat WHERE author = 'owner' AND rowid >"
        " COALESCE((SELECT MAX(rowid) FROM chat WHERE author = ? AND run_id IS NULL), 0)"
        " ORDER BY rowid",
        conn,
        [by],
    )


def thread(
    last: int = 30,
    ticker: str | None = None,
    conn: store.Conn | None = None,
    path: Path | None = None,
) -> pd.DataFrame:
    """The last ``last`` messages, oldest first, after pulling the owner's replies. With
    ``ticker``: that stock's posts plus everything not tied to a stock."""
    own = conn is None
    conn = conn or connect(path)
    try:
        pull_owner(conn)
        sql, params = "SELECT at, author, text, run_id, ticker FROM chat", []
        if ticker:
            sql += " WHERE ticker = ? OR ticker IS NULL OR ticker = '_BOOK'"
            params.append(ticker)
        df = store.query(sql + " ORDER BY at DESC, rowid DESC LIMIT ?", conn, [*params, last])
    finally:
        if own:
            conn.close()
    return df.iloc[::-1].reset_index(drop=True)


def render(df: pd.DataFrame) -> str:
    """The thread as plain text, one block per message."""
    if df.empty:
        return "(the desk thread is empty)"
    blocks = []
    for r in df.to_dict("records"):
        ticker = r["ticker"] if isinstance(r["ticker"], str) else None
        run = f" (run {int(r['run_id'])})" if pd.notna(r["run_id"]) else ""
        blocks.append(f"[{str(r['at'])[:16]}] {_label(str(r['author']), ticker)}{run}\n{r['text']}")
    return "\n\n".join(blocks)


def chats() -> list[dict[str, Any]]:
    """Chats that have messaged the bot recently (id, type, title / name), to find the chat id
    for ``SOLARA_TELEGRAM_CHAT``. Needs only the token."""
    if not settings.telegram_token.get_secret_value():
        raise RuntimeError("SOLARA_TELEGRAM_TOKEN is not set")
    seen: dict[int, dict[str, Any]] = {}
    for u in _call("getUpdates", {"timeout": 0}):
        c = (u.get("message") or u.get("my_chat_member") or {}).get("chat")
        if c:
            name = c.get("title") or " ".join(
                x for x in (c.get("first_name"), c.get("last_name")) if x
            )
            seen[c["id"]] = {"id": c["id"], "type": c.get("type"), "name": name}
    return list(seen.values())
