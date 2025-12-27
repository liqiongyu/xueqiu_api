from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class XueqiuError(Exception):
    """Base exception for this SDK."""


class XueqiuAuthError(XueqiuError):
    """Raised when an endpoint requires auth but no cookie was provided."""


@dataclass(frozen=True, slots=True)
class XueqiuHTTPError(XueqiuError):
    status_code: int
    url: str
    method: str | None = None
    response_text: str | None = None

    def __str__(self) -> str:
        method = f"{self.method} " if self.method else ""
        msg = f"HTTP {self.status_code} for {method}{self.url}"
        if self.response_text:
            return f"{msg}: {self.response_text}"
        return msg


@dataclass(frozen=True, slots=True)
class XueqiuDecodeError(XueqiuError):
    url: str
    message: str
    method: str | None = None
    response_text: str | None = None

    def __str__(self) -> str:
        method = f"{self.method} " if self.method else ""
        msg = f"Failed to decode JSON for {method}{self.url}: {self.message}"
        if self.response_text:
            return f"{msg}: {self.response_text}"
        return msg


@dataclass(frozen=True, slots=True)
class XueqiuAPIError(XueqiuError):
    error_code: int
    error_description: str | None
    url: str
    payload: Any | None = None
    method: str | None = None

    def __str__(self) -> str:
        method = f"{self.method} " if self.method else ""
        desc = self.error_description or ""
        return f"Xueqiu API error {self.error_code} for {method}{self.url}: {desc}".rstrip()
