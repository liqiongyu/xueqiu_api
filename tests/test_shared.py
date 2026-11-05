"""The state file other processes share: slots, clock rollback, pauses, breakers and leases."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from xueqiu_api._core import HostLimiter
from xueqiu_api._paths import cache_dir
from xueqiu_api._shared import (
    HOME_LEASE_S,
    MAX_PAUSE_S,
    STATE_FILE,
    SharedBreaker,
    SharedLimiter,
    SharedState,
    default_state_path,
)
from xueqiu_api._sqlite import open_db

from .harness import FakeClock

HOST = "stock.xueqiu.com"
EPOCH = 1_790_000_000.0
WALLS = pytest.mark.parametrize(
    "offset", [0.0, EPOCH], ids=["wall equals the clock", "wall at an epoch offset"]
)
NOT_A_DATABASE = b"These bytes are not a SQLite database.\n" * 100

MakeState = Callable[..., SharedState]


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "state" / STATE_FILE


@pytest.fixture
def make_state(clock: FakeClock, path: Path) -> Iterator[MakeState]:
    """SharedStates on ``path`` with the FakeClock as clock and, by default, as wall clock."""
    made: list[SharedState] = []

    def factory(**options: Any) -> SharedState:
        options = {"clock": clock.now, "wall": clock.now, **options}
        state = SharedState(path, **options)
        made.append(state)
        return state

    yield factory
    for state in made:
        state.close()


def rows(path: Path, table: str) -> list[tuple[Any, ...]]:
    conn = open_db(path, busy_timeout_s=1, readonly=True)
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    finally:
        conn.close()


def test_the_state_file_is_in_the_cache_directory() -> None:
    assert default_state_path() == cache_dir() / "state-v1.sqlite3"


@WALLS
def test_a_wall_clock_set_back_keeps_the_queued_slots(
    clock: FakeClock, make_state: MakeState, offset: float
) -> None:
    wall = [offset]
    state = make_state(wall=lambda: clock.now() + wall[0])
    now = clock.now()

    booked = [state.book(HOST, 1.0, now, now + 100) for _ in range(10)]
    wall[0] -= 3600

    assert booked == [(True, pytest.approx(float(slot))) for slot in range(10)]
    assert state.book(HOST, 1.0, now, now + 100) == (True, pytest.approx(10.0))
    assert state.book(HOST, 1.0, now, now + 100) == (True, pytest.approx(11.0))


@WALLS
def test_a_booking_past_its_deadline_writes_nothing(
    clock: FakeClock, make_state: MakeState, path: Path, offset: float
) -> None:
    state = make_state(wall=lambda: clock.now() + offset)
    now = clock.now()
    assert state.book(HOST, 1.0, now + 5, now + 100) == (True, pytest.approx(5.0))
    before = rows(path, "hosts")
    clock.advance(0.5)

    assert state.book(HOST, 1.0, now, now + 5.9) == (False, pytest.approx(5.5))

    assert rows(path, "hosts") == before
    assert state.book(HOST, 1.0, now, now + 7) == (True, pytest.approx(5.5))


def test_two_states_on_one_file_share_the_slots_and_a_limiter_keeps_them_without_it(
    clock: FakeClock, make_state: MakeState, path: Path
) -> None:
    limiter = SharedLimiter({HOST: 1.0}, make_state(busy_timeout_s=0.1))
    other = make_state()
    now = clock.now()

    assert limiter.reserve(HOST, now=now, earliest=now, deadline=now + 100) == (True, 0.0)
    assert other.book(HOST, 1.0, now, now + 100) == (True, 1.0)
    assert limiter.reserve(HOST, now=now, earliest=now, deadline=now + 100) == (True, 2.0)
    holder = open_db(path, busy_timeout_s=1)
    holder.execute("BEGIN IMMEDIATE")
    try:
        # The file is locked, so the slot comes from the limiter's own copy of the queue.
        assert limiter.reserve(HOST, now=now, earliest=now, deadline=now + 100) == (True, 3.0)
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_a_limiter_counts_its_slot_from_the_end_of_the_wait_for_the_file(
    clock: FakeClock, make_state: MakeState, path: Path
) -> None:
    def slow_wall() -> float:
        clock.advance(0.5)  # the time another process held the file's lock
        return clock.now()

    limiter = SharedLimiter({HOST: 1.0}, make_state(wall=slow_wall, busy_timeout_s=0.1))
    now = clock.now()
    assert limiter.reserve(HOST, now=now, earliest=now, deadline=now + 100) == (True, 0.0)
    holder = open_db(path, busy_timeout_s=1)
    holder.execute("BEGIN IMMEDIATE")
    try:
        now = clock.now()
        assert limiter.reserve(HOST, now=now, earliest=now, deadline=now + 100) == (True, 1.0)
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_a_long_pause_is_capped_in_the_file_but_not_in_the_process(
    clock: FakeClock, make_state: MakeState
) -> None:
    limiter = SharedLimiter({HOST: 1.0}, make_state())
    other = make_state()
    now = clock.now()

    limiter.pause(HOST, now + 7200)

    assert limiter.paused(HOST, now) == 7200
    assert other.holds(HOST) == (0.0, MAX_PAUSE_S)


def test_pauses_and_open_breakers_report_the_larger_of_the_process_and_the_file(
    clock: FakeClock, make_state: MakeState
) -> None:
    first, second = make_state(), make_state()
    limiter, breaker = SharedLimiter({HOST: 1.0}, first), SharedBreaker(300.0, first)
    other_limiter, other_breaker = SharedLimiter({HOST: 1.0}, second), SharedBreaker(300.0, second)
    now = clock.now()
    limiter.pause(HOST, now + 5)
    breaker.trip(HOST, now)
    assert (other_limiter.paused(HOST, now), other_breaker.remaining(HOST, now)) == (5, 300)

    clock.advance(100)
    now = clock.now()
    other_limiter.pause(HOST, now + 60)
    other_breaker.trip(HOST, now)

    assert limiter.paused(HOST, now) == 60
    assert breaker.remaining(HOST, now) == 300
    assert first.holds(HOST) == second.holds(HOST) == (300, 60)
    limiter.pause(HOST, now + 1)
    assert second.holds(HOST) == (300, 60)


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
        "not a database",
        "locked",
    ],
)
def test_an_unusable_file_leaves_the_limiter_in_process_until_the_reprobe(
    clock: FakeClock, make_state: MakeState, path: Path, case: str
) -> None:
    store = path.parent
    store.mkdir()
    now = clock.now()
    holder = None
    before: list[tuple[Any, ...]] = []
    if case == "read-only directory":
        store.chmod(0o500)
    elif case == "not a database":
        path.write_bytes(NOT_A_DATABASE)
    else:
        assert make_state().book(HOST, 20.0, now, now) == (True, 0.0)
        before = rows(path, "hosts")
        holder = open_db(path, busy_timeout_s=1)
        holder.execute("BEGIN IMMEDIATE")
    state = make_state(busy_timeout_s=0.1)
    limiter = SharedLimiter({HOST: 20.0}, state)
    try:
        assert state.book(HOST, 20.0, now, now + 100) is None
        assert not state.status().available
        assert limiter.reserve(HOST, now=now, earliest=now, deadline=now + 100) == (True, 0.0)
        assert limiter.reserve(HOST, now=now, earliest=now, deadline=now + 100) == (True, 20.0)
        if case == "read-only directory":
            assert list(store.iterdir()) == []
        elif case == "not a database":
            assert path.read_bytes() == NOT_A_DATABASE
        else:
            assert rows(path, "hosts") == before
    finally:
        store.chmod(0o700)
        if holder is not None:
            holder.execute("ROLLBACK")
            holder.close()
    if case == "not a database":
        path.unlink()

    clock.advance(30)
    now = clock.now()

    # The in-process slots run until now + 10, so the file's first slot comes after them.
    assert limiter.reserve(HOST, now=now, earliest=now, deadline=now + 100) == (True, 10.0)
    assert state.status().available
    [(host, next_at, *_)] = rows(path, "hosts")
    assert (host, next_at) == (HOST, now + 30)


def test_a_held_lease_gives_its_remaining_seconds_until_it_expires(
    clock: FakeClock, make_state: MakeState
) -> None:
    first, second = make_state(), make_state()
    now = clock.now()

    assert first.lease("home", now + 10) == 0.0
    assert first.lease("home", now + 10) == 0.0
    assert second.lease("home", now + 10) == 10.0
    clock.advance(4)
    assert second.lease("home", clock.now() + 10) == 6.0
    clock.advance(6)
    assert second.lease("home", clock.now() + 10) == 0.0
    assert first.lease("home", clock.now() + 10) == 10.0


def test_only_the_holder_releases_a_lease(
    clock: FakeClock, make_state: MakeState, path: Path
) -> None:
    first, second = make_state(), make_state()
    now = clock.now()
    assert first.lease("home", now + 10) == 0.0
    before = rows(path, "leases")

    second.release("home")
    assert rows(path, "leases") == before
    assert second.lease("home", now + 10) == 10.0

    first.release("home")
    assert rows(path, "leases") == []
    assert second.lease("home", now + 10) == 0.0


@pytest.mark.skipif(sys.platform == "win32", reason="Windows relies on lease expiry alone")
def test_a_lease_of_an_exited_process_is_taken_at_once(
    clock: FakeClock, make_state: MakeState, path: Path
) -> None:
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait()
    state = make_state()
    now = clock.now()
    assert state.lease("first", now) == 0.0
    conn = open_db(path, busy_timeout_s=1)
    try:
        conn.executemany(
            "INSERT INTO leases VALUES (?, ?, ?)",
            [("home", f"{child.pid}:exited", now + 100), ("init", "1:running", now + 100)],
        )
    finally:
        conn.close()

    assert state.lease("home", now + 10) == 0.0
    # Probing pid 1 raises PermissionError unless the tests run as root: alive either way.
    assert state.lease("init", now + 10) == 100.0


def test_the_home_lease_ends_at_the_deadline_or_after_home_lease_s(
    clock: FakeClock, make_state: MakeState
) -> None:
    limiter = SharedLimiter({HOST: 1.0}, make_state())
    other = make_state()
    now = clock.now()

    assert limiter.lease_home(now + 30) == 0.0
    assert other.lease("home", now + 30) == HOME_LEASE_S
    clock.advance(HOME_LEASE_S)
    assert other.lease("home", clock.now() + 30) == 0.0
    other.release("home")

    now = clock.now()
    assert limiter.lease_home(now + 5) == 0.0
    assert other.lease("home", now + 30) == 5.0
    limiter.release_home()
    assert other.lease("home", now + 30) == 0.0


def test_an_in_process_limiter_has_no_home_lease() -> None:
    limiter = HostLimiter({HOST: 1.0})
    assert limiter.lease_home(1e9) is None
    limiter.release_home()
