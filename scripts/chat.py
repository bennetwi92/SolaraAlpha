"""The desk thread: read it, post to it (docs/decisions/0033).

uv run python scripts/chat.py read [--last 30] [--ticker JET2.L] [--db PATH]
uv run python scripts/chat.py post --as agent:pm [--run N] [--ticker T] (TEXT | --file FILE)
uv run python scripts/chat.py pending    # the owner's messages the PM has not answered (exit 3: none)
uv run python scripts/chat.py chats      # setup: the Telegram chats that messaged the bot

read pulls the owner's new Telegram replies into the thread, then prints the last messages,
oldest first (with --ticker: that stock's posts plus everything not tied to a stock). post adds
one message and sends it to Telegram when SOLARA_TELEGRAM_TOKEN / SOLARA_TELEGRAM_CHAT are set.
pending pulls too, then lists the owner's messages since agent:pm last posted (desk-reply.yml).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solaraalpha import desk_chat


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read")
    r.add_argument("--last", type=int, default=30)
    r.add_argument("--ticker", default=None)
    w = sub.add_parser("post")
    w.add_argument("--as", dest="author", required=True, help="agent:<name> / owner / pipeline")
    w.add_argument("--run", type=int, default=None)
    w.add_argument("--ticker", default=None)
    w.add_argument("--file", type=Path, default=None, help="read the message from a file")
    w.add_argument("text", nargs="?", default="")
    pe = sub.add_parser("pending")
    sub.add_parser("chats")
    for s in (r, w, pe):
        s.add_argument("--db", type=Path, default=None, help="local DB file (default: configured)")
    args = p.parse_args(argv)
    if args.cmd == "chats":
        found = desk_chat.chats()
        if not found:
            print("no chats yet: send the bot a message (or add it to a group), then run again")
        for c in found:
            print(f"{c['id']}\t{c['type']}\t{c['name']}")
        return
    if args.cmd == "pending":
        conn = desk_chat.connect(args.db)
        try:
            desk_chat.pull_owner(conn)
            waiting = desk_chat.unanswered(conn)
        finally:
            conn.close()
        if waiting.empty:
            print("nothing from the owner to answer")
            sys.exit(3)
        for r in waiting.to_dict("records"):
            print(f"[{str(r['at'])[:16]}] owner\n{r['text']}\n")
        return
    if args.cmd == "read":
        print(desk_chat.render(desk_chat.thread(args.last, args.ticker, path=args.db)))
        return
    text = args.file.read_text() if args.file else args.text
    if not text.strip():
        p.error("post needs TEXT or --file")
    sent = desk_chat.post(args.author, text, args.run, args.ticker, path=args.db)
    print("posted" + (" and sent to Telegram" if sent else " (thread only)"))


if __name__ == "__main__":
    main()
