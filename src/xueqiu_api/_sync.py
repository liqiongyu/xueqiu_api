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

from ._base import DEFAULT_DEADLINE_S, ClientBase, http_result, no_cookie_jar
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
from ._finance import Financials, ReportType
from ._pages import PageT, PageWalk, start_walk
from .endpoints.batch_quote import ENDPOINT as BATCH_QUOTE
from .endpoints.f10_company import ENDPOINT as F10_COMPANY
from .endpoints.f10_company import Company
from .endpoints.finance_balance import ENDPOINT as FINANCE_BALANCE
from .endpoints.finance_cash_flow import ENDPOINT as FINANCE_CASH_FLOW
from .endpoints.finance_income import ENDPOINT as FINANCE_INCOME
from .endpoints.finance_indicator import ENDPOINT as FINANCE_INDICATOR
from .endpoints.kline import ENDPOINT as KLINE
from .endpoints.kline import Adjust, BarWalk, Kline, Period
from .endpoints.quote_detail import ENDPOINT as QUOTE_DETAIL
from .endpoints.quote_detail import QuoteDetail
from .endpoints.quotec import ENDPOINT as QUOTEC
from .endpoints.quotec import Quote
from .endpoints.suggest_stock import ENDPOINT as SUGGEST_STOCK
from .endpoints.suggest_stock import StockSuggestion
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
        )
        self._sleep = runtime.sleep
        self._http = httpx2.Client(
            transport=runtime.transport, cookies=no_cookie_jar(), max_redirects=MAX_REDIRECTS
        )
        self._session_lock = threading.Lock()

    def quotec(self, symbols: str | Sequence[str]) -> list[Quote]:
        """Realtime quotes of up to 50 symbols in one request; a string may be comma-separated."""
        return self._call(QUOTEC, {"symbols": symbols})

    def batch_quote(self, symbols: str | Sequence[str]) -> list[QuoteDetail]:
        """Quotes of up to 20 symbols in one request, with names and market status."""
        return self._call(BATCH_QUOTE, {"symbols": symbols})

    def quote_detail(self, symbol: str) -> QuoteDetail:
        """Full quote of one symbol, with its name, valuation fields and market status."""
        return self._call(QUOTE_DETAIL, {"symbol": symbol})

    def kline(
        self,
        symbol: str,
        *,
        period: Period = "day",
        adjust: Adjust = "before",
        begin: date | None = None,
        end: date | None = None,
        before: datetime | None = None,
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
                "before": before,
                "count": count,
            },
        )

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

    def suggest_stock(self, *, query: str) -> list[StockSuggestion]:
        """Securities matching a code, pinyin initials or name, such as ``pfyh`` or ``浦发银行``."""
        return self._call(SUGGEST_STOCK, {"query": query})

    def finance_income(
        self,
        symbol: str,
        *,
        report_type: ReportType = "all",
        is_detail: bool = True,
        count: int = 5,
    ) -> Financials:
        """Income statement, latest report period first; see ``FinanceParams``."""
        return self._call(
            FINANCE_INCOME,
            {"symbol": symbol, "report_type": report_type, "is_detail": is_detail, "count": count},
        )

    def finance_balance(
        self,
        symbol: str,
        *,
        report_type: ReportType = "all",
        is_detail: bool = True,
        count: int = 5,
    ) -> Financials:
        """Balance sheet, latest report period first; see ``FinanceParams``."""
        return self._call(
            FINANCE_BALANCE,
            {"symbol": symbol, "report_type": report_type, "is_detail": is_detail, "count": count},
        )

    def finance_cash_flow(
        self,
        symbol: str,
        *,
        report_type: ReportType = "all",
        is_detail: bool = True,
        count: int = 5,
    ) -> Financials:
        """Cash flow statement, latest report period first; see ``FinanceParams``."""
        return self._call(
            FINANCE_CASH_FLOW,
            {"symbol": symbol, "report_type": report_type, "is_detail": is_detail, "count": count},
        )

    def finance_indicator(
        self,
        symbol: str,
        *,
        report_type: ReportType = "all",
        is_detail: bool = True,
        count: int = 5,
    ) -> Financials:
        """Key financial indicators, latest report period first; see ``FinanceParams``."""
        return self._call(
            FINANCE_INDICATOR,
            {"symbol": symbol, "report_type": report_type, "is_detail": is_detail, "count": count},
        )

    def f10_company(self, symbol: str) -> Company:
        """Company profile of one listed company."""
        return self._call(F10_COMPANY, {"symbol": symbol})

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

    @contextlib.contextmanager
    def _session_locked(self, call: Call, deadline: float) -> Generator[None]:
        """Hold the session lock; give up waiting for it at the call's deadline."""
        if not self._session_lock.acquire(timeout=max(0.0, deadline - self._clock())):
            raise self._session_wait_error(call)
        try:
            yield
        finally:
            self._session_lock.release()

    def _bootstrap(self, deadline: float) -> None:
        self._session.set_anonymous(self._run(HOME_CALL, deadline))
