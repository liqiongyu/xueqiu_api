from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
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
from ._endpoints_sync import SyncEndpoints
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
class SyncRuntime:
    """Private injection point for tests and replay: transport, clocks and sleep.

    ``clock`` is monotonic and times waits; ``wall`` is seconds since the epoch.
    """

    transport: httpx2.BaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = _wall
    sleep: Callable[[float], None] = time.sleep


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
            directory. Login cookies are never written.
        min_intervals: longer minimum intervals between requests per host, in seconds.
        deadline: seconds a call may take, queueing and retries included. A response that
            keeps arriving after the deadline is given up when its next chunk comes, which
            can be up to one 10 s read timeout later.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        session_path: str | os.PathLike[str] | None = None,
        min_intervals: Mapping[str, float] | None = None,
        deadline: float = DEFAULT_DEADLINE_S,
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
        )
        self._sleep = runtime.sleep
        self._http = httpx2.Client(
            transport=runtime.transport, cookies=no_cookie_jar(), max_redirects=MAX_REDIRECTS
        )
        self._session_lock = threading.Lock()

    def kline_history(
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
            walk.accept(self._call(KLINE, walk.params))
        return walk.result()

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
        walk = SnapshotWalk(market, segments=segments, fetched_at=datetime.now(UTC))
        while (request := walk.next_request()) is not None:
            try:
                walk.feed(self.call(request.endpoint, **request.params))
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
        return calendar_from_kline(self._call(KLINE, params), fetched_at=datetime.now(UTC))

    def market_state(self, market: Market) -> MarketState:
        """Whether ``market`` is trading now, from one quote of its ``CALENDAR_SOURCES`` symbol.

        HK and US read the quote of a stock, so ``traded_today`` can be False on a day the market
        traded while that stock is suspended.
        """
        quote = self._call(QUOTE_DETAIL, {"symbol": market_symbol(market)})
        return market_state_from_quote(quote, now=datetime.now(UTC))

    def adjust_factors(self, symbol: str, *, end: date | None = None) -> AdjustFactors:
        """Adjustment factors of ``symbol`` up to ``end``, from its unadjusted and backward-adjusted
        daily bars: two requests, more only for a history one request cannot hold.

        ``end`` defaults to yesterday on the symbol's exchange, because today's bar can change
        between the two requests.
        """
        last = exchange_yesterday(symbol, datetime.now(UTC)) if end is None else end
        normal = self.kline_history(symbol, adjust="normal", end=last)
        after = self.kline_history(symbol, adjust="after", end=last)
        return derive_factors(normal, after, fetched_at=datetime.now(UTC))

    def call(self, endpoint: str, /, **params: Any) -> Any:
        """Call any registered endpoint by name and return its parsed result."""
        return self._call(self._endpoint(endpoint), params)

    def request(
        self, *, host: Host, path: str, params: Mapping[str, str | int | float] | None = None
    ) -> Any:
        """GET a path on a known host and return the raw JSON body.

        Goes through the same session, limiter and error handling as the endpoint methods.
        """
        call = self._raw_call(host, path, params)
        return self._run(call, self._clock() + self._deadline_s)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _call(self, spec: Endpoint[P, R], params: Mapping[str, Any]) -> R:
        deadline = self._clock() + self._deadline_s
        validated = spec.validate(params)
        call = self._endpoint_call(spec, validated)
        return self._finish(spec, validated, call, self._run(call, deadline))

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
            yield walk.accept(self._call(spec, walk.params))

    def _run(self, call: Call, deadline: float) -> Any:
        attempt, refreshed, last = 0, False, None
        earliest = self._clock()
        while True:
            if call.cookies:
                self._ensure_session(call, deadline)
            generation = self._session.generation
            wait = self._book(call, earliest=earliest, deadline=deadline, last=last)
            if wait > 0:
                self._sleep(wait)
            self._check_host(call)
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
                self._bootstrap(deadline)
        if not self._session.ready(call):
            raise self._cookie_error(call)

    def _refresh(self, call: Call, seen: int, deadline: float) -> None:
        with self._session_locked(call, deadline):
            if self._session.generation == seen:
                self._bootstrap(deadline)
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

    def _bootstrap(self, deadline: float) -> None:
        self._session.set_anonymous(self._run(HOME_CALL, deadline))
