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
    response_text: str | None = None

    def __str__(self) -> str:
        msg = f"HTTP {self.status_code} for {self.url}"
        if self.response_text:
            return f"{msg}: {self.response_text}"
        return msg


@dataclass(frozen=True, slots=True)
class XueqiuDecodeError(XueqiuError):
    url: str
    message: str

    def __str__(self) -> str:
        return f"Failed to decode JSON for {self.url}: {self.message}"


@dataclass(frozen=True, slots=True)
class XueqiuAPIError(XueqiuError):
    error_code: int
    error_description: str | None
    url: str
    payload: Any | None = None

    def __str__(self) -> str:
        desc = self.error_description or ""
        return f"Xueqiu API error {self.error_code} for {self.url}: {desc}".rstrip()


