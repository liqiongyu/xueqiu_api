from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Self, TypeVar

import httpx2
from pydantic import BaseModel

from ._base import DEFAULT_DEADLINE_S, ClientBase, http_result, no_cookie_jar
from ._core import (
    HOME_CALL,
    Call,
    Failure,
    Ok,
    classify,
    network_failure,
    next_step,
    request_headers,
)
from .endpoints.kline import ENDPOINT as KLINE
from .endpoints.kline import Adjust, Kline, Period
from .endpoints.quotec import ENDPOINT as QUOTEC
from .endpoints.quotec import Quote
from .errors import XueqiuError
from .registry import Endpoint
from .sites import Host

P = TypeVar("P", bound=BaseModel)
R = TypeVar("R")


@dataclass(frozen=True)
class SyncRuntime:
    """Private injection point for tests and replay: transport, clock and sleep."""

    transport: httpx2.BaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


class Client(ClientBase):
    """Synchronous client for Xueqiu and Danjuan; safe to share between threads.

    Args:
        token: a bare ``xq_a_token`` value or a cookie string such as ``"xq_a_token=...; u=..."``.
            Defaults to the ``XUEQIU_TOKEN`` environment variable; without either the client
            uses an anonymous session. Some endpoints (kline) also need the ``u`` cookie.
        session_path: where the anonymous cookies are cached; defaults to the user cache
            directory. Login cookies are never written.
        min_intervals: longer minimum intervals between requests per host, in seconds.
        deadline: seconds a call may take, queueing and retries included.
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
        )
        self._sleep = runtime.sleep
        self._http = httpx2.Client(transport=runtime.transport, cookies=no_cookie_jar())
        self._session_lock = threading.Lock()

    def quotec(self, symbols: str | Sequence[str]) -> list[Quote]:
        """Realtime quotes of up to 50 symbols in one request; a string may be comma-separated."""
        return self._call(QUOTEC, {"symbols": symbols})

    def kline(
        self,
        symbol: str,
        *,
        period: Period = "day",
        adjust: Adjust = "before",
        begin: date | None = None,
        end: date | None = None,
        count: int | None = None,
    ) -> Kline:
        """Bars of one symbol in one request; see ``KlineParams`` for how the range is chosen."""
        return self._call(
            KLINE,
            {
                "symbol": symbol,
                "period": period,
                "adjust": adjust,
                "begin": begin,
                "end": end,
                "count": count,
            },
        )

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

    def _run(self, call: Call, deadline: float) -> Any:
        attempt, refreshed, last = 0, False, None
        earliest = self._clock()
        while True:
            if call.cookies:
                self._ensure_session(deadline)
            generation = self._session.generation
            wait = self._book(call, earliest=earliest, deadline=deadline, last=last)
            if wait > 0:
                self._sleep(wait)
            self._check_breaker(call)
            outcome = self._attempt(call, deadline)
            if isinstance(outcome, Ok):
                return outcome.value
            self._note_failure(call, outcome)
            step = next_step(
                outcome,
                call=call,
                attempt=attempt,
                refreshed=refreshed,
                credentials=self._session.has_credentials,
            )
            if isinstance(step, XueqiuError):
                raise step
            if step.refresh:
                self._refresh(generation, deadline)
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
            headers=request_headers(call, self._session.cookie_header()),
            timeout=self._timeout(deadline),
        )
        try:
            response = self._http.send(request, follow_redirects=call.home)
        except httpx2.RequestError as exc:
            return network_failure(exc, call)
        return classify(call, http_result(call, response), self._session.secrets())

    def _ensure_session(self, deadline: float) -> None:
        if self._session.ready:
            return
        with self._session_lock:
            if not self._session.ready:
                self._bootstrap(deadline)

    def _refresh(self, seen: int, deadline: float) -> None:
        with self._session_lock:
            if self._session.generation == seen:
                self._bootstrap(deadline)

    def _bootstrap(self, deadline: float) -> None:
        self._session.set_anonymous(self._run(HOME_CALL, deadline))
