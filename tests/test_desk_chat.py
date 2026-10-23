from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from solaraalpha import desk_chat
from solaraalpha.config import settings


class _Resp:
    def __init__(self, result: Any) -> None:
        self.result = result

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Any:
        return {"ok": True, "result": self.result}


class _Telegram:
    """sendMessage succeeds; getUpdates returns the queued updates past ``offset``."""

    def __init__(self, updates: list[dict[str, Any]] | None = None) -> None:
        self.updates, self.sent = updates or [], []  # type: ignore[var-annotated]

    def post(self, url: str, json: dict[str, Any], timeout: int) -> _Resp:
        assert url.startswith("https://api.telegram.org/botTOK/")
        if url.endswith("sendMessage"):
            self.sent.append(json)
            return _Resp({"message_id": len(self.sent)})
        off = json.get("offset", 0)
        return _Resp([u for u in self.updates if u["update_id"] >= off])


def _msg(update_id: int, chat: int, text: str, bot: bool = False) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "date": 1792300000 + update_id,
            "chat": {"id": chat, "type": "private", "first_name": "Owner"},
            "from": {"is_bot": bot},
            "text": text,
        },
    }


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> _Telegram:
    tg = _Telegram()
    monkeypatch.setattr(settings, "telegram_token", SecretStr("TOK"))
    monkeypatch.setattr(settings, "telegram_chat", "42")
    monkeypatch.setattr(desk_chat, "session", lambda: tg)
    return tg


def test_thread_kept_without_telegram(db_backend: Path | None) -> None:
    assert desk_chat.post("agent:pm", "Funded AVG.L at 2%.", 8, "_BOOK", path=db_backend) is False
    desk_chat.post("agent:monitor", "Stop raised to 1480.", 9, "JET2.L", path=db_backend)
    desk_chat.post("agent:monitor", "Holding.", 9, "AVG.L", path=db_backend)
    df = desk_chat.thread(path=db_backend)
    assert list(df["author"]) == ["agent:pm", "agent:monitor", "agent:monitor"]
    jet2 = desk_chat.thread(ticker="JET2.L", path=db_backend)
    assert list(jet2["text"]) == ["Funded AVG.L at 2%.", "Stop raised to 1480."]
    assert list(desk_chat.thread(last=1, path=db_backend)["text"]) == ["Holding."]
    text = desk_chat.render(df)
    assert "pm · _BOOK (run 8)\nFunded AVG.L at 2%." in text


def test_post_sends_to_telegram(db_backend: Path | None, telegram: _Telegram) -> None:
    assert desk_chat.post("agent:research", "ACT, entry 805.", ticker="AVG.L", path=db_backend)
    assert telegram.sent == [{"chat_id": "42", "text": "research · AVG.L\nACT, entry 805."}]


def test_owner_replies_pulled_once(db_backend: Path | None, telegram: _Telegram) -> None:
    telegram.updates = [
        _msg(1, 42, "Why did we skip JET2?"),
        _msg(2, 99, "spam from a stranger"),
        _msg(3, 42, "bot echo", bot=True),
    ]
    df = desk_chat.thread(path=db_backend)
    assert list(zip(df["author"], df["text"], strict=True)) == [("owner", "Why did we skip JET2?")]
    telegram.updates.append(_msg(4, 42, "Thanks."))
    df = desk_chat.thread(path=db_backend)  # update 1 not pulled twice
    assert list(df["text"]) == ["Why did we skip JET2?", "Thanks."]


def test_failed_send_keeps_the_post(
    db_backend: Path | None, telegram: _Telegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("network down")

    monkeypatch.setattr(telegram, "post", boom)
    assert desk_chat.post("agent:pm", "Still here.", path=db_backend) is False
    assert desk_chat.render(desk_chat.thread(path=db_backend)).endswith("Still here.")


def test_empty_message_refused(db_backend: Path | None) -> None:
    with pytest.raises(ValueError):
        desk_chat.post("agent:pm", "  ", path=db_backend)


def test_pull_for_jobs(db_backend: Path | None, telegram: _Telegram) -> None:
    telegram.updates = [_msg(1, 42, "Anything on JET2?"), _msg(2, 7, "someone else")]
    assert desk_chat.pull(db_backend) == 1
    assert desk_chat.pull(db_backend) == 0
    assert list(desk_chat.thread(path=db_backend)["author"]) == ["owner"]


def test_pull_never_raises(monkeypatch: pytest.MonkeyPatch, telegram: _Telegram) -> None:
    def boom(path: Path | None = None) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(desk_chat, "connect", boom)
    assert desk_chat.pull() == 0


def test_pull_off_without_telegram(db_backend: Path | None) -> None:
    assert desk_chat.pull(db_backend) == 0


def test_unanswered_since_the_pm_posted(db_backend: Path | None, telegram: _Telegram) -> None:
    telegram.updates = [_msg(1, 42, "Why no JET2?")]
    conn = desk_chat.connect(db_backend)
    try:
        assert desk_chat.unanswered(conn).empty
        desk_chat.pull_owner(conn)
        assert list(desk_chat.unanswered(conn)["text"]) == ["Why no JET2?"]
        desk_chat.post("agent:monitor", "Holding AVG.L.", conn=conn)
        assert len(desk_chat.unanswered(conn)) == 1  # only the PM's post answers
        desk_chat.post("agent:pm", "JET2 failed the liquidity floor.", conn=conn)
        assert desk_chat.unanswered(conn).empty
    finally:
        conn.close()


def test_a_message_pulled_while_the_pm_wrote_still_waits(
    db_backend: Path | None, telegram: _Telegram
) -> None:
    conn = desk_chat.connect(db_backend)
    try:
        # the owner wrote (Telegram time 1792300001, 2026) before the PM's reply below was
        # posted, but it reached the thread only after it
        desk_chat.post("agent:pm", "Answering your earlier question.", conn=conn)
        telegram.updates = [_msg(1, 42, "And what about AVG?")]
        desk_chat.pull_owner(conn)
        assert list(desk_chat.unanswered(conn)["text"]) == ["And what about AVG?"]
        desk_chat.post("agent:pm", "Weekly allocation done.", 12, "_BOOK", conn=conn)
        assert len(desk_chat.unanswered(conn)) == 1  # the weekly /pm update answers nobody
    finally:
        conn.close()


def test_the_same_update_is_stored_once(
    db_backend: Path | None, telegram: _Telegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    telegram.updates = [_msg(1, 42, "Hello?")]
    stale = telegram.post
    # another job pulled meanwhile: this one asks Telegram from before that pull
    monkeypatch.setattr(telegram, "post", lambda url, json, timeout: stale(url, {}, timeout))
    conn = desk_chat.connect(db_backend)
    try:
        desk_chat.pull_owner(conn)
        desk_chat.pull_owner(conn)
        assert list(desk_chat.thread(conn=conn)["text"]) == ["Hello?"]
    finally:
        conn.close()
