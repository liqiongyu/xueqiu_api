"""Error taxonomy shared by the SDK, the CLI and the MCP server.

``ERROR_TABLE`` is the single place that maps each error code to its exception class, its
recovery class, its CLI exit code and its default hint.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Literal, cast

ErrorCode = Literal[
    "invalid_params",
    "unknown_endpoint",
    "symbol_not_found",
    "login_required",
    "auth_expired",
    "rate_limited",
    "blocked",
    "upstream_unavailable",
    "upstream_error",
    "upstream_changed",
    "disabled_by_config",
    "internal_error",
]
Recoverable = Literal["reauth", "backoff", "fix_params", "none"]

ISSUES_URL = "https://github.com/liqiongyu/xueqiu_api/issues"
REDACTED = "<redacted>"

_SECRET_KEY = re.compile(r"token|cookie|passw|secret|^session$|session_?id", re.IGNORECASE)


def redact_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Copy of ``params`` for error payloads: credential-like keys redacted, values JSON-safe."""
    return {
        key: REDACTED if _SECRET_KEY.search(key) else _json_safe(value)
        for key, value in params.items()
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float):
        return value
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in cast(list[Any], value)]
    if isinstance(value, dict):
        return redact_params({str(k): v for k, v in cast(dict[Any, Any], value).items()})
    return str(value)


class XueqiuError(Exception):
    """Base class of every error raised by this package.

    ``code`` is the stable error code; ``recoverable``, ``exit_code`` and the default ``hint``
    come from ``ERROR_TABLE``. ``params`` never holds credential values. ``field``,
    ``valid_values`` and ``candidates`` name the rejected input, its allowed values and the
    closest of them, when they are known.
    """

    code: ClassVar[ErrorCode] = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        endpoint: str | None = None,
        status: int | None = None,
        upstream_code: str | None = None,
        retry_after_s: float | None = None,
        params: Mapping[str, Any] | None = None,
        hint: str | None = None,
        field: str | None = None,
        valid_values: Sequence[str] = (),
        candidates: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.endpoint = endpoint
        self.status = status
        self.upstream_code = upstream_code
        self.retry_after_s = retry_after_s
        self.params = redact_params(params or {})
        self.hint = ERROR_TABLE[self.code].hint if hint is None else hint
        self.field = field
        self.valid_values = tuple(valid_values)
        self.candidates = tuple(candidates)

    @property
    def recoverable(self) -> Recoverable:
        return ERROR_TABLE[self.code].recoverable

    @property
    def exit_code(self) -> int:
        return ERROR_TABLE[self.code].exit_code

    def to_dict(self) -> dict[str, Any]:
        """Error payload with the fields the MCP error result and the CLI use."""
        payload: dict[str, Any] = {
            "error_code": self.code,
            "recoverable": self.recoverable,
            "message": self.message,
            "hint": self.hint,
        }
        extra = {
            "retry_after_s": self.retry_after_s,
            "endpoint": self.endpoint,
            "status": self.status,
            "upstream_code": self.upstream_code,
        }
        payload.update({key: value for key, value in extra.items() if value is not None})
        if self.field is not None:
            payload["field"] = self.field
        if self.valid_values:
            payload["valid_values"] = list(self.valid_values)
        if self.candidates:
            payload["candidates"] = list(self.candidates)
        if self.params:
            payload["params"] = self.params
        return payload

    def __repr__(self) -> str:
        fields = ", ".join(
            f"{key}={value!r}" for key, value in self.to_dict().items() if key != "message"
        )
        return f"{type(self).__name__}({self.message!r}, {fields})"


class InvalidParams(XueqiuError):
    """Parameters failed validation: wrong type, unknown name or a value outside the enum."""

    code = "invalid_params"


class UnknownEndpoint(XueqiuError):
    """No registered endpoint has this name."""

    code = "unknown_endpoint"


class SymbolNotFound(XueqiuError):
    """The code does not match a supported format, or the upstream has no such security."""

    code = "symbol_not_found"


class LoginRequired(XueqiuError):
    """The upstream wants a logged-in session and none was sent."""

    code = "login_required"


class AuthExpired(XueqiuError):
    """The configured credentials were rejected by the upstream."""

    code = "auth_expired"


class RateLimited(XueqiuError):
    """Too many requests, upstream or in the local per-host queue."""

    code = "rate_limited"


class Blocked(XueqiuError):
    """The host refused the request (HTTP 418, 403 or a WAF challenge page)."""

    code = "blocked"


class UpstreamUnavailable(XueqiuError):
    """HTTP 5xx, a timeout or a network error, after retries."""

    code = "upstream_unavailable"


class UpstreamError(XueqiuError):
    """The upstream returned a business error code not covered by another class."""

    code = "upstream_error"


class UpstreamChanged(XueqiuError):
    """The response no longer matches the registered envelope, model or auth class."""

    code = "upstream_changed"


class DisabledByConfig(XueqiuError):
    """The endpoint or tool is disabled by configuration."""

    code = "disabled_by_config"


class InternalError(XueqiuError):
    """A bug in this package."""

    code = "internal_error"


@dataclass(frozen=True)
class ErrorInfo:
    exception: type[XueqiuError]
    recoverable: Recoverable
    exit_code: int
    hint: str


_REAUTH_HINT = (
    "Set XUEQIU_TOKEN or pass token= to the client, then restart. "
    "Do not paste the token into a chat."
)

ERROR_TABLE: Mapping[ErrorCode, ErrorInfo] = MappingProxyType(
    {
        "invalid_params": ErrorInfo(
            InvalidParams,
            "fix_params",
            2,
            "Check the parameter names and values against the endpoint signature.",
        ),
        "unknown_endpoint": ErrorInfo(
            UnknownEndpoint, "fix_params", 3, "Use one of the registered endpoint names."
        ),
        "symbol_not_found": ErrorInfo(
            SymbolNotFound,
            "fix_params",
            4,
            "A shares are SH, SZ or BJ plus 6 digits, Hong Kong stocks 5 digits, US stocks "
            "their ticker. Indexes need a prefix: SH000001 is the Shanghai index, 000001 is "
            "SZ000001. Bonds and repos such as 113xxx or 204xxx need the prefix too. A code "
            "that was never listed and a delisted one get the same upstream answer.",
        ),
        "login_required": ErrorInfo(
            LoginRequired, "reauth", 5, "This data needs a logged-in session. " + _REAUTH_HINT
        ),
        "auth_expired": ErrorInfo(
            AuthExpired,
            "reauth",
            6,
            "The configured token was rejected. Log in again, update the token. " + _REAUTH_HINT,
        ),
        "rate_limited": ErrorInfo(
            RateLimited,
            "backoff",
            7,
            "Wait retry_after_s seconds before retrying, and merge symbols into one batch "
            "request where possible.",
        ),
        "blocked": ErrorInfo(
            Blocked,
            "backoff",
            8,
            "The host is refusing requests from this client. Retrying before retry_after_s "
            "will not help.",
        ),
        "upstream_unavailable": ErrorInfo(
            UpstreamUnavailable,
            "backoff",
            9,
            "The upstream did not answer. Retry after a short wait.",
        ),
        "upstream_error": ErrorInfo(
            UpstreamError,
            "none",
            10,
            "The upstream rejected this request. Check the parameters.",
        ),
        "upstream_changed": ErrorInfo(
            UpstreamChanged,
            "none",
            11,
            f"The upstream response no longer matches this package. Upgrade xueqiu-api or "
            f"report it at {ISSUES_URL}.",
        ),
        "disabled_by_config": ErrorInfo(
            DisabledByConfig, "none", 12, "This endpoint is disabled by the configuration."
        ),
        "internal_error": ErrorInfo(
            InternalError, "none", 1, f"This is a bug in xueqiu-api. Report it at {ISSUES_URL}."
        ),
    }
)
