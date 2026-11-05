"""Configuration and logic shared by Client and AsyncClient; nothing here sends or sleeps."""

from __future__ import annotations

import difflib
import os
import string
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
from typing import Any, TypeVar, cast
from urllib.parse import quote

import httpx2
from pydantic import BaseModel, ValidationError

from ._core import (
    ATTEMPT_TIMEOUT_S,
    BREAKER_COOLDOWN_S,
    MIN_ATTEMPT_S,
    UNAVAILABLE_RETRY_AFTER_S,
    Call,
    CircuitBreaker,
    Failure,
    HostLimiter,
    HttpResult,
    log,
    unwrap,
)
from ._session import TOKEN_ENV, Session, SessionStore, default_session_path, parse_credentials
from .endpoints import REGISTRY
from .errors import (
    Blocked,
    InvalidParams,
    LoginRequired,
    RateLimited,
    SymbolNotFound,
    UnknownEndpoint,
    UpstreamChanged,
)
from .registry import Endpoint
from .sites import SITES, Host

P = TypeVar("P", bound=BaseModel)
R = TypeVar("R")

DEFAULT_DEADLINE_S = 30.0


class ClientBase:
    def __init__(
        self,
        *,
        token: str | None,
        session_path: str | os.PathLike[str] | None,
        min_intervals: Mapping[str, float] | None,
        deadline: float,
        clock: Callable[[], float],
    ) -> None:
        if not deadline > 0:
            raise ValueError("deadline must be a positive number of seconds")
        raw = (token if token is not None else os.environ.get(TOKEN_ENV)) or None
        try:
            credentials = None if raw is None else parse_credentials(raw)
        except ValueError as exc:
            source = "token" if token is not None else TOKEN_ENV
            raise InvalidParams(
                f"{source} is not a valid token: {exc}",
                hint=f"Pass token= or set {TOKEN_ENV} to the xq_a_token value or to a cookie "
                "string such as 'xq_a_token=...; u=...'.",
            ) from None
        path = default_session_path() if session_path is None else Path(session_path)
        self._session = Session(credentials=credentials, store=SessionStore(path))
        self._limiter = HostLimiter(_intervals(min_intervals))
        self._breaker = CircuitBreaker(BREAKER_COOLDOWN_S)
        self._clock = clock
        self._deadline_s = float(deadline)
        self._attempt_budget_s = min(MIN_ATTEMPT_S, self._deadline_s / 2)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(session={self._session.kind!r})"

    def _endpoint(self, name: str) -> Endpoint[Any, Any]:
        spec = REGISTRY.get(name)
        if spec is None:
            close = difflib.get_close_matches(name, list(REGISTRY), n=3)
            raise UnknownEndpoint(
                f"unknown endpoint {name[:60]!r}",
                hint=f"Did you mean {', '.join(close)}?" if close else None,
            )
        return spec

    def _endpoint_call(self, spec: Endpoint[P, Any], params: P) -> Call:
        """The call of validated params; ``LoginRequired`` when it cannot be sent as it needs."""
        site = SITES[spec.host]
        path, query = _fill_path(spec.path, spec.build_query(params, datetime.now(UTC)))
        cookies = site.cookies and spec.cookies
        call = Call(
            site=site,
            url=f"https://{site.host}{path}",
            query=query,
            endpoint=spec.name,
            auth=spec.auth,
            cookies=cookies,
            params=params.model_dump(mode="json", exclude_none=True),
            credentials=cookies and self._session.has_credentials and spec.auth != "anonymous",
            needs_cookies=spec.needs_cookies,
            not_found=spec.not_found,
        )
        if spec.auth == "required" and not self._session.has_credentials:
            raise LoginRequired(
                f"{spec.name} needs a logged-in session", endpoint=spec.name, params=call.params
            )
        if call.credentials and not self._session.ready(call):
            raise LoginRequired(
                f"{spec.name} needs the {_cookie_names(call)} in the configured credentials",
                endpoint=spec.name,
                params=call.params,
            )
        return call

    def _raw_call(
        self, host: str, path: str, params: Mapping[str, str | int | float] | None
    ) -> Call:
        site = SITES.get(cast(Host, host))
        if site is None:
            raise InvalidParams(
                f"unknown host {host[:60]!r}", hint=f"Use one of: {', '.join(SITES)}."
            )
        if not path.startswith("/"):
            raise InvalidParams("path must start with /", params={"path": path[:200]})
        query = {str(key): _query_value(value) for key, value in (params or {}).items()}
        url = f"https://{site.host}{path}"
        try:
            httpx2.URL(url, params=query)
        except httpx2.InvalidURL as exc:
            raise InvalidParams(
                f"invalid request URL: {exc}", params={"host": host, "path": path[:200]}
            ) from None
        return Call(
            site=site,
            url=url,
            query=query,
            endpoint=None,
            auth=None,
            cookies=site.cookies,
            params={"host": host, "path": path, **query},
            credentials=site.cookies and self._session.has_credentials,
        )

    def _finish(self, spec: Endpoint[P, R], params: P, call: Call, body: Any) -> R:
        try:
            payload = unwrap(spec.envelope, body, may_lack_data=spec.not_found is not None)
            return spec.parse(payload, params)
        except SymbolNotFound as exc:
            raise SymbolNotFound(exc.message, endpoint=spec.name, params=call.params) from None
        except (ValueError, TypeError) as exc:
            raise UpstreamChanged(
                f"{spec.name} response does not match the expected shape: {_reason(exc)}",
                endpoint=spec.name,
                params=call.params,
            ) from None

    def _book(self, call: Call, *, earliest: float, deadline: float, last: Failure | None) -> float:
        """Book a limiter slot and return the wait; raise when the deadline cannot be met.

        A slot must leave the request ``MIN_ATTEMPT_S`` before the deadline (half the deadline
        when that is shorter).
        """
        now = self._clock()
        self._check_host(call, now)
        booked, wait = self._limiter.reserve(
            call.site.host, now=now, earliest=earliest, deadline=deadline - self._attempt_budget_s
        )
        if booked:
            return wait
        if last is not None and not last.login:
            raise last.error(call)
        raise RateLimited(
            f"the local queue for {call.site.host} would exceed the {self._deadline_s:g} s "
            "deadline",
            endpoint=call.endpoint,
            retry_after_s=round(wait, 1),
            params=call.params,
        )

    def _check_host(self, call: Call, now: float | None = None) -> None:
        """Fail fast while the host's breaker is open or the host is paused after a rate limit."""
        now = self._clock() if now is None else now
        remaining = self._breaker.remaining(call.site.host, now)
        if remaining > 0:
            raise Blocked(
                f"circuit breaker open for {call.site.host}",
                endpoint=call.endpoint,
                retry_after_s=round(remaining, 1),
                params=call.params,
            )
        paused = self._limiter.paused(call.site.host, now)
        if paused > 0:
            raise RateLimited(
                f"{call.site.host} rate limited an earlier request; requests to it are held "
                f"for {paused:.1f} s more",
                endpoint=call.endpoint,
                retry_after_s=round(paused, 1),
                params=call.params,
            )

    def _cookie_error(self, call: Call) -> UpstreamChanged:
        """The error of a call whose needed cookies a fresh anonymous set still lacks."""
        return UpstreamChanged(
            f"the home page did not set the {_cookie_names(call)} that {call.endpoint} needs",
            endpoint=call.endpoint,
            params=call.params,
        )

    def _session_wait_error(self, call: Call) -> RateLimited:
        """The error of a call whose deadline passed while it waited for the session cookies."""
        return RateLimited(
            f"the {self._deadline_s:g} s deadline passed while another call fetched the session "
            "cookies",
            endpoint=call.endpoint,
            retry_after_s=UNAVAILABLE_RETRY_AFTER_S,
            params=call.params,
        )

    def _timeout(self, deadline: float) -> float:
        return max(0.001, min(ATTEMPT_TIMEOUT_S, deadline - self._clock()))

    def _note_failure(self, call: Call, failure: Failure) -> None:
        log.debug("GET %s failed: %s", call.url, failure.message)
        if failure.code == "rate_limited" and failure.retry_after_s:
            self._limiter.pause(call.site.host, self._clock() + failure.retry_after_s)
        if failure.trips_breaker:
            self._breaker.trip(call.site.host, self._clock())
            log.warning(
                "circuit breaker open for %s for %g s: %s",
                call.site.host,
                BREAKER_COOLDOWN_S,
                failure.message,
            )


def no_cookie_jar() -> CookieJar:
    """A jar that stores nothing, so requests carry only the session's own Cookie header."""
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


def http_result(call: Call, response: httpx2.Response, content: bytes) -> HttpResult:
    cookies: dict[str, str] = {}
    if call.home:
        for hop in (*response.history, response):
            for cookie in hop.cookies.jar:
                if cookie.value is not None:
                    cookies[cookie.name] = cookie.value
    log.debug("GET %s -> HTTP %d", call.url, response.status_code)
    return HttpResult(response.status_code, response.headers, content, cookies)


def _intervals(overrides: Mapping[str, float] | None) -> dict[str, float]:
    intervals: dict[str, float] = {host: site.min_interval_s for host, site in SITES.items()}
    for host, seconds in (overrides or {}).items():
        if host not in intervals:
            raise ValueError(f"unknown host {host!r}; expected one of {', '.join(intervals)}")
        if not seconds >= intervals[host]:
            raise ValueError(
                f"the interval for {host} cannot be shorter than {intervals[host]:g} s"
            )
        intervals[host] = float(seconds)
    return intervals


def _query_value(value: str | int | float) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _fill_path(template: str, values: Mapping[str, str]) -> tuple[str, dict[str, str]]:
    names = {name for _, name, _, _ in string.Formatter().parse(template) if name}
    if not names:
        return template, dict(values)
    path = template.format_map({name: quote(values[name], safe="") for name in names})
    return path, {key: value for key, value in values.items() if key not in names}


def _cookie_names(call: Call) -> str:
    return f"{', '.join(call.needs_cookies)} cookie"


def _reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "data"
        return f"{location}: {error['msg']} ({exc.error_count()} errors)"
    return str(exc)[:200]
