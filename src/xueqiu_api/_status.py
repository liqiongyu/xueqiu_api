"""What a client knows without sending a request, for ``doctor`` and the MCP status tool."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ._sqlite import StoreStatus


@dataclass(frozen=True)
class AuthError:
    """The last login-class error code an upstream answered."""

    upstream_code: str
    at: datetime
    """When it came, in UTC, on the client's wall clock."""


@dataclass(frozen=True)
class HostStatus:
    host: str
    interval_s: float
    """Least seconds between two request starts."""
    wait_s: float
    """Seconds until the next free slot."""
    paused_s: float
    """Seconds the host stays paused after a rate limit."""
    open_s: float
    """Seconds its circuit breaker stays open."""


@dataclass(frozen=True)
class ClientStatus:
    credentials: Literal["none", "token"]
    last_auth_error: AuthError | None
    hosts: tuple[HostStatus, ...]
    shared: StoreStatus | None
    """The state file shared with other processes; None when the client keeps it in process."""
