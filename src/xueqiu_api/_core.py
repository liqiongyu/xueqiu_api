"""Sans-IO core shared by the sync and async clients.

Request building, response classification, retry decisions and backoff are pure functions.
``HostLimiter`` and ``CircuitBreaker`` keep per-client state but never sleep or do I/O. The
shells in ``_sync`` and ``_async`` only send, sleep and loop.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

import httpx2

from .errors import (
    ERROR_TABLE,
    ISSUES_URL,
    REDACTED,
    AuthExpired,
    ErrorCode,
    LoginRequired,
    UpstreamChanged,
    XueqiuError,
)
from .registry import Auth, Envelope
from .sites import HOME_HOST, HOME_URL, SITES, Site

log = logging.getLogger("xueqiu_api")

MAX_RETRIES = 2
"""Retries after HTTP 5xx, timeouts and network errors."""
BACKOFF_BASE_S = 1.0
ATTEMPT_TIMEOUT_S = 10.0
BREAKER_COOLDOWN_S = 300.0
RATE_LIMIT_RETRY_AFTER_S = 10.0
"""retry_after_s for upstream rate limits that come without a Retry-After header."""
UNAVAILABLE_RETRY_AFTER_S = 5.0

# Aliyun WAF challenge pages, measured on xueqiu.com: HTML whose first bytes carry
# {"_waf_<hex>": ...} render data and a <meta name="aliyun_waf_aa"> tag.
_WAF_PAGE = re.compile(rb"aliyun_waf|_waf_[0-9a-f]{6,}", re.IGNORECASE)


@dataclass(frozen=True, kw_only=True)
class Call:
    """One logical request and the context its errors report."""

    site: Site
    url: str
    query: Mapping[str, str]
    endpoint: str | None
    """Registry name; None for raw requests and the cookie bootstrap."""
    auth: Auth | None
    """Registered auth class; None when unknown (raw requests)."""
    cookies: bool
    """Whether the request carries the session cookies."""
    params: Mapping[str, Any]
    """Caller-facing params for error payloads; credential-like keys are redacted later."""
    home: bool = False
    """The anonymous cookie bootstrap: follow redirects, expect HTML and cookies."""


HOME_CALL = Call(
    site=SITES[HOME_HOST],
    url=HOME_URL,
    query={},
    endpoint=None,
    auth=None,
    cookies=False,
    params={},
    home=True,
)


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    content: bytes
    cookies: Mapping[str, str] = field(default_factory=dict[str, str])
    """Cookies set by every response in the redirect chain; collected for the bootstrap only."""


@dataclass(frozen=True)
class Ok:
    value: Any


@dataclass(frozen=True, kw_only=True)
class Failure:
    code: ErrorCode
    message: str
    status: int | None = None
    upstream_code: str | None = None
    retry_after_s: float | None = None
    trips_breaker: bool = False
    login: bool = False
    """A login-class upstream code; ``next_step`` decides which error it becomes."""

    def error(self, call: Call, cls: type[XueqiuError] | None = None, **extra: Any) -> XueqiuError:
        return (cls or ERROR_TABLE[self.code].exception)(
            self.message,
            endpoint=call.endpoint,
            status=self.status,
            upstream_code=self.upstream_code,
            retry_after_s=self.retry_after_s,
            params=call.params,
            **extra,
        )


@dataclass(frozen=True)
class Retry:
    delay_s: float
    refresh: bool = False
    """Refresh the anonymous cookies before retrying."""


def request_headers(call: Call, cookie_header: str | None) -> dict[str, str]:
    headers = dict(call.site.headers)
    if call.cookies and cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def scrub(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def excerpt(content: bytes, secrets: Iterable[str], limit: int = 80) -> str:
    """Short single-line excerpt of a body with known secret values removed."""
    text = " ".join(scrub(content[:2000].decode("utf-8", "replace"), secrets).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def upstream_error(body: Any) -> tuple[str, str] | None:
    """Non-zero ``error_code`` (Xueqiu) or ``result_code`` (Danjuan) and its description."""
    if not isinstance(body, dict):
        return None
    fields = cast(dict[str, Any], body)
    for key in ("error_code", "result_code"):
        code = fields.get(key)
        if code is not None and code not in (0, "0", ""):
            description = fields.get("error_description") or fields.get("message") or ""
            return str(code), str(description)
    return None


def network_failure(exc: httpx2.RequestError, call: Call) -> Failure:
    host = call.site.host
    if isinstance(exc, httpx2.TimeoutException):
        message = f"{host} timed out"
    else:
        message = f"{host} network error ({type(exc).__name__})"
    return Failure(
        code="upstream_unavailable", message=message, retry_after_s=UNAVAILABLE_RETRY_AFTER_S
    )


def classify(call: Call, result: HttpResult, secrets: Iterable[str]) -> Ok | Failure:
    """Turn one HTTP response into the parsed JSON body (or bootstrap cookies) or a failure."""
    host = call.site.host
    status = result.status
    try:
        body: Any = json.loads(result.content)
        is_json = True
    except ValueError:
        body, is_json = None, False
    if status == 418:
        return Failure(
            code="blocked",
            message=f"{host} blocked the request (HTTP 418)",
            status=status,
            trips_breaker=True,
        )
    if not is_json and _WAF_PAGE.search(result.content[:4096]):
        return Failure(
            code="blocked",
            message=f"{host} answered with a WAF challenge page (HTTP {status})",
            status=status,
            trips_breaker=True,
        )
    if status == 403:
        # Seen as a path-level refusal (openresty 403 page); the host keeps answering.
        return Failure(code="blocked", message=f"{host} refused the request (HTTP 403)", status=403)
    if status == 429:
        return Failure(
            code="rate_limited",
            message=f"{host} rate limited the request (HTTP 429)",
            status=429,
            retry_after_s=_retry_after(result.headers),
        )
    if status >= 500:
        return Failure(
            code="upstream_unavailable",
            message=f"{host} returned HTTP {status}",
            status=status,
            retry_after_s=UNAVAILABLE_RETRY_AFTER_S,
        )
    if call.home:
        if status == 200 and "xq_a_token" in result.cookies:
            return Ok(dict(result.cookies))
        return Failure(
            code="upstream_changed",
            message=f"the home page returned HTTP {status} without the anonymous token cookie",
            status=status,
        )
    error = upstream_error(body) if is_json else None
    if error is not None:
        code, description = error
        message = f"{host} returned error {code}"
        if description:
            message += f": {scrub(description, secrets)[:200]}"
        if code in call.site.login_codes:
            return Failure(
                code="login_required",
                message=message,
                status=status,
                upstream_code=code,
                login=True,
            )
        if code in call.site.rate_limit_codes:
            return Failure(
                code="rate_limited",
                message=message,
                status=status,
                upstream_code=code,
                retry_after_s=_retry_after(result.headers),
            )
        return Failure(code="upstream_error", message=message, status=status, upstream_code=code)
    if 300 <= status < 400 or status == 404:
        return Failure(
            code="upstream_changed", message=f"{host} returned HTTP {status}", status=status
        )
    if status >= 400:
        return Failure(
            code="upstream_error", message=f"{host} returned HTTP {status}", status=status
        )
    if not is_json:
        return Failure(
            code="upstream_changed",
            message=f"{host} returned a non-JSON body: {excerpt(result.content, secrets)!r}",
            status=status,
        )
    return Ok(body)


def unwrap(envelope: Envelope, body: Any) -> Any:
    """Payload of a successful response; ``ValueError`` when the envelope does not match."""
    if not isinstance(body, dict) or "data" not in body:
        raise ValueError(f"expected the {envelope} envelope with a data key")
    return cast(dict[str, Any], body)["data"]


def _retry_after(headers: Mapping[str, str]) -> float:
    value = headers.get("retry-after", "")
    try:
        seconds = float(value)
    except ValueError:
        return RATE_LIMIT_RETRY_AFTER_S
    return seconds if math.isfinite(seconds) and seconds >= 0 else RATE_LIMIT_RETRY_AFTER_S


def next_step(
    failure: Failure, *, call: Call, attempt: int, refreshed: bool, credentials: bool
) -> Retry | XueqiuError:
    """Decide whether a failed attempt is retried, refreshes the session, or raises.

    Login-class codes: a request that carried the configured credentials raises
    ``auth_expired``; one that carried anonymous cookies refreshes them once and retries. When
    that does not help, or no cookies were sent, the error is ``login_required`` if the
    endpoint is registered as needing login or unknown (raw requests). For an endpoint the
    registry lists as working without login it is ``upstream_changed``: the registered auth
    class no longer holds.
    """
    if failure.code == "upstream_unavailable" and attempt < MAX_RETRIES:
        return Retry(BACKOFF_BASE_S * 2**attempt)
    if not failure.login:
        return failure.error(call)
    if call.cookies and credentials:
        return failure.error(call, AuthExpired)
    if call.cookies and not refreshed:
        return Retry(0.0, refresh=True)
    if call.auth is None or call.auth == "required":
        return failure.error(call, LoginRequired)
    return failure.error(
        call,
        UpstreamChanged,
        hint=(
            f"{call.endpoint} was verified to work without login and now returns a login "
            f"error. Setting XUEQIU_TOKEN may help; please report it at {ISSUES_URL}."
        ),
    )


class HostLimiter:
    """Minimum interval between request starts per host, shared by all calls of one client.

    ``reserve`` books the next free slot under a lock and returns the wait; it never sleeps,
    so threads of the sync client and tasks of the async client share it.
    """

    def __init__(self, intervals: Mapping[str, float]) -> None:
        self._intervals = dict(intervals)
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def reserve(
        self, host: str, *, now: float, earliest: float, deadline: float
    ) -> tuple[bool, float]:
        """Book the first slot at or after ``earliest``; return (booked, wait from now).

        Nothing is booked when the slot would start after ``deadline``.
        """
        with self._lock:
            slot = max(now, earliest, self._next.get(host, -math.inf))
            if slot > deadline:
                return False, slot - now
            self._next[host] = slot + self._intervals[host]
            return True, slot - now


class CircuitBreaker:
    """Per-host breaker: a block opens it for ``cooldown_s``, during which calls fail fast."""

    def __init__(self, cooldown_s: float) -> None:
        self._cooldown_s = cooldown_s
        self._open_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def remaining(self, host: str, now: float) -> float:
        with self._lock:
            return max(0.0, self._open_until.get(host, now) - now)

    def trip(self, host: str, now: float) -> None:
        with self._lock:
            self._open_until[host] = now + self._cooldown_s
