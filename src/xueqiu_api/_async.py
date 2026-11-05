from __future__ import annotations

import asyncio
import contextlib
import functools
import os
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ParamSpec, Self, TypeVar, cast

import httpx2
from pydantic import BaseModel

from ._base import (
    DEFAULT_DEADLINE_S,
    CachePlan,
    ClientBase,
    exchange_yesterday,
    http_result,
    market_symbol,
    no_cookie_jar,
    restamp,
)
from ._core import (
    HOME_CALL,
    MAX_REDIRECTS,
    Call,
    Failure,
    Ok,
    classify,
    network_failure,
    next_step,
    request_headers,
)
from ._endpoints_async import AsyncEndpoints
from ._pages import PageT, PageWalk, start_walk
from ._result import Rows
from ._shared import LEASE_POLL_S, default_state_path
from ._status import ClientStatus
from .adjust import AdjustFactors, derive_factors
from .cache import CacheBackend, MemoryCache
from .endpoints.kline import ENDPOINT as KLINE
from .endpoints.kline import MAX_COUNT, Adjust, BarWalk, Kline, Period
from .endpoints.quote_detail import ENDPOINT as QUOTE_DETAIL
from .errors import XueqiuError
from .registry import Endpoint
from .sites import Host
from .symbols import Market
from .trading_calendar import (
    MarketState,
    TradingCalendar,
    calendar_from_kline,
    market_state_from_quote,
)
from .universe import SecuritySnapshot, Segment, SnapshotWalk

P = TypeVar("P", bound=BaseModel)
R = TypeVar("R")
T = TypeVar("T")
A = ParamSpec("A")


def _wall() -> float:
    """Seconds since the epoch, read through this module's ``datetime`` at call time."""
    return datetime.now(UTC).timestamp()


@dataclass(frozen=True)
class AsyncRuntime:
    """Private injection point for tests and replay: transport, clocks, sleep and state file.

    ``clock`` is monotonic and times waits; ``wall`` is seconds since the epoch.
    ``state_path`` is the file limits are shared through with other processes; None keeps
    them in process.
    """

    transport: httpx2.AsyncBaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = _wall
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    state_path: Path | None = None


class AsyncClient(ClientBase, AsyncEndpoints):
    """Asynchronous (asyncio) client with the same surface as ``Client``; methods are coroutines.

    Concurrent tasks share one per-host limiter, circuit breaker and session. Arguments are
    the same as for ``Client``; the deadline also bounds a response that is still arriving.
    While the shared state file is in use, it and the session file are read and written in
    worker threads, and so is a cache other than a ``MemoryCache``.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        session_path: str | os.PathLike[str] | None = None,
        min_intervals: Mapping[str, float] | None = None,
        deadline: float = DEFAULT_DEADLINE_S,
        cache: CacheBackend | None = None,
        stale_if_error: float | None = None,
        _runtime: AsyncRuntime | None = None,
    ) -> None:
        runtime = _runtime or AsyncRuntime()
        super().__init__(
            token=token,
            session_path=session_path,
            min_intervals=min_intervals,
            deadline=deadline,
            clock=runtime.clock,
            wall=runtime.wall,
            state_path=default_state_path() if _runtime is None else runtime.state_path,
            cache=cache,
            stale_if_error=stale_if_error,
        )
        self._sleep = runtime.sleep
        self._http = httpx2.AsyncClient(
            transport=runtime.transport, cookies=no_cookie_jar(), max_redirects=MAX_REDIRECTS
        )
        self._session_lock = asyncio.Lock()
        self._flights: dict[str, asyncio.Task[tuple[Any, float]]] = {}

    async def kline_history(
        self,
        symbol: str,
        *,
        period: Period = "day",
        adjust: Adjust = "before",
        begin: date | None = None,
        end: date | None = None,
        cache: bool = True,
        max_age: float | None = None,
    ) -> Kline:
        """Every bar of one symbol in a range, oldest first, in as many requests as that takes.

        Only a range one request covers can come from the cache, as a cached window is never
        joined to new ones. The result carries the fetch facts of the first request.
        """
        now = self._now()

        def start() -> BarWalk:
            return BarWalk(symbol, period=period, adjust=adjust, begin=begin, end=end, now=now)

        walk, first = start(), None
        while (params := walk.params) is not None:
            if first is not None and first.from_cache:
                # A second window is needed: start again, this time without reading.
                walk, first, max_age = start(), None, 0
                continue
            bars = await self._call(
                KLINE, params, cache=cache, max_age=max_age if first is None else 0
            )
            walk.accept(bars)
            first = bars if first is None else first
        return restamp(walk.result(), first)

    async def security_snapshot(
        self, market: Market, *, segments: Sequence[Segment] | None = None
    ) -> SecuritySnapshot:
        """Every security listed in ``market``, or in some of its ``segments``.

        One request per page at the host's minimum interval, so a walk takes minutes: 61 requests
        for CN, 29 for HK and 92 for US at the sizes measured on 2026-11-04 and 2026-11-05. An
        error on any page of a list raises and leaves no snapshot; an error on the last request of
        a CN walk, for the ranking's count, only leaves ``screen_total`` None.
        ``SecuritySnapshot.complete`` says whether all rows came;
        ``xueqiu_api.universe.check_snapshot`` checks the rest.
        """
        walk = SnapshotWalk(market, segments=segments, fetched_at=self._now())
        while (request := walk.next_request()) is not None:
            try:
                # Pages fetched at different moments can drop rows, so none comes from the cache.
                walk.feed(await self.call(request.endpoint, cache=False, **request.params))
            except XueqiuError:
                if not request.optional:
                    raise
                walk.skip()
        return walk.result()

    async def trading_calendar(self, market: Market) -> TradingCalendar:
        """Past sessions of ``market``, from one request for the unadjusted daily bars of its
        ``CALENDAR_SOURCES`` symbol."""
        params = {
            "symbol": market_symbol(market),
            "period": "day",
            "adjust": "normal",
            "count": MAX_COUNT,
        }
        bars = await self._call(KLINE, params)
        return calendar_from_kline(bars, fetched_at=self._fetched_at(bars))

    async def market_state(self, market: Market) -> MarketState:
        """Whether ``market`` is trading now, from one quote of its ``CALENDAR_SOURCES`` symbol.

        HK and US read the quote of a stock, so ``traded_today`` can be False on a day the market
        traded while that stock is suspended.
        """
        quote = await self._call(QUOTE_DETAIL, {"symbol": market_symbol(market)})
        return market_state_from_quote(quote, now=self._now())

    async def adjust_factors(self, symbol: str, *, end: date | None = None) -> AdjustFactors:
        """Adjustment factors of ``symbol`` up to ``end``, from its unadjusted and backward-adjusted
        daily bars: two requests, more only for a history one request cannot hold.

        ``end`` defaults to yesterday on the symbol's exchange, because today's bar can change
        between the two requests.
        """
        last = exchange_yesterday(symbol, self._now()) if end is None else end
        normal = await self.kline_history(symbol, adjust="normal", end=last)
        after = await self.kline_history(symbol, adjust="after", end=last)
        fetched_at = min(self._fetched_at(normal), self._fetched_at(after))
        return derive_factors(normal, after, fetched_at=fetched_at)

    async def call(
        self, endpoint: str, /, *, cache: bool = True, max_age: float | None = None, **params: Any
    ) -> Any:
        """Call any registered endpoint by name and return its parsed result; ``cache`` and
        ``max_age`` work as on the endpoint methods."""
        return await self._call(self._endpoint(endpoint), params, cache=cache, max_age=max_age)

    async def request(
        self, *, host: Host, path: str, params: Mapping[str, str | int | float] | None = None
    ) -> Any:
        """GET a path on a known host and return the raw JSON body.

        Goes through the same session, limiter and error handling as the endpoint methods.
        """
        call = self._raw_call(host, path, params)
        return await self._run(call, self._clock() + self._deadline_s)

    async def status(self) -> ClientStatus:
        """What this client knows locally: credentials, the last login error, the limits of
        each host, the shared state file and the response cache. Sends no request."""
        cache = self._cache
        thread = self._state is not None or (cache is not None and _in_threads(cache))
        return await self._offload(thread, self._status)

    async def close(self) -> None:
        await self._http.aclose()
        if self._state is not None:
            await asyncio.to_thread(self._state.close)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _call(
        self,
        spec: Endpoint[P, R],
        params: Mapping[str, Any],
        *,
        cache: bool = True,
        max_age: float | None = None,
    ) -> R:
        deadline = self._clock() + self._deadline_s
        validated = spec.validate(params)
        call = self._endpoint_call(spec, validated)
        plan = self._plan(spec, call, cache, max_age)
        if plan is None:
            body = await self._run(call, deadline)
            return self._fetched(self._finish(spec, validated, call, body), self._wall())
        thread = _in_threads(plan.backend)
        if plan.read:
            found = await self._offload(
                thread, self._from_entry, spec, validated, call, plan, max_age=max_age
            )
            if found is not None:
                return found
        try:
            body, fetched, leader = await self._fly(plan, call, deadline)
        except XueqiuError as error:
            found = await self._offload(
                thread, self._stale_result, spec, validated, call, plan, error
            )
            if found is None:
                raise
            return found
        result = self._fetched(self._finish(spec, validated, call, body), fetched)
        if leader and plan.write:
            await self._offload(thread, self._store, plan, spec, body, result, fetched)
        return result

    async def _rows(
        self,
        spec: Endpoint[P, list[T]],
        params: Mapping[str, Any],
        *,
        cache: bool = True,
        max_age: float | None = None,
    ) -> Rows[T]:
        """``_call`` of an endpoint whose result is a list, which comes back as ``Rows``."""
        return cast(Rows[T], await self._call(spec, params, cache=cache, max_age=max_age))

    async def _fly(self, plan: CachePlan, call: Call, deadline: float) -> tuple[Any, float, bool]:
        """The body of the call, when it came on the wall clock and whether this call sent
        the request; another task's request in flight for the same key is shared.

        The request runs in a task of its own, so that cancelling the call that sent it leaves
        it to the others.
        """
        if not plan.coalesce:
            return await self._run(call, deadline), self._wall(), True
        flight = self._flights.get(plan.key)
        if flight is not None:
            try:
                async with asyncio.timeout(max(0.0, deadline - self._clock())):
                    body, fetched = await asyncio.shield(flight)
            except TimeoutError:
                raise self._flight_wait_error(call) from None
            return body, fetched, False
        flight = self._flights[plan.key] = asyncio.ensure_future(self._fetch(call, deadline))
        flight.add_done_callback(functools.partial(self._landed, plan.key))
        body, fetched = await asyncio.shield(flight)
        return body, fetched, True

    async def _fetch(self, call: Call, deadline: float) -> tuple[Any, float]:
        return await self._run(call, deadline), self._wall()

    def _landed(self, key: str, flight: asyncio.Task[tuple[Any, float]]) -> None:
        """Forget a finished flight, and take its exception in case no caller is left to."""
        if self._flights.get(key) is flight:
            del self._flights[key]
        if not flight.cancelled():
            flight.exception()

    def _pages(
        self,
        spec: Endpoint[P, PageT],
        params: Mapping[str, Any],
        *,
        max_pages: int | None = None,
        max_items: int | None = None,
    ) -> AsyncIterator[PageT]:
        """Pages one at a time, each one call; bad params raise here, before any request."""
        return self._walk(spec, start_walk(spec, params, max_pages, max_items))

    async def _walk(self, spec: Endpoint[P, PageT], walk: PageWalk) -> AsyncIterator[PageT]:
        while walk.params is not None:
            # Every page is fetched anew: pages of different moments can drop or repeat rows.
            page = await self._call(spec, walk.params, max_age=0)
            yield restamp(walk.accept(page), page)

    async def _run(self, call: Call, deadline: float) -> Any:
        thread = self._state is not None
        attempt, refreshed, last = 0, False, None
        earliest = self._clock()
        while True:
            if call.cookies:
                await self._ensure_session(call, deadline)
            generation = self._session.generation
            wait = await self._offload(
                thread, self._book, call, earliest=earliest, deadline=deadline, last=last
            )
            if wait > 0:
                await self._sleep(wait)
                # Another call or process may have paused the host or opened its breaker.
                await self._offload(thread, self._check_host, call)
            outcome = await self._attempt(call, deadline)
            if isinstance(outcome, Ok):
                return outcome.value
            await self._offload(thread, self._note_failure, call, outcome)
            step = next_step(outcome, call=call, attempt=attempt, refreshed=refreshed)
            if isinstance(step, XueqiuError):
                raise step
            if step.refresh:
                await self._refresh(call, generation, deadline)
                refreshed = True
            else:
                attempt += 1
            earliest = self._clock() + step.delay_s
            last = outcome

    async def _attempt(self, call: Call, deadline: float) -> Ok | Failure:
        request = self._http.build_request(
            "GET",
            call.url,
            params=call.query,
            headers=request_headers(call, self._session.cookie_header(call.credentials)),
            timeout=self._timeout(deadline),
        )
        try:
            async with asyncio.timeout(max(0.001, deadline - self._clock())):
                response = await self._http.send(request, follow_redirects=call.home)
        except (httpx2.RequestError, TimeoutError) as exc:
            return network_failure(exc, call)
        result = http_result(call, response, response.content)
        return classify(call, result, self._session.secrets())

    async def _ensure_session(self, call: Call, deadline: float) -> None:
        seen = self._session.generation
        if self._session.ready(call):
            return
        async with self._session_locked(call, deadline):
            if self._session.generation == seen:
                await self._bootstrap(call, deadline)
        if not self._session.ready(call):
            raise self._cookie_error(call)

    async def _refresh(self, call: Call, seen: int, deadline: float) -> None:
        async with self._session_locked(call, deadline):
            if self._session.generation == seen:
                await self._bootstrap(call, deadline)
        if not self._session.ready(call):
            raise self._cookie_error(call)

    @contextlib.asynccontextmanager
    async def _session_locked(self, call: Call, deadline: float) -> AsyncGenerator[None]:
        """Hold the session lock; give up waiting for it at the call's deadline."""
        try:
            async with asyncio.timeout(max(0.0, deadline - self._clock())):
                await self._session_lock.acquire()
        except TimeoutError:
            raise self._session_wait_error(call) from None
        try:
            yield
        finally:
            self._session_lock.release()

    async def _bootstrap(self, call: Call, deadline: float) -> None:
        """Take a newer anonymous set another process saved, or fetch the home page for one.

        One process at a time fetches, under the home lease; the others look for its set
        every ``LEASE_POLL_S`` until the deadline.
        """
        thread = self._state is not None
        while not await self._offload(thread, self._session.adopt_saved):
            left = await self._lease_home(deadline)
            if not left:
                try:
                    if not await self._offload(thread, self._session.adopt_saved):
                        cookies = await self._run(HOME_CALL, deadline)
                        await self._offload(thread, self._session.set_anonymous, cookies)
                finally:
                    await self._release_home()
                return
            pause = min(LEASE_POLL_S, left)
            if self._clock() + pause > deadline:
                raise self._session_wait_error(call)
            await self._sleep(pause)

    async def _lease_home(self, deadline: float) -> float | None:
        """Take the home lease in a thread; a cancellation meanwhile waits for the attempt and
        gives back a lease it took."""
        if self._state is None:
            return self._limiter.lease_home(deadline)
        taking = asyncio.ensure_future(asyncio.to_thread(self._limiter.lease_home, deadline))
        try:
            return await asyncio.shield(taking)
        except asyncio.CancelledError:
            await _wait_out(taking)
            if taking.exception() is None and taking.result() == 0.0:
                await self._release_home()
            raise

    async def _release_home(self) -> None:
        """Give the home lease back in a thread, to the end even when cancelled meanwhile."""
        if self._state is None:
            self._limiter.release_home()
            return
        releasing = asyncio.ensure_future(asyncio.to_thread(self._limiter.release_home))
        try:
            await asyncio.shield(releasing)
        except asyncio.CancelledError:
            await _wait_out(releasing)
            raise

    async def _offload(
        self, thread: bool, fn: Callable[A, R], /, *args: A.args, **kwargs: A.kwargs
    ) -> R:
        """Call ``fn`` in a worker thread when ``thread``, else right here on the loop."""
        if thread:
            return await asyncio.to_thread(fn, *args, **kwargs)
        return fn(*args, **kwargs)


def _in_threads(backend: CacheBackend) -> bool:
    """Whether calls of ``backend`` go to worker threads: all but a ``MemoryCache`` may block."""
    return not isinstance(backend, MemoryCache)


async def _wait_out(task: asyncio.Future[Any]) -> None:
    """Wait for ``task`` while the caller is being cancelled; further cancellations change
    nothing, as the caller raises the first one afterwards."""
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait([task])
