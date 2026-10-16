"""Results DB tooling: which DB is in use, and copies between the local file and Turso.

uv run python scripts/db.py status                       # Turso host or local path; rows per table
uv run python scripts/db.py push [--from PATH] [--append-new-runs]   # local file -> Turso
uv run python scripts/db.py pull [--to PATH]             # Turso -> a new local file

push refuses when Turso already holds runs, unless --append-new-runs, which copies only the
runs absent from Turso (matched on started_at, script, git_sha) under new run ids. pull refuses
to overwrite an existing file. Both need SOLARA_TURSO_URL / SOLARA_TURSO_TOKEN (.env or env).
See docs/decisions/0013.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solaraalpha import store
from solaraalpha.config import settings


def _turso() -> store.Conn:
    if not settings.turso_url:
        sys.exit("Turso not configured: set SOLARA_TURSO_URL and SOLARA_TURSO_TOKEN")
    return store.connect()


def _describe() -> str:
    if settings.turso_url:
        return f"Turso {settings.turso_url}"
    return f"local {settings.db_path}"


def status() -> None:
    conn = store.connect()
    try:
        print(f"Results DB: {_describe()}")
        for table, n in store.counts(conn).items():
            print(f"  {table:<16} {n:>10,}")
    finally:
        conn.close()


def push(src_path: Path, append_new_runs: bool) -> None:
    if not src_path.exists():
        sys.exit(f"no local DB at {src_path}")
    src, dst = store.connect(src_path), _turso()
    try:
        copied = store.copy_db(src, dst, append_new_runs=append_new_runs)
    except ValueError as e:
        sys.exit(f"refused: {e} (--append-new-runs copies only the runs Turso lacks)")
    finally:
        src.close()
        dst.close()
    _print_copied(f"{src_path} -> Turso", copied)


def pull(dst_path: Path) -> None:
    if dst_path.exists():
        sys.exit(f"refused: {dst_path} exists; move it or pass another --to")
    src, dst = _turso(), store.connect(dst_path)
    try:
        copied = store.copy_db(src, dst)
    finally:
        src.close()
        dst.close()
    _print_copied(f"Turso -> {dst_path}", copied)


def _print_copied(what: str, copied: dict[str, int]) -> None:
    print(f"copied {what}:")
    for table, n in copied.items():
        print(f"  {table:<16} {n:>10,}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="which DB is in use and rows per table")
    ps = sub.add_parser("push", help="copy a local DB file into Turso")
    ps.add_argument("--from", dest="src", type=Path, default=None, help="default data/solara.db")
    ps.add_argument(
        "--append-new-runs", action="store_true", help="copy only runs Turso lacks (new ids)"
    )
    pl = sub.add_parser("pull", help="copy Turso into a new local DB file")
    pl.add_argument("--to", dest="dst", type=Path, default=None, help="default data/solara.db")
    args = p.parse_args(argv)
    if args.cmd == "status":
        status()
    elif args.cmd == "push":
        push(args.src or settings.db_path, args.append_new_runs)
    else:
        pull(args.dst or settings.db_path)


if __name__ == "__main__":
    main()
