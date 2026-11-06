"""How every SQLite file of the package is opened, and a connection that steps aside on failure.

The opener keeps the files private (directory 0700, files 0600 on POSIX), uses WAL with
``synchronous=NORMAL`` and retries the journal switch and the schema statements while another
process holds the lock, which the busy timeout does not cover. Nothing here deletes a file.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import threading
import time
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, TypeVar

from ._core import log

if TYPE_CHECKING:
    import sqlite3

T = TypeVar("T")

REPROBE_S = 30.0
"""Seconds a ``Db`` leaves its file alone after a failure."""

_RETRY_S = 0.005

NETWORK_FILESYSTEMS = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb3",
        "smbfs",
        "9p",
        "fuse.sshfs",
        "afs",
        "ceph",
        "glusterfs",
        "fuse.glusterfs",
        "lustre",
        "gpfs",
        "beegfs",
    }
)
"""Linux filesystem types on which SQLite's locking and WAL cannot be relied on."""

_ABANDONED: list[sqlite3.Connection] = []
"""Connections given up after a fork or after their file was replaced, kept open until exit.

Closing one in a fork child, or after another file took its name, can damage a database
that is in use, so they are never closed.
"""

_DBS: weakref.WeakSet[Db] = weakref.WeakSet()
"""Every ``Db``, so that a fork child can give each one a new lock."""


def open_db(
    path: str | os.PathLike[str],
    *,
    busy_timeout_s: float,
    setup: Sequence[str] = (),
    readonly: bool = False,
) -> sqlite3.Connection:
    """Open ``path`` in autocommit mode, usable from any thread, and run ``setup`` on it.

    Read-write opens create the directory with mode 0700, take the group and other
    permissions off the file before the first statement, so that a new file is 0600 and
    ``-wal`` and ``-shm`` inherit that, and switch to WAL with ``synchronous=NORMAL``. A file
    without write permission keeps it that way, so the open fails at its first write.
    Read-only opens neither create nor change anything; they go through an encoded ``file:``
    URI, so ``#``, ``?`` and ``%`` stay part of the path.
    """
    import sqlite3

    target = Path(path)
    database: str | Path = target
    pragmas: tuple[str, ...] = ()
    if readonly:
        database = target.resolve().as_uri() + "?mode=ro"
    else:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        pragmas = ("PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL")
    conn = sqlite3.connect(
        database,
        uri=readonly,
        timeout=busy_timeout_s,
        isolation_level=None,
        check_same_thread=False,
    )
    try:
        if not readonly and os.name == "posix":
            mode = stat.S_IMODE(os.stat(target).st_mode)
            if mode & 0o077:
                os.chmod(target, mode & ~0o077)
        deadline = time.monotonic() + busy_timeout_s
        for statement in (*pragmas, *setup):
            _execute(conn, statement, deadline)
    except BaseException:
        conn.close()
        raise
    return conn


def _execute(conn: sqlite3.Connection, statement: str, deadline: float) -> None:
    """Run one statement, retrying while another connection holds a lock it needs."""
    import sqlite3

    while True:
        try:
            conn.execute(statement).fetchall()
            return
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            if time.monotonic() >= deadline:
                raise
        time.sleep(_RETRY_S)


def network_fs(path: str | os.PathLike[str], mountinfo_text: str | None = None) -> str | None:
    """The network filesystem type ``path`` is on, or None.

    ``path`` is an absolute POSIX path without symlinks. The mount table is
    ``/proc/self/mountinfo`` unless ``mountinfo_text`` gives one; without it, platforms
    other than Linux always get None.
    """
    if mountinfo_text is None:
        if not sys.platform.startswith("linux"):
            return None
        try:
            mountinfo_text = Path("/proc/self/mountinfo").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            return None
    target = PurePosixPath(os.fspath(path))
    best: tuple[int, str] | None = None
    for line in mountinfo_text.splitlines():
        fields = line.split(" ")
        try:
            fstype = fields[fields.index("-", 6) + 1]
        except (ValueError, IndexError):
            continue
        mount = PurePosixPath(_unescape(fields[4]))
        if target.is_relative_to(mount) and (best is None or len(mount.parts) >= best[0]):
            best = (len(mount.parts), fstype)
    return best[1] if best is not None and best[1] in NETWORK_FILESYSTEMS else None


def _unescape(field: str) -> str:
    """Undo the octal escapes the kernel writes for spaces, tabs, newlines and backslashes."""
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), field)


@dataclass(frozen=True)
class StoreStatus:
    """What a ``Db`` last saw of its file."""

    path: str
    available: bool
    reason: str | None
    """Why the last attempt failed, such as ``"OperationalError SQLITE_READONLY"``."""
    journal: str | None
    """The journal mode of the open file; ``"wal"`` unless the filesystem refused it."""


class Db:
    """One lazily opened read-write connection to ``path``, shared by threads under a lock.

    ``run(fn)`` calls ``fn`` with the connection and returns its result, or None when the
    file cannot be used: any ``sqlite3.Error`` or ``OSError``, a Python without ``sqlite3``,
    or a Linux network filesystem. A failure logs one warning and leaves the file alone for
    ``REPROBE_S`` seconds on ``clock``; the first run after that tries again and logs the
    recovery. A relative path is made absolute when the ``Db`` is made. The connection is
    reopened after a fork, with a new lock, and when the path names a different file; the old
    one is kept open, never closed.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        busy_timeout_s: float,
        setup: Sequence[str],
        label: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = Path(path).absolute()
        self._busy_timeout_s = busy_timeout_s
        self._setup = tuple(setup)
        self._label = label
        self._clock = clock
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._pid = 0
        self._ident: tuple[int, int] | None = None
        self._failed_at: float | None = None
        self._reason: str | None = None
        self._journal: str | None = None
        _DBS.add(self)

    def run(self, fn: Callable[[sqlite3.Connection], T]) -> T | None:
        with self._lock:
            if self._failed_at is not None and self._clock() - self._failed_at < REPROBE_S:
                return None
            try:
                import sqlite3
            except ImportError as exc:
                return self._fail(type(exc).__name__)
            try:
                conn = self._current()
                if conn is None:
                    # realpath, as Path.resolve raises RuntimeError on a symlink loop up to 3.12.
                    fstype = network_fs(os.path.realpath(self.path))
                    if fstype is not None:
                        return self._fail(
                            f"{fstype} network filesystem; point XDG_CACHE_HOME to a local disk"
                        )
                    conn = self._open()
                result = fn(conn)
            except (sqlite3.Error, OSError) as exc:
                self._drop()
                return self._fail(_describe(exc))
            if self._failed_at is not None:
                log.warning("%s %s is available again", self._label, self.path)
                self._failed_at = self._reason = None
            return result

    @staticmethod
    def renew_locks() -> None:
        """Give every ``Db`` a new lock; runs in a fork child, where the parent's thread that
        may have held the old one does not exist."""
        for db in list(_DBS):
            db._lock = threading.Lock()

    def status(self) -> StoreStatus:
        with self._lock:
            return StoreStatus(str(self.path), self._reason is None, self._reason, self._journal)

    def close(self) -> None:
        with self._lock:
            self._drop()

    def _current(self) -> sqlite3.Connection | None:
        """The open connection, unless it was opened before a fork or the file was replaced."""
        if self._conn is not None and (
            self._pid != os.getpid() or _ident(self.path) != self._ident
        ):
            _ABANDONED.append(self._conn)
            self._conn = None
        return self._conn

    def _open(self) -> sqlite3.Connection:
        conn = open_db(self.path, busy_timeout_s=self._busy_timeout_s, setup=self._setup)
        self._conn, self._pid = conn, os.getpid()
        self._ident = _ident(self.path)
        [(journal,)] = conn.execute("PRAGMA journal_mode").fetchall()
        self._journal = str(journal)
        return conn

    def _drop(self) -> None:
        """Let go of the connection; close it when this process opened it."""
        conn, self._conn = self._conn, None
        if conn is None:
            return
        if self._pid == os.getpid():
            conn.close()
        else:
            _ABANDONED.append(conn)

    def _fail(self, reason: str) -> None:
        if self._failed_at is None:
            log.warning(
                "%s %s is unavailable (%s); trying again in %.0f s",
                self._label,
                self.path,
                reason,
                REPROBE_S,
            )
        self._failed_at = self._clock()
        self._reason = reason


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=Db.renew_locks)


def _ident(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_dev, info.st_ino


def _describe(exc: BaseException) -> str:
    """The exception class and, for SQLite errors, the result code name."""
    name: object = getattr(exc, "sqlite_errorname", None)
    return type(exc).__name__ if name is None else f"{type(exc).__name__} {name}"
