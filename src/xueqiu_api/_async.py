from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ParamSpec, Self, TypeVar

import httpx2
from pydantic import BaseModel

from ._base import (
    DEFAULT_DEADLINE_S,
    ClientBase,
    exchange_yesterday,
    http_result,
    market_symbol,
    no_cookie_jar,
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
from ._shared import LEASE_POLL_S, default_state_path
from ._status import ClientStatus
from .adjust import AdjustFactors, derive_factors
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
    worker threads.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        session_path: str | os.PathLike[str] | None = None,
        min_intervals: Mapping[str, float] | None = None,
        deadline: float = DEFAULT_DEADLINE_S,
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
        )
        self._sleep = runtime.sleep
        self._http = httpx2.AsyncClient(
            transport=runtime.transport, cookies=no_cookie_jar(), max_redirects=MAX_REDIRECTS
        )
        self._session_lock = asyncio.Lock()

    async def kline_history(
        self,
        symbol: str,
        *,
        period: Period = "day",
        adjust: Adjust = "before",
        begin: date | None = None,
        end: date | None = None,
    ) -> Kline:
        """Every bar of one symbol in a range, oldest first, in as many requests as that takes."""
        walk = BarWalk(
            symbol, period=period, adjust=adjust, begin=begin, end=end, now=datetime.now(UTC)
        )
        while walk.params is not None:
            walk.accept(await self._call(KLINE, walk.params))
        return walk.result()

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
        walk = SnapshotWalk(market, segments=segments, fetched_at=datetime.now(UTC))
        while (request := walk.next_request()) is not None:
            try:
                walk.feed(await self.call(request.endpoint, **request.params))
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
        return calendar_from_kline(await self._call(KLINE, params), fetched_at=datetime.now(UTC))

    async def market_state(self, market: Market) -> MarketState:
        """Whether ``market`` is trading now, from one quote of its ``CALENDAR_SOURCES`` symbol.

        HK and US read the quote of a stock, so ``traded_today`` can be False on a day the market
        traded while that stock is suspended.
        """
        quote = await self._call(QUOTE_DETAIL, {"symbol": market_symbol(market)})
        return market_state_from_quote(quote, now=datetime.now(UTC))

    async def adjust_factors(self, symbol: str, *, end: date | None = None) -> AdjustFactors:
        """Adjustment factors of ``symbol`` up to ``end``, from its unadjusted and backward-adjusted
        daily bars: two requests, more only for a history one request cannot hold.

        ``end`` defaults to yesterday on the symbol's exchange, because today's bar can change
        between the two requests.
        """
        last = exchange_yesterday(symbol, datetime.now(UTC)) if end is None else end
        normal = await self.kline_history(symbol, adjust="normal", end=last)
        after = await self.kline_history(symbol, adjust="after", end=last)
        return derive_factors(normal, after, fetched_at=datetime.now(UTC))

    async def call(self, endpoint: str, /, **params: Any) -> Any:
        """Call any registered endpoint by name and return its parsed result."""
        return await self._call(self._endpoint(endpoint), params)

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
        each host and the shared state file. Sends no request."""
        return await self._offload(self._state is not None, self._status)

    async def close(self) -> None:
        await self._http.aclose()
        if self._state is not None:
            await asyncio.to_thread(self._state.close)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _call(self, spec: Endpoint[P, R], params: Mapping[str, Any]) -> R:
        deadline = self._clock() + self._deadline_s
        validated = spec.validate(params)
        call = self._endpoint_call(spec, validated)
        return self._finish(spec, validated, call, await self._run(call, deadline))

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
            yield walk.accept(await self._call(spec, walk.params))

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


async def _wait_out(task: asyncio.Future[Any]) -> None:
    """Wait for ``task`` while the caller is being cancelled; further cancellations change
    nothing, as the caller raises the first one afterwards."""
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait([task])
