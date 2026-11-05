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
    SymbolNotFound,
    UpstreamChanged,
    XueqiuError,
)
from .registry import Auth, Envelope, NotFound
from .sites import HOME_HOST, HOME_URL, SITES, Site

log = logging.getLogger("xueqiu_api")

MAX_RETRIES = 2
"""Retries after HTTP 5xx, timeouts and network errors."""
BACKOFF_BASE_S = 1.0
ATTEMPT_TIMEOUT_S = 10.0
MIN_ATTEMPT_S = 1.0
"""Least time a request gets before its call's deadline; later limiter slots are not booked."""
MAX_REDIRECTS = 3
"""Redirects the cookie bootstrap follows; the real chain has one, from the apex to www."""
BREAKER_COOLDOWN_S = 300.0
RATE_LIMIT_RETRY_AFTER_S = 10.0
"""retry_after_s for upstream rate limits that come without a Retry-After header."""
UNAVAILABLE_RETRY_AFTER_S = 5.0

# Aliyun WAF challenge pages, measured on xueqiu.com: HTML whose first bytes carry
# {"_waf_<hex>": ...} render data and a <meta name="aliyun_waf_aa"> tag.
_WAF_PAGE = re.compile(rb"aliyun_waf|_waf_[0-9a-f]{6,}", re.IGNORECASE)
_PATH_REFUSED_HINT = (
    "The host refuses this path, so retrying will not help. If an endpoint of this package "
    f"gets this, upgrade xueqiu-api or report it at {ISSUES_URL}."
)

COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
COOKIE_VALUE = re.compile(r"[!-:<-~]*")
"""Visible ASCII except ``;``: RFC 6265 cookie octets plus the double quotes, commas and
backslashes that browsers send."""


def usable_cookie(name: str, value: str) -> bool:
    """Whether a cookie can go into a Cookie header as it is."""
    return bool(COOKIE_NAME.fullmatch(name) and COOKIE_VALUE.fullmatch(value))


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
    """Whether the request carries session cookies."""
    params: Mapping[str, Any]
    """Caller-facing params for error payloads; credential-like keys are redacted later."""
    credentials: bool = False
    """Whether those cookies are the configured login cookies; otherwise the anonymous set."""
    needs_cookies: tuple[str, ...] = ()
    """Cookies that must be in the set for the request to be worth sending."""
    not_found: NotFound | None = None
    """Upstream errors of this endpoint that mean the requested code does not exist."""
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
    wait_s: float = 0.0
    """Least delay before a retry, from the upstream's Retry-After header."""
    hint: str | None = None
    """Hint of this failure; None keeps the default hint of its code."""
    trips_breaker: bool = False
    login: bool = False
    """A login-class upstream code; ``next_step`` decides which error it becomes."""

    def error(self, call: Call, cls: type[XueqiuError] | None = None, **extra: Any) -> XueqiuError:
        extra.setdefault("hint", self.hint)
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
    # Longest first: a value that starts with another must not leave its tail behind.
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def excerpt(content: bytes, secrets: Iterable[str], limit: int = 80) -> str:
    """Short single-line excerpt of a body with known secret values removed."""
    text = " ".join(scrub(content[:2000].decode("utf-8", "replace"), secrets).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def upstream_error(body: Any) -> tuple[str, str] | None:
    """Upstream error code and description of a body, or ``None``.

    A non-zero ``error_code`` (Xueqiu) or ``result_code`` (Danjuan), or the ``code`` of a query
    envelope with ``success`` false, such as ``{"code": 400016, "success": false}``.
    """
    if not isinstance(body, dict):
        return None
    fields = cast(dict[str, Any], body)
    for key in ("error_code", "result_code"):
        code = fields.get(key)
        if code is not None and code not in (0, "0", ""):
            description = fields.get("error_description") or fields.get("message") or ""
            return str(code), str(description)
    code = fields.get("code")
    if fields.get("success") is False and code not in (None, ""):
        return str(code), str(fields.get("message") or "")
    return None


def network_failure(exc: httpx2.RequestError | TimeoutError, call: Call) -> Failure:
    """Failure of an attempt without a response; ``TimeoutError`` is the call's deadline."""
    host = call.site.host
    if isinstance(exc, httpx2.TooManyRedirects):
        return Failure(
            code="upstream_changed",
            message=f"{call.url} redirected more than {MAX_REDIRECTS} times",
        )
    if isinstance(exc, httpx2.TimeoutException | TimeoutError):
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
            retry_after_s=BREAKER_COOLDOWN_S,
            trips_breaker=True,
        )
    if not is_json and _WAF_PAGE.search(result.content[:4096]):
        return Failure(
            code="blocked",
            message=f"{host} answered with a WAF challenge page (HTTP {status})",
            status=status,
            retry_after_s=BREAKER_COOLDOWN_S,
            trips_breaker=True,
        )
    if status == 403:
        # Seen as a path-level refusal (openresty 403 page) that lasts: the host keeps
        # answering other paths, so the breaker stays closed.
        return Failure(
            code="blocked",
            message=f"{host} refused the request (HTTP 403)",
            status=403,
            hint=_PATH_REFUSED_HINT,
        )
    if status == 429:
        return Failure(
            code="rate_limited",
            message=f"{host} rate limited the request (HTTP 429)",
            status=429,
            retry_after_s=_retry_after(result.headers, RATE_LIMIT_RETRY_AFTER_S),
        )
    if status >= 500:
        return Failure(
            code="upstream_unavailable",
            message=f"{host} returned HTTP {status}",
            status=status,
            retry_after_s=_retry_after(result.headers, UNAVAILABLE_RETRY_AFTER_S),
            wait_s=_retry_after(result.headers, 0.0),
        )
    if call.home:
        cookies = {
            name: value for name, value in result.cookies.items() if usable_cookie(name, value)
        }
        if status == 200 and cookies.get("xq_a_token"):
            return Ok(cookies)
        return Failure(
            code="upstream_changed",
            message=f"the home page returned HTTP {status} without a usable anonymous token cookie",
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
                retry_after_s=_retry_after(result.headers, RATE_LIMIT_RETRY_AFTER_S),
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
        log.debug("non-JSON body from %s: %r", host, excerpt(result.content, secrets))
        return Failure(
            code="upstream_changed",
            message=f"{host} returned a non-JSON body (HTTP {status}, {len(result.content)} bytes)",
            status=status,
        )
    return Ok(body)


def unwrap(envelope: Envelope, body: Any, *, may_lack_data: bool = False) -> Any:
    """Payload of a successful response; ``ValueError`` when the envelope does not match.

    With ``may_lack_data``, a danjuan envelope without a data key gives None: the upstream
    answers unknown codes that way, and ``parse`` raises ``SymbolNotFound``.
    """
    if envelope == "bare":
        return body
    if not isinstance(body, dict):
        raise ValueError(f"expected the {envelope} envelope with a data key")
    fields = cast(dict[str, Any], body)
    if envelope == "danjuan":
        if fields.get("result_code") not in (0, "0"):
            raise ValueError("expected the danjuan envelope with result_code 0")
        if may_lack_data:
            return fields.get("data")
    if "data" not in fields:
        raise ValueError(f"expected the {envelope} envelope with a data key")
    if envelope == "query" and fields.get("success") is not True:
        raise ValueError("expected the query envelope with success true")
    return fields["data"]


def _retry_after(headers: Mapping[str, str], default: float) -> float:
    """Seconds of the Retry-After header, or ``default`` without a usable one."""
    try:
        seconds = float(headers.get("retry-after", ""))
    except ValueError:
        return default
    return seconds if math.isfinite(seconds) and seconds >= 0 else default


def next_step(
    failure: Failure, *, call: Call, attempt: int, refreshed: bool
) -> Retry | XueqiuError:
    """Decide whether a failed attempt is retried, refreshes the session, or raises.

    Retries back off exponentially, and wait at least as long as a Retry-After header asks.
    Errors the endpoint declares as ``not_found`` raise ``symbol_not_found``.
    Login-class codes: a request that carried the configured credentials raises
    ``auth_expired``; one that carried anonymous cookies refreshes them once and retries. When
    that does not help, or no cookies were sent, the error is ``login_required`` if the
    endpoint is registered as needing login or unknown (raw requests). For an endpoint the
    registry lists as working without login it is ``upstream_changed``: the registered auth
    class no longer holds.
    """
    if failure.code == "upstream_unavailable" and attempt < MAX_RETRIES:
        return Retry(max(BACKOFF_BASE_S * 2**attempt, failure.wait_s))
    if call.not_found is not None and _means_not_found(failure, call.not_found):
        return failure.error(call, SymbolNotFound)
    if not failure.login:
        return failure.error(call)
    if call.credentials:
        return failure.error(call, AuthExpired)
    if call.cookies and not refreshed:
        return Retry(0.0, refresh=True)
    if call.auth is None and not call.cookies:
        hint = f"{call.site.host} takes no credentials from this package, so a token does not help."
        return failure.error(call, LoginRequired, hint=hint)
    if call.auth is None or call.auth == "required":
        return failure.error(call, LoginRequired)
    if not call.cookies:
        hint = (
            f"{call.endpoint} was verified to work without cookies and now returns a login "
            f"error; please report it at {ISSUES_URL}."
        )
    else:
        hint = (
            f"{call.endpoint} was verified to work without login and now returns a login "
            f"error; please report it at {ISSUES_URL}."
        )
    return failure.error(call, UpstreamChanged, hint=hint)


def _means_not_found(failure: Failure, rule: NotFound) -> bool:
    if failure.code not in ("upstream_error", "upstream_changed"):
        return False
    if failure.upstream_code is not None:
        return failure.upstream_code in rule.codes
    return failure.status in rule.statuses


class HostLimiter:
    """Minimum interval between request starts per host, shared by all calls of one client.

    ``reserve`` books the next free slot under a lock and returns the wait; it never sleeps,
    so threads of the sync client and tasks of the async client share it. ``pause`` holds a
    host that rate limited a request: calls to it fail fast until then.
    """

    def __init__(self, intervals: Mapping[str, float]) -> None:
        self._intervals = dict(intervals)
        self._next: dict[str, float] = {}
        self._paused_until: dict[str, float] = {}
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

    def pause(self, host: str, until: float) -> None:
        with self._lock:
            self._paused_until[host] = max(until, self._paused_until.get(host, until))

    def paused(self, host: str, now: float) -> float:
        """Seconds the host stays paused."""
        with self._lock:
            return max(0.0, self._paused_until.get(host, now) - now)

    def lease_home(self, deadline: float) -> float | None:
        """Take the lease on the home-page fetch: 0.0 when taken, else the seconds it stays held.

        None: no other process shares this limiter, so the caller fetches without one.
        """
        return None

    def release_home(self) -> None:
        """Give the home-page lease back; nothing to do without sharing."""


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
