from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Self, TypeVar

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


def _wall() -> float:
    """Seconds since the epoch, read through this module's ``datetime`` at call time."""
    return datetime.now(UTC).timestamp()


@dataclass(frozen=True)
class AsyncRuntime:
    """Private injection point for tests and replay: transport, clocks and sleep.

    ``clock`` is monotonic and times waits; ``wall`` is seconds since the epoch.
    """

    transport: httpx2.AsyncBaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = _wall
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


class AsyncClient(ClientBase, AsyncEndpoints):
    """Asynchronous (asyncio) client with the same surface as ``Client``; methods are coroutines.

    Concurrent tasks share one per-host limiter, circuit breaker and session. Arguments are
    the same as for ``Client``; the deadline also bounds a response that is still arriving.
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

    async def close(self) -> None:
        await self._http.aclose()

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
        attempt, refreshed, last = 0, False, None
        earliest = self._clock()
        while True:
            if call.cookies:
                await self._ensure_session(call, deadline)
            generation = self._session.generation
            wait = self._book(call, earliest=earliest, deadline=deadline, last=last)
            if wait > 0:
                await self._sleep(wait)
            self._check_host(call)
            outcome = await self._attempt(call, deadline)
            if isinstance(outcome, Ok):
                return outcome.value
            self._note_failure(call, outcome)
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
                await self._bootstrap(deadline)
        if not self._session.ready(call):
            raise self._cookie_error(call)

    async def _refresh(self, call: Call, seen: int, deadline: float) -> None:
        async with self._session_locked(call, deadline):
            if self._session.generation == seen:
                await self._bootstrap(deadline)
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

    async def _bootstrap(self, deadline: float) -> None:
        self._session.set_anonymous(await self._run(HOME_CALL, deadline))
