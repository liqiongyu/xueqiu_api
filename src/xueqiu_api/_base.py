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
from .errors import Blocked, InvalidParams, RateLimited, UnknownEndpoint, UpstreamChanged
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
        raw = token if token is not None else os.environ.get(TOKEN_ENV) or None
        credentials = None if raw is None else parse_credentials(raw)
        path = default_session_path() if session_path is None else Path(session_path)
        self._session = Session(credentials=credentials, store=SessionStore(path))
        self._limiter = HostLimiter(_intervals(min_intervals))
        self._breaker = CircuitBreaker(BREAKER_COOLDOWN_S)
        self._clock = clock
        self._deadline_s = float(deadline)

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
        site = SITES[spec.host]
        path, query = _fill_path(spec.path, spec.build_query(params, datetime.now(UTC)))
        return Call(
            site=site,
            url=f"https://{site.host}{path}",
            query=query,
            endpoint=spec.name,
            auth=spec.auth,
            cookies=site.cookies and spec.cookies,
            params=params.model_dump(mode="json", exclude_none=True),
        )

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
        return Call(
            site=site,
            url=f"https://{site.host}{path}",
            query=query,
            endpoint=None,
            auth=None,
            cookies=site.cookies,
            params={"host": host, "path": path, **query},
        )

    def _finish(self, spec: Endpoint[P, R], params: P, call: Call, body: Any) -> R:
        try:
            return spec.parse(unwrap(spec.envelope, body), params)
        except (ValueError, TypeError) as exc:
            raise UpstreamChanged(
                f"{spec.name} response does not match the expected shape: {_reason(exc)}",
                endpoint=spec.name,
                params=call.params,
            ) from None

    def _book(self, call: Call, *, earliest: float, deadline: float, last: Failure | None) -> float:
        """Book a limiter slot and return the wait; raise when the deadline cannot be met."""
        now = self._clock()
        self._check_breaker(call, now)
        booked, wait = self._limiter.reserve(
            call.site.host, now=now, earliest=earliest, deadline=deadline
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

    def _check_breaker(self, call: Call, now: float | None = None) -> None:
        remaining = self._breaker.remaining(call.site.host, self._clock() if now is None else now)
        if remaining > 0:
            raise Blocked(
                f"circuit breaker open for {call.site.host}",
                endpoint=call.endpoint,
                retry_after_s=round(remaining, 1),
                params=call.params,
            )

    def _timeout(self, deadline: float) -> float:
        return max(0.001, min(ATTEMPT_TIMEOUT_S, deadline - self._clock()))

    def _note_failure(self, call: Call, failure: Failure) -> None:
        log.debug("GET %s failed: %s", call.url, failure.message)
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


def http_result(call: Call, response: httpx2.Response) -> HttpResult:
    cookies: dict[str, str] = {}
    if call.home:
        for hop in (*response.history, response):
            for cookie in hop.cookies.jar:
                if cookie.value is not None:
                    cookies[cookie.name] = cookie.value
    log.debug("GET %s -> HTTP %d", call.url, response.status_code)
    return HttpResult(response.status_code, response.headers, response.content, cookies)


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


def _reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "data"
        return f"{location}: {error['msg']} ({exc.error_count()} errors)"
    return str(exc)[:200]
