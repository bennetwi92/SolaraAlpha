"""Results DB on the `db` branch (docs/decisions/0041): pull it, see it, push it back.

uv run python scripts/db.py pull [--force]        # fetch + decrypt the branch -> data/solara.db
uv run python scripts/db.py status                # local file, rows per table, pulled vs branch
uv run python scripts/db.py push [--message M]    # integrity check, encrypt, replace the branch

The file is encrypted with SOLARA_DB_KEY (.env or env). pull refuses to overwrite a local DB
with changes not pushed unless --force; with no branch yet it starts an empty DB. push is
leased on the pulled commit: if another writer saved first it is refused, never merged, and
nothing is pushed when the file is unchanged. Workflows pull first and push last (every DB
step runs in between, inside the `book` concurrency group); a chat session pulls to read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solaraalpha import dbsync, store
from solaraalpha.config import settings


def _key() -> str:
    return settings.db_key.get_secret_value()


def status(db: Path) -> None:
    head = dbsync.remote_head()
    rec = dbsync.pulled(db)
    print(f"db branch: {head[:10] or 'none yet'}")
    if not db.exists():
        print(f"local {db}: none (run `db.py pull`)")
        return
    changed = rec is None or dbsync.sha256(db) != rec.sha256
    pulled_from = (rec.commit[:10] or "fresh DB") if rec else "never pulled"
    print(f"local {db}: {db.stat().st_size / 1e6:.1f} MB, pulled from {pulled_from}")
    if rec and rec.commit != head:
        print("  the branch has moved since: `db.py pull` for the latest")
    if changed:
        print("  local changes not pushed")
    conn = store.connect(db)
    try:
        for table, n in store.counts(conn).items():
            print(f"  {table:<16} {n:>10,}")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--db", type=Path, default=None, help="default data/solara.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="the local DB, rows per table, and the branch head")
    pl = sub.add_parser("pull", help="fetch and decrypt the db branch into the local file")
    pl.add_argument("--force", action="store_true", help="overwrite local changes not pushed")
    ps = sub.add_parser("push", help="encrypt the local file and replace the db branch")
    ps.add_argument("--message", default="results DB", help="the db commit's message")
    args = p.parse_args(argv)
    db = args.db or settings.db_path
    try:
        if args.cmd == "status":
            status(db)
        elif args.cmd == "pull":
            rec = dbsync.pull(db, _key(), force=args.force)
            print(f"pulled {rec.commit[:10] or 'a fresh DB (no db branch yet)'} into {db}")
        else:
            commit = dbsync.push(db, _key(), args.message)
            print(f"pushed {db} as {commit[:10]}" if commit else "unchanged: nothing pushed")
    except dbsync.DBSyncError as e:
        sys.exit(f"refused: {e}")


if __name__ == "__main__":
    main()
