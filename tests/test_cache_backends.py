"""The response cache backends: the shared contract, LRU and age eviction, the 7-day purge,
file safety and processes sharing one file."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import sqlite3
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from xueqiu_api._paths import cache_dir
from xueqiu_api._sqlite import StoreStatus, open_db
from xueqiu_api.cache import (
    CACHE_FILE,
    PURGE_EVERY_S,
    RETENTION_S,
    CacheBackend,
    CacheEntry,
    CacheStats,
    MemoryCache,
    SqliteCache,
)

WORKER = Path(__file__).with_name("cache_worker.py")
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes and fork")
T0 = 1_793_000_000.0
"""2026-10-27, a Tuesday."""
DAY = 86400.0


def entry(fetched_at: float = T0, endpoint: str = "kline", size: int = 100) -> CacheEntry:
    return CacheEntry(os.urandom(size), fetched_at, endpoint)


@pytest.fixture(params=["memory", "sqlite"])
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> CacheBackend:
    if request.param == "memory":
        return MemoryCache()
    return SqliteCache(tmp_path / "cache" / "responses.sqlite3")


def read(path: Path, sql: str) -> list[Any]:
    """Rows of ``sql`` on a separate read-only connection."""
    conn = open_db(path, busy_timeout_s=5, readonly=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ---- the contract of both backends


def test_get_put_and_replace(backend: CacheBackend) -> None:
    first, second = entry(), entry(T0 + 1, "f10_company")
    assert backend.get("a") is None

    backend.put("a", first)
    assert backend.get("a") == first
    backend.put("a", second)
    assert backend.get("a") == second
    assert backend.stats().entries == 1


def test_delete(backend: CacheBackend) -> None:
    backend.put("a", entry())
    backend.put("b", entry())
    backend.delete("a")
    backend.delete("missing")

    assert backend.get("a") is None
    assert backend.get("b") is not None
    assert backend.stats().entries == 1


def test_clear_returns_counts(backend: CacheBackend) -> None:
    for index in range(3):
        backend.put(f"kline-{index}", entry(endpoint="kline"))
    for index in range(2):
        backend.put(f"company-{index}", entry(endpoint="f10_company"))

    assert backend.clear("kline") == 3
    assert backend.clear("kline") == 0
    assert backend.stats().entries == 2
    assert backend.get("company-0") is not None
    assert backend.get("kline-0") is None
    assert backend.clear() == 2
    assert backend.stats().entries == 0
    assert backend.clear() == 0


def test_stats_bytes_follow_the_bodies(backend: CacheBackend) -> None:
    empty = backend.stats()
    backend.put("a", entry(size=40_000))
    full = backend.stats()
    backend.delete("a")

    assert full.entries == 1
    assert full.bytes - empty.bytes > 30_000
    assert backend.stats().bytes < full.bytes
    if isinstance(backend, MemoryCache):
        assert (empty, full, backend.stats()) == (CacheStats(0, 0), CacheStats(1, 40_000), empty)


# ---- MemoryCache


def test_memory_drops_the_least_recently_used_entry() -> None:
    cache = MemoryCache(max_bytes=300)
    for key in "abc":
        cache.put(key, entry(size=100))
    assert cache.get("a") is not None
    cache.put("d", entry(size=100))

    assert cache.get("b") is None
    assert [key for key in "acd" if cache.get(key) is not None] == ["a", "c", "d"]
    assert cache.stats() == CacheStats(3, 300)


def test_memory_keeps_no_entry_larger_than_the_limit() -> None:
    cache = MemoryCache(max_bytes=300)
    cache.put("a", entry(size=100))
    cache.put("big", entry(size=301))
    assert cache.stats() == CacheStats(0, 0)


# ---- SqliteCache


def test_the_default_file_is_in_the_user_cache_directory(tmp_path: Path) -> None:
    cache = SqliteCache()
    assert cache.path == cache_dir() / CACHE_FILE == cache_dir() / "responses-v1.sqlite3"
    assert cache.path.is_relative_to(tmp_path)
    assert not cache.path.exists()

    cache.put("a", entry())
    assert cache.path.exists()
    assert cache.status() == StoreStatus(str(cache.path), True, None, "wal")


@POSIX
def test_the_file_and_its_directory_are_private(tmp_path: Path) -> None:
    previous = os.umask(0o022)
    try:
        cache = SqliteCache()
        cache.put("a", entry())
    finally:
        os.umask(previous)
    names = (CACHE_FILE, f"{CACHE_FILE}-wal", f"{CACHE_FILE}-shm")
    modes = {path.name: stat.S_IMODE(path.stat().st_mode) for path in cache.path.parent.iterdir()}
    assert modes == dict.fromkeys(names, 0o600)
    assert stat.S_IMODE(cache.path.parent.stat().st_mode) == 0o700


def test_bodies_are_stored_compressed(tmp_path: Path) -> None:
    cache = SqliteCache(tmp_path / "responses.sqlite3")
    body = json.dumps({"data": {"items": [{"symbol": "SH600000"}] * 500}}).encode()
    cache.put("a", CacheEntry(body, T0, "kline"))

    [(stored,)] = read(cache.path, "SELECT length(body) FROM responses")
    assert stored < len(body) / 10
    assert cache.get("a") == CacheEntry(body, T0, "kline")


def test_eviction_deletes_the_entries_fetched_first_down_to_90_percent(tmp_path: Path) -> None:
    # Four bodies fit a 4 KiB page, so a batch of 32 frees about 3% of the limit.
    limit, count, page = 1024 * 1024, 1400, 4096
    cache = SqliteCache(tmp_path / "responses.sqlite3", max_bytes=limit)
    cache.put("fetched-last", entry(T0 + 10_000, size=900))
    evictions = 0
    for index in range(count):
        before = cache.stats().entries
        cache.put(f"key-{index:04}", entry(T0 + index, size=900))
        stats = cache.stats()
        assert stats.bytes <= limit
        if stats.entries <= before:
            evictions += 1
            assert limit * 0.9 - 16 * page < stats.bytes <= limit * 0.9
            assert (before + 1 - stats.entries) % 32 == 0

    keys = [key for (key,) in read(cache.path, "SELECT key FROM responses ORDER BY fetched_at")]
    assert evictions > 1
    assert keys[-1] == "fetched-last"
    assert keys[:-1] == [f"key-{index:04}" for index in range(count - len(keys) + 1, count)]


def test_old_entries_are_purged_on_the_first_write_and_then_hourly(tmp_path: Path) -> None:
    path = tmp_path / "responses.sqlite3"
    cache = SqliteCache(path)

    def keys() -> set[str]:
        return {key for (key,) in read(path, "SELECT key FROM responses")}

    cache.put("old", entry(T0 - RETENTION_S - 1))
    assert keys() == {"old"}
    cache.put("edge", entry(T0 - RETENTION_S + PURGE_EVERY_S))
    cache.put("now", entry(T0 + PURGE_EVERY_S))
    assert keys() == {"edge", "now"}

    cache.put("late", entry(T0 - 30 * DAY))
    cache.put("soon", entry(T0 + 2 * PURGE_EVERY_S - 1))
    assert keys() == {"edge", "now", "late", "soon"}
    cache.put("hour", entry(T0 + 2 * PURGE_EVERY_S))
    assert keys() == {"now", "soon", "hour"}

    cache.put("late", entry(T0 - 30 * DAY))
    SqliteCache(path).put("fresh", entry(T0 + 2 * PURGE_EVERY_S))
    assert keys() == {"now", "soon", "hour", "fresh"}


def test_reads_never_write(tmp_path: Path) -> None:
    cache = SqliteCache(tmp_path / "responses.sqlite3")
    for index in range(5):
        cache.put(f"key-{index}", entry(T0 + index, size=5000))
    files = [cache.path, Path(f"{cache.path}-wal")]

    def digests() -> list[str]:
        return [hashlib.sha256(path.read_bytes()).hexdigest() for path in files]

    before = digests()
    for index in range(10):
        cache.get(f"key-{index}")
    cache.stats()
    assert digests() == before


def test_clearing_everything_vacuums_and_clearing_one_endpoint_does_not(tmp_path: Path) -> None:
    cache = SqliteCache(tmp_path / "responses.sqlite3")
    for index in range(10):
        cache.put(f"kline-{index}", entry(size=20_000, endpoint="kline"))
        cache.put(f"fund-{index}", entry(size=20_000, endpoint="fund_detail"))

    assert cache.clear("kline") == 10
    assert read(cache.path, "PRAGMA freelist_count") != [(0,)]
    assert cache.clear() == 10
    assert read(cache.path, "PRAGMA freelist_count") == [(0,)]


class RefusingVacuum(sqlite3.Connection):
    """A connection that fails ``VACUUM`` as a lock held by another process would."""

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if sql == "VACUUM":
            raise sqlite3.OperationalError("database is locked")
        return super().execute(sql, parameters)


def test_a_vacuum_that_cannot_run_leaves_the_cache_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    connect = functools.partial(sqlite3.connect, factory=RefusingVacuum)
    monkeypatch.setattr(sqlite3, "connect", connect)
    cache = SqliteCache(tmp_path / "responses.sqlite3")
    cache.put("a", entry())
    cache.put("b", entry())

    with caplog.at_level(logging.WARNING, logger="xueqiu_api"):
        assert cache.clear() == 2
        cache.put("c", entry())
    assert cache.get("c") is not None
    assert cache.status().available
    assert caplog.records == []


NOT_A_DATABASE = b"These bytes are not a SQLite database.\n" * 100


def test_a_file_that_is_not_a_database_is_left_alone_until_removed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "cache" / "responses.sqlite3"
    path.parent.mkdir()
    path.write_bytes(NOT_A_DATABASE)
    cache = SqliteCache(path)

    with caplog.at_level(logging.WARNING, logger="xueqiu_api"):
        assert cache.get("a") is None
        cache.put("a", entry())
        assert cache.get("a") is None
        assert cache.stats() == CacheStats(0, 0)
        assert cache.clear() == 0
    assert path.read_bytes() == NOT_A_DATABASE
    assert sorted(item.name for item in path.parent.iterdir()) == ["responses.sqlite3"]
    assert cache.status().reason == "DatabaseError SQLITE_NOTADB"
    assert len(caplog.records) == 1

    cache.remove_files()
    stored = entry()
    cache.put("a", stored)
    assert cache.get("a") == stored
    assert cache.status() == StoreStatus(str(path), True, None, "wal")


@POSIX
def test_a_fork_child_reopens_an_inherited_cache(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(WORKER), "fork", str(tmp_path / "responses.sqlite3")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["child_exit"] == 0
    assert result["bodies"] == {"parent-1": "parent 1", "child": "child", "parent-2": "parent 2"}
    assert result["parent_kept_its_connection"] is True
    assert result["available"] is True
    assert result["integrity"] == [["ok"]]


@pytest.fixture
def children() -> Iterator[list[subprocess.Popen[str]]]:
    started: list[subprocess.Popen[str]] = []
    yield started
    for child in started:
        if child.poll() is None:
            child.kill()
            child.communicate()


def test_processes_sharing_one_file_see_no_errors(
    tmp_path: Path, children: list[subprocess.Popen[str]]
) -> None:
    count, ops = 4, 500
    path = tmp_path / "cache" / "responses.sqlite3"
    SqliteCache(path).stats()
    for index in range(count):
        children.append(
            subprocess.Popen(
                [sys.executable, str(WORKER), "mixed", str(path), str(index), str(ops)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    give_up = time.monotonic() + 120
    while not all((path.parent / f"ready-{index}").exists() for index in range(count)):
        exited = [child for child in children if child.poll() is not None]
        assert not exited, exited[0].communicate()[1]
        assert time.monotonic() < give_up, "the children never reached the barrier"
        time.sleep(0.005)
    (path.parent / "go").touch()
    outputs = [child.communicate(timeout=120) for child in children]

    results = [json.loads(out) for out, _ in outputs]
    for child, (_, err) in zip(children, outputs, strict=True):
        assert child.returncode == 0, err
    assert [result["errors"] for result in results] == [[]] * count
    assert sum(result["foreign_reads"] for result in results) > 0
    assert read(path, "PRAGMA integrity_check") == [("ok",)]
