"""The response cache in the call path of both clients: hits, freshness, login isolation,
fallback on errors, and what is never stored."""

from __future__ import annotations

import asyncio
import copy
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest

from xueqiu_api import (
    AsyncClient,
    Blocked,
    InvalidParams,
    RateLimited,
    SymbolNotFound,
    UpstreamChanged,
    UpstreamUnavailable,
    XueqiuError,
)
from xueqiu_api._freshness import MAX_ENTRY_BYTES, cache_key, encode_body
from xueqiu_api._model import UpstreamModel
from xueqiu_api._status import CacheStatus
from xueqiu_api._time import to_ms
from xueqiu_api.cache import RETENTION_S, CacheBackend, CacheEntry, MemoryCache, SqliteCache
from xueqiu_api.endpoints.kline import ENDPOINT as KLINE
from xueqiu_api.symbols import SymbolParams, normalize_symbol

from .harness import (
    ANON_COOKIES,
    FAKE_LOGIN,
    FakeClock,
    FakeUpstream,
    Harness,
    Register,
    Reply,
    danjuan_error_reply,
    error_reply,
    fixture_reply,
    json_reply,
    raise_reply,
    replay_body,
    synthetic_endpoint,
    text_reply,
    waf_reply,
)

STOCK = "stock.xueqiu.com"
WWW = "www.xueqiu.com"
DANJUAN = "danjuanfunds.com"
QUOTEC = "/v5/stock/realtime/quotec.json"
STOCKS = "/v5/stock/portfolio/stock/list.json"
FUND_NAV = "/djapi/fund/nav/history/008975"
SYNTHETIC = "/v5/synthetic.json"
SESSION = datetime(2026, 11, 5, 2, 0, tzinfo=UTC)
"""Thursday 10:00 in Shanghai, while the CN market trades."""
SATURDAY = datetime(2026, 11, 7, 2, 0, tzinfo=UTC)
OTHER_LOGIN = "xq_a_token=fake-login-token-0002; u=fake-login-u-0002"
WATCHLIST = {"pid": -1, "category": "stock"}

Cached = Callable[..., Harness]


@pytest.fixture
def cached(make: Callable[..., Harness], clock: FakeClock) -> Cached:
    """Clients with a cache, a ``MemoryCache`` unless one is given, whose wall clock reads
    ``at`` now and follows the fake clock."""

    def factory(
        *, at: datetime = SESSION, cache: CacheBackend | None = None, **options: Any
    ) -> Harness:
        offset = at.timestamp() - clock.now()
        backend = MemoryCache() if cache is None else cache
        return make(cache=backend, wall=lambda: offset + clock.now(), **options)

    return factory


def kline_reply(h: Harness, *replies: Reply) -> None:
    h.upstream.route(STOCK, KLINE.path, *(replies or (fixture_reply("kline/sh600000_day"),)))


def endpoints_in(path: Path) -> list[str]:
    """The endpoint column of every row in a cache file, read on a connection of its own."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return [endpoint for (endpoint,) in conn.execute("SELECT endpoint FROM responses")]
    finally:
        conn.close()


def kline_key() -> str:
    validated = KLINE.validate({"symbol": "SH600000"})
    return cache_key("kline", validated.model_dump(mode="json", exclude_none=True), "anonymous")


# ---- hits


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_a_second_identical_call_comes_from_the_cache(
    cached: Cached, clock: FakeClock, tmp_path: Path, backend: str
) -> None:
    cache = MemoryCache() if backend == "memory" else SqliteCache(tmp_path / "responses.sqlite3")
    h = cached(cache=cache)
    kline_reply(h)

    first = h.kline("SH600000")
    sent, sleeps = list(h.upstream.sent), list(clock.sleeps)
    second = h.kline("600000")

    assert h.upstream.sent == sent
    assert clock.sleeps == sleeps
    assert second == first
    assert (first.from_cache, second.from_cache, second.stale) == (False, True, False)
    assert first.fetched_at == second.fetched_at == SESSION
    status = h.run("status").cache
    assert isinstance(status, CacheStatus)
    assert (status.backend, status.entries) == (type(cache).__name__, 1)
    assert (status.hits, status.misses, status.stale) == (1, 1, 0)
    if isinstance(cache, SqliteCache):
        assert status.store is not None and status.store.path == str(cache.path)
    else:
        assert status.store is None


@pytest.mark.parametrize("kind", ["async"])
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_the_async_client_sends_only_blocking_backends_to_threads(
    cached: Cached, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    started: list[str] = []
    to_thread = asyncio.to_thread

    async def spy(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        started.append(fn.__name__)
        return await to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    cache = MemoryCache() if backend == "memory" else SqliteCache(tmp_path / "responses.sqlite3")
    h = cached(cache=cache)
    kline_reply(h)

    h.kline("SH600000")
    h.kline("SH600000")
    h.run("status")

    threaded = ["_from_entry", "_store", "_from_entry", "_status"]
    assert started == ([] if backend == "memory" else threaded)


def test_a_client_without_a_cache_stamps_its_results_and_has_no_cache_status(
    make: Callable[..., Harness],
) -> None:
    h = make(wall=lambda: SESSION.timestamp())
    kline_reply(h)

    bars = h.kline("SH600000", max_age=600)
    again = h.kline("SH600000")

    assert (bars.fetched_at, bars.from_cache, again.from_cache) == (SESSION, False, False)
    assert len(h.upstream.requests(STOCK, KLINE.path)) == 2
    assert h.run("status").cache is None


# ---- freshness


@pytest.mark.parametrize(
    ("at", "endpoint", "params", "fresh_s", "stale_s"),
    [
        (SESSION, "kline", {}, 59, 61),
        (SATURDAY, "kline", {}, 899, 901),
        # Daily bars of a range that ended before the exchange date are daily.
        (SESSION, "kline", {"end": date(2026, 11, 4)}, 21_599, 21_601),
        (SESSION, "daily", {}, 21_599, 21_601),
        (SESSION, "static", {}, 86_399, 86_401),
    ],
    ids=["intraday-in-session", "intraday-saturday", "kline-closed-range", "daily", "static"],
)
def test_a_result_is_fresh_for_the_time_of_its_freshness(
    cached: Cached,
    clock: FakeClock,
    register_endpoint: Register,
    at: datetime,
    endpoint: str,
    params: dict[str, Any],
    fresh_s: float,
    stale_s: float,
) -> None:
    """``endpoint`` is kline, or the freshness of a synthetic endpoint."""
    h = cached(at=at)
    if endpoint == "kline":
        kline_reply(h)
        path, name, params = KLINE.path, "kline", {"symbol": "SH600000", **params}
    else:
        register_endpoint(synthetic_endpoint(freshness=endpoint))
        h.upstream.route(STOCK, SYNTHETIC, json_reply({"data": {"value": 7}, "error_code": 0}))
        path, name, params = SYNTHETIC, "quote_synthetic", {"symbol": "SH600000"}

    def call() -> Any:
        return h.call(name, **params)

    first = call()
    clock.advance(fresh_s)
    hit = call()
    clock.advance(stale_s - fresh_s)
    refetched = call()

    assert (hit.from_cache, refetched.from_cache) == (True, False)
    assert hit.fetched_at == first.fetched_at
    assert len(h.upstream.requests(STOCK, path)) == 2


def test_realtime_results_are_never_stored(cached: Cached) -> None:
    cache = MemoryCache()
    h = cached(cache=cache)
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))

    first = h.quotec("SH600000")
    second = h.quotec("SH600000")

    assert len(h.upstream.requests(STOCK, QUOTEC)) == 2
    assert (first.from_cache, second.from_cache) == (False, False)
    assert cache.stats().entries == 0


def test_identical_realtime_calls_in_flight_share_one_request(cached: Cached) -> None:
    h = cached()
    body = replay_body("quotec/cn_hk_us")

    def slow(request: httpx2.Request) -> httpx2.Response:
        # Threads overlap only in real time: the fake clock does not block them.
        time.sleep(0.3)
        return httpx2.Response(200, json=body)

    h.upstream.route(STOCK, QUOTEC, slow)

    results = h.concurrently([("quotec", ("SH600000",), {}), ("quotec", ("SH600000",), {})])

    assert len(h.upstream.requests(STOCK, QUOTEC)) == 1
    first, second = results
    assert first == second and len(first) == 4
    assert first.fetched_at == second.fetched_at


def test_a_call_that_outlives_its_deadline_waiting_for_a_shared_request_is_rate_limited(
    upstream: FakeUpstream, clock: FakeClock, session_path: Path
) -> None:
    h = Harness(
        "sync",
        upstream=upstream,
        clock=clock,
        cache=MemoryCache(),
        deadline=0.5,
        session_path=session_path,
    )
    entered, release = threading.Event(), threading.Event()
    body = replay_body("quotec/cn_hk_us")

    def held(request: httpx2.Request) -> httpx2.Response:
        entered.set()
        release.wait(10)
        return httpx2.Response(200, json=body)

    upstream.route(STOCK, QUOTEC, held)
    leader = threading.Thread(target=h.quotec, args=("SH600000",))
    leader.start()
    try:
        assert entered.wait(10)
        with pytest.raises(RateLimited) as caught:
            h.quotec("SH600000")
    finally:
        release.set()
        leader.join(10)
        h.close()

    assert "in flight" in caught.value.message
    assert len(upstream.requests(STOCK, QUOTEC)) == 1


@pytest.mark.parametrize("kind", ["async"])
def test_cancelling_the_call_that_sent_a_shared_request_leaves_it_to_the_others(
    cached: Cached,
) -> None:
    h = cached()
    body = replay_body("quotec/cn_hk_us")
    client = h.client
    assert isinstance(client, AsyncClient)

    async def scenario() -> tuple[Any, bool]:
        sent, release = asyncio.Event(), asyncio.Event()

        async def held(request: httpx2.Request) -> httpx2.Response:
            sent.set()
            await release.wait()
            return httpx2.Response(200, json=body)

        # The mock transport of the async client awaits the coroutine a reply returns.
        h.upstream.route(STOCK, QUOTEC, cast(Reply, held))
        leader = asyncio.ensure_future(client.quotec("SH600000"))
        await sent.wait()
        follower = asyncio.ensure_future(client.quotec("SH600000"))
        await asyncio.sleep(0)
        leader.cancel()
        await asyncio.sleep(0)
        release.set()
        rows = await follower
        await asyncio.wait([leader])
        return rows, leader.cancelled()

    runner = h._runner  # pyright: ignore[reportPrivateUsage]
    assert runner is not None
    rows, cancelled = runner.run(scenario())

    assert cancelled
    assert len(rows) == 4
    assert len(h.upstream.requests(STOCK, QUOTEC)) == 1


# ---- per-call control


def test_max_age_0_refetches_and_stores_the_new_result(cached: Cached, clock: FakeClock) -> None:
    h = cached()
    kline_reply(h)

    h.kline("SH600000")
    at_once = h.kline("SH600000", max_age=0)
    clock.advance(10)
    renewed = h.kline("SH600000", max_age=0)
    clock.advance(10)
    hit = h.kline("SH600000")

    assert len(h.upstream.requests(STOCK, KLINE.path)) == 3
    assert (at_once.from_cache, renewed.from_cache) == (False, False)
    assert hit.from_cache is True
    assert hit.fetched_at == renewed.fetched_at


def test_max_age_replaces_the_freshness_both_ways(cached: Cached, clock: FakeClock) -> None:
    h = cached()
    kline_reply(h)

    h.kline("SH600000")
    clock.advance(120)
    older = h.kline("SH600000", max_age=300)
    younger = h.kline("SH600000", max_age=60)

    assert older.from_cache is True
    assert younger.from_cache is False
    assert len(h.upstream.requests(STOCK, KLINE.path)) == 2


def test_cache_false_neither_reads_nor_writes(cached: Cached) -> None:
    cache = MemoryCache()
    h = cached(cache=cache)
    kline_reply(h)

    h.kline("SH600000", cache=False)
    assert cache.stats().entries == 0
    h.kline("SH600000")
    bypassed = h.call("kline", symbol="SH600000", cache=False)

    assert bypassed.from_cache is False
    assert len(h.upstream.requests(STOCK, KLINE.path)) == 3


@pytest.mark.parametrize("max_age", [-1, float("nan")])
def test_a_negative_max_age_is_invalid_params(cached: Cached, max_age: float) -> None:
    h = cached()

    with pytest.raises(InvalidParams) as caught:
        h.kline("SH600000", max_age=max_age)

    assert caught.value.field == "max_age"
    assert h.upstream.sent == []


@pytest.mark.parametrize("seconds", [0, -5])
def test_stale_if_error_must_be_positive(cached: Cached, seconds: float) -> None:
    with pytest.raises(ValueError, match="stale_if_error"):
        cached(stale_if_error=seconds)


# ---- login isolation


def test_results_sent_with_login_cookies_stay_off_the_file(
    cached: Cached, clock: FakeClock, tmp_path: Path
) -> None:
    path = tmp_path / "responses.sqlite3"
    h = cached(cache=SqliteCache(path), token=FAKE_LOGIN)
    kline_reply(h)
    h.upstream.route(STOCK, STOCKS, fixture_reply("watchlist_stocks/all_stocks"))

    h.kline("SH600000")
    assert endpoints_in(path) == ["kline"]
    # Past the host's interval, so that no request waits: an async request in flight sleeps
    # on a fake time of its own, which the calls after it do not see.
    clock.advance(1)
    first = h.call("watchlist_stocks", **WATCHLIST)
    again = h.call("watchlist_stocks", **WATCHLIST)

    assert endpoints_in(path) == ["kline"]
    assert (first.from_cache, again.from_cache) == (False, True)
    assert len(h.upstream.requests(STOCK, STOCKS)) == 1


def test_two_accounts_miss_each_other_even_in_one_private_cache(cached: Cached) -> None:
    first = cached(token=FAKE_LOGIN)
    second = cached(token=OTHER_LOGIN)
    second.client._private = first.client._private  # pyright: ignore[reportPrivateUsage]
    first.upstream.route(STOCK, STOCKS, fixture_reply("watchlist_stocks/all_stocks"))

    first.call("watchlist_stocks", **WATCHLIST)
    other = second.call("watchlist_stocks", **WATCHLIST)

    assert other.from_cache is False
    cookies = [sent.headers["cookie"] for sent in first.upstream.requests(STOCK, STOCKS)]
    assert cookies == [FAKE_LOGIN, OTHER_LOGIN]


@pytest.mark.parametrize("token_first", [True, False], ids=["token-first", "anonymous-first"])
def test_an_anonymous_result_is_shared_by_clients_with_and_without_a_token(
    cached: Cached, token_first: bool
) -> None:
    cache = MemoryCache()
    with_token = cached(cache=cache, token=FAKE_LOGIN)
    without = cached(cache=cache, token="")
    kline_reply(with_token)
    writer, reader = (with_token, without) if token_first else (without, with_token)

    writer.kline("SH600000")
    hit = reader.kline("SH600000")

    assert hit.from_cache is True
    assert len(with_token.upstream.requests(STOCK, KLINE.path)) == 1


# ---- stale_if_error


FAILURES = [
    pytest.param(json_reply({}, 503), UpstreamUnavailable, id="503"),
    pytest.param(text_reply("I'm a teapot", 418), Blocked, id="418-breaker"),
    pytest.param(json_reply({}, 429, {"Retry-After": "600"}), RateLimited, id="429-pause"),
]


@pytest.mark.parametrize(("reply", "error"), FAILURES)
def test_stale_if_error_serves_an_old_result_when_the_upstream_fails(
    cached: Cached, clock: FakeClock, reply: Reply, error: type[XueqiuError]
) -> None:
    h = cached(stale_if_error=3600)
    kline_reply(h, fixture_reply("kline/sh600000_day"), reply)

    first = h.kline("SH600000")
    clock.advance(120)
    stale = h.kline("SH600000")
    sent = len(h.upstream.requests(STOCK, KLINE.path))
    # The open breaker or the paused host now refuses before any request.
    again = h.kline("SH600000")

    assert stale == first
    assert (stale.from_cache, stale.stale, stale.fetched_at) == (True, True, first.fetched_at)
    assert again.stale is True
    if error is not UpstreamUnavailable:
        assert len(h.upstream.requests(STOCK, KLINE.path)) == sent
    status = h.run("status").cache
    assert status is not None and status.stale == 2


@pytest.mark.parametrize(("reply", "error"), FAILURES)
def test_without_stale_if_error_the_failure_is_raised(
    cached: Cached, clock: FakeClock, reply: Reply, error: type[XueqiuError]
) -> None:
    h = cached()
    kline_reply(h, fixture_reply("kline/sh600000_day"), reply)

    h.kline("SH600000")
    clock.advance(120)
    with pytest.raises(error):
        h.kline("SH600000")


def test_stale_if_error_does_not_serve_past_its_age(cached: Cached, clock: FakeClock) -> None:
    h = cached(stale_if_error=100)
    kline_reply(h, fixture_reply("kline/sh600000_day"), json_reply({}, 503))

    h.kline("SH600000")
    clock.advance(120)

    with pytest.raises(UpstreamUnavailable):
        h.kline("SH600000")


def test_stale_if_error_does_not_cover_an_error_of_the_request(
    cached: Cached, clock: FakeClock
) -> None:
    h = cached(stale_if_error=3600)
    kline_reply(h, fixture_reply("kline/sh600000_day"), error_reply("100002", "synthetic", 400))

    h.kline("SH600000")
    clock.advance(120)

    with pytest.raises(XueqiuError) as caught:
        h.kline("SH600000")
    assert caught.value.recoverable == "none"


@pytest.mark.parametrize("how", ["max_age", "stale_if_error"])
def test_a_sqlite_cache_entry_past_its_retention_is_never_served(
    cached: Cached, clock: FakeClock, tmp_path: Path, how: str
) -> None:
    month = 30 * 86400
    h = cached(cache=SqliteCache(tmp_path / "responses.sqlite3"), stale_if_error=month)
    later = json_reply({}, 503) if how == "stale_if_error" else fixture_reply("kline/sh600000_day")
    kline_reply(h, fixture_reply("kline/sh600000_day"), later)
    h.kline("SH600000")
    clock.advance(RETENTION_S + 3600)

    if how == "max_age":
        assert h.kline("SH600000", max_age=month).from_cache is False
    else:
        with pytest.raises(UpstreamUnavailable):
            h.kline("SH600000")


def test_an_unusable_cache_file_leaves_the_cache_in_process(cached: Cached, tmp_path: Path) -> None:
    path = tmp_path / "responses.sqlite3"
    path.write_bytes(b"These bytes are not a SQLite database.\n" * 100)
    h = cached(cache=SqliteCache(path))
    kline_reply(h)

    first = h.kline("SH600000")
    second = h.kline("SH600000")

    assert (first.from_cache, second.from_cache) == (False, True)
    assert len(h.upstream.requests(STOCK, KLINE.path)) == 1
    status = h.run("status").cache
    assert status is not None and status.store is not None
    assert (status.store.available, status.store.reason) == (False, "DatabaseError SQLITE_NOTADB")


def test_an_entry_fetched_after_the_wall_clock_now_is_a_miss(
    make: Callable[..., Harness], clock: FakeClock
) -> None:
    offset = [SESSION.timestamp() - clock.now()]
    h = make(cache=MemoryCache(), wall=lambda: offset[0] + clock.now())
    kline_reply(h)
    h.kline("SH600000")

    offset[0] -= 10

    assert h.kline("SH600000").from_cache is False
    assert len(h.upstream.requests(STOCK, KLINE.path)) == 2


# ---- what is not stored


@pytest.mark.parametrize(("extra", "stored"), [(0, 1), (1, 0)], ids=["at-the-cap", "over-it"])
def test_a_body_over_the_size_cap_in_its_stored_form_is_not_stored(
    cached: Cached, extra: int, stored: int
) -> None:
    cache = MemoryCache()
    h = cached(cache=cache)
    body = copy.deepcopy(replay_body("kline/sh600000_day"))
    body["data"]["pad"] = ""
    body["data"]["pad"] = "x" * (MAX_ENTRY_BYTES - len(encode_body(body)) + extra)
    assert len(encode_body(body)) == MAX_ENTRY_BYTES + extra
    kline_reply(h, json_reply(body))

    h.kline("SH600000")

    assert cache.stats().entries == stored


def test_an_empty_result_is_not_stored(cached: Cached) -> None:
    cache = MemoryCache()
    h = cached(cache=cache)
    empty = {"data": {"symbol": "SH600000", "column": ["timestamp", "close"], "item": []}}
    kline_reply(h, json_reply({**empty, "error_code": 0}))

    assert len(h.kline("SH600000")) == 0
    assert len(h.kline("SH600000")) == 0

    assert len(h.upstream.requests(STOCK, KLINE.path)) == 2
    assert cache.stats().entries == 0


def test_a_body_holding_a_cookie_value_is_not_stored(cached: Cached) -> None:
    cache = MemoryCache()
    h = cached(cache=cache)
    body = copy.deepcopy(replay_body("kline/sh600000_day"))
    body["data"]["echo"] = ANON_COOKIES["xq_a_token"]
    kline_reply(h, json_reply(body))

    h.kline("SH600000")
    h.kline("SH600000")

    assert len(h.upstream.requests(STOCK, KLINE.path)) == 2
    assert cache.stats().entries == 0


class RecordingCache(MemoryCache):
    def __init__(self) -> None:
        super().__init__()
        self.deleted: list[str] = []

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        super().delete(key)


@pytest.mark.parametrize(
    "stored",
    [b"not json", b'{"data": {"column": ["volume"], "item": [[1]]}, "error_code": 0}'],
    ids=["not-json", "wrong-shape"],
)
def test_an_entry_that_no_longer_parses_is_deleted_and_fetched_again(
    cached: Cached, stored: bytes
) -> None:
    cache = RecordingCache()
    h = cached(cache=cache)
    kline_reply(h)
    key = kline_key()
    cache.put(key, CacheEntry(stored, SESSION.timestamp(), "kline"))

    bars = h.kline("SH600000")
    hit = h.kline("SH600000")

    assert cache.deleted == [key]
    assert (bars.from_cache, len(bars)) == (False, 3)
    assert hit.from_cache is True
    assert len(h.upstream.requests(STOCK, KLINE.path)) == 1
    status = h.run("status").cache
    assert status is not None and (status.hits, status.misses) == (1, 1)


class Mended(UpstreamModel):
    value: int
    mended: bool = True


def _mended_parse(payload: Any, params: SymbolParams) -> Mended:
    return Mended.model_validate(payload)


def test_a_new_parse_function_reads_the_stored_body_without_a_request(
    cached: Cached, register_endpoint: Register
) -> None:
    h = cached()
    register_endpoint(synthetic_endpoint(freshness="daily"))
    h.upstream.route(STOCK, SYNTHETIC, json_reply({"data": {"value": 7}, "error_code": 0}))
    first = h.call("quote_synthetic", symbol="SH600000")

    register_endpoint(synthetic_endpoint(freshness="daily", parse=_mended_parse))
    mended = h.call("quote_synthetic", symbol="SH600000")

    assert type(first).__name__ == "SyntheticRow"
    assert isinstance(mended, Mended)
    assert (mended.value, mended.mended, mended.from_cache) == (7, True, True)
    assert len(h.upstream.requests(STOCK, SYNTHETIC)) == 1


class BrokenCache(MemoryCache):
    def get(self, key: str) -> CacheEntry | None:
        raise RuntimeError("synthetic backend failure")

    def put(self, key: str, entry: CacheEntry) -> None:
        raise RuntimeError("synthetic backend failure")


def test_a_failing_backend_is_logged_once_and_calls_go_on(
    cached: Cached, caplog: pytest.LogCaptureFixture
) -> None:
    h = cached(cache=BrokenCache())
    kline_reply(h)

    with caplog.at_level(logging.WARNING, logger="xueqiu_api"):
        assert len(h.kline("SH600000")) == 3
        assert len(h.kline("SH600000")) == 3

    warnings = [r.getMessage() for r in caplog.records if "response cache" in r.getMessage()]
    assert warnings == ["the response cache failed with RuntimeError; calls go on without it"]
    assert len(h.upstream.requests(STOCK, KLINE.path)) == 2


# ---- walks


def one_bar(body: dict[str, Any], stamp_ms: int) -> dict[str, Any]:
    older = copy.deepcopy(body)
    older["data"]["item"] = [[stamp_ms, *older["data"]["item"][0][1:]]]
    return older


OCT_30_MS = 1793289600000
"""2026-10-30 00:00 in Shanghai."""


def test_kline_history_restarts_when_a_hit_first_window_needs_more(
    cached: Cached, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("xueqiu_api.endpoints.kline.MAX_COUNT", 3)
    h = cached()
    full = replay_body("kline/sh600000_day")
    kline_reply(h, json_reply(full), json_reply(full), json_reply(one_bar(full, OCT_30_MS)))
    h.kline("SH600000", count=3)

    bars = h.run("kline_history", "SH600000")

    assert len(h.upstream.requests(STOCK, KLINE.path)) == 3
    assert bars.from_cache is False
    assert [stamp.date() for stamp in bars.timestamps] == [
        date(2026, 10, 30),
        date(2026, 11, 2),
        date(2026, 11, 3),
        date(2026, 11, 4),
    ]


def test_kline_history_of_one_window_may_come_from_the_cache(
    cached: Cached, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("xueqiu_api.endpoints.kline.MAX_COUNT", 3)
    h = cached()
    kline_reply(h, json_reply(one_bar(replay_body("kline/sh600000_day"), OCT_30_MS)))
    first = h.kline("SH600000", count=3)

    bars = h.run("kline_history", "SH600000")

    assert len(h.upstream.requests(STOCK, KLINE.path)) == 1
    assert (bars.from_cache, bars.fetched_at, len(bars)) == (True, first.fetched_at, 1)


@pytest.mark.parametrize(("reply", "error"), FAILURES)
def test_kline_history_raises_when_a_stale_first_window_needs_more(
    cached: Cached,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    reply: Reply,
    error: type[XueqiuError],
) -> None:
    """A stale window is not joined to new ones, and the walk starts again only once."""
    monkeypatch.setattr("xueqiu_api.endpoints.kline.MAX_COUNT", 3)
    h = cached(stale_if_error=3600)
    full = replay_body("kline/sh600000_day")
    kline_reply(h, json_reply(full), json_reply(one_bar(full, OCT_30_MS)), reply)
    assert len(h.run("kline_history", "SH600000")) == 4
    clock.advance(600)
    calls: list[float | None] = []
    call = h.client._call  # pyright: ignore[reportPrivateUsage]

    def counted(*args: Any, max_age: float | None = None, **kwargs: Any) -> Any:
        calls.append(max_age)
        assert len(calls) <= 4, "kline_history keeps starting again"
        return call(*args, max_age=max_age, **kwargs)

    monkeypatch.setattr(h.client, "_call", counted)

    with pytest.raises(error):
        h.run("kline_history", "SH600000")
    assert calls == [None, 0]


def test_a_page_walk_raises_rather_than_join_a_stored_page_to_new_ones(
    cached: Cached, clock: FakeClock
) -> None:
    h = cached(stale_if_error=3600)
    second_pages: list[int] = []

    def nav(request: httpx2.Request) -> httpx2.Response:
        page = request.url.params["page"]
        if page == "2":
            second_pages.append(1)
            if len(second_pages) > 1:
                return httpx2.Response(503, json={})
        return httpx2.Response(200, json=replay_body(f"fund_nav_history/008975_p{page}"))

    h.upstream.route(DANJUAN, FUND_NAV, nav)
    assert [len(page) for page in h.pages("fund_nav_history_pages", "008975")] == [3, 2]
    clock.advance(10)

    with pytest.raises(UpstreamUnavailable):
        h.pages("fund_nav_history_pages", "008975")


def nav_page(request: httpx2.Request) -> httpx2.Response:
    page = request.url.params["page"]
    return httpx2.Response(200, json=replay_body(f"fund_nav_history/008975_p{page}"))


def test_every_page_of_a_walk_is_fetched_and_carries_fetch_facts(cached: Cached) -> None:
    cache = MemoryCache()
    h = cached(cache=cache)
    h.upstream.route(DANJUAN, FUND_NAV, nav_page)

    first = h.pages("fund_nav_history_pages", "008975")
    again = h.pages("fund_nav_history_pages", "008975")

    assert [len(page) for page in first] == [len(page) for page in again] == [3, 2]
    assert len(h.upstream.requests(DANJUAN, FUND_NAV)) == 4
    for page in (*first, *again):
        assert page.fetched_at is not None and page.fetched_at >= SESSION
        assert page.from_cache is False
    assert cache.stats().entries == 2


def daily_bars(symbol: str, first: date, count: int, close: float = 10.0) -> dict[str, Any]:
    start = datetime.combine(first, datetime.min.time(), normalize_symbol(symbol).timezone)
    rows = [[to_ms(start + timedelta(days=day)), *[close] * 4] for day in range(count)]
    data = {"symbol": symbol, "column": ["timestamp", "open", "high", "low", "close"], "item": rows}
    return {"data": data, "error_code": 0}


def test_a_calendar_from_cached_bars_keeps_their_fetch_time(
    cached: Cached, clock: FakeClock
) -> None:
    h = cached()
    h.upstream.route(STOCK, KLINE.path, json_reply(daily_bars("SH000001", date(2020, 1, 1), 1100)))

    first = h.run("trading_calendar", "CN")
    clock.advance(30)
    second = h.run("trading_calendar", "CN")

    assert first.fetched_at == second.fetched_at == SESSION
    assert len(h.upstream.requests(STOCK, KLINE.path)) == 1


def test_factors_take_the_earlier_fetch_time_of_their_two_requests(cached: Cached) -> None:
    h = cached()

    def reply(request: httpx2.Request) -> httpx2.Response:
        close = 20.0 if request.url.params["type"] == "after" else 10.0
        return httpx2.Response(200, json=daily_bars("SH600000", date(2026, 7, 1), 16, close))

    h.upstream.route(STOCK, KLINE.path, reply)

    factors = h.run("adjust_factors", "SH600000", end=date(2026, 7, 16))

    normal, after = h.upstream.requests(STOCK, KLINE.path)
    assert after.at - normal.at == pytest.approx(1.0)
    assert factors.fetched_at == SESSION


def test_a_security_snapshot_keeps_its_pages_out_of_the_cache(
    cached: Cached, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = cached()
    asked: list[bool] = []

    def record(cache: bool) -> None:
        asked.append(cache)
        raise UpstreamChanged("synthetic stop after the first page")

    def call(self: Any, endpoint: str, /, *, cache: bool = True, **params: Any) -> Any:
        record(cache)

    async def async_call(self: Any, endpoint: str, /, *, cache: bool = True, **params: Any) -> Any:
        record(cache)

    monkeypatch.setattr(type(h.client), "call", call if h.kind == "sync" else async_call)

    with pytest.raises(UpstreamChanged):
        h.run("security_snapshot", "CN")
    assert asked == [False]


# ---- failures leave nothing behind


ERRORS = [
    pytest.param(STOCK, error_reply(60201, "synthetic"), UpstreamChanged, id="60201"),
    pytest.param(WWW, error_reply("10022", "synthetic", 400), UpstreamChanged, id="10022"),
    pytest.param(WWW, error_reply("21101", "synthetic", 400), UpstreamChanged, id="21101"),
    pytest.param(STOCK, error_reply("400016", "", 400), UpstreamChanged, id="400016"),
    pytest.param(DANJUAN, danjuan_error_reply(300001, "synthetic"), UpstreamChanged, id="300001"),
    pytest.param(WWW, error_reply("31005", "synthetic", 400), RateLimited, id="31005"),
    pytest.param(STOCK, json_reply({}, 429), RateLimited, id="429"),
    pytest.param(STOCK, text_reply("I'm a teapot", 418), Blocked, id="418"),
    pytest.param(STOCK, waf_reply(403), Blocked, id="403"),
    pytest.param(STOCK, json_reply({}, 503), UpstreamUnavailable, id="5xx"),
    pytest.param(STOCK, raise_reply(httpx2.ReadTimeout), UpstreamUnavailable, id="timeout"),
    pytest.param(
        STOCK,
        json_reply({"error_code": 0, "error_description": ""}),
        UpstreamChanged,
        id="envelope",
    ),
    pytest.param(STOCK, json_reply({"data": {}, "error_code": 0}), SymbolNotFound, id="empty"),
]


@pytest.mark.parametrize(("host", "reply", "error"), ERRORS)
def test_a_failed_call_stores_nothing(
    cached: Cached,
    register_endpoint: Register,
    host: Any,
    reply: Reply,
    error: type[XueqiuError],
) -> None:
    cache = MemoryCache()
    h = cached(cache=cache, stale_if_error=3600)
    envelope = "danjuan" if host == DANJUAN else "xueqiu"
    register_endpoint(synthetic_endpoint(host=host, path=SYNTHETIC, envelope=envelope))
    h.upstream.route(host, SYNTHETIC, reply)

    with pytest.raises(error):
        h.call("quote_synthetic", symbol="SH600000")

    assert cache.stats().entries == 0
    assert h.upstream.requests(host, SYNTHETIC)
