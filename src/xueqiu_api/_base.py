"""Configuration and logic shared by Client and AsyncClient; nothing here sends or sleeps."""

from __future__ import annotations

import difflib
import json
import math
import os
import string
import threading
from collections.abc import Callable, Mapping, Sized
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
from typing import Any, Literal, ParamSpec, TypeVar, cast
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
from ._freshness import MAX_ENTRY_BYTES, cache_key, encode_body, ttl_s
from ._pages import Page, finish
from ._result import FetchFacts, FetchInfo, info_of, stamp
from ._session import TOKEN_ENV, Session, SessionStore, default_session_path, parse_credentials
from ._shared import SharedBreaker, SharedLimiter, SharedState
from ._status import AuthError, CacheStatus, ClientStatus, HostStatus
from .cache import CacheBackend, CacheEntry, CacheStats, MemoryCache, SqliteCache
from .endpoints import REGISTRY
from .endpoints.kline import ENDPOINT as KLINE
from .errors import (
    Blocked,
    InvalidParams,
    LoginRequired,
    RateLimited,
    SymbolNotFound,
    UnknownEndpoint,
    UpstreamChanged,
    XueqiuError,
)
from .registry import Endpoint
from .sites import SITES, Host
from .symbols import Market, normalize_symbol
from .trading_calendar import CALENDAR_SOURCES

P = TypeVar("P", bound=BaseModel)
R = TypeVar("R")
T = TypeVar("T")
A = ParamSpec("A")

DEFAULT_DEADLINE_S = 30.0
PRIVATE_CACHE_BYTES = 8 * 2**20
"""Size of the in-memory cache that holds the results of calls sent with login cookies."""

CacheCount = Literal["hits", "misses", "stale"]


@dataclass(frozen=True, slots=True)
class CachePlan:
    """How one call uses the response cache."""

    backend: CacheBackend
    key: str
    read: bool
    """Look for a fresh entry before asking the upstream."""
    write: bool
    """Store the new result."""
    coalesce: bool
    """Share a request for the same key that another call has in flight."""


class ClientBase:
    def __init__(
        self,
        *,
        token: str | None,
        session_path: str | os.PathLike[str] | None,
        min_intervals: Mapping[str, float] | None,
        deadline: float,
        clock: Callable[[], float],
        wall: Callable[[], float],
        state_path: Path | None,
        cache: CacheBackend | None,
        stale_if_error: float | None,
    ) -> None:
        if not deadline > 0:
            raise ValueError("deadline must be a positive number of seconds")
        if stale_if_error is not None and not stale_if_error > 0:
            raise ValueError("stale_if_error must be a positive number of seconds")
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
        self._intervals = _intervals(min_intervals)
        # SharedState opens its file on first use, so constructing a client creates nothing.
        self._state = (
            None if state_path is None else SharedState(state_path, clock=clock, wall=wall)
        )
        if self._state is None:
            self._limiter = HostLimiter(self._intervals)
            self._breaker = CircuitBreaker(BREAKER_COOLDOWN_S)
        else:
            self._limiter = SharedLimiter(self._intervals, self._state)
            self._breaker = SharedBreaker(BREAKER_COOLDOWN_S, self._state)
        self._last_auth_error: AuthError | None = None
        self._clock = clock
        self._wall = wall
        self._deadline_s = float(deadline)
        self._attempt_budget_s = min(MIN_ATTEMPT_S, self._deadline_s / 2)
        self._cache = cache
        # Results of calls sent with the login cookies never reach a shared or caller's backend.
        self._private = MemoryCache(max_bytes=PRIVATE_CACHE_BYTES)
        self._stale_if_error = stale_if_error
        self._cache_counts: dict[CacheCount, int] = {"hits": 0, "misses": 0, "stale": 0}
        self._counts_lock = threading.Lock()
        self._cache_failed = False

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
        path, query = _fill_path(spec.path, spec.build_query(params, self._now()))
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
        if "?" in path:
            raise InvalidParams(
                "path must not hold a query",
                params={"path": path[:200]},
                hint="Pass the query values in params.",
            )
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
            result = spec.parse(payload, params)
            if spec.pagination is not None and isinstance(result, Page):
                return cast(R, finish(spec.pagination, cast(Page[Any], result), call.params))
            return result
        except SymbolNotFound as exc:
            raise SymbolNotFound(exc.message, endpoint=spec.name, params=call.params) from None
        except (ValueError, TypeError) as exc:
            raise UpstreamChanged(
                f"{spec.name} response does not match the expected shape: {_reason(exc)}",
                endpoint=spec.name,
                params=call.params,
            ) from None

    def _plan(
        self, spec: Endpoint[Any, Any], call: Call, cache: bool, max_age: float | None
    ) -> CachePlan | None:
        """How the call uses the cache; None without one or with ``cache=False``.

        Realtime results are never stored, only shared while in flight. ``max_age=0`` stores
        a new result without reading or sharing.
        """
        if max_age is not None and not max_age >= 0:
            raise InvalidParams(
                f"max_age must be 0 or more seconds, not {max_age!r}",
                endpoint=spec.name,
                params=call.params,
                field="max_age",
            )
        if self._cache is None or not cache:
            return None
        stored = spec.freshness != "realtime"
        return CachePlan(
            backend=self._private if call.credentials else self._cache,
            key=cache_key(spec.name, call.params, self._session.identity(call.credentials)),
            read=stored and max_age != 0,
            write=stored,
            coalesce=max_age != 0,
        )

    def _from_entry(
        self,
        spec: Endpoint[P, R],
        params: P,
        call: Call,
        plan: CachePlan,
        *,
        max_age: float | None,
        stale: bool = False,
    ) -> R | None:
        """The stored result of the call when it is young enough, else None.

        Young enough means within ``max_age``, by default the freshness of the params, or with
        ``stale`` within ``stale_if_error``. An entry that no longer parses is deleted and
        counts as a miss.
        """
        entry = self._cache_io(plan.backend.get, plan.key)
        found: R | None = None
        if entry is not None:
            now = self._wall()
            fetched_at = _utc(entry.fetched_at)
            if stale:
                limit = self._stale_if_error
            elif max_age is not None:
                limit = max_age
            else:
                limit = ttl_s(spec, params, fetched_at, _utc(now))
            if limit is not None and 0 <= now - entry.fetched_at <= limit:
                try:
                    result = self._finish(spec, params, call, json.loads(entry.body))
                except (XueqiuError, ValueError):
                    self._cache_io(plan.backend.delete, plan.key)
                else:
                    found = stamp(result, FetchInfo(fetched_at, from_cache=True, stale=stale))
        if found is not None:
            self._count("stale" if stale else "hits")
        elif not stale:
            self._count("misses")
        return found

    def _stale_result(
        self, spec: Endpoint[P, R], params: P, call: Call, plan: CachePlan, error: XueqiuError
    ) -> R | None:
        """A stored result within ``stale_if_error`` for an error worth backing off from: a
        rate limit, a block, an open breaker, a paused host or an outage."""
        if self._stale_if_error is None or not plan.write or error.recoverable != "backoff":
            return None
        return self._from_entry(spec, params, call, plan, max_age=None, stale=True)

    def _entry(
        self, spec: Endpoint[Any, Any], body: Any, result: object, fetched: float
    ) -> CacheEntry | None:
        """What to store of a new result: nothing for an empty one, nor for a body larger than
        ``MAX_ENTRY_BYTES`` in its stored form or holding a cookie value of this session."""
        if isinstance(result, Sized) and len(result) == 0:
            return None
        data = encode_body(body)
        if len(data) > MAX_ENTRY_BYTES:
            return None
        if any(secret.encode() in data for secret in self._session.secrets()):
            return None
        return CacheEntry(data, fetched, spec.name)

    def _store(
        self, plan: CachePlan, spec: Endpoint[Any, Any], body: Any, result: object, fetched: float
    ) -> None:
        entry = self._entry(spec, body, result, fetched)
        if entry is not None:
            self._cache_io(plan.backend.put, plan.key, entry)

    def _cache_io(self, fn: Callable[A, T], /, *args: A.args, **kwargs: A.kwargs) -> T | None:
        """A backend call whose failure costs only the cache: None, logged the first time."""
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not self._cache_failed:
                self._cache_failed = True
                name = type(exc).__name__
                log.warning("the response cache failed with %s; calls go on without it", name)
            return None

    def _count(self, name: CacheCount) -> None:
        with self._counts_lock:
            self._cache_counts[name] += 1

    def _fetched(self, result: R, fetched: float) -> R:
        """``result`` stamped as fetched from the upstream at ``fetched`` on the wall clock."""
        return stamp(result, FetchInfo(_utc(fetched)))

    def _fetched_at(self, result: FetchFacts) -> datetime:
        """When the upstream answered for a result this client returned."""
        return result.fetched_at or self._now()

    def _now(self) -> datetime:
        return _utc(self._wall())

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

    def _flight_wait_error(self, call: Call) -> RateLimited:
        """The error of a call whose deadline passed while the same request, sent by another
        call, was still in flight."""
        return RateLimited(
            f"the {self._deadline_s:g} s deadline passed while another call's request for the "
            "same data was in flight",
            endpoint=call.endpoint,
            retry_after_s=UNAVAILABLE_RETRY_AFTER_S,
            params=call.params,
        )

    def _timeout(self, deadline: float) -> float:
        return max(0.001, min(ATTEMPT_TIMEOUT_S, deadline - self._clock()))

    def _status(self) -> ClientStatus:
        now = self._clock()
        hosts = tuple(
            HostStatus(
                host=host,
                interval_s=interval,
                # A deadline no slot can meet books nothing and still gives the wait.
                wait_s=self._limiter.reserve(host, now=now, earliest=now, deadline=-math.inf)[1],
                paused_s=self._limiter.paused(host, now),
                open_s=self._breaker.remaining(host, now),
            )
            for host, interval in self._intervals.items()
        )
        return ClientStatus(
            credentials="token" if self._session.has_credentials else "none",
            last_auth_error=self._last_auth_error,
            hosts=hosts,
            shared=None if self._state is None else self._state.status(),
            cache=self._cache_status(),
        )

    def _cache_status(self) -> CacheStatus | None:
        if self._cache is None:
            return None
        stats = self._cache_io(self._cache.stats) or CacheStats(0, 0)
        store = self._cache.status() if isinstance(self._cache, SqliteCache) else None
        with self._counts_lock:
            counts = dict(self._cache_counts)
        return CacheStatus(type(self._cache).__name__, store, stats.entries, stats.bytes, **counts)

    def _note_failure(self, call: Call, failure: Failure) -> None:
        log.debug("GET %s failed: %s", call.url, failure.message)
        if failure.login and failure.upstream_code is not None:
            at = datetime.fromtimestamp(self._wall(), UTC)
            self._last_auth_error = AuthError(failure.upstream_code, at)
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


def restamp(result: T, source: object) -> T:
    """``result`` with the fetch facts ``source`` carries, for a result built from it."""
    info = info_of(source)
    return result if info is None else stamp(result, info)


def market_symbol(market: Market) -> str:
    """The symbol whose daily bars and quote stand for ``market``."""
    source = CALENDAR_SOURCES.get(market)
    if source is None:
        raise InvalidParams(
            f"unknown market {str(market)[:40]!r}",
            field="market",
            valid_values=tuple(CALENDAR_SOURCES),
        )
    return source.symbol


def exchange_yesterday(symbol: str, now: datetime) -> date:
    """The day before ``now`` in the exchange timezone of ``symbol``."""
    checked = KLINE.validate({"symbol": symbol})
    return now.astimezone(normalize_symbol(checked.symbol).timezone).date() - timedelta(days=1)


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


def _utc(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def _reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "data"
        return f"{location}: {error['msg']} ({exc.error_count()} errors)"
    return str(exc)[:200]
