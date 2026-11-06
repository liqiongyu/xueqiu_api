"""Response cache backends: an in-process LRU, a SQLite file that processes share, and the
protocol a caller's own backend follows.

A backend stores what the client gives it and judges nothing: the client decides what is
fresh from ``CacheEntry.fetched_at`` and its own clock.
"""

from __future__ import annotations

import contextlib
import os
import threading
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ._paths import cache_dir
from ._sqlite import Db, StoreStatus

if TYPE_CHECKING:
    import sqlite3

__all__ = ["CacheBackend", "CacheEntry", "CacheStats", "MemoryCache", "SqliteCache"]

CACHE_FILE = "responses-v1.sqlite3"
"""File name in the user cache directory; a new schema gets a new name."""
RETENTION_S = 7 * 86400
"""A ``SqliteCache`` deletes entries fetched longer ago than this."""
PURGE_EVERY_S = 3600
"""How often a ``SqliteCache`` looks for such entries, after its first write."""

_SETUP = (
    "CREATE TABLE IF NOT EXISTS responses ("
    "key TEXT PRIMARY KEY, endpoint TEXT NOT NULL, fetched_at REAL NOT NULL, body BLOB NOT NULL)",
    "CREATE INDEX IF NOT EXISTS responses_fetched_at ON responses (fetched_at)",
)
_EVICT_BATCH = 32
_EVICT_TO = 0.9
"""Eviction stops at this share of ``max_bytes``."""


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One stored response."""

    body: bytes
    """The upstream body in its stored form."""
    fetched_at: float
    """When the upstream answered, in seconds since the epoch on the client's wall clock."""
    endpoint: str


@dataclass(frozen=True, slots=True)
class CacheStats:
    """How much a cache holds."""

    entries: int
    bytes: int
    """What ``max_bytes`` bounds: the bodies a ``MemoryCache`` holds, or the database pages a
    ``SqliteCache`` uses, schema included."""


class CacheBackend(Protocol):
    """Where a client keeps responses, under opaque hex keys.

    Clients call the methods from any thread, so they must be thread-safe; they may block.
    """

    def get(self, key: str) -> CacheEntry | None: ...

    def put(self, key: str, entry: CacheEntry) -> None: ...

    def delete(self, key: str) -> None: ...

    def clear(self, endpoint: str | None = None) -> int:
        """Delete every entry, or those of ``endpoint``; return how many were deleted."""
        ...

    def stats(self) -> CacheStats: ...


class MemoryCache:
    """A cache in this process that drops the least recently used entries once the bodies
    exceed ``max_bytes``."""

    def __init__(self, *, max_bytes: int = 32 * 2**20) -> None:
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._bytes = 0

    def get(self, key: str) -> CacheEntry | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: str, entry: CacheEntry) -> None:
        with self._lock:
            self._pop(key)
            self._entries[key] = entry
            self._bytes += len(entry.body)
            while self._bytes > self.max_bytes:
                _, dropped = self._entries.popitem(last=False)
                self._bytes -= len(dropped.body)

    def delete(self, key: str) -> None:
        with self._lock:
            self._pop(key)

    def clear(self, endpoint: str | None = None) -> int:
        with self._lock:
            keys = [
                key
                for key, entry in self._entries.items()
                if endpoint is None or entry.endpoint == endpoint
            ]
            for key in keys:
                self._pop(key)
            return len(keys)

    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(len(self._entries), self._bytes)

    def _pop(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= len(entry.body)


class SqliteCache:
    """A cache in one SQLite file that processes share; by default ``responses-v1.sqlite3``
    in the user cache directory.

    Bodies are stored zlib-compressed. A write that takes the pages in use past
    ``max_bytes`` deletes the entries fetched first, 32 at a time, down to 90% of it. Entries
    fetched more than 7 days before the entry being written are deleted on the first write
    and then hourly, both measured on the ``fetched_at`` of the writes. Reads never write.

    While the file cannot be used, a ``MemoryCache`` in this process stands in for it and
    ``status()`` says why; the file is tried again after 30 seconds and never deleted, except
    by ``remove_files``. ``close()`` releases the file; the next call opens it again.
    """

    def __init__(
        self, path: str | os.PathLike[str] | None = None, *, max_bytes: int = 128 * 2**20
    ) -> None:
        self.path = cache_dir() / CACHE_FILE if path is None else Path(path).absolute()
        self.max_bytes = max_bytes
        self._db = self._new_db()
        self._purged_at: float | None = None
        self._fallback = MemoryCache()

    def get(self, key: str) -> CacheEntry | None:
        query = "SELECT body, fetched_at, endpoint FROM responses WHERE key = ?"
        rows = self._db.run(lambda conn: conn.execute(query, (key,)).fetchall())
        if rows is None:
            return self._fallback.get(key)
        if not rows:
            return None
        [(body, fetched_at, endpoint)] = rows
        return CacheEntry(zlib.decompress(body), fetched_at, endpoint)

    def put(self, key: str, entry: CacheEntry) -> None:
        body = zlib.compress(entry.body, 1)

        def write(conn: sqlite3.Connection) -> bool:
            conn.execute(
                "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?)",
                (key, entry.endpoint, entry.fetched_at, body),
            )
            if self._purged_at is None or entry.fetched_at >= self._purged_at + PURGE_EVERY_S:
                cutoff = entry.fetched_at - RETENTION_S
                conn.execute("DELETE FROM responses WHERE fetched_at < ?", (cutoff,))
                self._purged_at = entry.fetched_at
            if _usage(conn) > self.max_bytes:
                self._evict(conn)
            return True

        if self._db.run(write) is None:
            self._fallback.put(key, entry)

    def delete(self, key: str) -> None:
        self._fallback.delete(key)
        self._db.run(lambda conn: conn.execute("DELETE FROM responses WHERE key = ?", (key,)))

    def clear(self, endpoint: str | None = None) -> int:
        """Delete every entry, or those of ``endpoint``, and return how many. Clearing
        everything ends with a ``VACUUM``, given up when another connection keeps the file
        locked."""

        def remove(conn: sqlite3.Connection) -> int:
            import sqlite3

            if endpoint is not None:
                sql = "DELETE FROM responses WHERE endpoint = ?"
                return conn.execute(sql, (endpoint,)).rowcount
            removed = conn.execute("DELETE FROM responses").rowcount
            # Locked by another connection: later writes reuse the free pages.
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("VACUUM")
            return removed

        return (self._db.run(remove) or 0) + self._fallback.clear(endpoint)

    def stats(self) -> CacheStats:
        def count(conn: sqlite3.Connection) -> CacheStats:
            [(entries,)] = conn.execute("SELECT count(*) FROM responses").fetchall()
            return CacheStats(entries, _usage(conn))

        return self._db.run(count) or self._fallback.stats()

    def status(self) -> StoreStatus:
        return self._db.status()

    def close(self) -> None:
        self._db.close()

    def remove_files(self) -> None:
        """Delete the file with its ``-wal`` and ``-shm``; the next write creates a new one.

        For a file that is not a database, on the user's request (``xueqiu cache clear``):
        another process may be using a database at this path.
        """
        self._db.close()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.path}{suffix}").unlink(missing_ok=True)
        self._db = self._new_db()
        self._purged_at = None

    def _new_db(self) -> Db:
        return Db(self.path, busy_timeout_s=1.0, setup=_SETUP, label="response cache")

    def _evict(self, conn: sqlite3.Connection) -> None:
        oldest = (
            "DELETE FROM responses WHERE key IN "
            "(SELECT key FROM responses ORDER BY fetched_at LIMIT ?)"
        )
        while _usage(conn) > self.max_bytes * _EVICT_TO:
            if conn.execute(oldest, (_EVICT_BATCH,)).rowcount == 0:
                return


def _usage(conn: sqlite3.Connection) -> int:
    """Bytes of the pages in use: what the file would hold after a ``VACUUM``."""
    [(pages,)] = conn.execute("PRAGMA page_count").fetchall()
    [(free,)] = conn.execute("PRAGMA freelist_count").fetchall()
    [(size,)] = conn.execute("PRAGMA page_size").fetchall()
    return (pages - free) * size
