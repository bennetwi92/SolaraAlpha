"""The results DB on the ``db`` branch (docs/decisions/0041).

The DB is one SQLite file, encrypted with ``gpg`` (AES-256, passphrase ``SOLARA_DB_KEY``) and
kept as ``solara.db.gpg`` on an orphan branch that always holds a single commit. The repo is
public: the branch holds only ciphertext.

:func:`pull` fetches and decrypts it to the local DB file and records what it pulled next to it
(``<db>.pulled``: the commit and the file's sha256). :func:`push` checks the file's integrity,
encrypts it, commits it as a fresh orphan and force-pushes *with a lease* on the pulled commit,
so a push never overwrites a save it has not seen: a second writer is refused, not merged.
Nothing is pushed when the file is unchanged since the pull.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from solaraalpha.config import REPO_ROOT
from solaraalpha.log import get_logger

log = get_logger(__name__)

FILE = "solara.db.gpg"
BRANCH = "db"
MAX_BYTES = 95_000_000  # GitHub refuses files over 100 MB
WARN_BYTES = 50_000_000
_REF = "refs/solara/db"  # where the fetched branch is kept locally
_GPG = ["gpg", "--batch", "--yes", "--quiet", "--pinentry-mode", "loopback", "--passphrase-fd", "0"]


class DBSyncError(RuntimeError):
    """A pull or push that must not go ahead (moved branch, bad key, corrupt file, ...)."""


@dataclass(frozen=True)
class Pulled:
    commit: str  # the db-branch commit pulled; "" = the branch did not exist (a fresh DB)
    sha256: str  # of the decrypted file as pulled


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _gpg(key: str, *args: str) -> None:
    if not key:
        raise DBSyncError("SOLARA_DB_KEY is not set")
    with tempfile.TemporaryDirectory() as home:  # a throwaway keyring: symmetric only
        res = subprocess.run(
            [*_GPG, "--homedir", home, *args],
            input=key.encode(),
            capture_output=True,
        )
    if res.returncode:
        raise DBSyncError(f"gpg failed: {res.stderr.decode(errors='replace').strip()}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _record_path(db: Path) -> Path:
    return db.with_name(db.name + ".pulled")


def pulled(db: Path) -> Pulled | None:
    """What was last pulled into ``db``, or None if it never was."""
    p = _record_path(db)
    if not p.exists():
        return None
    raw = json.loads(p.read_text())
    return Pulled(str(raw["commit"]), str(raw["sha256"]))


def _record(db: Path, rec: Pulled) -> None:
    _record_path(db).write_text(json.dumps({"commit": rec.commit, "sha256": rec.sha256}))


def remote_head(repo: Path = REPO_ROOT, remote: str = "origin", branch: str = BRANCH) -> str:
    """The db branch's commit on ``remote``; "" when the branch does not exist."""
    out = _git(repo, "ls-remote", remote, f"refs/heads/{branch}")
    return out.split()[0] if out else ""


def integrity(db: Path) -> str:
    """``"ok"``, or what is wrong with the SQLite file (unreadable counts as wrong)."""
    conn = sqlite3.connect(db)
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    except sqlite3.DatabaseError as e:
        return str(e)
    finally:
        conn.close()


def pull(
    db: Path,
    key: str,
    repo: Path = REPO_ROOT,
    remote: str = "origin",
    branch: str = BRANCH,
    force: bool = False,
) -> Pulled:
    """Fetch and decrypt the db branch into ``db``. Refuses to overwrite a local DB with changes
    not pushed (or one never pulled) unless ``force``. With no branch yet, ``db`` starts empty."""
    rec = pulled(db)
    if db.exists() and not force and (rec is None or sha256(db) != rec.sha256):
        raise DBSyncError(f"{db} has changes not pushed (or was never pulled); push or --force")
    db.parent.mkdir(parents=True, exist_ok=True)
    head = remote_head(repo, remote, branch)
    if not head:
        from solaraalpha import store  # store imports config only; kept local to stay light

        db.unlink(missing_ok=True)
        store.connect(db).close()  # the schema, no rows
        new = Pulled("", sha256(db))
        _record(db, new)
        log.info("no %s branch on %s: started a fresh DB at %s", branch, remote, db)
        return new
    _git(repo, "fetch", "--quiet", "--depth", "1", remote, f"+refs/heads/{branch}:{_REF}")
    with tempfile.TemporaryDirectory() as tmp:
        enc, plain = Path(tmp) / FILE, Path(tmp) / "solara.db"
        blob = subprocess.run(
            ["git", "cat-file", "blob", f"{head}:{FILE}"], cwd=repo, check=True, capture_output=True
        ).stdout
        enc.write_bytes(blob)
        _gpg(key, "--output", str(plain), "--decrypt", str(enc))
        if (ok := integrity(plain)) != "ok":
            raise DBSyncError(f"the pulled DB fails its integrity check: {ok}")
        os.replace(plain, db)
    new = Pulled(head, sha256(db))
    _record(db, new)
    log.info("pulled %s@%s into %s", branch, head[:10], db)
    return new


def push(
    db: Path,
    key: str,
    message: str,
    repo: Path = REPO_ROOT,
    remote: str = "origin",
    branch: str = BRANCH,
) -> str | None:
    """Encrypt ``db`` and replace the db branch with it, leased on the pulled commit. Returns the
    new commit, or None when ``db`` is unchanged since the pull (nothing pushed)."""
    rec = pulled(db)
    if rec is None or not db.exists():
        raise DBSyncError(f"{db} was never pulled: pull first, then write, then push")
    if (ok := integrity(db)) != "ok":
        raise DBSyncError(f"{db} fails its integrity check, not pushed: {ok}")
    digest = sha256(db)
    if digest == rec.sha256:
        log.info("%s unchanged since the pull: nothing pushed", db)
        return None
    with tempfile.TemporaryDirectory() as tmp:
        enc = Path(tmp) / FILE
        _gpg(key, "--symmetric", "--cipher-algo", "AES256", "--output", str(enc), str(db))
        size = enc.stat().st_size
        if size > MAX_BYTES:
            raise DBSyncError(f"encrypted DB is {size / 1e6:.0f} MB, over GitHub's file limit")
        if size > WARN_BYTES:
            log.warning("encrypted DB is %.0f MB (limit %.0f MB)", size / 1e6, MAX_BYTES / 1e6)
        blob = _git(repo, "hash-object", "-w", str(enc))
    tree = subprocess.run(
        ["git", "mktree"],
        cwd=repo,
        input=f"100644 blob {blob}\t{FILE}\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "solara-db",
        "GIT_AUTHOR_EMAIL": "solara-db@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "solara-db",
        "GIT_COMMITTER_EMAIL": "solara-db@users.noreply.github.com",
    }
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-m", message],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    ref = f"refs/heads/{branch}"
    lease = f"{ref}:{rec.commit}"  # an empty commit: the branch must not exist yet
    res = subprocess.run(
        ["git", "push", "--quiet", f"--force-with-lease={lease}", remote, f"{commit}:{ref}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if res.returncode:
        raise DBSyncError(
            f"the {branch} branch moved since this DB was pulled ({rec.commit[:10] or 'none'}):"
            f" another writer saved first, nothing pushed. {res.stderr.strip()}"
        )
    _record(db, Pulled(commit, digest))
    log.info("pushed %s to %s@%s (%.1f MB encrypted)", db, branch, commit[:10], size / 1e6)
    return commit
