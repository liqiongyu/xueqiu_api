from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Self, TypeVar, cast

import httpx2
from pydantic import BaseModel

from ._base import (
    DEFAULT_DEADLINE_S,
    LATE_WAKE_S,
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
from ._endpoints_sync import SyncEndpoints
from ._pages import PageT, PageWalk, start_walk
from ._result import Rows
from ._shared import LEASE_POLL_S, default_state_path
from ._status import ClientStatus
from .adjust import AdjustFactors, derive_factors
from .cache import CacheBackend
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


def _wall() -> float:
    """Seconds since the epoch, read through this module's ``datetime`` at call time."""
    return datetime.now(UTC).timestamp()


@dataclass(frozen=True)
class SyncRuntime:
    """Private injection point for tests and replay: transport, clocks, sleep and state file.

    ``clock`` is monotonic and times waits; ``wall`` is seconds since the epoch.
    ``state_path`` is the file limits are shared through with other processes; None keeps
    them in process.
    """

    transport: httpx2.BaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = _wall
    sleep: Callable[[float], None] = time.sleep
    state_path: Path | None = None


class Client(ClientBase, SyncEndpoints):
    """Synchronous client for Xueqiu and Danjuan; safe to share between threads.

    One method per endpoint, grouped by prefix: quote, capital, f10, finance, screener, market,
    search, index, fund, cube and watchlist; besides these kline, quotec, batch_quote and
    suggest_stock. ``<name>_pages`` walks a list with numbered pages. Result types are in
    ``xueqiu_api.models``.

    ``security_snapshot``, ``trading_calendar``, ``market_state`` and ``adjust_factors`` derive
    data from those endpoints. Their result types are in ``xueqiu_api.universe``,
    ``xueqiu_api.trading_calendar`` and ``xueqiu_api.adjust``.

    Args:
        token: a bare ``xq_a_token`` value or a cookie string such as ``"xq_a_token=...; u=..."``.
            Defaults to the ``XUEQIU_TOKEN`` environment variable; ``token=""`` means none. It
            is sent only to endpoints registered as needing a login and by ``request()``; every
            other endpoint sends the anonymous cookies, with or without a token.
        session_path: where the anonymous cookies are cached; defaults to the user cache
            directory. Login cookies are never written. Request spacing, rate-limit pauses and
            open circuit breakers are shared with the user's other processes through
            ``state-v1.sqlite3`` in the user cache directory, also when ``session_path`` points
            elsewhere; constructing a client creates no file.
        min_intervals: longer minimum intervals between requests per host, in seconds.
        deadline: seconds a call may take, queueing and retries included. A response that
            keeps arriving after the deadline is given up when its next chunk comes, which
            can be up to one 10 s read timeout later.
        cache: where responses are kept for reuse, such as ``MemoryCache()`` or
            ``SqliteCache()`` from ``xueqiu_api.cache``. By default there is none and every
            call asks the upstream. Results are fresh for a time that depends on the
            endpoint's freshness and its market's trading hours; ``cache`` and ``max_age`` on
            each method change that for one call. Realtime results are never stored, and
            identical calls in flight at the same time share one request. Results sent with
            login cookies stay in a private in-memory cache of this client.
        stale_if_error: with a cache, when the upstream fails with an error worth backing off
            from (rate limited, blocked or unavailable), return a stored result up to this
            many seconds old instead, marked ``stale``; not for a call with ``max_age=0``, nor
            for the pages of a walk. Off by default.
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
        _runtime: SyncRuntime | None = None,
    ) -> None:
        runtime = _runtime or SyncRuntime()
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
        self._http = httpx2.Client(
            transport=runtime.transport, cookies=no_cookie_jar(), max_redirects=MAX_REDIRECTS
        )
        self._session_lock = threading.Lock()
        self._flights: dict[str, Future[tuple[Any, float]]] = {}
        self._flights_lock = threading.Lock()

    def kline_history(
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
            bars = self._call(KLINE, params, cache=cache, max_age=max_age if first is None else 0)
            walk.accept(bars)
            first = bars if first is None else first
        return restamp(walk.result(), first)

    def security_snapshot(
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
                walk.feed(self.call(request.endpoint, cache=False, **request.params))
            except XueqiuError:
                if not request.optional:
                    raise
                walk.skip()
        return walk.result()

    def trading_calendar(self, market: Market) -> TradingCalendar:
        """Past sessions of ``market``, from one request for the unadjusted daily bars of its
        ``CALENDAR_SOURCES`` symbol."""
        params = {
            "symbol": market_symbol(market),
            "period": "day",
            "adjust": "normal",
            "count": MAX_COUNT,
        }
        bars = self._call(KLINE, params)
        return calendar_from_kline(bars, fetched_at=self._fetched_at(bars))

    def market_state(self, market: Market) -> MarketState:
        """Whether ``market`` is trading now, from one quote of its ``CALENDAR_SOURCES`` symbol.

        HK and US read the quote of a stock, so ``traded_today`` can be False on a day the market
        traded while that stock is suspended.
        """
        quote = self._call(QUOTE_DETAIL, {"symbol": market_symbol(market)})
        return market_state_from_quote(quote, now=self._now())

    def adjust_factors(self, symbol: str, *, end: date | None = None) -> AdjustFactors:
        """Adjustment factors of ``symbol`` up to ``end``, from its unadjusted and backward-adjusted
        daily bars: two requests, more only for a history one request cannot hold.

        ``end`` defaults to yesterday on the symbol's exchange, because today's bar can change
        between the two requests.
        """
        last = exchange_yesterday(symbol, self._now()) if end is None else end
        normal = self.kline_history(symbol, adjust="normal", end=last)
        after = self.kline_history(symbol, adjust="after", end=last)
        fetched_at = min(self._fetched_at(normal), self._fetched_at(after))
        return derive_factors(normal, after, fetched_at=fetched_at)

    def call(
        self, endpoint: str, /, *, cache: bool = True, max_age: float | None = None, **params: Any
    ) -> Any:
        """Call any registered endpoint by name and return its parsed result; ``cache`` and
        ``max_age`` work as on the endpoint methods."""
        return self._call(self._endpoint(endpoint), params, cache=cache, max_age=max_age)

    def request(
        self, *, host: Host, path: str, params: Mapping[str, str | int | float] | None = None
    ) -> Any:
        """GET a path on a known host and return the raw JSON body.

        Goes through the same session, limiter and error handling as the endpoint methods.
        """
        call = self._raw_call(host, path, params)
        return self._run(call, self._clock() + self._deadline_s)

    def status(self) -> ClientStatus:
        """What this client knows locally: credentials, the last login error, the limits of
        each host, the shared state file and the response cache. Sends no request."""
        return self._status()

    def close(self) -> None:
        self._http.close()
        if self._state is not None:
            self._state.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _call(
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
            body = self._run(call, deadline)
            return self._fetched(self._finish(spec, validated, call, body), self._wall())
        if plan.read:
            found = self._from_entry(spec, validated, call, plan, max_age=max_age)
            if found is not None:
                return found
        try:
            body, fetched, leader = self._fly(plan, call, deadline)
        except XueqiuError as error:
            found = self._stale_result(spec, validated, call, plan, error)
            if found is None:
                raise
            return found
        result = self._fetched(self._finish(spec, validated, call, body), fetched)
        if leader and plan.write:
            self._store(plan, spec, body, result, fetched)
        return result

    def _rows(
        self,
        spec: Endpoint[P, list[T]],
        params: Mapping[str, Any],
        *,
        cache: bool = True,
        max_age: float | None = None,
    ) -> Rows[T]:
        """``_call`` of an endpoint whose result is a list, which comes back as ``Rows``."""
        return cast(Rows[T], self._call(spec, params, cache=cache, max_age=max_age))

    def _fly(self, plan: CachePlan, call: Call, deadline: float) -> tuple[Any, float, bool]:
        """The body of the call, when it came on the wall clock and whether this call sent
        the request; another thread's request in flight for the same key is shared."""
        if not plan.coalesce:
            return self._run(call, deadline), self._wall(), True
        with self._flights_lock:
            flight = self._flights.get(plan.key)
            leader = flight is None
            if flight is None:
                flight = self._flights[plan.key] = Future()
        if not leader:
            wait = min(max(0.0, deadline - self._clock()), threading.TIMEOUT_MAX)
            try:
                body, fetched = flight.result(timeout=wait)
            except TimeoutError:
                raise self._flight_wait_error(call) from None
            return body, fetched, False
        try:
            body, fetched = self._run(call, deadline), self._wall()
        except BaseException as exc:
            flight.set_exception(exc)
            raise
        else:
            flight.set_result((body, fetched))
        finally:
            with self._flights_lock:
                del self._flights[plan.key]
        return body, fetched, True

    def _pages(
        self,
        spec: Endpoint[P, PageT],
        params: Mapping[str, Any],
        *,
        max_pages: int | None = None,
        max_items: int | None = None,
    ) -> Iterator[PageT]:
        """Pages one at a time, each one call; bad params raise here, before any request."""
        return self._walk(spec, start_walk(spec, params, max_pages, max_items))

    def _walk(self, spec: Endpoint[P, PageT], walk: PageWalk) -> Iterator[PageT]:
        while walk.params is not None:
            # Every page is fetched anew: pages of different moments can drop or repeat rows.
            page = self._call(spec, walk.params, max_age=0)
            yield restamp(walk.accept(page), page)

    def _run(self, call: Call, deadline: float) -> Any:
        attempt, refreshed, last = 0, False, None
        earliest = self._clock()
        while True:
            if call.cookies:
                self._ensure_session(call, deadline)
            generation = self._session.generation
            wait = self._book(call, earliest=earliest, deadline=deadline, last=last)
            if wait > 0:
                slot = self._clock() + wait
                self._sleep(wait)
                # Another call or process may have paused the host or opened its breaker.
                self._check_host(call)
                if self._clock() > slot + LATE_WAKE_S:
                    continue
            outcome = self._attempt(call, deadline)
            if isinstance(outcome, Ok):
                return outcome.value
            self._note_failure(call, outcome)
            step = next_step(outcome, call=call, attempt=attempt, refreshed=refreshed)
            if isinstance(step, XueqiuError):
                raise step
            if step.refresh:
                self._refresh(call, generation, deadline)
                refreshed = True
            else:
                attempt += 1
            earliest = self._clock() + step.delay_s
            last = outcome

    def _attempt(self, call: Call, deadline: float) -> Ok | Failure:
        request = self._http.build_request(
            "GET",
            call.url,
            params=call.query,
            headers=request_headers(call, self._session.cookie_header(call.credentials)),
            timeout=self._timeout(deadline),
        )
        chunks: list[bytes] = []
        try:
            # Streamed, so that a body still arriving at the deadline is given up.
            response = self._http.send(request, stream=True, follow_redirects=call.home)
            try:
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    if self._clock() > deadline:
                        return network_failure(TimeoutError(), call)
            finally:
                response.close()
        except httpx2.RequestError as exc:
            return network_failure(exc, call)
        result = http_result(call, response, b"".join(chunks))
        return classify(call, result, self._session.secrets())

    def _ensure_session(self, call: Call, deadline: float) -> None:
        seen = self._session.generation
        if self._session.ready(call):
            return
        with self._session_locked(call, deadline):
            if self._session.generation == seen:
                self._bootstrap(call, deadline)
        if not self._session.ready(call):
            raise self._cookie_error(call)

    def _refresh(self, call: Call, seen: int, deadline: float) -> None:
        with self._session_locked(call, deadline):
            if self._session.generation == seen:
                self._bootstrap(call, deadline)
        if not self._session.ready(call):
            raise self._cookie_error(call)

    @contextlib.contextmanager
    def _session_locked(self, call: Call, deadline: float) -> Generator[None]:
        """Hold the session lock; give up waiting for it at the call's deadline."""
        wait = min(max(0.0, deadline - self._clock()), threading.TIMEOUT_MAX)
        if not self._session_lock.acquire(timeout=wait):
            raise self._session_wait_error(call)
        try:
            yield
        finally:
            self._session_lock.release()

    def _bootstrap(self, call: Call, deadline: float) -> None:
        """Take a newer anonymous set another process saved, or fetch the home page for one.

        One process at a time fetches, under the home lease; the others look for its set
        every ``LEASE_POLL_S`` until the deadline.
        """
        while not self._session.adopt_saved():
            left = self._limiter.lease_home(deadline)
            if not left:
                try:
                    if not self._session.adopt_saved():
                        self._session.set_anonymous(self._run(HOME_CALL, deadline))
                finally:
                    self._limiter.release_home()
                return
            pause = min(LEASE_POLL_S, left)
            if self._clock() + pause > deadline:
                raise self._session_wait_error(call)
            self._sleep(pause)
