"""Declarative endpoint specs.

Each module in ``xueqiu_api.endpoints`` defines one ``Endpoint`` named ``ENDPOINT``; the
package collects them into ``xueqiu_api.endpoints.REGISTRY``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from .errors import InvalidParams
from .sites import Host

Auth = Literal["anonymous", "optional", "required"]
Freshness = Literal["realtime", "intraday", "daily", "static"]
Envelope = Literal["xueqiu", "query"]
"""``xueqiu``: ``{"data": ..., "error_code": 0, "error_description": ...}``.

``query`` (``/query/v1/`` on www.xueqiu.com): ``{"code": 200, "success": true, "data": ...}``.
"""

P = TypeVar("P", bound=BaseModel)
R = TypeVar("R")


@dataclass(frozen=True, kw_only=True)
class AgentInfo:
    """What the MCP server and the CLI need to present an endpoint."""

    title_zh: str
    title_en: str
    summary: str
    """One Chinese sentence."""
    keywords: tuple[str, ...]
    """Chinese and English search terms."""
    default_fields: tuple[str, ...]
    """Fields rendered when the caller does not choose any."""
    row_default: int
    row_cap: int
    upstream_cap: int
    """Most items one upstream request accepts or returns."""
    examples: tuple[Mapping[str, Any], ...]
    """Parameter sets that validate against the params model."""
    mcp_tool: str | None = None
    """Dedicated MCP tool, if any; every endpoint is also reachable through ``xq_call``."""


@dataclass(frozen=True, kw_only=True)
class Endpoint(Generic[P, R]):
    """One upstream endpoint.

    ``build_query`` maps validated params and the current time to upstream query values; values
    whose names appear as ``{name}`` in ``path`` fill the path template instead. ``parse`` turns
    the unwrapped envelope payload into the result and raises ``ValueError`` or ``TypeError`` on
    a shape mismatch.
    """

    name: str
    host: Host
    path: str
    params: type[P]
    build_query: Callable[[P, datetime], dict[str, str]]
    parse: Callable[[Any, P], R]
    auth: Auth
    freshness: Freshness
    envelope: Envelope
    verified_on: date
    agent: AgentInfo
    cookies: bool = True
    """False when the endpoint was measured to work without cookies; then none are sent."""

    def validate(self, params: Mapping[str, Any]) -> P:
        """Validate caller params; raise ``InvalidParams`` listing every problem."""
        try:
            return self.params.model_validate(dict(params))
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'params'}: {error['msg']}"
                for error in exc.errors()
            )
            raise InvalidParams(
                f"invalid parameters for {self.name}: {problems}",
                endpoint=self.name,
                params={key: value for key, value in params.items() if value is not None},
            ) from None
