"""Per-host limiter, per-call deadline and circuit breaker, under threads and tasks."""

from __future__ import annotations

import asyncio
import functools
import itertools
import math
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx2
import pytest

from xueqiu_api import AsyncClient, Blocked, Client, RateLimited, UpstreamUnavailable
from xueqiu_api._async import AsyncRuntime
from xueqiu_api._core import HostLimiter
from xueqiu_api._shared import STATE_FILE, SharedState
from xueqiu_api._sqlite import open_db
from xueqiu_api._sync import SyncRuntime

from .harness import FakeClock, FakeUpstream, Harness, fixture_reply, json_reply, text_reply

STOCK = "stock.xueqiu.com"
WWW = "www.xueqiu.com"
DANJUAN = "danjuanfunds.com"
QUOTEC = "/v5/stock/realtime/quotec.json"
KLINE = "/v5/stock/chart/kline.json"
WEB_PATH = "/query/v1/suggest_stock.json"
FUND_PATH = "/djapi/fund/008975"

Calls = list[tuple[str, tuple[Any, ...], dict[str, Any]]]


def gaps(times: Sequence[float]) -> list[float]:
    ordered = sorted(times)
    return [later - earlier for earlier, later in itertools.pairwise(ordered)]


@pytest.fixture(params=["memory", "shared"])
def mode(request: pytest.FixtureRequest) -> str:
    """Limits kept in process, or in the state file shared with other processes."""
    return request.param


@pytest.fixture
def make(make: Callable[..., Harness], mode: str) -> Callable[..., Harness]:
    return functools.partial(make, shared=mode == "shared")


IN_MEMORY = pytest.mark.parametrize("mode", ["memory"])
"""For concurrent callers: the FakeClock gives each its own time, and the shared file would
take a write from a caller further ahead for a wall clock set back."""


@IN_MEMORY
def test_concurrent_calls_are_spaced_per_host(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))
    h.upstream.route(WWW, WEB_PATH, json_reply({"data": []}))
    h.upstream.route(DANJUAN, FUND_PATH, json_reply({"data": {}, "result_code": 0}))
    calls: Calls = [("quotec", ("SH600000",), {}) for _ in range(6)]
    calls += [("request", (), {"host": WWW, "path": WEB_PATH}) for _ in range(3)]
    calls += [("request", (), {"host": DANJUAN, "path": FUND_PATH}) for _ in range(3)]

    results = h.concurrently(calls)

    assert not [result for result in results if isinstance(result, Exception)]
    stock = [sent.at for sent in h.upstream.requests(STOCK)]
    web = [sent.at for sent in h.upstream.requests(WWW, WEB_PATH)]
    home = [sent.at for sent in h.upstream.requests("xueqiu.com", "/")]
    danjuan = [sent.at for sent in h.upstream.requests(DANJUAN)]
    assert len(stock) == 6 and len(web) == 3 and len(home) == 1 and len(danjuan) == 3
    assert gaps(stock) == pytest.approx([1.0] * 5)
    assert gaps(home + web) == pytest.approx([3.0] * 3)
    assert gaps(danjuan) == pytest.approx([1.0] * 2)
    # Hosts do not wait for each other: every host starts at the same time.
    assert min(stock) == min(home) == min(danjuan)


@IN_MEMORY
def test_longer_intervals_can_be_configured(make: Callable[..., Harness]) -> None:
    h = make(min_intervals={STOCK: 2.5})
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))

    h.concurrently([("quotec", ("SH600000",), {}) for _ in range(3)])

    assert gaps([sent.at for sent in h.upstream.sent]) == pytest.approx([2.5, 2.5])


@pytest.mark.parametrize(
    "overrides", [{STOCK: 0.5}, {WWW: 2.0}, {"example.com": 5.0}, {STOCK: math.nan}]
)
def test_intervals_cannot_be_shortened(
    make: Callable[..., Harness], overrides: dict[str, float]
) -> None:
    with pytest.raises(ValueError):
        make(min_intervals=overrides)


@IN_MEMORY
def test_calls_that_cannot_start_before_the_deadline_fail_fast(h: Harness) -> None:
    quotes = fixture_reply("quotec/cn_hk_us")
    read_timeouts: list[float] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        read_timeouts.append(request.extensions["timeout"]["read"])
        return quotes(request)

    h.upstream.route(STOCK, QUOTEC, reply)

    results = h.concurrently([("quotec", ("SH600000",), {}) for _ in range(40)])

    limited = [result for result in results if isinstance(result, RateLimited)]
    assert len(h.upstream.sent) == 30
    assert len(limited) == 10
    assert all(error.retry_after_s == 30.0 for error in limited)
    assert all("local queue" in error.message for error in limited)
    assert max(sent.at for sent in h.upstream.sent) - min(sent.at for sent in h.upstream.sent) == 29
    # The last slot still leaves its request a second before the 30 s deadline.
    assert min(read_timeouts) == pytest.approx(1.0)


@IN_MEMORY
def test_a_shorter_deadline_limits_queueing(make: Callable[..., Harness]) -> None:
    h = make(deadline=2.5)
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))

    results = h.concurrently([("quotec", ("SH600000",), {}) for _ in range(5)])

    assert sum(isinstance(result, RateLimited) for result in results) == 3
    assert len(h.upstream.sent) == 2


def test_retries_wait_for_the_host_interval(h: Harness) -> None:
    h.upstream.route(WWW, WEB_PATH, json_reply({}, 500), json_reply({}, 502), json_reply({}))

    h.request(host=WWW, path=WEB_PATH)

    home = h.upstream.requests("xueqiu.com", "/")
    attempts = h.upstream.requests(WWW, WEB_PATH)
    assert gaps([sent.at for sent in home + attempts]) == pytest.approx([3.0, 3.0, 3.0])


def test_retry_that_would_miss_the_deadline_reports_the_failure(
    make: Callable[..., Harness],
) -> None:
    h = make(deadline=1.5)
    h.upstream.route(STOCK, QUOTEC, json_reply({}, 500))

    with pytest.raises(UpstreamUnavailable):
        h.quotec("SH600000")

    # The retry would start at 1 s, leaving less than half the 1.5 s deadline for it.
    assert len(h.upstream.sent) == 1


def test_a_retry_waits_as_long_as_retry_after_asks(h: Harness) -> None:
    h.upstream.route(
        STOCK, QUOTEC, json_reply({}, 503, {"Retry-After": "4"}), fixture_reply("quotec/cn_hk_us")
    )

    assert len(h.quotec("SH600000")) == 4

    assert gaps([sent.at for sent in h.upstream.sent]) == pytest.approx([4.0])


def test_a_retry_after_past_the_deadline_is_not_attempted(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTEC, json_reply({}, 503, {"Retry-After": "120"}))

    with pytest.raises(UpstreamUnavailable) as caught:
        h.quotec("SH600000")

    assert caught.value.retry_after_s == 120.0
    assert len(h.upstream.sent) == 1


def test_a_rate_limit_holds_the_host_until_retry_after(h: Harness) -> None:
    h.upstream.route(
        STOCK, QUOTEC, json_reply({}, 429, {"Retry-After": "60"}), fixture_reply("quotec/cn_hk_us")
    )
    h.upstream.route(DANJUAN, FUND_PATH, json_reply({"data": {}, "result_code": 0}))

    with pytest.raises(RateLimited):
        h.quotec("SH600000")
    with pytest.raises(RateLimited) as held:
        h.quotec("SH600000")
    # Other hosts are not held.
    h.request(host=DANJUAN, path=FUND_PATH)
    h.clock.advance(60.0)
    quotes = h.quotec("SH600000")

    assert held.value.retry_after_s == 60.0
    assert len(quotes) == 4
    assert len(h.upstream.requests(STOCK)) == 2


# Tasks only: threads do not interleave deterministically on the fake clock.
@pytest.mark.parametrize("kind", ["async"])
@IN_MEMORY
def test_calls_queued_behind_a_rate_limited_one_are_held(h: Harness) -> None:
    quotes = fixture_reply("quotec/cn_hk_us")
    h.upstream.route(STOCK, QUOTEC, quotes, json_reply({}, 429), quotes)
    h.quotec("SH600000")

    # Both tasks book a slot and sleep; the first gets the 429, then the second wakes up.
    results = h.concurrently([("quotec", ("SH600000",), {}) for _ in range(2)])

    assert [type(result) for result in results] == [RateLimited, RateLimited]
    assert "held" in str(results[1])
    assert len(h.upstream.sent) == 2


def test_the_async_deadline_bounds_a_response_still_arriving(tmp_path: Path) -> None:
    async def slow(request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(5)
        return httpx2.Response(200, json={})

    async def main() -> tuple[float, UpstreamUnavailable]:
        runtime = AsyncRuntime(transport=httpx2.MockTransport(slow))
        session = tmp_path / "session.json"
        async with AsyncClient(session_path=session, deadline=0.3, _runtime=runtime) as client:
            start = time.monotonic()
            with pytest.raises(UpstreamUnavailable) as caught:
                await client.quotec("SH600000")
            return time.monotonic() - start, caught.value

    elapsed, error = asyncio.run(main())

    assert "timed out" in error.message
    assert elapsed < 1.5


def test_the_sync_deadline_gives_up_a_body_still_arriving(tmp_path: Path) -> None:
    def drip() -> Iterator[bytes]:
        for _ in range(40):
            time.sleep(0.05)
            yield b" "

    runtime = SyncRuntime(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, content=drip()))
    )
    session = tmp_path / "session.json"
    with Client(session_path=session, deadline=0.3, _runtime=runtime) as client:
        start = time.monotonic()
        with pytest.raises(UpstreamUnavailable) as caught:
            client.quotec("SH600000")
        elapsed = time.monotonic() - start

    assert "timed out" in caught.value.message
    assert elapsed < 1.5


def test_waiting_for_the_session_cookies_ends_at_the_deadline(tmp_path: Path) -> None:
    upstream = FakeUpstream(FakeClock())
    session = tmp_path / "session.json"
    runtime = SyncRuntime(transport=upstream.transport)

    with (
        Client(session_path=session, deadline=0.2, _runtime=runtime) as client,
        # As if another call were fetching the cookies.
        client._session_lock,  # pyright: ignore[reportPrivateUsage]
    ):
        start = time.monotonic()
        with pytest.raises(RateLimited) as caught:
            client.kline("SH600000")
        elapsed = time.monotonic() - start

    assert "fetched the session cookies" in caught.value.message
    assert 0.1 < elapsed < 1.5
    assert upstream.sent == []


def test_tasks_waiting_for_the_session_cookies_end_at_the_deadline(tmp_path: Path) -> None:
    upstream = FakeUpstream(FakeClock())
    session = tmp_path / "session.json"
    runtime = AsyncRuntime(transport=upstream.transport)

    async def main() -> tuple[float, RateLimited]:
        async with (
            AsyncClient(session_path=session, deadline=0.2, _runtime=runtime) as client,
            client._session_lock,  # pyright: ignore[reportPrivateUsage]
        ):
            start = time.monotonic()
            with pytest.raises(RateLimited) as caught:
                await client.kline("SH600000")
            return time.monotonic() - start, caught.value

    elapsed, error = asyncio.run(main())

    assert "fetched the session cookies" in error.message
    assert 0.1 < elapsed < 1.5
    assert upstream.sent == []


def test_breaker_fails_fast_then_recovers_after_the_cooldown(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTEC, text_reply("teapot", 418), fixture_reply("quotec/cn_hk_us"))
    h.upstream.route(DANJUAN, FUND_PATH, json_reply({"data": {}, "result_code": 0}))

    with pytest.raises(Blocked):
        h.quotec("SH600000")
    h.clock.advance(299.0)
    with pytest.raises(Blocked) as open_breaker:
        h.quotec("SH600000")
    # Other hosts are not affected.
    h.request(host=DANJUAN, path=FUND_PATH)
    h.clock.advance(1.0)
    quotes = h.quotec("SH600000")

    assert open_breaker.value.retry_after_s == pytest.approx(1.0)
    assert len(quotes) == 4
    assert len(h.upstream.requests(STOCK)) == 2


def test_breaker_reopens_when_the_host_still_blocks(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTEC, text_reply("teapot", 418))

    with pytest.raises(Blocked):
        h.quotec("SH600000")
    h.clock.advance(300.0)
    with pytest.raises(Blocked):
        h.quotec("SH600000")
    with pytest.raises(Blocked) as again:
        h.quotec("SH600000")

    assert again.value.retry_after_s == 300.0
    assert len(h.upstream.sent) == 2


def test_limiter_books_slots_and_never_books_past_the_deadline() -> None:
    limiter = HostLimiter({STOCK: 1.0})

    assert limiter.reserve(STOCK, now=0.0, earliest=0.0, deadline=30.0) == (True, 0.0)
    assert limiter.reserve(STOCK, now=0.0, earliest=0.0, deadline=30.0) == (True, 1.0)
    assert limiter.reserve(STOCK, now=0.0, earliest=5.0, deadline=30.0) == (True, 5.0)
    assert limiter.reserve(STOCK, now=0.0, earliest=0.0, deadline=5.5) == (False, 6.0)
    assert limiter.reserve(STOCK, now=0.0, earliest=0.0, deadline=6.0) == (True, 6.0)


@pytest.mark.parametrize("mode", ["shared"])
def test_clients_on_one_state_file_share_pauses_and_open_breakers(
    make: Callable[..., Harness],
) -> None:
    first, second = make(), make()
    first.upstream.route(STOCK, QUOTEC, json_reply({}, 429, {"Retry-After": "60"}))
    first.upstream.route(DANJUAN, FUND_PATH, text_reply("teapot", 418))
    with pytest.raises(RateLimited):
        first.quotec("SH600000")
    with pytest.raises(Blocked):
        first.request(host=DANJUAN, path=FUND_PATH)
    sent = len(first.upstream.sent)

    with pytest.raises(RateLimited) as paused:
        second.quotec("SH600000")
    with pytest.raises(Blocked) as open_breaker:
        second.request(host=DANJUAN, path=FUND_PATH)

    assert len(first.upstream.sent) == sent == 2
    assert paused.value.retry_after_s == 60.0
    assert open_breaker.value.retry_after_s == 300.0


@pytest.mark.parametrize("kind", ["async"])
def test_only_the_shared_state_sends_async_work_to_threads(
    h: Harness, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: set[str] = set()
    to_thread = asyncio.to_thread

    async def spy(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        started.add(fn.__name__)
        return await to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    h.upstream.route(
        STOCK, KLINE, json_reply({"error_code": 60201}), fixture_reply("kline/sh600000_day")
    )

    # A bootstrap, a failure, a refresh that waits for its www.xueqiu.com slot, and the status.
    assert len(h.kline("SH600000")) == 3
    h.run("status")

    shared = {
        "_book",
        "_check_host",
        "_note_failure",
        "_status",
        "adopt_saved",
        "lease_home",
        "release_home",
        "set_anonymous",
    }
    assert started == (shared if mode == "shared" else set())


def test_a_booking_that_waits_for_the_state_file_leaves_the_event_loop_free(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / STATE_FILE
    first = SharedState(path, clock=time.monotonic, wall=time.time)
    assert first.book(STOCK, 1.0, 0.0, math.inf) is not None
    first.close()
    holder = open_db(path, busy_timeout_s=1)
    release = threading.Timer(0.5, holder.execute, ("ROLLBACK",))
    upstream = FakeUpstream(FakeClock())
    upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))
    runtime = AsyncRuntime(transport=upstream.transport, state_path=path)

    async def main() -> tuple[float, float]:
        lags: list[float] = []

        async def heartbeat() -> None:
            while True:
                start = time.monotonic()
                await asyncio.sleep(0.01)
                lags.append(time.monotonic() - start - 0.01)

        async with AsyncClient(session_path=tmp_path / "session.json", _runtime=runtime) as client:
            beat = asyncio.create_task(heartbeat())
            while not lags:
                await asyncio.sleep(0.01)
            holder.execute("BEGIN IMMEDIATE")
            release.start()
            start = time.monotonic()
            await client.quotec("SH600000")
            took = time.monotonic() - start
            beat.cancel()
        return took, max(lags)

    try:
        took, lag = asyncio.run(main())
    finally:
        release.cancel()
        if release.is_alive():
            release.join()
        holder.close()

    # The booking waited for the lock, and the loop went on meanwhile.
    assert took >= 0.4
    assert lag < 0.1
    assert len(upstream.sent) == 1
