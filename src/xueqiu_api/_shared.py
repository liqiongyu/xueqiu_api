"""Limiter slots, pauses, breakers and the home-page lease, shared by the processes of one user.

One SQLite file in the cache directory holds a row per host and a table of leases. The file
stores wall-clock seconds; callers pass and get times on their own clock, converted inside
the write lock. A row written later than the current wall time means the wall clock went
back, and the row's times move back with it, so queued slots stay queued. When the file
cannot be used the methods return None, and the shared classes keep to their in-process state.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from ._core import CircuitBreaker, HostLimiter
from ._paths import cache_dir
from ._sqlite import Db, StoreStatus

if TYPE_CHECKING:
    import sqlite3

STATE_FILE = "state-v1.sqlite3"
BUSY_TIMEOUT_S = 2.0
MAX_PAUSE_S = 3600.0
"""Longest pause the file keeps; the caller's own pause and retry_after_s are not capped."""
LEASE_POLL_S = 0.25
"""How often a call waiting on another process's home-page fetch looks for its cookies."""
HOME_LEASE_S = 15.0
"""Longest home lease: one 10 s attempt timeout, a queued 3 s www.xueqiu.com slot and a margin."""

_SETUP = (
    "CREATE TABLE IF NOT EXISTS hosts (host TEXT PRIMARY KEY, next_at REAL NOT NULL, "
    "paused_until REAL NOT NULL, open_until REAL NOT NULL, written_at REAL NOT NULL) "
    "WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS leases (name TEXT PRIMARY KEY, holder TEXT NOT NULL, "
    "until REAL NOT NULL) WITHOUT ROWID",
)


def default_state_path() -> Path:
    return cache_dir() / STATE_FILE


class SharedState:
    """The shared state file, read and written with ``clock`` times.

    ``wall`` is the wall clock the file stores. Every method returns None while the file
    cannot be used. A lease belongs to this object in this process: another ``SharedState``,
    even in the same process, is another holder.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        clock: Callable[[], float],
        wall: Callable[[], float],
        busy_timeout_s: float = BUSY_TIMEOUT_S,
    ) -> None:
        self.clock = clock
        self._wall = wall
        self._token = uuid4().hex
        self._db = Db(
            path, busy_timeout_s=busy_timeout_s, setup=_SETUP, label="shared state", clock=clock
        )

    def book(
        self, host: str, interval_s: float, earliest: float, deadline: float
    ) -> tuple[bool, float] | None:
        """Book the first slot at or after ``earliest``; return (booked, wait from now).

        Nothing is written when the slot would start after ``deadline``.
        """

        def run(conn: sqlite3.Connection) -> tuple[bool, float]:
            with _immediate(conn):
                now, next_at, paused, opened = _times(conn, host, self._wall)
                offset = now - self.clock()
                slot = max(now, earliest + offset, next_at)
                if slot > deadline + offset:
                    return False, slot - now
                _put(conn, host, slot + interval_s, paused, opened, now)
                return True, slot - now

        return self._db.run(run)

    def holds(self, host: str) -> tuple[float, float] | None:
        """Seconds the breaker of ``host`` stays open and seconds the host stays paused."""

        def run(conn: sqlite3.Connection) -> tuple[float, float]:
            now, _, paused, opened = _times(conn, host, self._wall)
            return max(0.0, opened - now), max(0.0, paused - now)

        return self._db.run(run)

    def extend(
        self, host: str, column: Literal["paused_until", "open_until"], seconds: float
    ) -> None:
        """Keep ``host`` paused, or its breaker open, for at least ``seconds`` from now.

        Pauses are capped at ``MAX_PAUSE_S``, so an outsized Retry-After cannot hold every
        process for long.
        """
        if column == "paused_until":
            seconds = min(seconds, MAX_PAUSE_S)

        def run(conn: sqlite3.Connection) -> None:
            with _immediate(conn):
                now, next_at, paused, opened = _times(conn, host, self._wall)
                if column == "paused_until":
                    paused = max(paused, now + seconds)
                else:
                    opened = max(opened, now + seconds)
                _put(conn, host, next_at, paused, opened, now)

        self._db.run(run)

    def lease(self, name: str, until: float) -> float | None:
        """Take lease ``name`` until ``until``: 0.0 when taken, else the seconds it stays held.

        A lease can be taken when it expired, is this object's own, or, on POSIX, its holder
        process has exited. Windows relies on expiry alone.
        """
        holder = self._holder()

        def run(conn: sqlite3.Connection) -> float:
            with _immediate(conn):
                row: tuple[str, float] | None = conn.execute(
                    "SELECT holder, until FROM leases WHERE name = ?", (name,)
                ).fetchone()
                now = self._wall()
                if row is not None and row[0] != holder and row[1] > now and _alive(row[0]):
                    return row[1] - now
                conn.execute(
                    "INSERT OR REPLACE INTO leases VALUES (?, ?, ?)",
                    (name, holder, until + now - self.clock()),
                )
                conn.execute("COMMIT")
                return 0.0

        return self._db.run(run)

    def release(self, name: str) -> None:
        """Give lease ``name`` back when this object holds it."""
        holder = self._holder()

        def run(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM leases WHERE name = ? AND holder = ?", (name, holder))

        self._db.run(run)

    def status(self) -> StoreStatus:
        return self._db.status()

    def close(self) -> None:
        self._db.close()

    def _holder(self) -> str:
        """The pid is read at call time, so a fork child is a holder of its own."""
        return f"{os.getpid()}:{self._token}"


@contextmanager
def _immediate(conn: sqlite3.Connection) -> Generator[None]:
    """A transaction that takes the write lock at its start; rolled back unless committed."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def _times(
    conn: sqlite3.Connection, host: str, wall: Callable[[], float]
) -> tuple[float, float, float, float]:
    """The wall time now, then next_at, paused_until and open_until of ``host``.

    Read after the row, so the row cannot be newer than now unless the wall clock went back;
    then the three times move back by as much.
    """
    row: tuple[float, float, float, float] | None = conn.execute(
        "SELECT next_at, paused_until, open_until, written_at FROM hosts WHERE host = ?",
        (host,),
    ).fetchone()
    now = wall()
    if row is None:
        return now, now, now, now
    next_at, paused, opened, written_at = row
    back = max(0.0, written_at - now)
    return now, next_at - back, paused - back, opened - back


def _put(
    conn: sqlite3.Connection,
    host: str,
    next_at: float,
    paused: float,
    opened: float,
    now: float,
) -> None:
    """Write the row of ``host`` and commit."""
    conn.execute(
        "INSERT OR REPLACE INTO hosts VALUES (?, ?, ?, ?, ?)",
        (host, next_at, paused, opened, now),
    )
    conn.execute("COMMIT")


def _alive(holder: str) -> bool:
    """Whether the process of a lease holder may still run.

    Always true on Windows, where ``os.kill`` terminates the process instead of probing it.
    """
    if os.name != "posix":
        return True
    try:
        os.kill(int(holder.split(":", 1)[0]), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


class SharedLimiter(HostLimiter):
    """``HostLimiter`` whose slots and pauses the other processes of the user share.

    The in-process state is kept as well, and used alone while the file cannot be; slots
    booked through the file count there, so a switch either way does not send a burst.
    """

    def __init__(self, intervals: Mapping[str, float], state: SharedState) -> None:
        super().__init__(intervals)
        self._state = state

    def reserve(
        self, host: str, *, now: float, earliest: float, deadline: float
    ) -> tuple[bool, float]:
        with self._lock:
            floor = max(earliest, self._next.get(host, -math.inf))
        interval = self._intervals[host]
        booked = self._state.book(host, interval, floor, deadline)
        if booked is None:
            return super().reserve(host, now=now, earliest=earliest, deadline=deadline)
        if booked[0]:
            # The clock after the booking: the wait counts from inside the file's lock.
            ends = self._state.clock() + booked[1] + interval
            with self._lock:
                self._next[host] = max(ends, self._next.get(host, ends))
        return booked

    def pause(self, host: str, until: float) -> None:
        super().pause(host, until)
        self._state.extend(host, "paused_until", until - self._state.clock())

    def paused(self, host: str, now: float) -> float:
        local = super().paused(host, now)
        held = self._state.holds(host)
        return local if held is None else max(local, held[1])

    def lease_home(self, deadline: float) -> float | None:
        return self._state.lease("home", min(deadline, self._state.clock() + HOME_LEASE_S))

    def release_home(self) -> None:
        self._state.release("home")


class SharedBreaker(CircuitBreaker):
    """``CircuitBreaker`` whose open hosts the other processes of the user share."""

    def __init__(self, cooldown_s: float, state: SharedState) -> None:
        super().__init__(cooldown_s)
        self._state = state

    def remaining(self, host: str, now: float) -> float:
        local = super().remaining(host, now)
        held = self._state.holds(host)
        return local if held is None else max(local, held[0])

    def trip(self, host: str, now: float) -> None:
        super().trip(host, now)
        self._state.extend(host, "open_until", self._cooldown_s)
