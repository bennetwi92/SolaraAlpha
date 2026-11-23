"""The results DB on the db branch (ADR 0041), against a local bare repo standing in for GitHub."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from solaraalpha import dbsync, store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import db

KEY = "correct horse battery staple"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    bare.mkdir()
    _git(bare, "init", "--quiet", "--bare")
    return bare


def _clone(tmp_path: Path, origin: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "remote", "add", "origin", str(origin))
    return repo


def _write(path: Path, ticker: str) -> None:
    store.save_run("scan", {}, ticker, {}, path=path, steps=[("screen", "technical", "pass", "x")])


def _tickers(path: Path) -> list[str]:
    conn = store.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT ticker FROM steps ORDER BY ticker")]
    finally:
        conn.close()


def test_fresh_db_round_trip_through_the_branch(tmp_path: Path, origin: Path) -> None:
    a, b = _clone(tmp_path, origin, "a"), _clone(tmp_path, origin, "b")
    db_a, db_b = tmp_path / "a.db", tmp_path / "b.db"
    rec = dbsync.pull(db_a, KEY, repo=a)
    assert rec.commit == "" and db_a.exists()  # no branch yet: an empty DB
    _write(db_a, "JET2.L")
    commit = dbsync.push(db_a, KEY, "scan", repo=a)
    assert commit and dbsync.remote_head(a) == commit
    dbsync.pull(db_b, KEY, repo=b)
    assert _tickers(db_b) == ["JET2.L"]


def test_unchanged_db_is_not_pushed(tmp_path: Path, origin: Path) -> None:
    a = _clone(tmp_path, origin, "a")
    dbsync.pull(tmp_path / "a.db", KEY, repo=a)
    assert dbsync.push(tmp_path / "a.db", KEY, "nothing", repo=a) is None
    assert dbsync.remote_head(a) == ""


def test_branch_keeps_one_commit_and_only_ciphertext(tmp_path: Path, origin: Path) -> None:
    a = _clone(tmp_path, origin, "a")
    path = tmp_path / "a.db"
    dbsync.pull(path, KEY, repo=a)
    for t in ("JET2.L", "SECRETTICKER.L"):
        _write(path, t)
        dbsync.push(path, KEY, f"save {t}", repo=a)
    assert _git(origin, "rev-list", "--count", "db") == "1"
    blob = subprocess.run(
        ["git", "cat-file", "blob", f"db:{dbsync.FILE}"],
        cwd=origin,
        check=True,
        capture_output=True,
    ).stdout
    assert b"SECRETTICKER" not in blob and b"SQLite format" not in blob


def test_second_writer_is_refused_not_merged(tmp_path: Path, origin: Path) -> None:
    a, b = _clone(tmp_path, origin, "a"), _clone(tmp_path, origin, "b")
    db_a, db_b = tmp_path / "a.db", tmp_path / "b.db"
    dbsync.pull(db_a, KEY, repo=a)
    _write(db_a, "JET2.L")
    dbsync.push(db_a, KEY, "first", repo=a)
    dbsync.pull(db_a, KEY, repo=a)
    dbsync.pull(db_b, KEY, repo=b)
    _write(db_a, "AAA.L")
    _write(db_b, "BBB.L")
    head = dbsync.push(db_a, KEY, "a saves first", repo=a)
    with pytest.raises(dbsync.DBSyncError, match="moved since"):
        dbsync.push(db_b, KEY, "b too late", repo=b)
    assert dbsync.remote_head(b) == head


def test_first_push_is_refused_when_the_branch_appeared_meanwhile(
    tmp_path: Path, origin: Path
) -> None:
    a, b = _clone(tmp_path, origin, "a"), _clone(tmp_path, origin, "b")
    db_a, db_b = tmp_path / "a.db", tmp_path / "b.db"
    dbsync.pull(db_a, KEY, repo=a)
    dbsync.pull(db_b, KEY, repo=b)
    _write(db_a, "AAA.L")
    _write(db_b, "BBB.L")
    dbsync.push(db_a, KEY, "a", repo=a)
    with pytest.raises(dbsync.DBSyncError):
        dbsync.push(db_b, KEY, "b", repo=b)


def test_pull_refuses_local_changes_unless_forced(tmp_path: Path, origin: Path) -> None:
    a = _clone(tmp_path, origin, "a")
    path = tmp_path / "a.db"
    dbsync.pull(path, KEY, repo=a)
    _write(path, "JET2.L")
    with pytest.raises(dbsync.DBSyncError, match="not pushed"):
        dbsync.pull(path, KEY, repo=a)
    dbsync.pull(path, KEY, repo=a, force=True)
    assert _tickers(path) == []


def test_wrong_key_is_refused(tmp_path: Path, origin: Path) -> None:
    a, b = _clone(tmp_path, origin, "a"), _clone(tmp_path, origin, "b")
    dbsync.pull(tmp_path / "a.db", KEY, repo=a)
    _write(tmp_path / "a.db", "JET2.L")
    dbsync.push(tmp_path / "a.db", KEY, "save", repo=a)
    with pytest.raises(dbsync.DBSyncError, match="gpg failed"):
        dbsync.pull(tmp_path / "b.db", "wrong", repo=b)
    with pytest.raises(dbsync.DBSyncError, match="not set"):
        dbsync.pull(tmp_path / "b.db", "", repo=b)


def test_corrupt_db_is_not_pushed(tmp_path: Path, origin: Path) -> None:
    a = _clone(tmp_path, origin, "a")
    path = tmp_path / "a.db"
    dbsync.pull(path, KEY, repo=a)
    raw = bytearray(path.read_bytes())
    raw[100:4096] = b"\xff" * (4096 - 100)  # trash the first page after the header
    path.write_bytes(bytes(raw))
    with pytest.raises(dbsync.DBSyncError):
        dbsync.push(path, KEY, "corrupt", repo=a)
    assert dbsync.remote_head(a) == ""


def test_cli_push_without_pull_is_refused(tmp_path: Path) -> None:
    _write(tmp_path / "x.db", "JET2.L")
    with pytest.raises(SystemExit, match="never pulled"):
        db.main(["--db", str(tmp_path / "x.db"), "push"])
