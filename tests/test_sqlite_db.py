"""The SQLite opener, the connection that falls back to None, and the network filesystem check."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from xueqiu_api._sqlite import REPROBE_S, Db, StoreStatus, network_fs, open_db

from .harness import FakeClock

WORKER = Path(__file__).with_name("sqlite_worker.py")
SETUP = ("CREATE TABLE IF NOT EXISTS rows (value INTEGER NOT NULL)",)
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes, inodes and fork")

MakeDb = Callable[..., Db]


@pytest.fixture
def make_db() -> Iterator[MakeDb]:
    made: list[Db] = []

    def factory(path: Path, **options: Any) -> Db:
        options = {"busy_timeout_s": 1.0, "setup": SETUP, "label": "test store", **options}
        db = Db(path, **options)
        made.append(db)
        return db

    yield factory
    for db in made:
        db.close()


def insert(value: int) -> Callable[[sqlite3.Connection], int]:
    return lambda conn: conn.execute("INSERT INTO rows VALUES (?)", (value,)).rowcount


def values(conn: sqlite3.Connection) -> list[int]:
    return [value for (value,) in conn.execute("SELECT value FROM rows ORDER BY rowid")]


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@POSIX
def test_the_store_and_its_wal_files_are_private_after_the_first_write(
    tmp_path: Path, make_db: MakeDb
) -> None:
    store = tmp_path / "store"
    previous = os.umask(0o022)
    try:
        db = make_db(store / "test.sqlite3")
        assert db.run(insert(1)) == 1
        modes = {path.name: mode(path) for path in store.iterdir()}
    finally:
        os.umask(previous)
    names = ("test.sqlite3", "test.sqlite3-wal", "test.sqlite3-shm")
    assert modes == dict.fromkeys(names, 0o600)
    assert mode(store) == 0o700
    assert db.status() == StoreStatus(str(store / "test.sqlite3"), True, None, "wal")


def wait_for(paths: list[Path], children: list[subprocess.Popen[str]]) -> None:
    """Wait until every path exists; fail at once when a child has exited."""
    give_up = time.monotonic() + 120
    while not all(path.exists() for path in paths):
        exited = [child for child in children if child.poll() is not None]
        assert not exited, exited[0].communicate()[1]
        assert time.monotonic() < give_up, "the children never reached the barrier"
        time.sleep(0.005)


def test_processes_opening_one_fresh_file_at_once_all_succeed(tmp_path: Path) -> None:
    count, rounds = 8, 10
    argv = [sys.executable, str(WORKER), "open", str(tmp_path)]
    children = [
        subprocess.Popen(
            [*argv, str(index), str(rounds)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(count)
    ]
    try:
        for round_ in range(rounds):
            wait_for([tmp_path / f"ready-{round_}-{index}" for index in range(count)], children)
            (tmp_path / f"go-{round_}").touch()
        outputs = [child.communicate(timeout=120) for child in children]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate()
    for child, (out, err) in zip(children, outputs, strict=True):
        assert child.returncode == 0, err
        assert json.loads(out) == ["ok"] * rounds
    for round_ in range(rounds):
        conn = open_db(tmp_path / f"round-{round_}.sqlite3", busy_timeout_s=1, readonly=True)
        try:
            assert conn.execute("SELECT count(*) FROM rows").fetchall() == [(count,)]
            assert conn.execute("PRAGMA journal_mode").fetchall() == [("wal",)]
        finally:
            conn.close()


NOT_A_DATABASE = b"These bytes are not a SQLite database.\n" * 100


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            "read-only directory",
            marks=pytest.mark.skipif(
                sys.platform == "win32" or os.geteuid() == 0,
                reason="needs POSIX permissions that apply to the user",
            ),
        ),
        "a directory",
        "not a database",
    ],
)
def test_an_unusable_file_makes_run_return_none_and_is_left_as_it_was(
    tmp_path: Path, make_db: MakeDb, caplog: pytest.LogCaptureFixture, case: str
) -> None:
    store = tmp_path / "store"
    path = store / "test.sqlite3"
    store.mkdir()
    if case == "read-only directory":
        store.chmod(0o500)
    elif case == "a directory":
        path.mkdir()
    else:
        path.write_bytes(NOT_A_DATABASE)
    try:
        with caplog.at_level(logging.WARNING, logger="xueqiu_api"):
            db = make_db(path)
            assert db.run(insert(1)) is None
            assert db.run(insert(2)) is None
    finally:
        store.chmod(0o700)

    [warning] = caplog.records
    assert str(path) in warning.getMessage()
    status = db.status()
    assert not status.available
    assert status.reason is not None
    if case == "read-only directory":
        assert list(store.iterdir()) == []
    elif case == "a directory":
        assert list(path.iterdir()) == []
    else:
        assert status.reason == "DatabaseError SQLITE_NOTADB"
        assert warning.getMessage().count("DatabaseError") == 1
        assert path.read_bytes() == NOT_A_DATABASE
        assert sorted(item.name for item in store.iterdir()) == ["test.sqlite3"]


def test_the_first_run_after_the_reprobe_interval_opens_the_file_again(
    tmp_path: Path, make_db: MakeDb, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock()
    path = tmp_path / "test.sqlite3"
    path.mkdir()
    db = make_db(path, clock=clock.now)
    with caplog.at_level(logging.WARNING, logger="xueqiu_api"):
        assert db.run(insert(1)) is None
        path.rmdir()
        clock.advance(REPROBE_S - 1)
        assert db.run(insert(2)) is None
        assert not path.exists()
        clock.advance(1)
        assert db.run(insert(3)) == 1

    assert db.run(values) == [3]
    assert db.status() == StoreStatus(str(path), True, None, "wal")
    unavailable, recovered = (record.getMessage() for record in caplog.records)
    assert unavailable.startswith(f"test store {path} is unavailable (")
    assert recovered == f"test store {path} is available again"


def test_a_python_without_sqlite3_gets_none(
    tmp_path: Path, make_db: MakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "sqlite3", None)
    db = make_db(tmp_path / "test.sqlite3")
    assert db.run(insert(1)) is None
    assert db.status().reason in {"ImportError", "ModuleNotFoundError"}
    assert not (tmp_path / "test.sqlite3").exists()


def test_a_network_filesystem_gets_none_without_opening_the_file(
    tmp_path: Path, make_db: MakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    def nfs4(path: Path) -> str:
        return "nfs4"

    monkeypatch.setattr("xueqiu_api._sqlite.network_fs", nfs4)
    db = make_db(tmp_path / "store" / "test.sqlite3")
    assert db.run(insert(1)) is None
    assert (db.status().reason or "").startswith("nfs4 network filesystem")
    assert not (tmp_path / "store").exists()


@POSIX
def test_a_fork_child_reopens_an_inherited_db_and_the_parent_keeps_working(
    tmp_path: Path,
) -> None:
    done = subprocess.run(
        [sys.executable, str(WORKER), "fork", str(tmp_path / "test.sqlite3")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["child_exit"] == 0
    assert result["parent_wrote"] == 1
    assert result["parent_kept_its_connection"] is True
    assert result["rows"] == [result["parent"], result["child"], result["parent"]]
    assert result["integrity"] == [["ok"]]


@POSIX
@pytest.mark.parametrize("how", ["moved away", "deleted"])
def test_a_store_replaced_by_a_new_file_is_reopened(
    tmp_path: Path, make_db: MakeDb, how: str
) -> None:
    store = tmp_path / "store"
    path = store / "test.sqlite3"
    db = make_db(path)
    assert db.run(insert(1)) == 1
    if how == "moved away":
        store.rename(tmp_path / "old")
        fresh = open_db(path, busy_timeout_s=1, setup=SETUP)
        fresh.execute("INSERT INTO rows VALUES (2)")
        fresh.close()
    else:
        shutil.rmtree(store)

    assert db.run(insert(3)) == 1
    assert db.run(values) == ([2, 3] if how == "moved away" else [3])
    assert db.run(lambda conn: conn.execute("PRAGMA integrity_check").fetchall()) == [("ok",)]


def test_a_read_only_open_keeps_special_characters_in_the_path(tmp_path: Path) -> None:
    name = "a#b%d" if sys.platform == "win32" else "a#b?c%d"
    path = tmp_path / name / "store.sqlite3"
    conn = open_db(path, busy_timeout_s=1, setup=SETUP)
    conn.executemany("INSERT INTO rows VALUES (?)", [(1,), (2,)])
    conn.close()

    readonly = open_db(path, busy_timeout_s=1, readonly=True)
    try:
        assert values(readonly) == [1, 2]
        with pytest.raises(sqlite3.OperationalError) as refused:
            readonly.execute("INSERT INTO rows VALUES (3)")
    finally:
        readonly.close()
    assert refused.value.sqlite_errorname.startswith("SQLITE_READONLY")
    assert sorted(item.name for item in tmp_path.iterdir()) == [name]


def test_a_read_only_open_of_a_missing_path_raises_and_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "store.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        open_db(path, busy_timeout_s=1, readonly=True)
    assert list(tmp_path.iterdir()) == []


MOUNTINFO = """\
22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw
25 22 0:40 / /home rw,relatime shared:12 - nfs4 server:/export/home rw,vers=4.2
26 25 0:41 / /home/local\\040disk rw,relatime - ext4 /dev/sdb1 rw
"""


def test_network_fs_reads_the_longest_mount_prefix() -> None:
    assert network_fs("/home/u/.cache", MOUNTINFO) == "nfs4"
    assert network_fs("/home", MOUNTINFO) == "nfs4"
    assert network_fs("/tmp", MOUNTINFO) is None
    assert network_fs("/homes/u", MOUNTINFO) is None
    assert network_fs("/home/local disk/cache", MOUNTINFO) is None
